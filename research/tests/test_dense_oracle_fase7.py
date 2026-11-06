"""Offline dense-oracle and replication-profile regression checks."""
import json
import tempfile
import unittest
from pathlib import Path
from research.campaign_v3 import make_plan
from analyze_v2 import evaluate_run

class DenseOracleTests(unittest.TestCase):
    def test_dense_oracle_is_separate_from_observed_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate_run(Path(tmp))
            self.assertIn('dense_synthetic_oracle_fidelity', report)
            self.assertIsNone(report['dense_synthetic_oracle_fidelity'])

    def test_replicated_profile_locks_configurations_in_each_block(self):
        trials = make_plan('replication', seed=20261030)
        self.assertEqual(len(trials), 21)
        self.assertEqual(sorted(set(t['repeat'] for t in trials)), [0,1,2])
        for repeat in range(3):
            block = [t for t in trials if t['repeat'] == repeat]
            self.assertEqual(len(block), 7)
            self.assertEqual(set(t['variant'] for t in block),
                             {'fixed_2s','fixed_3s','fixed_4s','aoi_6s','original','budget_1500','weights_resource'})
            self.assertTrue(all(t['n'] == 4 for t in block))
            self.assertTrue(all(t['churn_fraction'] == 0 for t in block))
            self.assertTrue(all('policy_env' in t for t in block))

    def test_replication_order_is_repeatable(self):
        a = make_plan('replication', seed=20261030)
        b = make_plan('replication', seed=20261030)
        self.assertEqual(a,b)

if __name__ == '__main__': unittest.main()
