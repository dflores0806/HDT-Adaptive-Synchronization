"""Safety/regression checks for capacity diagnostics, independent of Docker."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from middleware.routers.telemetry import timed_store

ROOT = Path(__file__).resolve().parents[2]


class CapacityObservabilityTests(unittest.TestCase):
    def test_logs_slow_sql_transaction_without_changing_result(self):
        with patch('middleware.routers.telemetry.time.perf_counter', side_effect=[1.0, 1.4]):
            with self.assertLogs('hdt.capacity.db', level='WARNING') as captured:
                result = timed_store('reference', lambda a, b: a + b, 4, 7)
        self.assertEqual(result, 11)
        self.assertIn('HDT_DB_SLOW', captured.output[0])
        self.assertIn('reference', captured.output[0])

    def test_capacity_diagnostics_keep_protocol_and_retain_server_logs(self):
        compose = (ROOT/'docker-compose.yml').read_text(encoding='utf-8')
        self.assertIn('HDT_DB_POOL_SIZE:', compose)
        self.assertIn('log_min_duration_statement=250', compose)
        runner = (ROOT/'exp_iotj_run.py').read_text(encoding='utf-8')
        self.assertIn("export_scheduler_logs(['policy','middleware','postgres',*nodes]", runner)
        from research.final_campaign_v13 import prepare_specs, capacity_manifest
        self.assertEqual(len(prepare_specs()), 63)
        self.assertEqual(len(capacity_manifest()['tasks']), 1)

if __name__ == '__main__':
    unittest.main()
