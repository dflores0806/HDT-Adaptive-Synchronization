#!/usr/bin/env python3
"""Phase 13 reproducible audit of predeclared Docker experiments.

Accepts campaign directory or packaged ZIP. Does NOT infer IP wire bytes,
physical electricity, PUE prediction quality or unmeasured broker queues.
No match is reported if quality, modeled SLO, or fixed fidelity ceiling fails.
"""
from __future__ import annotations
import argparse
from collections import defaultdict, Counter
import csv
import io
import json
import math
from pathlib import Path
import random
import statistics
import sys
import zipfile
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from research.perturbation_v12 import (analyze_one, extract_http_observations, parse_ts,
                                       dense_per_phase, as_df)

TIERS=(0.012,0.018)

def load(path):
    path=Path(path)
    if path.is_file():
        with zipfile.ZipFile(path) as z:
            blobs={n:z.read(n) for n in z.namelist() if n.endswith(('.json','.csv','.log'))}
    else:
        blobs={p.relative_to(path).as_posix():p.read_bytes() for p in path.rglob('*')
              if p.is_file() and p.suffix in ('.json','.csv','.log')}
    plans=[n for n in blobs if n.endswith('plan.json')]
    if len(plans)!=1:raise ValueError('Expected exactly one plan.json')
    plan=json.loads(blobs[plans[0]])
    version=plan.get('protocol_version')
    if version not in ('iotj-v13-integrated-fixed-v1','iotj-v13-capacity-check-v1'):
        raise ValueError('Not an authorized Phase 13 protocol')
    if version=='iotj-v13-capacity-check-v1':
        if (plan.get('profile')!='diagnostic_capacity_only' or
                len(plan.get('tasks',[]))!=1 or
                plan['tasks'][0]['config'].get('block')!='gate_capacity'):
            raise ValueError('Invalid capacity-only diagnostic manifest')
    return blobs,plans[0][:-len('plan.json')],plan


def csv_rows(blob):
    return list(csv.DictReader(io.StringIO(blob.decode('utf-8-sig')))) if blob else []


def read_json(blobs,key,default=None):
    return json.loads(blobs[key]) if key in blobs else default


def safe_float(v,default=None):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except (ValueError,TypeError):return default


def pct(v):
    v=[x for x in v if x is not None]
    return statistics.mean(v) if v else None


def is_true(v):return str(v).strip().lower() in ('t','true','1')


def resource_metrics(entries):
    # Docker 'stats' may report CPU >100% for multi-core usage. Memory is a
    # container RSS estimate, not application-queue occupancy.
    cpus=[]; used=[]; total_samples=0
    inflights=[]; ingress_p95=[]; samples=[]
    middleware_cpu=[];broker_cpu=[];mem_by_stamp=defaultdict(float)
    def mem_mb(s):
        if not s: return None
        token=str(s).split('/')[0].strip()
        units={'B':1/1e6,'kB':1/1000,'KB':1/1000,'KiB':1024/1e6,
               'MB':1,'MiB':1024**2/1e6,'GB':1000,'GiB':1024**3/1e6}
        for unit in sorted(units,key=len,reverse=True):
            if token.endswith(unit):
                num=safe_float(token[:-len(unit)].strip());return num*units[unit] if num is not None else None
        return None
    for row in entries or []:
        ingress=row.get('http_ingress_metrics') or {}
        if ingress:
            inflights.append(safe_float(ingress.get('inflight')))
            ingress_p95.append(safe_float(ingress.get('last_2048_ingress_wall_ms_p95')))
            samples.append(ingress)
        stats=row.get('docker_stats') or {}
        if not stats:continue
        total_samples+=1
        cpu=safe_float(str(stats.get('CPUPerc','')).replace('%',''))
        mem=mem_mb(stats.get('MemUsage'))
        cpus.append(cpu)
        used.append(mem)
        name=str(stats.get('Name') or '').lower()
        if 'middleware-' in name and cpu is not None:middleware_cpu.append(cpu)
        if 'mosquitto-' in name and cpu is not None:broker_cpu.append(cpu)
        if mem is not None:mem_by_stamp[row.get('ts_utc','unknown')]+=mem
    return {'docker_container_sample_count':total_samples,
            'docker_cpu_pct_max_single_container':max((v for v in cpus if v is not None),default=None),
            'docker_memory_mb_max_single_container':max((v for v in used if v is not None),default=None),
            'http_inflight_max_sampled':max((v for v in inflights if v is not None),default=None),
            'http_ingress_recent_p95_ms_max':max((v for v in ingress_p95 if v is not None),default=None),
            'http_ingress_samples':len(samples),
            'middleware_cpu_pct_max':max(middleware_cpu,default=None),
            'broker_cpu_pct_max':max(broker_cpu,default=None),
            'total_containers_memory_mb_max_sample':max(mem_by_stamp.values(),default=None)}


