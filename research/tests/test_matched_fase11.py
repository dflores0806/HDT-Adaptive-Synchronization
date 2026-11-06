"""Phase 11 protocol regression tests; no Docker or experimental conclusions."""
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'research'))
from middleware.services.action_controller import NodeEstimate, ControlLimits
from middleware.services.strategies import choose_action
from research.campaign_v3 import make_plan,build_command
from research.matched_v11 import tier_matches,fully_eligible
from exp_iotj_run import build_node_override
from synthetic_process import physical_state
from analyze_v2 import synthetic_oracle_dataframe

class Phase11Tests(unittest.TestCase):
    def test_raw_b1_is_unchanged_and_guard_preserves_coverage(self):
        nodes=[NodeEstimate('a','compute',0.1,0.,.001),
               NodeEstimate('b','rack',7.,.008,.001),
               NodeEstimate('c','compute',.1,0.,.001)]
        limits=ControlLimits(comm_budget_Bps=5000,energy_budget_Jps=2)
        original=choose_action(nodes,limits,'B1',2.,6.)
        guarded=choose_action(nodes,limits,'B1_guard',2.,6.)
        self.assertEqual(original.selected,{'b'})
        self.assertEqual(guarded.selected,{'b','c'})
        self.assertNotIn('subsystem_coverage',guarded.violations)
        self.assertIn('subsystem_coverage',original.violations)
        self.assertEqual(guarded.interval_s,2.)

    def test_guard_tie_breaker_is_deterministic(self):
        nodes=[NodeEstimate('a','rack',.1,0.,.001),
               NodeEstimate('b','rack',.1,0.,.001),
               NodeEstimate('z','compute',.1,0.,.001)]
        lim=ControlLimits(comm_budget_Bps=2000,energy_budget_Jps=3)
        a=choose_action(nodes,lim,'B1_guard',2.,8.)
        b=choose_action(tuple(reversed(nodes)),lim,'B1_guard',2.,8.)
        self.assertEqual(a.selected,b.selected)

    def test_matched_plan_locked_budget_and_scenario(self):
        plan=make_plan('matched')
        self.assertEqual(len(plan),30)
        for repeat in range(3):
            cohort=[r for r in plan if r['repeat']==repeat]
            self.assertEqual(len(cohort),10)
            self.assertEqual({r['scenario_id'] for r in cohort},{repeat+1})
            self.assertEqual({r['policy_env']['COMM_BUDGET_BPS'] for r in cohort},{'1500'})
            self.assertEqual({r['policy_env']['ERROR_MAX_NORM'] for r in cohort},{'0.08'})
            self.assertEqual({r['mode'] for r in cohort},{'B0','B1','B1_guard','A1'})
        args=build_command(plan[0],Path('runs'),'test01',20,120)
        self.assertEqual(args[args.index('--scenario-id')+1],str(plan[0]['scenario_id']))

    def test_source_scenarios_are_reproducible_and_oracle_agrees(self):
        at=23.
        original=physical_state('node-000',at)
        self.assertEqual(original,physical_state('node-000',at,0))
        self.assertEqual(physical_state('node-000',at,1),physical_state('node-000',at,1))
        self.assertNotEqual(physical_state('node-000',at,1),physical_state('node-000',at,2))
        override,_=build_node_override(2,1740000000.,'run_B1',scenario_id=2)
        self.assertIn('SYNTH_SCENARIO_ID: "2"',override)
        meta={'physical_epoch_unix_s':1740000000., 'node_ids':['node-000'],
              'synthetic_scenario_id':2,'start_utc':'2025-02-19T21:20:00+00:00',
              'end_utc':'2025-02-19T21:20:02+00:00'}
        frame=synthetic_oracle_dataframe(meta,1.)
        import json
        self.assertEqual(json.loads(frame.iloc[0]['state']),physical_state('node-000',0.,2))

    def _row(self,mode,variant,bytes_,nmae,rep=0,feasible=1.,coverage=0,scenario=1):
        r={'mode':mode,'variant':variant,'repeat':rep,'scenario_id':scenario,
           'operational_http_bytes':bytes_,'nmae_dense_0p5s':nmae,
           'run_complete':True,'quality_ok':True,
           'feasible_fraction':feasible,'coverage_violations':coverage,
           'measurement_s':120,'scenario_verified':True,'config_verified':True,
           'agent_slots_skipped':0,'policy_slots_skipped':0,
           'timing_log_status':'available','agent_reference_http_failures':0}
        r['eligible']=not bool(fully_eligible(r,1500))
        return r

    def test_unmatched_tier_never_claims_benefit(self):
        a=self._row('A1','joint_1500',60_000,.016)
        b=self._row('B0','fixed_2s',100_000,.007)
        match=tier_matches([a,b],[.01],1500)[0]
        self.assertEqual(match['status'],'no_match')
        self.assertIsNone(match['traffic_delta_pct_A1_vs_B'])

    def test_only_eligible_coverage_safe_baselines_can_match(self):
        a=self._row('A1','joint_1500',80_000,.008)
        bad=self._row('B1','aoi_6s_unprotected',30_000,.009,feasible=.8,coverage=12)
        good=self._row('B1_guard','aoi_guard_6s',90_000,.009)
        b0=self._row('B0','fixed_3s',100_000,.009)
        matched=tier_matches([a,bad,good,b0],[.01],1500)[0]
        self.assertEqual(matched['status'],'matched_synthetic_tier')
        self.assertEqual(matched['baseline_variant'],'aoi_guard_6s')
        self.assertAlmostEqual(matched['traffic_delta_pct_A1_vs_B'],100*(80/90-1))

    def test_common_budget_gate_and_scenario_separation(self):
        expensive=self._row('A1','joint_1500',240_000,.006)
        self.assertIn('average_operational_payload_over_budget',fully_eligible(expensive,1500))
        a=self._row('A1','joint_1500',70_000,.007,rep=0,scenario=1)
        b=self._row('B0','fixed_2s',80_000,.007,rep=1,scenario=2)
        tiers=tier_matches([a,b],[.008],1500)
        self.assertEqual(len(tiers),2)
        self.assertTrue(all(t['status']=='no_match' for t in tiers))

if __name__=='__main__': unittest.main()
