#!/usr/bin/env python3
"""Predeclared, gated, one-command Phase-13 Docker campaign for IoTJ-68754-2026.

Runs on the user's Docker host. Default is dry-run. A failed calibration GATE
halts the campaign before expensive tests; subsequent failures are retained
and reported, never silently discarded. All trials are sequential/paired by
synthetic scenario; this is NOT physical-sensor or wire-level evidence.
"""
from __future__ import annotations
import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research.campaign_v3 import build_command

SEED=687542026
COMMON={'W_COMM':'0.35','W_ENERGY':'0.15','W_ERROR':'0.35','W_AOI':'0.15',
        'AOI_MAX_S':'12','ERROR_MAX_NORM':'0.08','ENERGY_BUDGET_JPS':'6',
        'LATENCY_MAX_MS':'120','CONTROL_PERIOD_S':'2','ACTION_GRID_S':'1,2,4,8'}
LIMITS={'synthetic_fidelity_tiers':[0.012,0.018], 'min_reference_cadence':0.95,
        'min_decision_cadence':0.95,'calibration_min_stress_p95_ms':140,
        'calibration_min_stress_delta_p95_ms':80,
        'host_min_memory_bytes_for_n32':6*1024**3,'host_min_cpus_for_n32':4}


def spec(block,n,scenario,mode,variant,dt=2.,impair='none',churn=False):
    # Per-agent policy-state budget scales with deployment size, identically
    # for all policies within a fixed-size comparison. Heartbeats are reported
    # separately and never hidden from total application traffic.
    env=dict(COMMON, COMM_BUDGET_BPS=str(int(1500*n/4)))
    return {'block':block,'mode':mode,'variant':variant,'n':n,
            'scenario_id':scenario,'repeat':scenario-1,'fixed_interval':dt,
            'aoi_threshold':6.,'policy_env':env,
            'impair_profile':impair,'impair_start_s':65.,
            'impair_duration_s':45.,'impair_delay_ms':200 if impair!='none' else 0,
            'impair_drop_every':5 if impair=='delay_loss' else 0,
            'churn_fraction':.5 if churn else 0.,
            'churn_at_s':40 if churn else 0,
            'churn_down_s':50 if churn else 0,
            'warmup':20,'measure':160 if churn else (150 if impair!='none' else 120)}


def prepare_specs(seed=SEED):
    gates=[spec('gate_transport',8,1,'B0','transport_control',impair='none'),
           spec('gate_transport',8,1,'B0','transport_stress',impair='delay_loss'),
           spec('gate_capacity',32,1,'B0','capacity_32')]
    # A small paired scalability study, with 3 independent sources for the
    # decisive 16/32-agent cases and 2 for the lower-load cross-checks.
    blocks={'scalability':[],'churn':[],'ablations':[],'dynamic':[]}
    for n in (4,8,16,32):
        for scenario in range(1,4 if n>=16 else 3):
            for mode,label,dt in [('B0','fixed_2s',2.),('B1_guard','aoi_guard_6s',2.),
                                  ('A1','joint',2.)]:
                blocks['scalability'].append(spec('scalability',n,scenario,mode,label,dt))
            if n in (16,32):
                blocks['scalability'].append(spec('scalability',n,scenario,'B0','fixed_4s',4.))
    for scenario in (1,2,3):
        for mode,label in [('B0','fixed_2s'),('B1_guard','aoi_guard_6s'),('A1','joint')]:
            blocks['churn'].append(spec('churn',16,scenario,mode,label,churn=True))
        for mode,label in [('A1_interval','interval_only'),('A1_selection','selection_only')]:
            blocks['ablations'].append(spec('ablations',16,scenario,mode,label))
        for mode,label in [('B0','fixed_2s'),('B1_guard','aoi_guard_6s'),('A1','joint')]:
            blocks['dynamic'].append(spec('dynamic',16,scenario,mode,label,impair='delay_loss'))
    rnd=random.Random(seed)
    for block in blocks.values():
        rnd.shuffle(block)
    return gates+[s for key in ('scalability','ablations','dynamic','churn') for s in blocks[key]]


