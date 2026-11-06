"""FastAPI/SQLAlchemy instrumentation tests against an isolated SQLite database.
Not a substitute for a full Mosquitto + PostgreSQL + Docker experiment.
"""
import sys
import os
os.environ['POSTGRES_DSN'] = 'sqlite://'
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from middleware.models import Base, StateUpdate, ReferenceSample, ExperimentConfig
from middleware.routers import telemetry, admin
from middleware.services.action_controller import NodeEstimate, ControlLimits
from middleware.services.strategies import choose_action

class InstrumentationTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread': False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session=sessionmaker(bind=self.engine)
        self.dbpatch=patch.object(telemetry,'get_session',self.session)
        self.dbpatch.start()
        self.adminpatch=patch.object(admin,'get_session',self.session)
        self.adminpatch.start()
        app=FastAPI()
        app.include_router(telemetry.router)
        app.include_router(admin.router)
        self.client=TestClient(app)
        self.client.post('/admin/experiment',json={'run_id':'unit_test','mode':'A1',
                'fixed_interval_s':2.,'aoi_threshold_s':6.}).raise_for_status()

    def tearDown(self):
        self.dbpatch.stop()
        self.adminpatch.stop()
        self.engine.dispose()

    def payload(self, seq=1, t='2026-10-29T09:00:00+00:00'):
        return {'node_id':'test-1','sample_time':t,'seq':seq,
                'state':{'temperature_c':23.0,'load_fraction':0.3,'cooling_fraction':0.4}}

    def test_reference_is_separate_from_operational_state(self):
        r=self.client.post('/telemetry/reference',json=self.payload())
        self.assertTrue(r.json()['ok'])
        with self.session() as s:
            self.assertEqual(s.query(ReferenceSample).count(),1)
            self.assertEqual(s.query(StateUpdate).count(),0)

    def test_duplicate_and_out_of_order_rejected_but_audited(self):
        self.assertTrue(self.client.post('/telemetry/state',json=self.payload()).json()['ok'])
        self.assertFalse(self.client.post('/telemetry/state',json=self.payload()).json()['ok'])
        self.assertFalse(self.client.post('/telemetry/state',json=self.payload(seq=0)).json()['ok'])
        with self.session() as s:
            rows=s.execute(select(StateUpdate)).scalars().all()
            self.assertEqual(len(rows),3)
            self.assertEqual(sum(x.accepted for x in rows),1)
            self.assertEqual(sum(x.payload_bytes for x in rows)>0,True)

    def test_stale_run_is_rejected(self):
        payload=self.payload()
        payload['run_id']='a_previous_run'
        self.assertEqual(self.client.post('/telemetry/state',json=payload).status_code,409)
        self.assertEqual(self.client.post('/telemetry/reference',json=payload).status_code,409)
        self.assertEqual(self.client.post('/telemetry/heartbeat',json={
            'run_id':'a_previous_run','node_id':'test-1','subsystem':'rack',
            'latency_ms':1.,'energy_w':7.,'drift_rate_norm_s':.01,
            'bytes_per_update':100}).status_code,409)
        with self.session() as sess:
            self.assertEqual(sess.query(StateUpdate).count(),0)
            self.assertEqual(sess.query(ReferenceSample).count(),0)

    def test_mode_is_persisted(self):
        res=self.client.post('/admin/experiment',json={'run_id':'new_run','mode':'B0',
            'fixed_interval_s':4.,'aoi_threshold_s':5.})
        self.assertEqual(res.status_code,200)
        with self.session() as s:
            self.assertEqual(s.get(ExperimentConfig,1).mode,'B0')
            self.assertEqual(s.get(ExperimentConfig,1).run_id,'new_run')

    def test_guard_mode_can_be_configured_via_api(self):
        result=self.client.post('/admin/experiment',json={
            'run_id':'guard_trial','mode':'B1_guard',
            'fixed_interval_s':2.,'aoi_threshold_s':6.})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['mode'],'B1_guard')

    def test_all_six_modes_change_actual_selection(self):
        nodes=[NodeEstimate('a','compute',0.1,0.,.001),
               NodeEstimate('b','rack',8.,.008,.001)]
        limits=ControlLimits(comm_budget_Bps=5000.,energy_budget_Jps=2.)
        b0=choose_action(nodes,limits,'B0',2.,6.)
        b1=choose_action(nodes,limits,'B1',2.,6.)
        b1_guard=choose_action(nodes,limits,'B1_guard',2.,6.)
        a1=choose_action(nodes,limits,'A1',2.,6.)
        self.assertEqual(b0.selected,{'a','b'})
        self.assertEqual(b1.selected,{'b'})
        self.assertEqual(b1_guard.selected,{'a','b'})
        self.assertNotEqual(b0.selected,b1.selected)
        self.assertTrue(a1.selected)
        self.assertEqual(choose_action(nodes,limits,'A1_interval',2.,6.).selected,{'a','b'})
        self.assertEqual(choose_action(nodes,limits,'A1_selection',2.,6.).interval_s,2.)

if __name__=='__main__':unittest.main()
