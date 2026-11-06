#!/usr/bin/env python3
"""Repeatable, counterbalanced IoTJ campaign planner/runner.

Default is DRY RUN; --execute requires local Docker and intentionally performs
an extended, sequential campaign. No empirical result is invented by planning.
"""
import argparse
import json
import os
import random
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def make_plan(profile='smoke',repeats=None,seed=68754,include_churn=False):
    if profile not in ('pilot','diagnostic','sensitivity','replication','cadence','matched','perturbation','smoke','full'):
        raise ValueError('unknown profile')
    n_vals=[4] if profile in ('pilot','diagnostic','sensitivity','replication','cadence','matched','perturbation','smoke') else [4,8,16,32]
    if repeats is None:
        repeats = 3 if profile in ('replication','matched') else 2 if profile in ('smoke','perturbation') else 5 if profile=='full' else 1
    if not 1<=repeats<=20:
        raise ValueError('repeats out of bounds')
    configs=[('B0',1.,6.),('B0',2.,6.),('B0',4.,6.),('B0',8.,6.),
             ('B1',2.,4.),('B1',2.,6.),('B1',2.,8.),
             ('A1',2.,6.),('A1_interval',2.,6.),('A1_selection',2.,6.)]
    if profile=='pilot':
        configs=[('B0',2.,6.),('B1',2.,6.),('A1',2.,6.)]
    elif profile=='diagnostic':
        # Exploratory only, with predeclared configurations, not cherry-picked
        # baselines chosen post-hoc from the first pilot's measurements.
        configs=[('B0',2.,6.),('B0',3.,6.),('B0',4.,6.),
                 ('B1',2.,6.),('A1',2.,6.),('A1_interval',2.,6.),
                 ('A1_selection',2.,6.)]
    elif profile=='sensitivity':
        # Predeclared exploratory sensitivity of the *same* weighted objective.
        # Do not select a winning configuration based on the outcome of this sweep.
        default = {'W_COMM':'0.35','W_ENERGY':'0.15','W_ERROR':'0.35',
                   'W_AOI':'0.15','COMM_BUDGET_BPS':'6000'}
        configs=[
            ('B0', 2., 6., default, 'fixed_2s'),
            ('B0', 3., 6., default, 'fixed_3s'),
            ('B0', 4., 6., default, 'fixed_4s'),
            ('A1', 2., 6., default, 'original'),
            ('A1', 2., 6., dict(default, W_COMM='0.50', W_ENERGY='0.20', W_ERROR='0.20', W_AOI='0.10'), 'weights_balanced'),
            ('A1', 2., 6., dict(default, W_COMM='0.65', W_ENERGY='0.15', W_ERROR='0.10', W_AOI='0.10'), 'weights_resource'),
            ('A1', 2., 6., dict(default, COMM_BUDGET_BPS='1500'), 'budget_1500'),
            ('A1', 2., 6., dict(default, COMM_BUDGET_BPS='900'), 'budget_900'),
        ]
    elif profile=='cadence':
        # Phase 9: instrumented diagnosis; not a matched-fidelity claim.
        default={'W_COMM':'0.35','W_ENERGY':'0.15','W_ERROR':'0.35',
                 'W_AOI':'0.15','COMM_BUDGET_BPS':'6000'}
        configs=[
            ('B0',2.,6.,default,'fixed_2s'),
            ('B0',3.,6.,default,'fixed_3s'),
            ('B1',2.,6.,default,'aoi_6s'),
            ('A1',2.,6.,dict(default,COMM_BUDGET_BPS='1500'),'budget_1500'),
        ]
    elif profile=='matched':
        # Phase 11 predeclared comparison: a COMMON resource/SLO budget and
        # deterministic synthetic scenario per repetition, shared by all modes.
        # B1_guard is a separately identified coverage-aware alternative; raw
        # B1 is kept for transparency but may not satisfy coverage.
        common={'W_COMM':'0.35','W_ENERGY':'0.15','W_ERROR':'0.35',
                'W_AOI':'0.15','COMM_BUDGET_BPS':'1500',
                'AOI_MAX_S':'12','ERROR_MAX_NORM':'0.08',
                'ENERGY_BUDGET_JPS':'6','LATENCY_MAX_MS':'120',
                'CONTROL_PERIOD_S':'2','ACTION_GRID_S':'1,2,4,8'}
        configs=[
            *[('B0',float(dt),6.,common,f'fixed_{dt}s') for dt in (1,2,3,4,6)],
            ('B1',2.,6.,common,'aoi_6s_unprotected'),
            *[('B1_guard',2.,float(th),common,f'aoi_guard_{th}s') for th in (4,6,8)],
            ('A1',2.,6.,common,'joint_1500'),
        ]
    elif profile=='perturbation':
        # Phase 12: the exact same physical source, policy weights, budgets
        # and coverage constraints per scenario/transport condition. These are
        # controlled APPLICATION-layer losses/delays, not packet-level netem.
        common={'W_COMM':'0.35','W_ENERGY':'0.15','W_ERROR':'0.35',
                'W_AOI':'0.15','COMM_BUDGET_BPS':'1500',
                'AOI_MAX_S':'12','ERROR_MAX_NORM':'0.08',
                'ENERGY_BUDGET_JPS':'6','LATENCY_MAX_MS':'120',
                'CONTROL_PERIOD_S':'2','ACTION_GRID_S':'1,2,4,8'}
        base=[('B0',2.,6.,common,'fixed_2s'),
              ('B0',3.,6.,common,'fixed_3s'),
              ('B1_guard',2.,6.,common,'aoi_guard_6s'),
              ('A1',2.,6.,common,'joint_1500')]
        # Expanded below to cross every policy with three perturbations.
        configs=[]
        for mode, dt, th, env, label in base:
            for perturb in ('none','delay','delay_loss'):
                configs.append((mode,dt,th,env,f'{label}__{perturb}',perturb))
    elif profile=='replication':
        # Pre-register a follow-up to the exploratory sensitivity sweep.  This
        # isolates configuration/timing variability, not new physical traces.
        default={'W_COMM':'0.35','W_ENERGY':'0.15','W_ERROR':'0.35',
                 'W_AOI':'0.15','COMM_BUDGET_BPS':'6000'}
        configs=[
            ('B0',2.,6.,default,'fixed_2s'),
            ('B0',3.,6.,default,'fixed_3s'),
            ('B0',4.,6.,default,'fixed_4s'),
            ('B1',2.,6.,default,'aoi_6s'),
            ('A1',2.,6.,default,'original'),
            ('A1',2.,6.,dict(default,COMM_BUDGET_BPS='1500'),'budget_1500'),
            ('A1',2.,6.,dict(default,W_COMM='0.65',W_ENERGY='0.15',W_ERROR='0.10',W_AOI='0.10'),'weights_resource'),
        ]
    experiments=[]
    rnd=random.Random(seed)
    for n in n_vals:
        for rep in range(repeats):
            if profile in ('sensitivity','replication','cadence','matched','perturbation'):
                if profile=='perturbation':
                    block=[{'mode':mode,'n':n,'repeat':rep,'fixed_interval':fixed,
                            'aoi_threshold':threshold,'churn_fraction':0.,
                            'churn_at_s':0,'churn_down_s':0,'scenario_id':rep+1,
                            'policy_env':dict(policy_env),'variant':variant,
                            'impair_profile':impair,
                            'impair_start_s':65.,'impair_duration_s':35.,
                            'impair_delay_ms':80 if impair!='none' else 0,
                            'impair_drop_every':5 if impair=='delay_loss' else 0}
                           for mode,fixed,threshold,policy_env,variant,impair in configs]
                else:
                    block=[{'mode':mode,'n':n,'repeat':rep,'fixed_interval':fixed,
                        'aoi_threshold':threshold,'churn_fraction':0.,
                        'churn_at_s':0,'churn_down_s':0,
                        'scenario_id':rep+1 if profile in ('matched','perturbation') else 0,
                        'policy_env':dict(policy_env), 'variant':variant}
                           for mode,fixed,threshold,policy_env,variant in configs]
            else:
                block=[{'mode':mode,'n':n,'repeat':rep,'fixed_interval':fixed,
                        'aoi_threshold':threshold,'churn_fraction':0.,
                        'churn_at_s':0,'churn_down_s':0,'scenario_id':0}
                       for mode,fixed,threshold in configs]
            rnd.shuffle(block)
            experiments.extend(block)
    if include_churn:
        for rep in range(repeats):
            for mode in ('B0','B1','A1'):
                experiments.append({'mode':mode,'n':16,'repeat':rep,
                                    'fixed_interval':2.,'aoi_threshold':6.,
                                    'churn_fraction':.5,'churn_at_s':45,
                                    'churn_down_s':45})
    return experiments


