#!/usr/bin/env python3
"""Validate basic structural integrity of an executed IoTJ pilot campaign.

This is a data-integrity gate, not a significance test or fidelity claim.
"""
import argparse
import csv
import json
from pathlib import Path


def rows(path):
    with path.open(newline='',encoding='utf-8') as f:
        return list(csv.DictReader(f))


def check_campaign(path):
    manifest=json.loads((path/'plan.json').read_text(encoding='utf8'))
    issues=[];report=[];quality_warnings=[]
    tasks=manifest.get('tasks',[])
    if not tasks:
        issues.append('plan.json sin tareas')
    if len({task['run_id'] for task in tasks})!=len(tasks):
        issues.append('IDs de ejecución repetidos')
    for task in tasks:
        name=task['run_id']
        folder=path/'runs'/name
        statuses={'run_id':name,'mode':task['config']['mode'],'status':task.get('status')}
        if task.get('status')!='complete':
            issues.append(f'{name}: estado no completo: {task.get("status")}')
        if not folder.is_dir():
            issues.append(f'{name}: falta carpeta de ejecución'); report.append(statuses);continue
        mp=folder/'metadata.json'; sp=folder/'summary_v2.json'
        if not mp.exists() or not sp.exists():
            issues.append(f'{name}: faltan metadata.json o summary_v2.json');report.append(statuses);continue
        meta=json.loads(mp.read_text(encoding='utf8'))
        summary=json.loads(sp.read_text(encoding='utf8'))
        if meta.get('run_id')!=name or not meta.get('run_complete'):
            issues.append(f'{name}: metadata indica ejecución incompleta o ID erróneo')
        for fname in ('heartbeats.csv','updates.csv','reference.csv','decisions.csv','initial_state.csv'):
            fp=folder/fname
            if not fp.exists():
                issues.append(f'{name}: falta {fname}');continue
            if fname!='initial_state.csv':
                content=rows(fp)
                if not content:
                    issues.append(f'{name}: {fname} sin registros')
                if any(row.get('run_id')!=name for row in content):
                    issues.append(f'{name}: {fname} contiene un ID distinto')
                statuses[fname.replace('.csv','_rows')]=len(content)
        comm=summary.get('communication',{})
        fidelity=summary.get('reference_fidelity',{})
        timing=summary.get('cadence_v9') or {}
        if str(meta.get('scheduler_version','')).startswith('phase9'):
            statuses['timing_log_status']=timing.get('timing_log_status','missing')
            statuses['agent_skipped_slots']=(timing.get('agent') or {}).get('ticks_skipped')
            statuses['policy_skipped_slots']=(timing.get('policy') or {}).get('ticks_skipped')
            if statuses['timing_log_status']!='available':
                quality_warnings.append(f'{name}: registros temporales incompletos; no asumir cadencia correcta')
            for group in ('agent','policy'):
                observed=(timing.get(group) or {}).get('ticks_per_expected')
                if observed is not None and observed<.95:
                    quality_warnings.append(f'{name}: {group} ejecuta menos del 95% de ciclos previstos')
        statuses.update({'operational_http_bytes':comm.get('operational_http_payload_bytes'),
                         'nmae':fidelity.get('nmae'),
                         'reference_coverage':fidelity.get('reference_cadence_coverage_fraction'),
                         'decision_count':summary.get('controller',{}).get('decision_count')})
        if not statuses.get('decision_count'):
            issues.append(f'{name}: ninguna decisión registrada')
        if fidelity.get('nmae') is None:
            issues.append(f'{name}: NMAE no evaluable (inspeccionar cobertura/estado inicial)')
        report.append(statuses)
    return {'campaign_id':manifest.get('campaign_id'),'integrity_ok':not issues,
            'issues':issues,'quality_warnings':quality_warnings,'runs':report,
            'disclaimer':'Validación estructural, no resultados comparativos ni tolerancias de calidad.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('campaign',type=Path)
    args=p.parse_args()
    result=check_campaign(args.campaign)
    output=args.campaign/'pilot_integrity_v4.json'
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8')
    print(json.dumps(result,indent=2,ensure_ascii=False))
    if not result['integrity_ok']:
        raise SystemExit(2)

if __name__=='__main__':main()
