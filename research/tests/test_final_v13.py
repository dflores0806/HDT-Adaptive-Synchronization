"""Purely local tests; no Docker and no generated scientific results."""
import json
import sys
import unittest
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research.final_campaign_v13 import (prepare_specs,stamp_manifest,enforce_gate,COMMON,LIMITS)
from research.final_report_v13 import (assess_run,recovery_metrics,resource_metrics,comparisons)

class FinalCampaignV13Tests(unittest.TestCase):
    def test_locked_matrix_and_order(self):
        x=prepare_specs();y=prepare_specs()
        self.assertEqual(x,y)
        self.assertEqual(len(x),63)
        self.assertEqual(Counter(t['block'] for t in x),
                         {'gate_transport':2,'gate_capacity':1,'scalability':36,
                          'churn':9,'ablations':6,'dynamic':9})
        self.assertEqual([x[i]['block'] for i in (0,1,2)],
                         ['gate_transport','gate_transport','gate_capacity'])
        self.assertEqual(set(t['n'] for t in x if t['block']=='scalability'),{4,8,16,32})
    def test_within_n_and_source_common_constraints(self):
        x=prepare_specs()
        for spec in x:
            self.assertEqual(spec['policy_env']['COMM_BUDGET_BPS'],str(1500*spec['n']//4))
            self.assertEqual({k:spec['policy_env'][k] for k in COMMON},COMMON)
            self.assertEqual(spec['scenario_id'],spec['repeat']+1)
        for block in ('scalability','churn','dynamic','ablations'):
            for n in set(x['n'] for x in x if x['block']==block):
                for source in set(xi['scenario_id'] for xi in x if xi['block']==block and xi['n']==n):
                    v=[q for q in x if q['block']==block and q['n']==n and q['scenario_id']==source]
                    self.assertEqual(len(set(q['policy_env']['COMM_BUDGET_BPS'] for q in v)),1)
    def test_run_ids_and_no_rerandomization(self):
        m=stamp_manifest()
        self.assertEqual(len(set(t['run_id'] for t in m['tasks'])),63)
        self.assertTrue(all(len(t['run_id'])<=64 for t in m['tasks']))
    def test_gate_rejects_nonstress(self):
        valid=[{'quality_ok':True,'stress_p95_ms':19},
               {'quality_ok':True,'stress_p95_ms':229,'injected_drop_attempts':9},
               {'quality_ok':True}]
        self.assertTrue(enforce_gate(valid)[0])
        valid[1]['stress_p95_ms']=85
        self.assertFalse(enforce_gate(valid)[0])
    def test_quality_bad_missing_metadata_not_eligible(self):
        t=stamp_manifest()['tasks'][0]
        out=assess_run({},t)
        self.assertFalse(out['quality_ok'])
        self.assertIn('incomplete',out['quality_flags'])
    def test_http_load_not_broker_queue(self):
        values=resource_metrics([{'http_ingress_metrics':{'inflight':2,
               'last_2048_ingress_wall_ms_p95':4.2}},
            {'docker_stats':{'CPUPerc':'1.5%','MemUsage':'30MiB / 8GiB'}}])
        self.assertEqual(values['http_inflight_max_sampled'],2)
        self.assertIsNone(values.get('queue_occupancy'))
        self.assertGreater(values['docker_memory_mb_max_single_container'],30)
    def test_churn_recovery_timestamp_and_rejects(self):
        base='runs/x/';meta={'churn_fraction':.5,'n':4}
        files={base+'churn_events.json':json.dumps([
          {'event':'failure_complete','ts_utc':'2026-11-04T10:00:00Z','node_ids':['node-000','node-001']},
          {'event':'recovery_restart_complete','ts_utc':'2026-11-04T10:00:50Z','node_ids':['node-000','node-001']}
        ]).encode()}
        updates=[{'node_id':'node-000','ts':'2026-11-04T10:00:52Z','accepted':'t'},
                 {'node_id':'node-001','ts':'2026-11-04T10:00:54Z','accepted':'t'},
                 {'node_id':'node-000','ts':'2026-11-04T10:00:55Z','accepted':'f'}]
        result=recovery_metrics(files,base,meta,updates)
        self.assertTrue(result['churn_state_reconciliation_complete'])
        self.assertEqual(result['churn_recovery_all_s'],4)
        self.assertEqual(result['churn_rejected_updates'],1)
    def test_nameless_matched_returns_no_match(self):
        rows=[{'block':'scalability','n':16,'scenario_id':1,'quality_ok':True,
               'mode':'A1','variant':'joint','predicted_feasible_fraction':1.,
               'predicted_violations':0,'dense_synthetic_nmae':.013,
               'operational_received_payload_bytes':2000}]
        result=comparisons(rows)
        self.assertEqual([r['status'] for r in result],['no_match','no_match'])

if __name__=='__main__':unittest.main()
