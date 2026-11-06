"""Offline structural checks: synthetic inputs, no claims of experimental fidelity."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.controller_v2 import NodeEstimate, ControlLimits, decide, evaluate, discrete_oracle


def example(age=0., drift=0.003):
    return [NodeEstimate('a', 'cooling', age, 0., drift),
            NodeEstimate('b', 'compute', age, 0., drift)]


class ControllerTests(unittest.TestCase):
    def test_action_dependent_objective_and_communication(self):
        nodes = example()
        lim = ControlLimits()
        short = evaluate(nodes, lim, 1., {'a','b'})
        long = evaluate(nodes, lim, 4., {'a','b'})
        self.assertGreater(short.comm_Bps, long.comm_Bps)
        self.assertGreater(long.max_error_norm, short.max_error_norm)
        self.assertNotEqual(short.objective, long.objective)

    def test_more_fidelity_pressure_shortens_interval(self):
        lim = ControlLimits(candidate_intervals_s=(1., 2., 4., 8.),
                            error_max_norm=0.06, aoi_max_s=10., comm_budget_Bps=2000.)
        normal = decide(example(drift=0.001), lim)
        high = decide(example(drift=0.025), lim)
        self.assertTrue(normal.feasible)
        self.assertTrue(high.feasible)
        self.assertLessEqual(high.interval_s, normal.interval_s)
        self.assertLess(high.interval_s, 4.)

    def test_tighter_communication_budget_forces_lower_traffic(self):
        lim = ControlLimits(comm_budget_Bps=300., aoi_max_s=10.)
        d = decide(example(drift=0.001), lim)
        self.assertTrue(d.feasible)
        self.assertLessEqual(d.comm_Bps, 300.)

    def test_infeasibility_reported(self):
        lim = ControlLimits(candidate_intervals_s=(1.,), aoi_max_s=0.1,
                            error_max_norm=0.0001)
        d = decide(example(age=12., drift=0.025), lim)
        self.assertFalse(d.feasible)
        self.assertTrue(d.violations)

    def test_subsystem_coverage(self):
        lim = ControlLimits()
        d = decide(example(), lim)
        self.assertEqual(d.selected, frozenset({'a','b'}))
        self.assertTrue(d.feasible)

    def test_excluded_nodes_age_over_control_cycle_not_short_interval(self):
        nodes=example(age=3.,drift=.004)
        limits=ControlLimits(candidate_intervals_s=(1.,2.,4.),decision_horizon_s=2.)
        d=evaluate(nodes,limits,1.,{'a'})
        # Unselected node 'b' continues aging for two seconds, not one.
        self.assertAlmostEqual(d.max_aoi_s,5.)
        self.assertAlmostEqual(d.max_error_norm,.008)
        both=evaluate(nodes,limits,1.,{'a','b'})
        self.assertAlmostEqual(both.max_aoi_s,1.)

    def test_control_cycle_is_validated(self):
        with self.assertRaises(ValueError):
            ControlLimits(decision_horizon_s=0.)

    def test_failed_node_does_not_count_as_fresh(self):
        nodes = example()
        nodes[1] = replace(nodes[1], available=False, age_s=100.)
        d = decide(nodes, ControlLimits())
        self.assertFalse(d.feasible)
        self.assertIn('aoi_limit', d.violations)
        self.assertNotIn('b', d.selected)

    def test_greedy_used_for_larger_n(self):
        nodes = [NodeEstimate('n'+str(i), 'g'+str(i%3), 0., 0., 0.001)
                 for i in range(16)]
        lim = ControlLimits(comm_budget_Bps=3000.)
        d = decide(nodes, lim)
        self.assertEqual(d.search, 'greedy')
        self.assertTrue(d.feasible)
        self.assertEqual({nodes[int(x[1:])].subsystem for x in d.selected}, {'g0','g1','g2'})

    def test_discrete_oracle_matches_exhaustive_best(self):
        nodes = example()
        lim = ControlLimits()
        d = discrete_oracle(nodes, lim)
        self.assertTrue(d.feasible)
        self.assertEqual(d.search, 'discrete_oracle')

    def test_no_duplicate_id(self):
        nodes = [example()[0],example()[0]]
        with self.assertRaises(ValueError):
            decide(nodes, ControlLimits())


if __name__ == '__main__':
    unittest.main()
