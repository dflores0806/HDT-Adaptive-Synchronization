#!/usr/bin/env python3
"""Predeclared synthetic fidelity-tier audit for the phase-11 matched campaign.

No observational facility data or statistical significance claim. A result is
eligible for a direct comparison ONLY if the policy settings match exactly,
the same scenario is used, measured average operational payload fits the common
budget, no controller feasibility/coverage violations occur, and diagnostic
quality checks pass. Raw B1 is retained but never silently deemed coverage-safe.

If no comparable baseline exists for a threshold, report NO_MATCH.
"""
from __future__ import annotations
import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from compare_v4 import _load_source, examine_campaign

REQUIRED_PROFILE='matched'


def fully_eligible(r, common_budget):
    problems=[]
    if not r.get('run_complete'): problems.append('incomplete')
    if not r.get('quality_ok'): problems.append('cadence_or_integrity')
    if r.get('nmae_dense_0p5s') is None: problems.append('missing_dense_fidelity')
    if r.get('feasible_fraction') is None or float(r['feasible_fraction']) < 1.0-1e-9:
        problems.append('predicted_slo_violations')
    if r.get('coverage_violations') is None or float(r['coverage_violations']) != 0:
        problems.append('subsystem_coverage_violations')
    seconds=r.get('measurement_s')
    operational=r.get('operational_http_bytes')
    if not seconds or operational is None:
        problems.append('missing_operational_rate')
    elif float(operational)/float(seconds) > common_budget + 1e-9:
        problems.append('average_operational_payload_over_budget')
    if not r.get('scenario_verified',False): problems.append('scenario_unverified')
    if not r.get('config_verified',False): problems.append('common_config_unverified')
    if r.get('timing_log_status')!='available': problems.append('missing_timing_logs')
    if r.get('agent_slots_skipped') is None or r['agent_slots_skipped']!=0:
        problems.append('agent_missing_or_skipped_slots')
    if r.get('policy_slots_skipped') is None or r['policy_slots_skipped']!=0:
        problems.append('policy_missing_or_skipped_slots')
    if r.get('agent_reference_http_failures') is None or r['agent_reference_http_failures']!=0:
        problems.append('reference_http_failures_or_missing')
    return problems


def tier_matches(rows, thresholds, budget):
    """Pure, independently testable selection; all statuses are exposed.

    A1 and baselines run sequentially with same deterministic SOURCE SCENARIO,
    not simultaneously; match refers to limits and a synthetic error tier,
    not to physical devices, network conditions, or equal realized fidelity.
    """
    blocks=defaultdict(list)
    for r in rows:
        blocks[(r['repeat'],r.get('scenario_id'))].append(r)
    results=[]
    for (rep,scenario_id), cohort in sorted(blocks.items()):
        adaptives=[r for r in cohort if r['mode']=='A1']
        baselines=[r for r in cohort if r['mode'] in ('B0','B1_guard')]
        for tol in thresholds:
            admissible_a=[r for r in adaptives if r.get('eligible') and r.get('nmae_dense_0p5s') is not None
                          and r['nmae_dense_0p5s']<=tol]
            admissible_b=[r for r in baselines if r.get('eligible') and r.get('nmae_dense_0p5s') is not None
                          and r['nmae_dense_0p5s']<=tol]
            chosen_a=min(admissible_a,key=lambda r:r['operational_http_bytes']) if admissible_a else None
            chosen_b=min(admissible_b,key=lambda r:r['operational_http_bytes']) if admissible_b else None
            status='matched_synthetic_tier' if chosen_a and chosen_b else 'no_match'
            result={'repeat':rep,'scenario_id':scenario_id,'threshold_nmae_synthetic':tol,
                    'status':status,'eligible_A1_count':len(admissible_a),
                    'eligible_baseline_count':len(admissible_b),
                    'A1_variant':chosen_a['variant'] if chosen_a else None,
                    'baseline_variant':chosen_b['variant'] if chosen_b else None,
                    'A1_bytes':chosen_a['operational_http_bytes'] if chosen_a else None,
                    'baseline_bytes':chosen_b['operational_http_bytes'] if chosen_b else None,
                    'A1_nmae':chosen_a['nmae_dense_0p5s'] if chosen_a else None,
                    'baseline_nmae':chosen_b['nmae_dense_0p5s'] if chosen_b else None,
                    'difference_bytes_A1_minus_B':(chosen_a['operational_http_bytes']-chosen_b['operational_http_bytes'])
                          if chosen_a and chosen_b else None,
                    'traffic_delta_pct_A1_vs_B':(100.*(chosen_a['operational_http_bytes']/chosen_b['operational_http_bytes']-1))
                          if chosen_a and chosen_b and chosen_b['operational_http_bytes'] else None,
                    'interpretation':'PREDECLARED synthetic NMAE acceptance tier; not a physical tolerance'}
            results.append(result)
    return results