def recovery_metrics(blobs,base,meta,updates):
    cfg={'churn_fraction':meta.get('churn_fraction',0)}
    if not cfg['churn_fraction']:
        return {'churn_recovery_all_s':None,'churn_recovered_nodes':None,
                'churn_failed_nodes':None,'churn_rejected_updates':None,
                'churn_state_reconciliation_complete':None}
    events=read_json(blobs,base+'churn_events.json',[])
    failures=next((x for x in events if x.get('event')=='failure_complete'),None)
    restarted=next((x for x in events if x.get('event')=='recovery_restart_complete'),None)
    if not failures or not restarted:
        return {'churn_recovery_all_s':None,'churn_recovered_nodes':0,
                'churn_failed_nodes':int(meta.get('n',0)*cfg['churn_fraction']),
                'churn_rejected_updates':None,'churn_state_reconciliation_complete':False}
    affected=set(restarted['node_ids']);t_recover=parse_ts(restarted['ts_utc'])
    times={};rejected=0
    for rec in updates:
        if rec.get('node_id') not in affected or not rec.get('ts'):continue
        ts=parse_ts(rec['ts'])
        if ts<t_recover:continue
        if is_true(rec.get('accepted')):
            node=rec['node_id']
            times[node]=min(ts,times[node]) if node in times else ts
        else:rejected+=1
    elapsed=max((ts-t_recover).total_seconds() for ts in times.values()) if times else None
    return {'churn_recovery_all_s':elapsed if len(times)==len(affected) else None,
            'churn_recovered_nodes':len(times),'churn_failed_nodes':len(affected),
            'churn_rejected_updates':rejected,
            'churn_state_reconciliation_complete':len(times)==len(affected)}


