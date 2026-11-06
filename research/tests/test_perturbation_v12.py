"""Phase 12 no-Docker contract tests; not experimental evidence."""
import os
import sys
import time
import unittest
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'research'))
from research.campaign_v3 import make_plan, build_command
from exp_iotj_run import build_node_override
from research.perturbation_v12 import phase_of,tier_comparisons
try:
    from agents.edge_agent import Agent
except ModuleNotFoundError as ex:
    if ex.name != 'aiomqtt':
        raise
    # Minimal MQTT stub for this isolated HTTP-method test on environments
    # without the optional agent runtime; Docker/Windows installs aiomqtt.
    import types
    mqttstub=types.ModuleType('aiomqtt')
    mqttstub.Client=object
    mqttstub.MqttError=Exception
    with patch.dict(sys.modules,{'aiomqtt':mqttstub}):
        from agents.edge_agent import Agent


class ContractV12(unittest.TestCase):
    def test_locked_profile_has_24_trials_and_equal_slos(self):
        trials=make_plan('perturbation',seed=20261112)
        self.assertEqual(len(trials),24)
        self.assertEqual(Counter(x['scenario_id'] for x in trials),{1:12,2:12})
        self.assertEqual(Counter(x['impair_profile'] for x in trials),
                         {'none':8,'delay':8,'delay_loss':8})
        for scenario in (1,2):
            for impair in ('none','delay','delay_loss'):
                subset=[x for x in trials if x['scenario_id']==scenario and x['impair_profile']==impair]
                self.assertEqual(Counter(x['mode'] for x in subset),{'B0':2,'B1_guard':1,'A1':1})
        self.assertEqual(len({tuple(sorted(x['policy_env'].items())) for x in trials}),1)
        self.assertEqual({x['policy_env']['COMM_BUDGET_BPS'] for x in trials},{'1500'})

    def test_compose_uses_per_agent_impairment_configuration(self):
        text,services=build_node_override(4,123.0,'trial',2,
                 {'profile':'delay_loss','start_s':65,'duration_s':35,'delay_ms':80,'drop_every':5})
        self.assertEqual(len(services),4)
        self.assertEqual(text.count('HDT_IMPAIR_PROFILE'),4)
        self.assertIn('HDT_IMPAIR_DROP_EVERY: "5"',text)
        self.assertIn('SYNTH_SCENARIO_ID: "2"',text)

    def test_cli_passes_controls(self):
        spec=make_plan('perturbation')[0]
        cmd=build_command(spec,Path('out'),'my_run',20,120)
        self.assertEqual(cmd[cmd.index('--impair-profile')+1],spec['impair_profile'])
        self.assertEqual(cmd[cmd.index('--impair-start-s')+1],'65.0')

    def test_phase_boundaries(self):
        base=datetime(2026,11,4,tzinfo=timezone.utc)
        p={'start_utc':base.isoformat(), 'end_utc':(base+timedelta(seconds=35)).isoformat()}
        self.assertEqual(phase_of(base-timedelta(milliseconds=1),p),'pre')
        self.assertEqual(phase_of(base,p),'stress')
        self.assertEqual(phase_of(base+timedelta(seconds=35),p),'recovery')

    def test_http_imposed_loss_only_operational_and_logged(self):
        with patch.dict(os.environ,{'HDT_IMPAIR_PROFILE':'delay_loss',
            'HDT_IMPAIR_START_S':'1','HDT_IMPAIR_DURATION_S':'200',
            'HDT_IMPAIR_DELAY_MS':'80','HDT_IMPAIR_DROP_EVERY':'5'}):
            a=Agent('node-000','http://fake','fake',1883)
        a.physical_epoch=time.time()-3
        fake=type('Res',(),{'raise_for_status':lambda self:None,'json':lambda self:{'ok':True}})()
        with patch('agents.edge_agent.requests.post',return_value=fake) as send, patch('agents.edge_agent.time.sleep') as delay, patch('builtins.print') as printed:
            for _ in range(5):
                a.post('state',{'x':1})
            self.assertEqual(send.call_count,4)
            self.assertEqual(delay.call_count,5)
            self.assertFalse(a.post('heartbeat',{'x':2})[0] is False)
            old=send.call_count
            self.assertEqual(a.post('reference',{'x':3})[0],True)
            self.assertEqual(send.call_count,old+1)
            logs='\n'.join(str(x) for x in printed.call_args_list)
            self.assertIn('HDT_HTTP_OBSERVATION',logs)
            self.assertIn('"injected_drop":true',logs)
            self.assertIn('"endpoint":"reference"',logs)
            self.assertIn('"injected_delay_ms":0',logs)

    def test_unmatched_tiers_stay_no_match(self):
        rows=[{'scenario_id':1,'impair_profile':'delay','mode':'A1','quality_ok':True,
               'run_complete':True,'dense_synthetic_nmae':0.010,'predicted_feasible_fraction':1.,
               'predicted_coverage_violation_count':0,'nmae_unmatched_fraction':0,
               'operational_received_mean_Bps':1000,'common_budget_Bps':1500,
               'operational_http_bytes':50000,'run_id':'a1'}]
        result=tier_comparisons(rows)
        self.assertEqual(len(result),2)
        self.assertTrue(all(r['status']=='no_match' for r in result))

if __name__=='__main__': unittest.main()
