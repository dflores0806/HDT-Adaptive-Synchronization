"""Phase 4: reference-truth chronology, campaign IDs, and pilot checks."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from analyze_v2 import truth_errors
from research.campaign_v3 import make_plan
from research.package_campaign_v4 import package
from research.validate_pilot_v4 import check_campaign

STATE={'temperature_c':20.,'load_fraction':.5,'cooling_fraction':.5}
TRUTH={'temperature_c':24.,'load_fraction':.6,'cooling_fraction':.6}

class Phase4Tests(unittest.TestCase):
    def test_reference_does_not_see_future_arrivals(self):
        synced=pd.DataFrame([
            {'node_id':'n1','sample_time':'2026-10-29T09:00:00Z',
             'ts':'2026-10-29T09:00:00.1Z','state':json.dumps(STATE)},
            {'node_id':'n1','sample_time':'2026-10-29T09:00:01Z',
             'ts':'2026-10-29T09:00:04Z','state':json.dumps(TRUTH)}])
        truth=pd.DataFrame([
            {'node_id':'n1','sample_time':'2026-10-29T09:00:02Z',
             'ts':'2026-10-29T09:00:03Z','state':json.dumps(TRUTH)}])
        for f in (synced,truth):
            for k in ('ts','sample_time'):
                f[k]=pd.to_datetime(f[k],utc=True,format="ISO8601")
        result=truth_errors(truth,synced)
        self.assertEqual(len(result),1)
        self.assertAlmostEqual(result[0]['error_norm'],.1)
        self.assertAlmostEqual(result[0]['aoi_s'],2.)

    def test_future_arrival_without_old_state_is_unmatched(self):
        synced=pd.DataFrame([{'node_id':'n1','sample_time':pd.Timestamp('2026-10-29T09:00:01Z'),
                              'ts':pd.Timestamp('2026-10-29T09:00:04Z'),'state':json.dumps(STATE)}])
        truth=pd.DataFrame([{'node_id':'n1','sample_time':pd.Timestamp('2026-10-29T09:00:02Z'),
                             'ts':pd.Timestamp('2026-10-29T09:00:03Z'),'state':json.dumps(TRUTH)}])
        self.assertEqual(truth_errors(truth,synced),[])

    def test_pilot_has_only_three_modes_and_uses_single_repeat(self):
        plan=make_plan('pilot',seed=68754)
        self.assertEqual(len(plan),3)
        self.assertEqual({p['mode'] for p in plan},{'B0','B1','A1'})
        self.assertTrue(all(p['n']==4 for p in plan))

    def test_package_diagnostic_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'iotj_demo';folder.mkdir()
            (folder/'plan.json').write_text('{"tasks": []}')
            (folder/'logs').mkdir()
            (folder/'logs'/'a.log').write_text('safe log')
            path=Path(tmp)/'bundle.zip'
            self.assertEqual(package(folder,path),2)
            self.assertTrue(path.exists())
            self.assertFalse(check_campaign(folder)['integrity_ok'])

if __name__=='__main__':unittest.main()