def assess_run(blobs,task,prefix=''):
    cfg=task['config'];rid=task['run_id'];base=prefix+'runs/'+rid+'/'
    meta=read_json(blobs,base+'metadata.json',{})
    summ=read_json(blobs,base+'summary_v2.json',{})
    updates=csv_rows(blobs.get(base+'updates.csv'))
    refs=csv_rows(blobs.get(base+'reference.csv'))
    decisions=csv_rows(blobs.get(base+'decisions.csv'))
    comm=summ.get('communication') or {};fidelity=summ.get('dense_synthetic_oracle_fidelity') or {}
    cad=summ.get('cadence_v9') or {};agent=cad.get('agent') or {};policy=cad.get('policy') or {}
    ctrl=summ.get('controller') or {}
    seconds=None
    if meta.get('start_utc') and meta.get('end_utc'):
        seconds=(parse_ts(meta['end_utc'])-parse_ts(meta['start_utc'])).total_seconds()
    reasons=[]
    if task.get('status')!='complete' or not meta.get('run_complete') or not summ:
        reasons.append('incomplete_or_missing_data')
    if meta.get('synthetic_scenario_id')!=cfg['scenario_id'] or meta.get('n')!=cfg['n'] or meta.get('mode')!=cfg['mode']:
        reasons.append('provenance_mismatch')
    if any(str((meta.get('policy_env_overrides') or {}).get(k))!=str(v)
           for k,v in cfg['policy_env'].items()):reasons.append('model_config_mismatch')
    decision_cadence=len(decisions)/(seconds/2) if seconds and seconds>0 else None
    if decision_cadence is None or decision_cadence<0.95:
        reasons.append('low_decision_cadence')
    if cad.get('timing_log_status')!='available':reasons.append('missing_timing_logs')
    if (policy.get('ticks_skipped') or 0)>0:reasons.append('skipped_policy_slots')
    if (agent.get('reference_http_failures') or 0)>0:
        reasons.append('reference_transport_failures')
    # For planned churn, missing samples from STOPPED nodes are not host
    # failures: calculate expected samples among online node-time instead.
    expected=cfg['n']*seconds/2 if seconds and seconds>0 else None
    if expected and cfg.get('churn_fraction'):
        events=read_json(blobs,base+'churn_events.json',[])
        failure=next((e for e in events if e.get('event')=='failure_complete'),None)
        restart=next((e for e in events if e.get('event')=='recovery_restart_complete'),None)
        if failure and restart:
            window=(parse_ts(restart['ts_utc'])-parse_ts(failure['ts_utc'])).total_seconds()
            expected-=len(failure['node_ids'])*window/2
        else:reasons.append('missing_churn_events')
    ref_ratio=len(refs)/expected if expected and expected>0 else None
    if ref_ratio is None or ref_ratio<0.95:reasons.append('reference_cadence_below_95pct')
    if cfg.get('churn_fraction')==0 and (agent.get('ticks_skipped') or 0)>0:
        reasons.append('skipped_agent_slots')
    if not fidelity or fidelity.get('nmae') is None:
        reasons.append('missing_dense_synthetic_oracle')
    if fidelity.get('unmatched_reference_fraction') not in (0,0.0,None):
        reasons.append('unmatched_synthetic_state')
    recovery=recovery_metrics(blobs,base,meta,updates)
    # Recovery failure is a scientific OUTCOME, not a data-quality exclusion.
    obs,_=extract_http_observations(blobs,prefix,rid)
    offered=[ev for ev in obs if ev.get('endpoint') in ('state','heartbeat')]
    state=[ev for ev in obs if ev.get('endpoint')=='state']
    accepted=sum(is_true(x.get('accepted')) for x in updates)
    if seconds and seconds>0:
        throughput=accepted/seconds
    else: throughput=None
    return {
        'run_id':rid,'block':cfg['block'],'variant':cfg['variant'],
        'mode':cfg['mode'],'n':cfg['n'],'scenario_id':cfg['scenario_id'],
        'impair_profile':cfg.get('impair_profile','none'),'churn_fraction':cfg.get('churn_fraction',0),
        'status':task.get('status'),'run_complete':bool(meta.get('run_complete')),
        'quality_ok':not reasons,'quality_flags':';'.join(dict.fromkeys(reasons)),
        'measurement_seconds':seconds,'reference_rows':len(refs),'expected_online_references':expected,
        'reference_cadence_ratio':ref_ratio,'decision_cadence_ratio':decision_cadence,
        'observed_state_updates':len(updates),'accepted_updates':accepted,
        'accepted_state_updates_per_s':throughput,
        'offered_state_attempts':len(state),'offered_operational_attempts':len(offered),
        'application_pre_send_drops':sum(bool(x.get('injected_drop')) for x in offered),
        'application_http_nacks':sum(not x.get('application_ok') for x in offered),
        'operational_received_payload_bytes':comm.get('operational_http_payload_bytes'),
        'common_budget_Bps':int(cfg['policy_env']['COMM_BUDGET_BPS']),
        'operational_received_Bps':(comm['operational_http_payload_bytes']/seconds
                                    if seconds and comm.get('operational_http_payload_bytes') is not None else None),
        'evaluation_reference_payload_bytes':comm.get('evaluation_reference_bytes'),
        'dense_synthetic_nmae':fidelity.get('nmae'),
        'dense_synthetic_aoi_p95_s':fidelity.get('aoi_p95_s'),
        'dense_synthetic_unmatched_fraction':fidelity.get('unmatched_reference_fraction'),
        'predicted_feasible_fraction':ctrl.get('feasible_fraction'),
        'predicted_violations':sum((ctrl.get('violation_counts') or {}).values()),
        'controller_runtime_p95_ms':ctrl.get('policy_runtime_p95_ms'),
        'controller_selected_mean':ctrl.get('mean_selected_per_decision'),
        'controller_intervals':json.dumps(ctrl.get('interval_distribution_s') or {},sort_keys=True),
        'state_update_rejections':comm.get('state_rejected_or_duplicate'),
        **recovery,**resource_metrics(read_json(blobs,base+'docker_resource_samples.json',[])),
        'queue_occupancy':None,'queue_occupancy_status':'not-instrumented',
        'physical_PUE_accuracy':None,'physical_energy_J':None,
    }