def audit_campaign(path):
    files,prefix,plan=_load_source(path)
    if plan.get('profile')!=REQUIRED_PROFILE:
        raise ValueError('This strict audit accepts only --profile matched campaigns')
    protocol=plan.get('phase11_protocol')
    if not isinstance(protocol,dict):
        raise ValueError('Missing locked phase-11 protocol')
    thresholds=protocol.get('fidelity_thresholds')
    budget=protocol.get('common_policy_comm_budget_Bps')
    if thresholds!=[0.006,0.009,0.012,0.018] or budget!=1500:
        raise ValueError('Protocol deviation: thresholds or budget were changed')
    base=examine_campaign(path,
        min_coverage=protocol['reference_and_decision_cadence_min'],
        min_decision_ratio=protocol['reference_and_decision_cadence_min'])
    ordinary={r['run_id']:dict(r) for r in base['runs']}
    expected_constraints=None
    for task in plan['tasks']:
        run_id=task['run_id']; spec=task['config']; row=ordinary[run_id]
        declared=dict(spec.get('policy_env') or {})
        if expected_constraints is None: expected_constraints=declared
        if declared!=expected_constraints:
            raise ValueError('Multiple resource/SLO configurations in a purported matched campaign')
        actual_meta_key=prefix+f'runs/{run_id}/metadata.json'
        metadata=json.loads(files[actual_meta_key]) if actual_meta_key in files else {}
        scenario=spec.get('scenario_id')
        row['scenario_id']=scenario
        row['scenario_verified']=(scenario is not None and
            metadata.get('synthetic_scenario_id')==scenario and
            scenario==spec['repeat']+1)
        overrides=metadata.get('policy_env_overrides') or {}
        row['config_verified']=all(str(overrides.get(k))==str(v) for k,v in declared.items())
        row['intended_budget_Bps']=budget
        row['protocol_kind']='fixed common synthetic constraints; independent policy trial'
        row['observed_mean_operational_Bps']=(row['operational_http_bytes']/row['measurement_s']
            if row.get('measurement_s') and row.get('operational_http_bytes') is not None else None)
        row['exclusion_reasons']=';'.join(fully_eligible(row,budget))
        row['eligible']=not bool(row['exclusion_reasons'])
    if expected_constraints is None or expected_constraints.get('COMM_BUDGET_BPS')!='1500':
        raise ValueError('Missing common matched campaign budget')
    runs=list(ordinary.values())
    tier_results=tier_matches(runs,thresholds,budget)
    grouped=[]
    for tol in thresholds:
        matched=[r['traffic_delta_pct_A1_vs_B'] for r in tier_results
            if r['threshold_nmae_synthetic']==tol and r['status']=='matched_synthetic_tier'
            and r['traffic_delta_pct_A1_vs_B'] is not None]
        grouped.append({'threshold_nmae_synthetic':tol,'matched_scenarios':len(matched),
            'scenarios_total':len({r['scenario_id'] for r in runs}),
            'mean_delta_pct_A1_vs_B':statistics.mean(matched) if matched else None,
            'sd_delta_pct':statistics.stdev(matched) if len(matched)>1 else None,
            'warning':'No inferential claim; no match means exactly no admissible comparison'})
    return {'campaign_id':plan.get('campaign_id'),'profile':plan['profile'],
        'protocol':protocol,'common_policy_env':expected_constraints,
        'summary':{'planned':len(plan['tasks']),'complete':sum(bool(r.get('run_complete')) for r in runs),
                   'quality_ok':sum(bool(r.get('quality_ok')) for r in runs),
                   'eligible':sum(bool(r.get('eligible')) for r in runs),
                   'matched_tiers':sum(r['status']=='matched_synthetic_tier' for r in tier_results)},
        'runs':runs,'tier_matches':tier_results,'tier_summary':grouped,
        'limitations':[
          'Synthetic deterministic scenarios, NOT measured physical DT states or PUE fidelity.',
          'Predeclared tiers are diagnostic NMAE thresholds, NOT validated application tolerances.',
          'Same model constraints do not imply same realized fidelity or end-to-end network bytes.',
          'Only server-observed HTTP POST bodies + heartbeats are included in operational payload.',
          'Policy-predicted feasibility is NOT a hard guarantee on measured physical SLOs.',
          'No-match tiers remain empty: no best-case replacement or post-hoc exclusion.',
          'Sequential runs have potential host-environment variability; no statistical dominance implied.'
        ]}


def write_csv(path,rows):
    if not rows: return
    keys=list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('campaign',type=Path)
    ap.add_argument('--out',type=Path,default=Path('auditoria_fase11'))
    args=ap.parse_args()
    data=audit_campaign(args.campaign)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'audit_v11.json').write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
    write_csv(args.out/'runs_v11.csv',data['runs'])
    write_csv(args.out/'fidelity_tiers_v11.csv',data['tier_matches'])
    write_csv(args.out/'tier_summary_v11.csv',data['tier_summary'])
    print(json.dumps(data['summary'],indent=2))
    for row in data['tier_summary']:
        print('NMAE <=',row['threshold_nmae_synthetic'],
              'match',row['matched_scenarios'],'of',row['scenarios_total'])

if __name__=='__main__': main()
