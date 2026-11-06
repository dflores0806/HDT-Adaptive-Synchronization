#!/usr/bin/env python3
"""Phase-10 audit of a completed, run-isolated IoTJ cadence pilot.

No refitting, policy selection, inference of wire-level throughput, or
statistical significance. Standard library only. Accepts a campaign ZIP or
extracted campaign, retaining every completed/incomplete run and its SLO flags.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from compare_v4 import _load_source, examine_campaign


def pareto_dominates(a, b):
    """Descriptive *within-run* traffic vs. oracle-error dominance only."""
    ab, ae = a['operational_http_bytes'], a['nmae_dense_0p5s']
    bb, be = b['operational_http_bytes'], b['nmae_dense_0p5s']
    return ab <= bb and ae <= be and (ab < bb or ae < be)


def assess_campaign(path):
    files, prefix, plan = _load_source(path)
    ordinary = examine_campaign(path)
    ordinary_runs = {r['run_id']: r for r in ordinary['runs']}
    enriched=[]
    for task in plan['tasks']:
        ident=task['run_id']
        summary_path=prefix+'runs/'+ident+'/summary_v2.json'
        row=dict(ordinary_runs[ident]); row['status_note']=''
        if summary_path not in files:
            row['status_note']='Missing summary: not eligible for comparisons'
            enriched.append(row); continue
        summary=json.loads(files[summary_path]); cadence=summary.get('cadence_v9') or {}
        agent=cadence.get('agent') or {}; policy=cadence.get('policy') or {}
        ctrl=summary.get('controller') or {}
        row.update({
            'agent_ticks':agent.get('tick_count'),
            'policy_ticks':policy.get('tick_count'),
            'agent_skipped_slots':agent.get('ticks_skipped'),
            'policy_skipped_slots':policy.get('ticks_skipped'),
            'agent_reference_http_failures':agent.get('reference_http_failures'),
            'agent_heartbeat_http_failures':agent.get('heartbeat_http_failures'),
            'agent_state_nacks':agent.get('state_application_nacks_logged'),
            'agent_schedule_lag_p95_ms':(agent.get('schedule_lag_ms') or {}).get('p95'),
            'policy_schedule_lag_p95_ms':(policy.get('schedule_lag_ms') or {}).get('p95'),
            'policy_cycle_wall_p95_ms':(policy.get('cycle_wall_ms') or {}).get('p95'),
            'selected_nodes_mean':ctrl.get('mean_selected_per_decision'),
            'interval_distribution_s':json.dumps(ctrl.get('interval_distribution_s') or {},sort_keys=True),
            'subsystem_coverage_violations':(ctrl.get('violation_counts') or {}).get('subsystem_coverage',0),
            'any_slo_violations':json.dumps(ctrl.get('violation_counts') or {},sort_keys=True),
        })
        if row.get('quality_ok') and row.get('run_complete'):
            row['status_note']='Complete, cadence passed; single realization (not confirmatory)'
        else:
            row['status_note']='Incomplete or cadence-quality warning'
        enriched.append(row)

    complete=[r for r in enriched if r.get('run_complete') and r.get('quality_ok')
              and r.get('nmae_dense_0p5s') is not None]
    comparisons=[]
    for a in complete:
        if a['mode']!='A1':continue
        for b in complete:
            if b['mode'] not in ('B0','B1') or b['n']!=a['n']:continue
            # Constraint mismatch must not be erased by 2D Pareto dominance.
            cmp={'adaptive_variant':a['variant'],'baseline_variant':b['variant'],
                 'adaptive_run_id':a['run_id'],'baseline_run_id':b['run_id'],
                 'traffic_change_pct':100*(a['operational_http_bytes']/b['operational_http_bytes']-1),
                 'oracle_nmae_change_pct':100*(a['nmae_dense_0p5s']/b['nmae_dense_0p5s']-1),
                 'adaptive_dominates_traffic_error':pareto_dominates(a,b),
                 'baseline_dominates_traffic_error':pareto_dominates(b,a),
                 'adaptive_feasible_fraction':a.get('feasible_fraction'),
                 'baseline_feasible_fraction':b.get('feasible_fraction'),
                 'constraint_matched':(a.get('feasible_fraction')==1 and b.get('feasible_fraction')==1),
                 'warning':'Descriptive independent sequential runs: not a statistically tested matched-fidelity claim'}
            comparisons.append(cmp)
    return {'campaign_id':plan.get('campaign_id'), 'profile':plan.get('profile'),
            'status':ordinary['summary'], 'runs':enriched,'comparisons':comparisons,
            'interpretation':[
              'Rates refer to measured HTTP POST bodies only; not wire throughput or energy.',
              'The dense oracle is a synthetic offline reference, not facility sensor data or PUE prediction.',
              'Four-agent runs are single realizations. No causal attribution or significance test.',
              'SLO-feasibility and per-subsystem coverage must accompany fidelity/traffic Pareto comparisons.',
              'Different controller/source versions are not pooled with earlier phase-7 trials.'
            ]}


def _csv(path, data):
    if not data:return
    keys=list(dict.fromkeys(k for row in data for k in row))
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(data)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('campaign',type=Path)
    p.add_argument('--out',type=Path,default=Path('auditoria_fase10'))
    args=p.parse_args()
    data=assess_campaign(args.campaign)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'audit_v10.json').write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
    _csv(args.out/'runs_v10.csv',data['runs'])
    _csv(args.out/'comparisons_v10.csv',data['comparisons'])
    print(json.dumps(data['status'],indent=2))
    for row in data['runs']:
        print(row['mode'],row['variant'],'ticks',row.get('agent_ticks'),row.get('policy_ticks'),
              'ref_fail',row.get('agent_reference_http_failures'),'heartbeat_fail',row.get('agent_heartbeat_http_failures'),
              'quality',row.get('quality_ok'))


if __name__=='__main__': main()
