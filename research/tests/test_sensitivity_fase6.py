"""Fase 6: predeclared policy sensitivity configs and campaign provenance."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.campaign_v3 import make_plan


class SensitivityTests(unittest.TestCase):
    def test_prespecified_profile_has_baselines_and_five_adaptive_variants(self):
        plan=make_plan('sensitivity')
        self.assertEqual(len(plan),8)
        self.assertEqual({r['variant'] for r in plan if r['mode']=='A1'},
                         {'original','weights_balanced','weights_resource','budget_1500','budget_900'})
        self.assertEqual({r['fixed_interval'] for r in plan if r['mode']=='B0'}, {2.,3.,4.})

    def test_comparable_policy_config_and_deterministic_order(self):
        plan=make_plan('sensitivity',seed=68754)
        self.assertEqual(plan,make_plan('sensitivity',seed=68754))
        for trial in plan:
            cfg=trial['policy_env']
            self.assertAlmostEqual(sum(float(cfg[k]) for k in ('W_COMM','W_ENERGY','W_ERROR','W_AOI')),1.)
            self.assertGreater(float(cfg['COMM_BUDGET_BPS']),0)
        self.assertNotEqual([r['variant'] for r in plan],
                            [r['variant'] for r in make_plan('sensitivity',seed=1337)])

    def test_older_diagnostic_profile_unchanged(self):
        previous=make_plan('diagnostic')
        self.assertEqual(len(previous),7)
        self.assertTrue(all('policy_env' not in x for x in previous))

if __name__=='__main__': unittest.main()