def stamp_manifest(seed=SEED):
    token=datetime.now(timezone.utc).strftime('%y%m%dT%H%M%S')+uuid.uuid4().hex[:4]
    items=[]
    for idx,cfg in enumerate(prepare_specs(seed)):
        run_id=f'v13{token}_{idx:03d}_{cfg["block"][:2]}_n{cfg["n"]:02d}_s{cfg["scenario_id"]}_{cfg["mode"]}'
        if len(run_id)>64:
            raise ValueError('run ID too long')
        items.append({'run_id':run_id,'config':cfg,'status':'planned'})
    tracked=('agents/edge_agent.py','middleware/app.py','middleware/db.py','docker-compose.yml',
             'middleware/workers/decision_loop.py','middleware/routers/telemetry.py','research/final_campaign_v13.py',
             'research/final_report_v13.py','synthetic_process.py','exp_iotj_run.py')
    checksums={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in tracked}
    return {'campaign_id':'iotj13_'+token,'profile':'final_v13',
            'source_sha256':checksums,
            'seed':seed,'protocol_version':'iotj-v13-integrated-fixed-v1',
            'protocol':{'common_policy_env_template':COMMON,
                        'comm_budget_per_four_nodes_Bps':1500,
                        'source_scenario_ids':[1,2,3],
                        'synthetic_normalization':'offline physical-process oracle every 0.5 seconds',
                        'controls':LIMITS,
                        'scope':'one local Docker host; application-layer injected disturbances, synthetic state only',
                        'queue_depth_measurement':'NOT directly measured; in-flight API requests are not a queue',
                        'no_post_hoc_tuning':True},
            'execution_environment':'NOT EXECUTED','tasks':items}


def write_manifest(path,data):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)


def run_one(task,root,manifest,plan_path):
    cfg=task['config']; rid=task['run_id']
    task['status']='running';write_manifest(plan_path,manifest)
    cmd=build_command(cfg,root/'runs',rid,cfg['warmup'],cfg['measure'])
    task['command']=cmd
    env=os.environ.copy();env.update(cfg['policy_env'])
    logdir=root/'logs';logdir.mkdir(exist_ok=True)
    with (logdir/(rid+'.log')).open('w',encoding='utf-8') as out:
        res=subprocess.run(cmd,cwd=ROOT,env=env,stdout=out,stderr=subprocess.STDOUT,check=False)
    task['exit_code']=res.returncode
    if res.returncode==0:
        with (logdir/(rid+'_analysis.log')).open('w',encoding='utf-8') as out:
            check=subprocess.run([sys.executable,str(ROOT/'analyze_v2.py'),str(root/'runs'/rid)],
                  cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,check=False)
        task['analysis_exit_code']=check.returncode
        task['status']='complete' if check.returncode==0 else 'analysis_failed'
    else:task['status']='failed'
    write_manifest(plan_path,manifest)
    return task['status']=='complete'


def files_for_run(folder,run_id):
    base=folder/'runs'/run_id
    return {'runs/'+run_id+'/'+p.relative_to(base).as_posix():p.read_bytes()
            for p in base.rglob('*') if p.is_file() and p.suffix.lower() in ('.json','.csv','.log')}


def gate_report(root,manifest,task):
    from research.perturbation_v12 import analyze_one
    from research.final_report_v13 import assess_run
    row=assess_run(files_for_run(root,task['run_id']),task)
    if task['config']['block']=='gate_transport':
        filemap=files_for_run(root,task['run_id'])
        phase_row, phases=analyze_one(filemap,'',task,int(task['config']['policy_env']['COMM_BUDGET_BPS']))
        stress=next(p for p in phases if p['phase']=='stress')
        pre=next(p for p in phases if p['phase']=='pre')
        row.update({'phase12_quality_ok':phase_row['quality_ok'],
                    'stress_p95_ms':stress['operational_http_request_p95_ms'],
                    'pre_p95_ms':pre['operational_http_request_p95_ms'],
                    'injected_drop_attempts':stress['injected_drop_attempts']})
    return row


