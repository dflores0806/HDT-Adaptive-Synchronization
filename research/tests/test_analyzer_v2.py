"""Reference-based evaluator contract on hand-constructed, known numeric records."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from analyze_v2 import evaluate_run

class AnalyzerTests(unittest.TestCase):
    def test_nmae_uses_last_prior_state_and_reports_unmatched(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            t0='2026-10-29T09:00:00+00:00'
            t1='2026-10-29T09:00:01+00:00'
            state={'temperature_c':20.,'load_fraction':.5,'cooling_fraction':.5}
            truth={'temperature_c':24.,'load_fraction':.6,'cooling_fraction':.6}
            pd.DataFrame([{'node_id':'n1','ts':t0,'sample_time':t0,'state':json.dumps(state),'accepted':True,'payload_bytes':100}]).to_csv(d/'updates.csv',index=False)
            pd.DataFrame([{'node_id':'n1','ts':t1,'sample_time':t1,'state':json.dumps(truth),'payload_bytes':100}]).to_csv(d/'reference.csv',index=False)
            pd.DataFrame([{'node_id':'n1','ts':t1,'heartbeat_bytes':50}]).to_csv(d/'heartbeats.csv',index=False)
            pd.DataFrame([{'ts':t1,'runtime_ms':12.,'feasible':True}]).to_csv(d/'decisions.csv',index=False)
            (d/'metadata.json').write_text(json.dumps({'n':1,'start_utc':t0,'end_utc':'2026-10-29T09:00:02+00:00'}))
            result=evaluate_run(d)
            self.assertAlmostEqual(result['reference_fidelity']['nmae'],.1)
            self.assertAlmostEqual(result['reference_fidelity']['aoi_p95_s'],1.)
            self.assertEqual(result['reference_fidelity']['matched_rows'],1)
            self.assertEqual(result['communication']['total_recorded_http_payload_bytes'],250)

if __name__=='__main__':unittest.main()