def eligible(row,threshold):
    return (row['quality_ok'] and row.get('predicted_feasible_fraction')==1.0
        and row.get('predicted_violations')==0
        and row.get('dense_synthetic_nmae') is not None
        and row['dense_synthetic_nmae']<=threshold
        and row.get('operational_received_payload_bytes') is not None
        and row.get('operational_received_Bps') is not None
        and row['operational_received_Bps']<=row['common_budget_Bps'])


def stress_tier_matches(phase_rows,run_rows):
    """Phase-local stress comparison; retains missing/invalid cells."""
    by_id={r['run_id']:r for r in run_rows}
    cohorts=defaultdict(list)
    for p in phase_rows:
        if p.get('phase')!='stress':continue
        r=by_id.get(p['run_id'])
        if not r or r['block']!='dynamic':continue
        cohorts[(r['n'],r['scenario_id'])].append((r,p))
    result=[]
    for (n,scenario),pairs in sorted(cohorts.items()):
        for threshold in TIERS:
            good=[(r,p) for r,p in pairs if r['quality_ok']
                and p.get('dense_synthetic_nmae') is not None
                and p['dense_synthetic_nmae']<=threshold
                and p.get('predicted_infeasible_decisions')==0
                and p.get('coverage_violating_decisions')==0
                and p.get('dense_unmatched_fraction')==0
                and p.get('operational_received_Bps') is not None
                and p['operational_received_Bps']<=r['common_budget_Bps']]
            a=next(((r,p) for r,p in good if r['mode']=='A1'),None)
            b=min(((r,p) for r,p in good if r['mode'] in ('B0','B1_guard')),
                key=lambda rp:rp[1]['operational_received_body_bytes'],default=None)
            result.append({'n':n,'scenario_id':scenario,'phase':'stress',
                'nmae_ceiling':threshold,'status':'tier_match' if a and b else 'no_match',
                'a1_run_id':a[0]['run_id'] if a else None,
                'baseline_run_id':b[0]['run_id'] if b else None,
                'a1_nmae':a[1]['dense_synthetic_nmae'] if a else None,
                'baseline_nmae':b[1]['dense_synthetic_nmae'] if b else None,
                'a1_operational_Bps':a[1]['operational_received_Bps'] if a else None,
                'baseline_operational_Bps':b[1]['operational_received_Bps'] if b else None,
                'delta_Bps_pct':(100*(a[1]['operational_received_Bps']/b[1]['operational_received_Bps']-1)
                    if a and b and b[1]['operational_received_Bps'] else None),
                'interpretation':'Same stress window and synthetic NMAE ceiling, not equal actual error or wire bytes'})
    return result


