#!/usr/bin/env python3
"""Phase 12 controlled operational HTTP-transport perturbation audit.

Consumes a campaign ZIP or an extracted directory. Distinguishes agent-offered
application requests, server-received HTTP bodies, and application-level
injected drop/delay. References are unperturbed; reference-based/fully synthetic
NMAE are independent from the online controller.

NOT network-emulator wire loss, calibrated energy, or measured physical SLOs.
No unpublished data and no affirmative performance claim is generated.
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import math
import statistics
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from analyze_v2 import (synthetic_oracle_dataframe, truth_errors)

PROFILE = 'perturbation'
DECLARED_LEVELS = ('none', 'delay', 'delay_loss')
THRESHOLDS = (0.012, 0.018)


def parse_ts(value):
    if not value:
        return None
    v = str(value).strip().replace('Z', '+00:00')
    dt = datetime.fromisoformat(v)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def load_campaign(path):
    path = Path(path)
    if path.is_file() and path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            files = {name: archive.read(name) for name in archive.namelist()
                     if name.endswith(('.json','.csv','.log'))
                     and not name.startswith('__MACOSX/')}
    elif path.is_dir():
        files = {name.relative_to(path).as_posix(): name.read_bytes()
                 for name in path.rglob('*') if name.is_file()
                 and name.suffix.lower() in ('.json','.csv','.log')}
    else:
        raise ValueError('Se esperaba ZIP o carpeta extraida')
    candidates = [name for name in files if name.endswith('plan.json')]
    if len(candidates) != 1:
        raise ValueError(f'Se necesita un plan.json, encontrados {len(candidates)}')
    key = candidates[0]
    plan = json.loads(files[key])
    if plan.get('profile') != PROFILE:
        raise ValueError('Solo se admite una campana --profile perturbation')
    proto = plan.get('phase12_protocol')
    if not isinstance(proto, dict) or proto.get('protocol_version') != 'phase12-app-http-impair-v1':
        raise ValueError('Falta el protocolo de perturbacion de fase 12')
    if (proto.get('impair_profiles') != list(DECLARED_LEVELS) or
            proto.get('fidelity_nmae_thresholds') != list(THRESHOLDS) or
            proto.get('common_policy_budget_Bps') != 1500):
        raise ValueError('El protocolo experimental no corresponde al predefinido')
    return files, key[:-len('plan.json')], plan


def csv_rows(content):
    if not content:
        return []
    return list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))


def percentile(values, pct):
    finite = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return float(np.percentile(finite, pct)) if finite else None


def phase_of(ts, perturb):
    lo = parse_ts(perturb['start_utc'])
    hi = parse_ts(perturb['end_utc'])
    return 'pre' if ts < lo else 'stress' if ts < hi else 'recovery'


def extract_http_observations(files, prefix, run_id):
    base = prefix + f'runs/{run_id}/scheduler_logs/'
    observations = []
    seen = []
    for name, blob in files.items():
        if not name.startswith(base) or not name.endswith('.log') or '/edge_' not in name:
            continue
        seen.append(name)
        for line in blob.decode('utf-8', errors='replace').splitlines():
            marker = 'HDT_HTTP_OBSERVATION '
            if marker not in line:
                continue
            raw = line.split(marker, 1)[1]
            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if event.get('run_id') == run_id:
                observations.append(event)
    return observations, seen


def as_df(rows, date_fields):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for name in date_fields:
        if name in df.columns:
            df[name] = pd.to_datetime(df[name], utc=True)
    return df


def dense_per_phase(meta, received_updates, initial_state, perturb):
    """Counterfactual deterministic synthetic oracle; NEVER online truth."""
    true = synthetic_oracle_dataframe(meta, step_s=0.5)
    if true.empty:
        return {}
    arr = pd.concat([initial_state, received_updates], ignore_index=True) if not (initial_state.empty and received_updates.empty) else pd.DataFrame()
    if arr.empty or 'accepted' not in arr:
        records = []
    else:
        accepted = arr['accepted'].astype(str).str.lower().isin(('true','t','1'))
        arr = arr[accepted].copy()
        records = truth_errors(true, arr) if not arr.empty else []
    phase_truth = defaultdict(int)
    phase_matched = defaultdict(list)
    for timestamp in true['sample_time']:
        phase_truth[phase_of(timestamp.to_pydatetime(), perturb)] += 1
    for record in records:
        phase = phase_of(record['sample_time'].to_pydatetime(), perturb)
        phase_matched[phase].append(record)
    output = {}
    for phase in ('pre','stress','recovery'):
        vals=phase_matched[phase]
        output[phase] = {'dense_synthetic_nmae':statistics.mean(x['error_norm'] for x in vals) if vals else None,
                         'dense_synthetic_nmae_p95':percentile([x['error_norm'] for x in vals],95),
                         'dense_synthetic_aoi_p95_s':percentile([x['aoi_s'] for x in vals],95),
                         'dense_synthetic_matched':len(vals),
                         'dense_synthetic_expected':phase_truth[phase],
                         'dense_unmatched_fraction':1-len(vals)/phase_truth[phase] if phase_truth[phase] else None}
    return output


def analyze_one(files, prefix, task, budget):
    run_id = task['run_id']; config = task['config']
    base = prefix + f'runs/{run_id}/'
    meta = json.loads(files[base+'metadata.json']) if base+'metadata.json' in files else {}
    summary = json.loads(files[base+'summary_v2.json']) if base+'summary_v2.json' in files else {}
    perturb = meta.get('transport_perturbation')
    planned = config.get('impair_profile')
    planned_flags = []
    if not summary or not meta.get('run_complete'):
        planned_flags.append('missing_summary_or_incomplete')
    if not perturb:
        planned_flags.append('missing_perturbation_metadata')
        perturb = {'start_utc':meta.get('start_utc','2000-01-01T00:00:00+00:00'),
                   'end_utc':meta.get('end_utc','2000-01-02T00:00:00+00:00'),
                   'profile':'missing'}
    if planned not in DECLARED_LEVELS or perturb['profile'] != planned:
        planned_flags.append('profile_mismatch')
    if meta.get('synthetic_scenario_id')!=config.get('scenario_id'):
        planned_flags.append('source_scenario_mismatch')
    if int(config.get('n',0))!=int(meta.get('n',-1)):
        planned_flags.append('agent_count_mismatch')
    policy_overrides = meta.get('policy_env_overrides') or {}
    if any(str(policy_overrides.get(k))!=str(v) for k,v in (config.get('policy_env') or {}).items()):
        planned_flags.append('policy_constraints_mismatch')
    obs, logs = extract_http_observations(files,prefix,run_id)
    if len(logs)<int(config.get('n',0)) or not obs:
        planned_flags.append('missing_http_observation_logs')
    # Externally recorded Python request bodies: not wire bytes nor energy.
    updates=csv_rows(files.get(base+'updates.csv'))
    heartbeats=csv_rows(files.get(base+'heartbeats.csv'))
    references=csv_rows(files.get(base+'reference.csv'))
    decisions=csv_rows(files.get(base+'decisions.csv'))
    comm=summary.get('communication') or {}
    control=summary.get('controller') or {}
    fidelity=summary.get('dense_synthetic_oracle_fidelity') or {}
    cadence=summary.get('cadence_v9') or {}
    all_reference_rows = len(references)
    reference_expected = (int(config['n'])*float(meta.get('measurement_s',120))/2.) if meta.get('measurement_s') else None
    if reference_expected is None and meta.get('start_utc') and meta.get('end_utc'):
        reference_expected = int(config['n'])*(parse_ts(meta['end_utc'])-parse_ts(meta['start_utc'])).total_seconds()/2.
    if (cadence.get('agent') or {}).get('reference_http_failures',0):
        planned_flags.append('reference_http_errors')
    for ev in obs:
        if ev.get('endpoint') == 'reference' and (ev.get('injected_drop') or ev.get('injected_delay_ms')):
            planned_flags.append('reference_improperly_perturbed')
            break
    phase_windows={}
    if meta.get('start_utc') and meta.get('end_utc'):
        mstart=parse_ts(meta['start_utc']); mend=parse_ts(meta['end_utc'])
        stressed_lo=parse_ts(perturb['start_utc']); stressed_hi=parse_ts(perturb['end_utc'])
        intervals={'pre':(mstart,min(stressed_lo,mend)),
                   'stress':(max(mstart,stressed_lo),min(mend,stressed_hi)),
                   'recovery':(max(mstart,stressed_hi),mend)}
        phase_windows={p:max(0,(end-beg).total_seconds()) for p,(beg,end) in intervals.items()}
    if not phase_windows or phase_windows.get('stress',0)<10:
        planned_flags.append('insufficient_stress_window')
    observations_by_phase=defaultdict(list)
    for event in obs:
        try:
            ts=parse_ts(event['utc'])
        except (KeyError,ValueError):
            continue
        if meta.get('start_utc') and meta.get('end_utc') and not (mstart<=ts<mend):
            continue
        observations_by_phase[phase_of(ts,perturb)].append(event)
    phase_details=[]
    dense={}
    if all(key in files for key in (base+'updates.csv',base+'initial_state.csv')):
        up_df=as_df(updates,('ts','sample_time'))
        init_df=as_df(csv_rows(files[base+'initial_state.csv']),('ts','sample_time'))
        try:
            dense=dense_per_phase(meta,up_df,init_df,perturb)
        except Exception as exc:
            planned_flags.append('phase_oracle_error:'+type(exc).__name__)
    for phase in ('pre','stress','recovery'):
        subset=observations_by_phase[phase]
        operational=[e for e in subset if e.get('endpoint') in ('state','heartbeat')]
        applied=[e for e in operational if e.get('injection_active')]
        dropped=sum(bool(e.get('injected_drop')) for e in operational)
        state_attempts=sum(e.get('endpoint')=='state' for e in operational)
        state_received=sum(phase_of(parse_ts(row['ts']),perturb)==phase for row in updates if row.get('ts'))
        hb_received=sum(phase_of(parse_ts(row['ts']),perturb)==phase for row in heartbeats if row.get('ts'))
        ref_received=sum(phase_of(parse_ts(row['ts']),perturb)==phase for row in references if row.get('ts'))
        op_bytes=(sum(int(float(r.get('payload_bytes') or 0)) for r in updates if r.get('ts') and phase_of(parse_ts(r['ts']),perturb)==phase)
                 +sum(int(float(r.get('heartbeat_bytes') or 0)) for r in heartbeats if r.get('ts') and phase_of(parse_ts(r['ts']),perturb)==phase))
        dec=[r for r in decisions if r.get('ts') and phase_of(parse_ts(r['ts']),perturb)==phase]
        bad_pred=0; coverage_bad=0
        for r in dec:
            try:
                detail=json.loads(r.get('details') or '{}')
            except ValueError:
                detail={}
            if not detail.get('feasible',False):
                bad_pred+=1
            if any('subsystem_coverage' in str(v) for v in detail.get('violations',[])):
                coverage_bad+=1
        expected_ref=(int(config['n'])*phase_windows.get(phase,0)/2.) if phase_windows else None
        phase_details.append({
            'run_id':run_id,'mode':config['mode'],'variant':config.get('variant'),
            'scenario_id':config.get('scenario_id'),'impair_profile':planned,'phase':phase,
            'phase_seconds':phase_windows.get(phase),
            'operational_offered_attempts':len(operational),'offered_state_attempts':state_attempts,
            'injected_drop_attempts':dropped,'injection_active_attempts':len(applied),
            'operational_http_request_p95_ms':percentile([e.get('request_wall_ms') for e in operational],95),
            'operational_state_received':state_received,'heartbeat_received':hb_received,
            'reference_received':ref_received,'expected_reference_count_approx':expected_ref,
            'reference_cadence_ratio':ref_received/expected_ref if expected_ref else None,
            'operational_received_body_bytes':op_bytes,
            'operational_received_Bps':op_bytes/phase_windows[phase] if phase_windows.get(phase) else None,
            'decision_count':len(dec),'predicted_infeasible_decisions':bad_pred,
            'coverage_violating_decisions':coverage_bad,
            **dense.get(phase,{})})
    if planned=='none' and any(x['injection_active_attempts'] for x in phase_details):
        planned_flags.append('control_not_unperturbed')
    if planned!='none' and next(x for x in phase_details if x['phase']=='stress')['injection_active_attempts']==0:
        planned_flags.append('no_stress_requests_in_window')
    if planned=='delay_loss' and not next(x for x in phase_details if x['phase']=='stress')['injected_drop_attempts']:
        planned_flags.append('missing_expected_injected_loss')
    if not obs and task.get('status')=='complete':
        planned_flags.append('http_logs_absent')
    agent_cadence=cadence.get('agent') or {}; policy_cadence=cadence.get('policy') or {}
    measured_secs=(parse_ts(meta['end_utc'])-parse_ts(meta['start_utc'])).total_seconds() if meta.get('start_utc') and meta.get('end_utc') else None
    if not measured_secs or float(control.get('decision_count') or 0)<0.95*(measured_secs/2):
        planned_flags.append('low_decision_cadence')
    if reference_expected and all_reference_rows/reference_expected<0.95:
        planned_flags.append('low_reference_cadence')
    if cadence.get('timing_log_status') != 'available':
        planned_flags.append('missing_scheduler_logs')
    if agent_cadence.get('ticks_skipped') not in (0,None):
        planned_flags.append('agent_missed_slots')
    if policy_cadence.get('ticks_skipped') not in (0,None):
        planned_flags.append('policy_missed_slots')
    row = {'run_id':run_id,'task_status':task.get('status'),
           'mode':config['mode'],'variant':config.get('variant'),
           'scenario_id':config.get('scenario_id'),'impair_profile':planned,
           'run_complete':bool(meta.get('run_complete')),
           'quality_ok':not planned_flags,'quality_flags':';'.join(dict.fromkeys(planned_flags)),
           'operational_http_bytes':comm.get('operational_http_payload_bytes'),
           'evaluation_reference_bytes':comm.get('evaluation_reference_bytes'),
           'dense_synthetic_nmae':fidelity.get('nmae'),
           'dense_synthetic_aoi_p95_s':fidelity.get('aoi_p95_s'),
           'nmae_unmatched_fraction':fidelity.get('unmatched_reference_fraction'),
           'predicted_feasible_fraction':control.get('feasible_fraction'),
           'predicted_coverage_violation_count':(control.get('violation_counts') or {}).get('subsystem_coverage',0),
           'reference_cadence_ratio':all_reference_rows/reference_expected if reference_expected else None,
           'transport_observation_count':len(obs),
           'operational_offered_attempts':sum(len([e for e in x if e.get('endpoint') in ('state','heartbeat')]) for x in observations_by_phase.values()),
           'injected_drop_attempts':sum(x['injected_drop_attempts'] for x in phase_details),
           'common_budget_Bps':budget,
           'operational_received_mean_Bps':(comm['operational_http_payload_bytes']/measured_secs
                 if comm.get('operational_http_payload_bytes') is not None and measured_secs else None)}
    return row,phase_details


def tier_comparisons(runs):
    """Fixed NMAE ceilings, never exact-error parity or a superiority claim."""
    groups=defaultdict(list)
    for row in runs:
        groups[(row['scenario_id'],row['impair_profile'])].append(row)
    results=[]
    for (scenario, profile), cohort in sorted(groups.items()):
        for threshold in THRESHOLDS:
            eligible=[r for r in cohort
                      if r.get('quality_ok') and r.get('run_complete')
                      and r.get('dense_synthetic_nmae') is not None
                      and r['dense_synthetic_nmae']<=threshold
                      and r.get('predicted_feasible_fraction') == 1.0
                      and r.get('predicted_coverage_violation_count') == 0
                      and r.get('nmae_unmatched_fraction') == 0
                      and r.get('operational_received_mean_Bps') is not None
                      and r['operational_received_mean_Bps']<=r['common_budget_Bps']]
            a=[r for r in eligible if r['mode']=='A1']
            b=[r for r in eligible if r['mode'] in ('B0','B1_guard')]
            a=min(a,key=lambda r:r['operational_http_bytes']) if a else None
            b=min(b,key=lambda r:r['operational_http_bytes']) if b else None
            results.append({'scenario_id':scenario,'impair_profile':profile,
                'nmae_ceiling':threshold,'status':'synthetic_tier_match' if a and b else 'no_match',
                'a1_run_id':a['run_id'] if a else None,
                'baseline_run_id':b['run_id'] if b else None,
                'a1_bytes':a['operational_http_bytes'] if a else None,
                'baseline_bytes':b['operational_http_bytes'] if b else None,
                'a1_nmae':a['dense_synthetic_nmae'] if a else None,
                'baseline_nmae':b['dense_synthetic_nmae'] if b else None,
                'traffic_delta_pct':(100*(a['operational_http_bytes']/b['operational_http_bytes']-1)
                       if a and b and b['operational_http_bytes'] else None),
                'warning':'Same synthetic NMAE ceiling, not equal error or physical SLO'} )
    return results


def save_csv(path,rows):
    if not rows:
        return
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',newline='',encoding='utf-8-sig') as file:
        writer=csv.DictWriter(file,fieldnames=keys)
        writer.writeheader();writer.writerows(rows)


def analyze_campaign(path):
    files,prefix,plan=load_campaign(path)
    run_rows=[];phases=[]
    for task in plan['tasks']:
        row,phase_rows=analyze_one(files,prefix,task,plan['phase12_protocol']['common_policy_budget_Bps'])
        run_rows.append(row);phases+=phase_rows
    # All scenario/profile cells are checked without assuming container order.
    counts=defaultdict(int)
    for r in run_rows:
        counts[(r['scenario_id'],r['impair_profile'])]+=1
    expected_matrix = {(sc,imp,mode,fixed): 1
                       for sc in (1,2) for imp in DECLARED_LEVELS
                       for mode,fixed in (('B0',2.),('B0',3.),('B1_guard',2.),('A1',2.))}
    actual_matrix=defaultdict(int)
    for task in plan['tasks']:
        cfg=task['config']
        actual_matrix[(cfg.get('scenario_id'),cfg.get('impair_profile'),
                       cfg.get('mode'),cfg.get('fixed_interval'))]+=1
    matrix_ok=(len(run_rows)==24 and dict(actual_matrix)==expected_matrix
               and len(counts)==6 and all(v==4 for v in counts.values()))
    audit={'campaign_id':plan.get('campaign_id'),'protocol_version':'phase12-app-http-impair-v1',
           'planned':len(plan['tasks']),'complete':sum(r['run_complete'] for r in run_rows),
           'quality_ok':sum(r['quality_ok'] for r in run_rows),
           'matrix_ok':matrix_ok,'profiles':list(DECLARED_LEVELS),'scenarios':[1,2],
           'matched_synthetic_tiers':sum(x['status']=='synthetic_tier_match' for x in tier_comparisons(run_rows)),
           'observational_limits':[
              'Injected delays and drops affect the agents HTTP application path, not IP/network packets.',
              'The independent reference channel is unperturbed; synthetic-oracle state is offline.',
              'Operational bytes include HTTP POST bodies received by the server only; injected drops are counted as offers, not wire frames.',
              'Modeled feasibility is not measured physical-SLO compliance.',
              'Each scenario/profile has one run per mode; this is a diagnostic, not a statistical superiority claim.',
              'NMAE tolerance tiers have not been validated against physical DT/PUE quality.'
           ]}
    return {'runs':run_rows,'phase':phases,'comparisons':tier_comparisons(run_rows),'audit':audit}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--out',type=Path,default=Path('auditoria_fase12'))
    args=parser.parse_args()
    result=analyze_campaign(args.source)
    args.out.mkdir(parents=True,exist_ok=True)
    save_csv(args.out/'runs_v12.csv',result['runs'])
    save_csv(args.out/'phase_v12.csv',result['phase'])
    save_csv(args.out/'comparisons_v12.csv',result['comparisons'])
    (args.out/'audit_v12.json').write_text(json.dumps(result['audit'],indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(result['audit'],indent=2,ensure_ascii=False))
    if not result['audit']['matrix_ok'] or result['audit']['complete']!=result['audit']['planned']:
        raise SystemExit(2)

if __name__=='__main__':
    main()