def enforce_gate(gate_results):
    control,stress,capacity=gate_results
    if not control['quality_ok'] or not stress['quality_ok'] or not capacity['quality_ok']:
        return False,'Some calibration or capacity runs failed cadence or reference quality controls'
    c=control.get('stress_p95_ms');s=stress.get('stress_p95_ms')
    if c is None or s is None or s<LIMITS['calibration_min_stress_p95_ms'] or s-c<LIMITS['calibration_min_stress_delta_p95_ms']:
        return False,f'Injected stress did not cross latency threshold (normal {c}, stress {s} ms)'
    if not stress.get('injected_drop_attempts'):
        return False,'Expected application-level drops are absent'
    return True,'Transport calibration and n=32 capacity gate satisfied'


def system_capacity():
    try:
        r=subprocess.run(['docker','info','--format','{{json .}}'],capture_output=True,text=True,timeout=25)
        if r.returncode:raise RuntimeError(r.stderr.strip())
        d=json.loads(r.stdout)
        mem=int(d.get('MemTotal') or 0);cpu=int(d.get('NCPU') or 0)
        return {'docker_memory_bytes':mem,'docker_ncpu':cpu,
                'gate_ok':mem>=LIMITS['host_min_memory_bytes_for_n32'] and cpu>=LIMITS['host_min_cpus_for_n32']}
    except Exception as exc:
        return {'gate_ok':False,'error':str(exc)}


def capacity_manifest(seed=SEED):
    """A single 32-node diagnostic gate, NEVER a replacement for the full matrix.

    Reuse the exact n=32 gate spec and quality criteria from the locked protocol.
    The new manifest is deliberately separate to avoid mixing builds or
    presenting diagnostic data as confirmation of the 63-trial campaign.
    """
    m=stamp_manifest(seed)
    task=next(t for t in m['tasks'] if t['config']['block']=='gate_capacity')
    task['run_id']=task['run_id'].replace('v13','v13cap',1)
    m['tasks']=[task]
    m['campaign_id']='iotj13cap_'+m['campaign_id'].removeprefix('iotj13_')
    m['profile']='diagnostic_capacity_only'
    m['protocol_version']='iotj-v13-capacity-check-v1'
    m['protocol']['capacity_diagnostic_only']=True
    m['protocol']['full_matrix_unchanged']=True
    return m