def churn_phase_report(blobs,prefix,task):
    """Offline synthetic truth before/during/after the timed node outage."""
    cfg=task['config'];rid=task['run_id'];base=prefix+'runs/'+rid+'/'
    meta=read_json(blobs,base+'metadata.json',{})
    events=read_json(blobs,base+'churn_events.json',[])
    fail=next((e for e in events if e.get('event')=='failure_complete'),None)
    back=next((e for e in events if e.get('event')=='recovery_restart_complete'),None)
    if not fail or not back:return []
    perturb={'start_utc':fail['ts_utc'],'end_utc':back['ts_utc']}
    updates=as_df(csv_rows(blobs.get(base+'updates.csv')),('ts','sample_time'))
    init=as_df(csv_rows(blobs.get(base+'initial_state.csv')),('ts','sample_time'))
    phases=dense_per_phase(meta,updates,init,perturb)
    records=[]
    for key,label in (('pre','pre_outage'),('stress','nodes_offline'),('recovery','post_restart')):
        records.append({'run_id':rid,'mode':cfg['mode'],'scenario_id':cfg['scenario_id'],
             'phase':label,'failed_nodes':len(fail.get('node_ids',[])),**phases.get(key,{})})
    return records


def comparisons(rows):
    grouped=defaultdict(list)
    for r in rows:
        if r['block'] not in ('scalability','dynamic'):continue
        grouped[(r['block'],r['n'],r['scenario_id'])].append(r)
    out=[]
    for key,cohort in sorted(grouped.items()):
        for threshold in TIERS:
            yes=[r for r in cohort if eligible(r,threshold)]
            a=next((r for r in yes if r['mode']=='A1'),None)
            b=min((r for r in yes if r['mode'] in ('B0','B1_guard')),
                  key=lambda r:r['operational_received_payload_bytes'],default=None)
            out.append({'block':key[0],'n':key[1],'scenario_id':key[2],
                        'synthetic_nmae_ceiling':threshold,
                        'status':'tier_match' if a and b else 'no_match',
                        'a1_variant':a['variant'] if a else None,
                        'baseline_variant':b['variant'] if b else None,
                        'a1_bytes':a['operational_received_payload_bytes'] if a else None,
                        'baseline_bytes':b['operational_received_payload_bytes'] if b else None,
                        'a1_nmae':a['dense_synthetic_nmae'] if a else None,
                        'baseline_nmae':b['dense_synthetic_nmae'] if b else None,
                        'delta_bytes_pct':(100*(a['operational_received_payload_bytes']/b['operational_received_payload_bytes']-1)
                                           if a and b and b['operational_received_payload_bytes'] else None),
                        'interpretation':'Same predeclared synthetic ceiling, NOT identical error or physical-SLO certification'})
    return out


def grouped_summary(rows):
    grp=defaultdict(list)
    for r in rows:
        grp[(r['block'],r['n'],r['variant'])].append(r)
    out=[]
    for (block,n,var),group in sorted(grp.items()):
        good=[r for r in group if r['quality_ok']]
        for field in ('operational_received_payload_bytes','dense_synthetic_nmae',
                      'accepted_state_updates_per_s','controller_runtime_p95_ms',
                      'churn_recovery_all_s','docker_cpu_pct_max_single_container'):
            pass
        record={'block':block,'n':n,'variant':var,'planned':len(group),
                'quality_ok':len(good),'admissible_strict':sum(eligible(r,0.018) for r in group)}
        for col in ('operational_received_payload_bytes','dense_synthetic_nmae',
                    'accepted_state_updates_per_s','controller_runtime_p95_ms',
                    'churn_recovery_all_s','docker_cpu_pct_max_single_container'):
            values=[r[col] for r in good if r.get(col) is not None]
            record[col+'_mean']=statistics.mean(values) if values else None
            record[col+'_sd']=statistics.stdev(values) if len(values)>=2 else None
        out.append(record)
    return out


