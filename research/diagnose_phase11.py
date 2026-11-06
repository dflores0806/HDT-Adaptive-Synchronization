#!/usr/bin/env python3
"""Phase 11: transparent decision-level reasons and eligibility diagnostic.

This is a post-experiment *audit only*. It never changes the control policy,
resource limits, fidelity thresholds or the phase-11 prespecified tier matches.
Uses standard library. Input: original campaign ZIP or extracted folder.
"""
from __future__ import annotations
import argparse
import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path
from compare_v4 import _load_source
from matched_v11 import audit_campaign, write_csv


def extract_decision_reasons(raw: bytes):
    """Return counts of decision rows carrying each modeled constraint violation.

    A decision can violate multiple constraints, hence counts need not sum to
    number of infeasible decisions. Counts are *predicted* not measured SLOs.
    """
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    total=0; infeasible=0; kinds=Counter(); parsing_errors=0
    for r in reader:
        total+=1
        try:
            details=json.loads(r.get('details') or '{}')
            reasons=details.get('violations') or []
            if not isinstance(reasons,list): raise ValueError('not a list')
            feasible=str(r.get('feasible','')).strip().lower() in ('true','t','1','yes')
            if not feasible: infeasible+=1
            for reason in set(map(str,reasons)):
                kinds[reason]+=1
        except (ValueError,TypeError,json.JSONDecodeError):
            parsing_errors+=1
    return {'decisions_parsed':total,'infeasible_decisions':infeasible,
            'detail_parse_errors':parsing_errors,**{f'flag_{k}':v for k,v in sorted(kinds.items())}}


def build_diagnostics(path):
    audit=audit_campaign(path)
    files,prefix,plan=_load_source(path)
    violations=[]
    for r in audit['runs']:
        key=prefix+f"runs/{r['run_id']}/decisions.csv"
        d=extract_decision_reasons(files[key]) if key in files else {'missing_decision_export':True}
        violations.append({k:r.get(k) for k in ('run_id','scenario_id','repeat','variant','mode','quality_ok','eligible','exclusion_reasons','feasible_fraction','coverage_violations') } | d)
    excluded=Counter()
    for r in audit['runs']:
        for reason in (r.get('exclusion_reasons') or '').split(';'):
            if reason: excluded[reason]+=1
    statuses=Counter(x['status'] for x in audit['tier_matches'])
    groups=defaultdict(list)
    for r in audit['runs']:
        groups[r['variant']].append(r)
    stats=[]
    for v, rows in sorted(groups.items()):
        stats.append({'variant':v,'executions':len(rows),
                      'quality_passed':sum(bool(r.get('quality_ok')) for r in rows),
                      'strict_eligible':sum(bool(r.get('eligible')) for r in rows),
                      'average_operational_kB':round(sum(r['operational_http_bytes'] for r in rows)/len(rows)/1000,4),
                      'average_dense_nmae':round(sum(r['nmae_dense_0p5s'] for r in rows)/len(rows),7),
                      'average_predicted_feasible_fraction':round(sum(r['feasible_fraction'] for r in rows)/len(rows),5)})
    summary={'campaign_id':audit['campaign_id'],
             'input_profile':audit['profile'],'counts':audit['summary'],
             'tier_status_counts':dict(statuses),'exclusion_reason_run_counts':dict(excluded),
             'scope_notes':[
               'Each synthetic source scenario is paired across policies but not repeated under independent host conditions.',
               'Tiers mean both policies pass the SAME ceiling, not equal realized NMAE.',
               'Constraint flags are from predicted model checks, especially observed latency proxy, not measured hard guarantees.',
               'Only application POST-body bytes for state plus heartbeats are counted as operational traffic.',
               'Scenarios 2 and 3 are different synthetic dynamics, not ordinary statistical replicates of Scenario 1.',
               'All policy choices/weights and thresholds are unchanged; this script is a diagnostic only.'
             ]}
    return summary, audit, violations, stats


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('campaign',type=Path)
    p.add_argument('--out',type=Path,default=Path('auditoria_fase11_diagnostico'))
    args=p.parse_args()
    summary,audit,violations,stats=build_diagnostics(args.campaign)
    args.out.mkdir(parents=True,exist_ok=True)
    write_csv(args.out/'runs_v11.csv',audit['runs'])
    write_csv(args.out/'fidelity_tiers_v11.csv',audit['tier_matches'])
    write_csv(args.out/'violations_v11.csv',violations)
    write_csv(args.out/'variant_summary_v11.csv',stats)
    (args.out/'audit_v11.json').write_text(json.dumps(audit,indent=2,ensure_ascii=False),encoding='utf-8')
    (args.out/'diagnostics_v11.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(summary,indent=2,ensure_ascii=False))

if __name__=='__main__': main()