def run_capacity_gate_only(manifest, base, plan_path):
    """Run just the existing n=32 gate; save evidence whether valid or not."""
    task=manifest['tasks'][0]
    print('[1/1] n=32 capacity gate, same thresholds and B0 configuration',flush=True)
    try:
        success=run_one(task,base,manifest,plan_path)
        if not success:
            manifest['gate']={'status':'failed','reason':'capacity run or analysis failed','results':[]}
        else:
            row=gate_report(base,manifest,task)
            ok=bool(row['quality_ok'])
            manifest['gate']={'status':'passed' if ok else 'failed',
                              'reason':'n=32 capacity quality gates passed' if ok else
                              'n=32 failed one or more original quality checks',
                              'results':[row]}
        write_manifest(plan_path,manifest)
    except Exception as exc:
        manifest['gate']={'status':'failed','reason':'capacity assessment exception: '+repr(exc),'results':[]}
        write_manifest(plan_path,manifest)
    manifest['summary']={'planned':1,'complete':sum(x['status']=='complete' for x in manifest['tasks']),
                         'failed':sum(x['status'] in ('failed','analysis_failed') for x in manifest['tasks']),
                         'not_started':sum(x['status']=='planned' for x in manifest['tasks'])}
    write_manifest(plan_path,manifest)
    print('GATE:',manifest['gate']['status'],manifest['gate']['reason'],flush=True)
    print('Campaign folder:',base,flush=True)
    print('SUMMARY',json.dumps(manifest['summary']),flush=True)
    return manifest['gate']['status']=='passed'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute',action='store_true',help='Run Docker sequentially (default: no Docker)')
    ap.add_argument('--seed',type=int,default=SEED)
    ap.add_argument('--capacity-only',action='store_true',
                    help='Execute only original n=32 capacity gate (diagnostic, not confirmatory)')
    ap.add_argument('--output',type=Path,default=ROOT/'campaigns_v13')
    args=ap.parse_args()
    manifest=capacity_manifest(args.seed) if args.capacity_only else stamp_manifest(args.seed)
    counts=Counter(t['config']['block'] for t in manifest['tasks'])
    print('Phase 13 fixed plan:',len(manifest['tasks']),'runs |',dict(counts),flush=True)
    if not args.execute:
        print('DRY RUN: no containers started; run --execute on Docker Desktop.',flush=True)
        return
    caps=system_capacity();manifest['host_preflight']=caps
    if not caps['gate_ok']:
        print('ABORTED before Docker execution: n=32 requires >=6 GiB and 4 CPUs allocated to Docker Desktop:',caps,flush=True)
        raise SystemExit(2)
    base=args.output/manifest['campaign_id'];base.mkdir(parents=True,exist_ok=False)
    plan_path=base/'plan.json'
    manifest['execution_environment']='local Docker Desktop or Engine, run-local measurements'
    manifest['docker_project']=os.getenv('HDT_COMPOSE_PROJECT','hdt_iotj26')
    manifest['python_version']=sys.version.split()[0]
    manifest['host_ports']={k:os.getenv(k) for k in ('HDT_API_PORT','HDT_PG_PORT','HDT_MQTT_PORT')}
    manifest['gate']={'status':'pending','results':[]}
    write_manifest(plan_path,manifest)
    if args.capacity_only:
        if not run_capacity_gate_only(manifest,base,plan_path):
            raise SystemExit(3)
        return
    gates=[]
    for idx,task in enumerate(manifest['tasks'],1):
        cfg=task['config']
        if cfg['block']!='gate_transport' and cfg['block']!='gate_capacity' and manifest['gate']['status']!='passed':
            break
        print(f'[{idx}/{len(manifest["tasks"])}] {cfg["block"]} n={cfg["n"]} mode={cfg["mode"]} scenario={cfg["scenario_id"]}',flush=True)
        success=run_one(task,base,manifest,plan_path)
        if cfg['block'].startswith('gate'):
            if success:
                try:
                    status=gate_report(base,manifest,task)
                    gates.append(status)
                    manifest['gate']['results']=gates
                    write_manifest(plan_path,manifest)
                except Exception as exc:
                    manifest['gate']={'status':'failed','reason':'gate analysis exception: '+repr(exc),'results':gates}
                    write_manifest(plan_path,manifest)
                    print('STOP: gate analysis failed',repr(exc),flush=True)
                    break
            else:
                manifest['gate']={'status':'failed','reason':'run or analysis failed','results':gates}
                write_manifest(plan_path,manifest)
                print('STOP: failed gate, remaining blocks not run',flush=True)
                break
            if len(gates)==3:
                passed,reason=enforce_gate(gates)
                manifest['gate']={'status':'passed' if passed else 'failed',
                                  'reason':reason,'results':gates}
                write_manifest(plan_path,manifest)
                print('GATE:',reason,flush=True)
                if not passed:break
        if cfg['block'] not in ('gate_transport','gate_capacity') and not success:
            # Retain the failure and proceed with independent tasks. The audit
            # will not count this run as scientifically admissible.
            print('WARNING: run failed, preserved logs; continuing independent trials.',flush=True)
    manifest['summary']={'planned':len(manifest['tasks']),
             'complete':sum(t['status']=='complete' for t in manifest['tasks']),
             'failed':sum(t['status'] in ('failed','analysis_failed') for t in manifest['tasks']),
             'not_started':sum(t['status']=='planned' for t in manifest['tasks'])}
    write_manifest(plan_path,manifest)
    print('Campaign folder:',base,flush=True)
    print('SUMMARY',json.dumps(manifest['summary']),flush=True)
    if manifest['gate']['status']!='passed':raise SystemExit(3)

if __name__=='__main__':main()