def save_csv(path,rows):
    if not rows:return
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=keys);wr.writeheader();wr.writerows(rows)


def analyze(source):
    blobs,prefix,plan=load(source)
    runs=[]; phases=[]; churn_phases=[]
    for task in plan['tasks']:
        if task.get('status')=='planned':continue
        r=assess_run(blobs,task,prefix);runs.append(r)
        if r['block']=='churn' and task['status']=='complete':
            try:churn_phases.extend(churn_phase_report(blobs,prefix,task))
            except Exception as exc:
                r['quality_ok']=False;r['quality_flags']+=';churn_phase_error_'+type(exc).__name__
        if r['block']=='dynamic' and task['status']=='complete':
            try:
                _,detail=analyze_one(blobs,prefix,task,int(task['config']['policy_env']['COMM_BUDGET_BPS']))
                phases.extend(detail)
            except Exception as exc:
                r['quality_ok']=False;r['quality_flags']+=';phase_analysis_failed_'+type(exc).__name__
    expected=Counter(t['config']['block'] for t in plan['tasks'])
    got=Counter(t['config']['block'] for t in plan['tasks'] if t['status']=='complete')
    summaries=grouped_summary(runs)
    matches=comparisons(runs)
    stress_matches=stress_tier_matches(phases,runs)
    gate=plan.get('gate',{})
    audit={'campaign_id':plan['campaign_id'],'protocol_version':plan['protocol_version'],
           'gate':gate,'capacity_diagnostic_only':plan['protocol_version']=='iotj-v13-capacity-check-v1',
           'gate_source_sha256':plan.get('source_sha256'),
           'planned':len(plan['tasks']),'complete':sum(t['status']=='complete' for t in plan['tasks']),
           'quality_ok':sum(r['quality_ok'] for r in runs),
           'block_planned':dict(expected),'block_complete':dict(got),
           'tier_matches':sum(c['status']=='tier_match' for c in matches),
           'stress_tier_matches':sum(c['status']=='tier_match' for c in stress_matches),
           'run_failures':[t['run_id'] for t in plan['tasks'] if t['status'] in ('failed','analysis_failed')],
           'unexecuted':[t['run_id'] for t in plan['tasks'] if t['status']=='planned'],
           'constraints':plan.get('protocol'),
           'limitations':['Synthetic-state oracle only: not sensor-ground truth or PUE prediction.',
               'Operational byte counts refer to server-received HTTP POST bodies, excluding IP/MQTT overhead.',
               'Application-layer delay/drop is not network packet loss.',
               'A policy modeled as feasible is not guaranteed to meet physical SLOs.',
               'One Windows Docker host cannot establish distributed multi-host scalability.',
               'No queue depth measured: concurrent requests and CPU utilization are not queue occupancy.',
               'Churn synthetic truth persists offline, unlike missing physical observations.',
               'No automatic superiority claim; lack of matched baselines remains no_match.']}
    return {'runs':runs,'phases':phases,'churn_phases':churn_phases,
            'comparisons':matches,'stress_matches':stress_matches,'groups':summaries,'audit':audit}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',type=Path)
    ap.add_argument('--out',type=Path,default=Path('auditoria_fase13'))
    args=ap.parse_args()
    result=analyze(args.source);args.out.mkdir(parents=True,exist_ok=True)
    for key,basename in [('runs','runs_v13.csv'),('phases','phases_v13.csv'),
                         ('comparisons','comparisons_v13.csv'),('stress_matches','stress_matches_v13.csv'),
                         ('churn_phases','churn_phases_v13.csv'),('groups','groups_v13.csv')]:
        save_csv(args.out/basename,result[key])
    (args.out/'audit_v13.json').write_text(json.dumps(result['audit'],indent=2,ensure_ascii=False),encoding='utf8')
    print(json.dumps({k:result['audit'][k] for k in ('planned','complete','quality_ok','tier_matches')},indent=2))

if __name__=='__main__':main()
