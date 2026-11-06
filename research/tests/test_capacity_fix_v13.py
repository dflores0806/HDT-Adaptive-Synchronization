"""Regression coverage for the Docker-32 capacity investigation.

Tests verify that synchronous persistence is no longer executed on ASGI's
main event loop and that the standalone capacity gate cannot be mistaken
for the complete 63-trial scientific campaign.
"""
import asyncio
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("POSTGRES_DSN", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
from fastapi import FastAPI
from middleware.routers import telemetry
from research.final_campaign_v13 import capacity_manifest, prepare_specs, enforce_gate


class NoDatabaseSession:
    def __init__(self, thread_ids):
        self.thread_ids = thread_ids

    def __enter__(self):
        # Simulates a blocking database transaction without depending on SQLite
        # StaticPool's unsafe concurrent use of one connection.
        self.thread_ids.append(threading.get_ident())
        time.sleep(0.015)
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, model, id):
        return SimpleNamespace(run_id='capacity-test')

    def add(self, row):
        pass

    def commit(self):
        pass


class CapacityFixTests(unittest.TestCase):
    def test_synchronous_reference_persistence_runs_off_event_loop(self):
        threads = []
        app = FastAPI()
        app.include_router(telemetry.router)
        payload = {
            'run_id': 'capacity-test',
            'node_id': 'test-001', 'seq': 1,
            'sample_time': '2026-11-04T10:00:00+00:00',
            'state': {'temperature_c': 25.0, 'load_fraction': 0.5, 'cooling_fraction': 0.3},
        }

        async def probe():
            loop_thread = threading.get_ident()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
                results = await asyncio.gather(*[
                    client.post('/telemetry/reference', json=payload) for _ in range(8)
                ])
            return loop_thread, results

        with patch.object(telemetry, 'get_session', lambda: NoDatabaseSession(threads)):
            loop_thread, results = asyncio.run(probe())
        self.assertEqual(len(threads), 8)
        self.assertTrue(all(t != loop_thread for t in threads),
                        'Blocking SQLAlchemy must not run on the ASGI loop')
        self.assertTrue(all(r.status_code == 200 and r.json()['ok'] for r in results))

    def test_heartbeat_persistence_runs_off_event_loop(self):
        threads = []
        app = FastAPI()
        app.include_router(telemetry.router)
        payload = {
            'run_id': 'capacity-test', 'node_id': 'test-001', 'subsystem': 'rack',
            'latency_ms': 10., 'energy_w': 8., 'drift_rate_norm_s': .01,
            'bytes_per_update': 200,
        }

        async def probe():
            loop_thread = threading.get_ident()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url='http://testserver') as client:
                result = await client.post('/telemetry/heartbeat', json=payload)
            return loop_thread, result

        with patch.object(telemetry, 'get_session', lambda: NoDatabaseSession(threads)):
            loop_thread, result = asyncio.run(probe())
        self.assertEqual(result.status_code, 200)
        self.assertTrue(threads and all(t != loop_thread for t in threads))

    def test_standalone_capacity_gate_reuses_original_spec_and_quality(self):
        manifest = capacity_manifest()
        self.assertEqual(manifest['profile'], 'diagnostic_capacity_only')
        self.assertEqual(len(manifest['tasks']), 1)
        self.assertEqual(manifest['tasks'][0]['config'],
                         next(x for x in prepare_specs() if x['block'] == 'gate_capacity'))
        self.assertEqual(len(prepare_specs()), 63)
        self.assertTrue(manifest['protocol']['capacity_diagnostic_only'])
        self.assertFalse(enforce_gate([
            {'quality_ok': True, 'stress_p95_ms': 10},
            {'quality_ok': True, 'stress_p95_ms': 210, 'injected_drop_attempts': 12},
            {'quality_ok': False}
        ])[0])


if __name__ == '__main__':
    unittest.main()
