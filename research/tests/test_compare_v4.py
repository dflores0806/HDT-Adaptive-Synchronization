import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from importlib.machinery import SourceFileLoader

from research.compare_v4 import examine_campaign


def mk_summary():
    return {
      'metadata': {'run_id':'run0','mode':'A1','n':4,'run_complete':True},
      'communication': {'operational_http_payload_bytes':10000,'operational_state_bytes':6000,'monitoring_heartbeat_bytes':4000,'evaluation_reference_bytes':1000,'total_recorded_http_payload_bytes':11000,'state_attempts_observed_by_server':10,'state_rejected_or_duplicate':0},
      'reference_fidelity':{'nmae':.01,'reference_cadence_coverage_fraction':1.},
      'dense_synthetic_oracle_fidelity':{'nmae':.012,'aoi_p95_s':3.,'unmatched_reference_fraction':0.},
      'controller':{'decision_count':60,'decision_horizons_s':[2.], 'feasible_fraction':1.,'violation_counts':{},'policy_runtime_p95_ms':5.}
    }

class CompareV4Tests(unittest.TestCase):
    def write_zip(self,filename,coverage=1.,decisions=60):
        plan={'campaign_id':'test','profile':'replication','measurement_s':120,
              'tasks':[{'run_id':'run0','status':'complete','config':{'mode':'A1','n':4,'repeat':0,'variant':'original','fixed_interval':2.,'aoi_threshold':6.,'policy_env':{'W_COMM':'0.35'}}},
                       {'run_id':'run1','status':'complete','config':{'mode':'A1','n':4,'repeat':0,'variant':'budget_1500','fixed_interval':2.,'aoi_threshold':6.,'policy_env':{'W_COMM':'0.35','COMM_BUDGET_BPS':'1500'}}}]}
        with zipfile.ZipFile(filename,'w') as z:
            z.writestr('camp/plan.json',json.dumps(plan))
            for run in ('run0','run1'):
                summary=mk_summary()
                summary['metadata']['run_id']=run
                summary['reference_fidelity']['reference_cadence_coverage_fraction']=coverage
                summary['controller']['decision_count']=decisions
                z.writestr(f'camp/runs/{run}/summary_v2.json',json.dumps(summary))
                z.writestr(f'camp/runs/{run}/metadata.json',json.dumps({'start_utc':'2026-11-03T13:00:00+00:00','end_utc':'2026-11-03T13:02:00+00:00','run_complete':True}))
    def test_a1_variants_never_merged(self):
        with tempfile.TemporaryDirectory() as p:
            f=Path(p)/'run.zip';self.write_zip(f)
            result=examine_campaign(f)
            self.assertEqual(result['summary']['planned'],2)
            self.assertEqual(result['summary']['n_variants'],2)
            self.assertEqual({x['variant'] for x in result['groups']},{'original','budget_1500'})
            self.assertEqual(result['summary']['quality_ok'],2)
    def test_cadence_quality_is_flag_not_data_removal(self):
        with tempfile.TemporaryDirectory() as p:
            f=Path(p)/'run.zip';self.write_zip(f,coverage=.80,decisions=50)
            result=examine_campaign(f)
            self.assertEqual(result['summary']['complete'],2)
            self.assertEqual(result['summary']['quality_ok'],0)
            self.assertEqual(len(result['runs']),2)
            self.assertTrue(all('reference_cadence_below_threshold' in x['quality_issues'] for x in result['runs']))

if __name__=='__main__': unittest.main()
