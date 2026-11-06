"""Contract tests added for the reproducibility defects identified in Phase 3."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from exp_iotj_run import build_node_override
from synthetic_process import physical_state, node_seed
from middleware.services.action_controller import NodeEstimate, ControlLimits, decide
from analyze_v2 import evaluate_run, synthetic_oracle_dataframe


class ExperimentalIntegrityTests(unittest.TestCase):
    def test_stable_node_identity_and_run_tag(self):
        one,names1=build_node_override(3,1740000000.,'run_A1')
        two,names2=build_node_override(3,1740000000.,'run_B0')
        self.assertEqual(names1,names2)
        self.assertIn('NODE_ID: node-000',one)
        self.assertIn('NODE_ID: node-002',two)
        self.assertIn('RUN_ID: "run_A1"',one)
        self.assertIn('RUN_ID: "run_B0"',two)
        self.assertIn('PHYSICAL_EPOCH: "1740000000.000000"',two)

    def test_synthetic_model_is_deterministic_and_run_independent(self):
        self.assertEqual(node_seed('node-001'),node_seed('node-001'))
        self.assertEqual(physical_state('node-001',15.),physical_state('node-001',15.))
        self.assertNotEqual(physical_state('node-001',15.),physical_state('node-001',25.))

    def test_simulation_oracle_continues_through_node_outage(self):
        data={'physical_epoch_unix_s':1740000000.0,
              'node_ids':['node-000','node-001'],
              'start_utc':'2025-02-19T21:20:00+00:00',
              'end_utc':'2025-02-19T21:20:12+00:00'}
        frame=synthetic_oracle_dataframe(data,2.)
        self.assertEqual(len(frame),12)
        self.assertEqual(set(frame.node_id),set(data['node_ids']))

    def test_warmup_state_used_for_fidelity_not_for_measured_bytes(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            first='2026-10-29T09:00:00Z'
            second='2026-10-29T09:00:02Z'
            prev='2026-10-29T08:59:59Z'
            state={'temperature_c':20.,'load_fraction':.5,'cooling_fraction':.5}
            same=json.dumps(state)
            pd.DataFrame([{'node_id':'n1','ts':prev,'sample_time':prev,'state':same,'accepted':True,
                           'payload_bytes':999}]).to_csv(d/'initial_state.csv',index=False)
            pd.DataFrame(columns=['node_id','ts','sample_time','state','accepted','payload_bytes']).to_csv(d/'updates.csv',index=False)
            pd.DataFrame([{'node_id':'n1','ts':second,'sample_time':second,'state':same,
                           'payload_bytes':120}]).to_csv(d/'reference.csv',index=False)
            pd.DataFrame(columns=['node_id','ts','heartbeat_bytes']).to_csv(d/'heartbeats.csv',index=False)
            pd.DataFrame(columns=['ts','runtime_ms','feasible']).to_csv(d/'decisions.csv',index=False)
            (d/'metadata.json').write_text(json.dumps({'n':1,'start_utc':first,'end_utc':second}))
            result=evaluate_run(d)
            self.assertEqual(result['reference_fidelity']['matched_rows'],1)
            self.assertEqual(result['reference_fidelity']['nmae'],0.)
            self.assertEqual(result['communication']['total_recorded_http_payload_bytes'],120)

    def test_reference_cadence_coverage_never_exceeds_one(self):
        import pandas as pd
        from analyze_v2 import summarize_reference
        now=pd.Timestamp('2026-10-29T12:00:00Z')
        later=now+pd.Timedelta(seconds=2)
        state=json.dumps({'temperature_c':21.,'load_fraction':.5,'cooling_fraction':.5})
        reference=pd.DataFrame([{'node_id':'n1','sample_time':later,'state':state}])
        synced=pd.DataFrame([{'node_id':'n1','ts':now,'sample_time':now,'state':state}])
        meta={'n':1,'start_utc':str(now),'end_utc':str(now+pd.Timedelta(seconds=1.999))}
        results=summarize_reference(reference,synced,meta)
        self.assertEqual(results['reference_cadence_coverage_fraction'],1.)
        self.assertGreater(results['reference_cadence_ratio_uncapped'],1.)

    def test_tighter_budget_does_not_force_more_sync_traffic(self):
        nodes=[NodeEstimate('node-a','rack',0,0,.002,bytes_per_update=256),
               NodeEstimate('node-b','cooling',0,0,.002,bytes_per_update=256)]
        roomy=ControlLimits(candidate_intervals_s=(1.,2.,4.,8.),comm_budget_Bps=1000.,energy_budget_Jps=3.)
        tight=ControlLimits(candidate_intervals_s=(1.,2.,4.,8.),comm_budget_Bps=200.,energy_budget_Jps=3.)
        a=decide(nodes,roomy)
        b=decide(nodes,tight)
        self.assertTrue(a.feasible)
        self.assertTrue(b.feasible)
        self.assertLessEqual(b.comm_Bps,a.comm_Bps)
        self.assertGreaterEqual(b.interval_s,a.interval_s)

if __name__=='__main__':unittest.main()
