#!/usr/bin/env python3
"""Phase-9 timing audit from per-run exported logs, without changing online policy.

No clock offsets between containers are inferred. Scheduler lateness is
computed within each process from a monotonic clock, and persisted database
rows use UTC server receipt time. Log-absence is UNKNOWN, not zero delay.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

AGENT_MARKER = 'HDT_AGENT_TIMING '
POLICY_MARKER = 'HDT_POLICY_TIMING '
UPDATE_MARKER = 'HDT_UPDATE_ATTEMPT '


def parse_events(text, marker, run_id):
    events = []
    invalid = 0
    for line in text.splitlines():
        if marker not in line:
            continue
        try:
            event = json.loads(line.split(marker, 1)[1].strip())
            if event.get('run_id') == run_id:
                events.append(event)
        except (ValueError, TypeError, AttributeError):
            invalid += 1
    return events, invalid


def percentile(values, p):
    values = sorted(x for x in values if isinstance(x, (int, float)) and math.isfinite(x))
    if not values:
        return None
    pos = (len(values)-1)*p/100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return float(values[lo] + (values[hi]-values[lo])*(pos-lo))


def describe(values):
    vals = [float(x) for x in values if isinstance(x, (int, float)) and math.isfinite(x)]
    return {'n':len(vals),'mean':statistics.mean(vals) if vals else None,
            'p95':percentile(vals,95),'max':max(vals) if vals else None}


def _utc(raw):
    # PostgreSQL timestamps use +00 / +00:00 or Z depending on serializer.
    s = str(raw).strip().replace('Z', '+00:00')
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t=t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def receipt_intervals(csv_path, group_col='node_id'):
    """Actual server receipt spacing; not a scheduler-lag estimate."""
    by_node=defaultdict(list)
    if not csv_path.exists():
        return {'rows':None,'interval_s':describe([]),'node_counts':{}}
    with csv_path.open(newline='',encoding='utf-8-sig') as file:
        reader=csv.DictReader(file)
        if not reader.fieldnames or 'ts' not in reader.fieldnames:
            return {'rows':0,'interval_s':describe([]),'node_counts':{}}
        for row in reader:
            if row.get(group_col) and row.get('ts'):
                by_node[row[group_col]].append(_utc(row['ts']))
    gaps=[]
    for times in by_node.values():
        times.sort()
        gaps.extend([(b-a).total_seconds() for a,b in zip(times,times[1:])])
    return {'rows':sum(len(v) for v in by_node.values()),
            'interval_s':describe(gaps),
            'node_counts':{k:len(v) for k,v in sorted(by_node.items())}}


def analyze_run(run_dir):
    folder = Path(run_dir)
    meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    run_id=meta['run_id']
    start=_utc(meta['start_utc'])
    end=_utc(meta['end_utc'])
    duration=max(0.0,(end-start).total_seconds())
    log_dir=folder/'scheduler_logs'
    agent_events=[]
    update_events=[]
    parse_errors=0
    agent_missing=[]
    for node_id in meta.get('node_ids',[]):
        service='edge_'+node_id.rsplit('-',1)[-1]
        path=log_dir/(service+'.log')
        if not path.is_file():
            agent_missing.append(service)
            continue
        parsed,invalid=parse_events(path.read_text(encoding='utf-8',errors='replace'),AGENT_MARKER,run_id)
        agent_events.extend(parsed)
        updates,update_invalid=parse_events(path.read_text(encoding='utf-8',errors='replace'),UPDATE_MARKER,run_id)
        update_events.extend(updates)
        invalid+=update_invalid
        parse_errors+=invalid
        if not parsed:
            agent_missing.append(service)
    policy_path=log_dir/'policy.log'
    policy_events=[]
    if policy_path.is_file():
        policy_events,invalid=parse_events(policy_path.read_text(encoding='utf-8',errors='replace'),POLICY_MARKER,run_id)
        parse_errors+=invalid
    capture_path=log_dir/'capture_status.json'
    capture_status=json.loads(capture_path.read_text(encoding='utf-8')) if capture_path.is_file() else None
    # Do not interpret zero events as success: old campaigns lack timing logs.
    available=bool(policy_events) and bool(agent_events) and not agent_missing
    failed_ref=sum(not r.get('reference_ok',False) for r in agent_events)
    failed_hb=sum(not r.get('heartbeat_ok',False) for r in agent_events)
    skipped_agent=sum(int(e.get('slots_skipped_after_tick',0)) for e in agent_events)
    skipped_policy=sum(int(e.get('slots_skipped_after_tick',0)) for e in policy_events)
    expected_agent_ticks=duration/2.0*int(meta.get('n',0))
    policy_period=float(meta.get('policy_env_overrides',{}).get('CONTROL_PERIOD_S') or 2.0)
    expected_policy_ticks=duration/policy_period
    result={
        'schema':'hdt-cadence-v9', 'run_id':run_id,'timing_log_status':'available' if available else 'missing_or_partial',
        'duration_s':duration,
        'agent':{'tick_count':len(agent_events),'expected_ticks_approx':expected_agent_ticks,
                 'ticks_per_expected':len(agent_events)/expected_agent_ticks if expected_agent_ticks else None,
                 'missing_log_services':agent_missing,
                 'schedule_lag_ms':describe([r.get('schedule_lag_ms') for r in agent_events]),
                 'request_reference_ms':describe([r.get('reference_http_ms') for r in agent_events]),
                 'request_heartbeat_ms':describe([r.get('heartbeat_http_ms') for r in agent_events]),
                 'ticks_skipped':skipped_agent, 'reference_http_failures':failed_ref,
                 'heartbeat_http_failures':failed_hb,
                 'reference_bodies_attempted':sum(int(r.get('reference_body_bytes_attempted',0)) for r in agent_events),
                 'heartbeat_bodies_attempted':sum(int(r.get('heartbeat_body_bytes_attempted',0)) for r in agent_events),
                 'state_attempt_count_logged':len(update_events),
                 'state_application_nacks_logged':sum(not x.get('application_ok',False) for x in update_events),
                 'state_bodies_attempted_bytes':sum(int(x.get('body_bytes_attempted',0)) for x in update_events),
                 'state_http_ms':describe([x.get('request_wall_ms') for x in update_events])},
        'policy':{'tick_count':len(policy_events),'expected_ticks_approx':expected_policy_ticks,
                  'ticks_per_expected':len(policy_events)/expected_policy_ticks if expected_policy_ticks else None,
                  'schedule_lag_ms':describe([r.get('schedule_lag_ms') for r in policy_events]),
                  'cycle_wall_ms':describe([r.get('cycle_wall_ms') for r in policy_events]),
                  'ticks_skipped':skipped_policy,
                  'cycles_failed':sum(bool(r.get('cycle_failed')) for r in policy_events)},
        'database_receipts':{
            'reference':receipt_intervals(folder/'reference.csv'),
            'heartbeat':receipt_intervals(folder/'heartbeats.csv'),
            'decisions':receipt_intervals(folder/'decisions.csv',group_col='run_id'),
        },
        'log_capture':capture_status,
        'parse_errors':parse_errors,
        'interpretation': 'Application timing only. Missing server rows can be caused by delayed scheduling, '+
            'HTTP failure, database ingestion, container startup or export windows. '+
            'No queue-depth or wire-byte inference is implied.'
    }
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run_dir',type=Path)
    p.add_argument('--out',type=Path)
    a=p.parse_args()
    result=analyze_run(a.run_dir)
    dst=a.out or a.run_dir/'cadence_v9.json'
    dst.parent.mkdir(parents=True,exist_ok=True)
    dst.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8')
    print(json.dumps({'run_id':result['run_id'],'timing_log_status':result['timing_log_status'],
        'agent_ticks':result['agent']['tick_count'],'policy_ticks':result['policy']['tick_count'],
        'agent_slots_skipped':result['agent']['ticks_skipped'],
        'policy_slots_skipped':result['policy']['ticks_skipped']},indent=2))


if __name__=='__main__':
    main()