def build_command(spec,results_dir,run_id,warmup,measure):
    return [sys.executable,str(ROOT/'exp_iotj_run.py'),
            '--mode',spec['mode'],'--n',str(spec['n']),
            '--fixed-interval',str(spec['fixed_interval']),
            '--scenario-id',str(spec.get('scenario_id',0)),
            '--aoi-threshold',str(spec['aoi_threshold']),
            '--churn-fraction',str(spec['churn_fraction']),
            '--churn-at-s',str(spec['churn_at_s']),
            '--churn-down-s',str(spec['churn_down_s']),
            '--impair-profile',spec.get('impair_profile','none'),
            '--impair-start-s',str(spec.get('impair_start_s',65.)),
            '--impair-duration-s',str(spec.get('impair_duration_s',35.)),
            '--impair-delay-ms',str(spec.get('impair_delay_ms',0)),
            '--impair-drop-every',str(spec.get('impair_drop_every',0)),
            '--warmup',str(warmup),'--measure',str(measure),
            '--results-dir',str(results_dir),'--run-id',run_id]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',choices=['pilot','diagnostic','sensitivity','replication','cadence','matched','perturbation','smoke','full'],default='pilot')
    p.add_argument('--repeats',type=int)
    p.add_argument('--seed',type=int,default=68754)
    p.add_argument('--warmup',type=int,default=60)
    p.add_argument('--measure',type=int,default=180)
    p.add_argument('--churn',action='store_true')
    p.add_argument('--execute',action='store_true',help='Runs full campaign on Docker, not a dry-run')
    p.add_argument('--output',default='campaigns_v3')
    args=p.parse_args()
    if args.warmup<0 or args.measure<=0:
        p.error('invalid measurement duration')
    if args.churn and args.measure <= 90:
        p.error('churn requires measurement > 90 seconds')
    if args.profile=='perturbation' and (args.warmup!=20 or args.measure!=120 or args.churn):
        p.error('Phase 12 locked protocol: --warmup 20 --measure 120, without churn')
    if args.churn and args.profile == 'pilot':
        p.error('pilot does not include churn: use --profile smoke --churn')
    plan=make_plan(args.profile,args.repeats,args.seed,args.churn)
    campaign_id=datetime.now(timezone.utc).strftime('iotj_%Y%m%dT%H%M%S_%fZ')
    base=ROOT/args.output/campaign_id
    base.mkdir(parents=True,exist_ok=False)
    output_dir=base/'runs'
    tasks=[]
    campaign_token = datetime.now(timezone.utc).strftime('%y%m%dT%H%M%S') + uuid.uuid4().hex[:4]
    for idx,spec in enumerate(plan):
        short_variant=(('p' + {'none':'n','delay':'d','delay_loss':'x'}[spec['impair_profile']])
                 if args.profile=='perturbation' else spec.get('variant',''))
        run_id=(f'c{campaign_token}_{idx:03d}_N{spec["n"]:03d}_r{spec["repeat"]:02d}_' 
                f'{spec["mode"]}_dt{spec["fixed_interval"]:g}_th{spec["aoi_threshold"]:g}'
                + (('_'+short_variant) if short_variant else '')
                + ('_churn' if spec['churn_fraction'] else ''))
        tasks.append({'run_id':run_id,'config':spec,
                      'command':build_command(spec,output_dir,run_id,args.warmup,args.measure),
                      'status':'planned'})
    manifest={'campaign_id':campaign_id,'seed':args.seed,'profile':args.profile,
              'repeats':args.repeats or (3 if args.profile in ('replication','matched') else 2 if args.profile=='perturbation' else 1 if args.profile in ('pilot','diagnostic','sensitivity','cadence') else 2 if args.profile=='smoke' else 5),
              'warmup_s':args.warmup,'measurement_s':args.measure,
              'randomization':'fixed-seed shuffle per N and repetition',
              'phase11_protocol': ({'primary_metric':'synthetic_oracle_NMAE_0.5s',
                 'fidelity_thresholds':[0.006,0.009,0.012,0.018],
                 'full_predicted_feasibility_required':True,
                 'reference_and_decision_cadence_min':0.95,
                 'no_unmatched_oracle_states':True,
                 'common_policy_comm_budget_Bps':1500,
                 'scope':'synthetic exploratory; not PUE or physical SLO tolerance'}
                 if args.profile=='matched' else None),
              'phase12_protocol': ({'protocol_version':'phase12-app-http-impair-v1',
                  'scope':'application HTTP delay and pre-send drops, not IP-layer loss or WAN emulation',
                  'scenario_ids':[1,2], 'impair_profiles':['none','delay','delay_loss'],
                  'start_offset_from_physical_epoch_s':65,
                  'duration_s':35, 'delay_ms':80, 'loss_every_k':5,
                  'source_reference_unperturbed':True,
                  'fidelity_nmae_thresholds':[0.012,0.018],
                  'common_policy_budget_Bps':1500,
                  'primary_phase_metrics':['operational_state_bytes','HTTP RTT p95','dense_oracle_NMAE',
                                           'observed reference coverage','coverage violations'],
                  'no_global_superiority_claim':True}
                  if args.profile=='perturbation' else None),
              'execution_environment':'NOT RUN', 'tasks':tasks}
    path=base/'plan.json'
    path.write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print(f'Planned {len(tasks)} independent trials -> {path}')
    if not args.execute:
        print('Dry run only. Use --execute on a Docker host to run all trials.')
        return
    manifest['execution_environment']='local Docker host, see each run metadata'
    logdir = base/'logs'
    logdir.mkdir(exist_ok=True)
    for idx,task in enumerate(tasks,1):
        print(f'[{idx}/{len(tasks)}] {task["run_id"]}',flush=True)
        task['status']='running'
        path.write_text(json.dumps(manifest,indent=2),encoding='utf8')
        # Docker Compose reads controller parameters from the subprocess environment.
        # A changed policy configuration forces re-creation of the policy service.
        run_env=os.environ.copy()
        run_env.update(task['config'].get('policy_env', {}))
        with (logdir / f'{task["run_id"]}.log').open('w',encoding='utf8') as out:
            result=subprocess.run(task['command'],cwd=ROOT,check=False,env=run_env,
                                  stdout=out,stderr=subprocess.STDOUT)
        task['status']='complete' if result.returncode==0 else 'failed'
        task['exit_code']=result.returncode
        if result.returncode==0:
            with (logdir / f'{task["run_id"]}_analysis.log').open('w',encoding='utf8') as out:
                analysis=subprocess.run([sys.executable,str(ROOT/'analyze_v2.py'),
                                     str(output_dir/task['run_id'])],cwd=ROOT,
                                     check=False,stdout=out,stderr=subprocess.STDOUT)
            task['analysis_exit_code']=analysis.returncode
            if analysis.returncode != 0:
                task['status']='analysis_failed'
        path.write_text(json.dumps(manifest,indent=2),encoding='utf8')
        if task['status'] != 'complete':
            raise SystemExit(f'Failed run or analysis {task["run_id"]}; see logs in {logdir}')
    print('Campaign completed; use research/compare_v3.py to assess matched fidelity.')

if __name__=='__main__':main()
