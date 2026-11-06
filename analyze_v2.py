#!/usr/bin/env python3
"""Independent post-hoc evaluation of run-isolated HTTP synchronizations.

Distinguishes received application payload from offered/wire bytes, reference
coverage from fidelity, and a fully known SYNTHETIC process from measured state.
The synthetic oracle is never accessed by the online policy.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from synthetic_process import physical_state
from research.cadence_v9 import analyze_run as analyze_cadence_v9

RANGES = {'temperature_c':40., 'load_fraction':1., 'cooling_fraction':1.}


def read_csv(path, dates=()):
    path=Path(path)
    if not path.exists():
        return pd.DataFrame()
    try:
        df=pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    for col in dates:
        if col in df:
            df[col]=pd.to_datetime(df[col],utc=True)
    return df


def truth_errors(truth, synced):
    """Return per-reference metrics with an as-of join of the latest accepted state.

    A missing synchronized value is unknown, NOT zero error.
    Available state is matched by server receipt timestamp; never retroactive.
    """
    records=[]
    if truth.empty or synced.empty:
        return records
    for node_id, observations in truth.groupby('node_id'):
        last = synced[synced.node_id == node_id]
        if last.empty:
            continue
        # Evaluate the replica as of the reference ACQUISITION timestamp.
        # A synchronized update may only contribute if the middleware had
        # already received it then; matching on sample_time alone leaks future
        # deliveries into the reconstructed historical DT state.
        # Requires synchronized clocks (agents share their Docker host here).
        left = observations[['sample_time', 'state']].copy()
        left['evaluation_time'] = observations['sample_time']
        left = left.rename(columns={'state': 'ref_state'}).sort_values('evaluation_time')
        right = last[['sample_time', 'state']].copy()
        right['received_time'] = last['ts'] if 'ts' in last else last['sample_time']
        right = right.rename(columns={'sample_time':'last_sync', 'state':'dt_state'})
        right = right.sort_values('received_time')
        # A state sent earlier but RECEIVED later cannot be used retroactively.
        merged = pd.merge_asof(left, right, left_on='evaluation_time',
                               right_on='received_time', direction='backward')
        for _,row in merged.iterrows():
            if not isinstance(row['dt_state'],str):
                continue
            try:
                true=json.loads(row['ref_state'])
                twin=json.loads(row['dt_state'])
                differences={k:abs(true[k]-twin[k])/scale for k,scale in RANGES.items()}
                age = (row['evaluation_time'] - row['last_sync']).total_seconds()
                if age < 0:  # Uncoordinated clocks or out-of-order source timestamps.
                    continue
                if not all(np.isfinite(v) for v in differences.values()):
                    continue
            except (ValueError,TypeError,KeyError):
                continue
            records.append({'node_id':node_id,'sample_time':row['sample_time'],
                            'aoi_s':age,
                            'error_norm':float(np.mean(list(differences.values()))),
                            'variables':differences})
    return records


def summarize_reference(truth,synced, metadata=None):
    records=truth_errors(truth,synced)
    result={'n_reference_rows':int(len(truth)),'matched_rows':len(records),
            'nmae':None,'aoi_p95_s':None,'aoi_max_s':None,'nmae_p95':None,
            'nmae_by_variable':{},'unmatched_reference_fraction':None,
            'reference_cadence_coverage_fraction':None,'node_coverage':{}}
    if records:
        result['nmae']=float(np.mean([r['error_norm'] for r in records]))
        result['nmae_p95']=float(np.percentile([r['error_norm'] for r in records],95))
        result['aoi_p95_s']=float(np.percentile([r['aoi_s'] for r in records],95))
        result['aoi_max_s']=float(max(r['aoi_s'] for r in records))
        result['nmae_by_variable']={k:float(np.mean([r['variables'][k] for r in records])) for k in RANGES}
    if len(truth):
        result['unmatched_reference_fraction']=float(1.-len(records)/len(truth))
        for node_id,g in truth.groupby('node_id'):
            matched=sum(r['node_id']==node_id for r in records)
            result['node_coverage'][str(node_id)]={'references':int(len(g)),
                         'matched':int(matched),'matched_fraction':float(matched/len(g))}
    if metadata and metadata.get('n') and metadata.get('start_utc') and metadata.get('end_utc'):
        secs=(pd.Timestamp(metadata['end_utc'])-pd.Timestamp(metadata['start_utc'])).total_seconds()
        if secs>0:
            expected=metadata['n']*secs/2.
            # Discrete 2-s sampling and wall-clock jitter can yield a value
            # slightly above 1; report both the raw ratio and bounded coverage.
            ratio=float(len(truth)/expected)
            result['reference_cadence_ratio_uncapped']=ratio
            result['reference_cadence_coverage_fraction']=min(1.,ratio)
            result['expected_reference_count_approx']=expected
    return result


def synthetic_oracle_dataframe(metadata, step_s=2.):
    """Generate offline simulation-only ground truth THROUGH container outages.

    The online worker has no import of this function. A sensor outage cannot be
    reconstructed this way in a real deployment; no empirical claim follows.
    """
    if not (metadata.get('physical_epoch_unix_s') is not None and
            metadata.get('start_utc') and metadata.get('end_utc') and metadata.get('node_ids')):
        return pd.DataFrame()
    start=pd.Timestamp(metadata['start_utc'])
    end=pd.Timestamp(metadata['end_utc'])
    if end <= start or step_s<=0:
        return pd.DataFrame()
    instants=pd.date_range(start,end,freq=pd.Timedelta(seconds=step_s),inclusive='left')
    if len(instants)*len(metadata['node_ids']) > 3_000_000:
        raise ValueError('Refusing to construct an unbounded synthetic oracle')
    rows=[]
    for node_id in metadata['node_ids']:
        for instant in instants:
            elapsed=instant.timestamp()-float(metadata['physical_epoch_unix_s'])
            rows.append({'node_id':node_id,'sample_time':instant,
                         'state':json.dumps(physical_state(node_id,elapsed,
                             int(metadata.get('synthetic_scenario_id',0))))})
    return pd.DataFrame(rows)


def bool_col(series):
    return series.astype(str).str.lower().isin(['true','t','1'])


def evaluate_run(folder):
    folder=Path(folder)
    meta_path=folder/'metadata.json'
    meta=json.loads(meta_path.read_text(encoding='utf8')) if meta_path.exists() else {}
    updates=read_csv(folder/'updates.csv',('ts','sample_time'))
    init=read_csv(folder/'initial_state.csv',('ts','sample_time'))
    ref=read_csv(folder/'reference.csv',('ts','sample_time'))
    hb=read_csv(folder/'heartbeats.csv',('ts',))
    dec=read_csv(folder/'decisions.csv',('ts',))

    # Bytes counted only IN the measurement window, not from the initialization
    # snapshot, although the snapshot is needed for correct initial fidelity.
    comm={}
    for key,frame,col in [('operational_state',updates,'payload_bytes'),
                          ('monitoring_heartbeat',hb,'heartbeat_bytes'),
                          ('evaluation_reference',ref,'payload_bytes')]:
        comm[key+'_bytes']=int(frame[col].fillna(0).sum()) if col in frame else 0
    comm['operational_http_payload_bytes']=(comm['operational_state_bytes']+
                                             comm['monitoring_heartbeat_bytes'])
    comm['total_recorded_http_payload_bytes']=comm['operational_http_payload_bytes']+comm['evaluation_reference_bytes']
    comm['state_attempts_observed_by_server']=int(len(updates))
    comm['state_rejected_or_duplicate']=int((~bool_col(updates.accepted)).sum()) if 'accepted' in updates else 0
    comm['excludes']=['HTTP headers','TCP/IP','MQTT policy and ACK bytes','failed HTTP requests']
    if meta.get('start_utc') and meta.get('end_utc'):
        seconds=(pd.Timestamp(meta['end_utc'])-pd.Timestamp(meta['start_utc'])).total_seconds()
        if seconds>0:
            comm['state_attempts_received_per_s']=float(len(updates)/seconds)
            comm['state_accepted_per_s']=(float(bool_col(updates.accepted).sum()/seconds)
                                          if 'accepted' in updates else None)
            comm['operational_payload_Bps']=float(comm['operational_http_payload_bytes']/seconds)
            comm['total_payload_Bps']=float(comm['total_recorded_http_payload_bytes']/seconds)

    all_states=pd.concat([x for x in [init,updates] if not x.empty],ignore_index=True) if len(init) or len(updates) else pd.DataFrame()
    if 'accepted' in all_states:
        synced=all_states[bool_col(all_states.accepted)].copy()
    else:
        synced=pd.DataFrame()
    if not synced.empty:
        synced=synced.sort_values(['node_id','sample_time']).drop_duplicates(
            ['node_id','sample_time'],keep='last')
    measured=summarize_reference(ref,synced,meta)
    oracle=synthetic_oracle_dataframe(meta)
    simulated=summarize_reference(oracle,synced) if not oracle.empty else None
    if simulated is not None:
        simulated['semantics']='OFFLINE SYNTHETIC PROCESS ORACLE (not observed sensor fidelity); 2 s grid'
        simulated['covers_node_failures']=True
    # Denser simulation-only evaluation breaks the accidental alignment between
    # B0's 2-s reports and evaluation references also sampled every 2 s.
    # This is an offline counterfactual using a fully specified synthetic process:
    # it is NEVER read by the deployed controller and is not an observed sensor.
    oracle_dense=synthetic_oracle_dataframe(meta,step_s=0.5)
    dense=summarize_reference(oracle_dense,synced) if not oracle_dense.empty else None
    if dense is not None:
        dense['semantics']='OFFLINE SYNTHETIC PROCESS ORACLE (not measured physical fidelity); 0.5 s grid'
        dense['covers_node_failures']=True
        dense['grid_step_s']=0.5
    metrics={'decision_count':int(len(dec)),
             'policy_runtime_p95_ms':float(dec.runtime_ms.quantile(.95)) if 'runtime_ms' in dec and len(dec) else None,
             'feasible_fraction':float(bool_col(dec.feasible).mean()) if 'feasible' in dec and len(dec) else None,
             'interval_distribution_s':{},'modes_observed':[],
             'mean_selected_per_decision':float(dec.selected_count.mean()) if 'selected_count' in dec and len(dec) else None,
             'selected_count_distribution':{},'violation_counts':{},
             'node_selection_counts':{}, 'decision_horizons_s':[]}
    if 'details' in dec and len(dec):
        from collections import Counter
        intervals=[]
        selected_counts=Counter()
        violations=Counter()
        nodes_selected=Counter()
        horizons=set()
        for raw in dec.details:
            try:
                obj=json.loads(raw) if isinstance(raw,str) else raw
                intervals.append(float(obj['interval_s']))
                selected=obj.get('selected',[])
                selected_counts[len(selected)]+=1
                nodes_selected.update(selected)
                violations.update(obj.get('violations',[]))
                if 'decision_horizon_s' in obj:
                    horizons.add(float(obj['decision_horizon_s']))
            except (ValueError,KeyError,TypeError):
                continue
        if intervals:
            metrics['interval_distribution_s']={str(v):int(intervals.count(v)) for v in sorted(set(intervals))}
        metrics['selected_count_distribution']={str(k):v for k,v in sorted(selected_counts.items())}
        metrics['violation_counts']=dict(sorted(violations.items()))
        metrics['node_selection_counts']=dict(sorted(nodes_selected.items()))
        metrics['decision_horizons_s']=sorted(horizons)
    if 'mode' in dec:
        metrics['modes_observed']=sorted(dec['mode'].dropna().unique().tolist())
    cadence=analyze_cadence_v9(folder) if (folder/'scheduler_logs').is_dir() and meta.get('end_utc') else None
    return {'cadence_v9':cadence, 'metadata':{'run_id':meta.get('run_id'),'mode':meta.get('mode'),
                        'n':meta.get('n'),'run_complete':meta.get('run_complete')},
            'communication':comm,'reference_fidelity':measured,
            'synthetic_oracle_fidelity':simulated,'dense_synthetic_oracle_fidelity':dense,
            'controller':metrics,
            'warning':'Synthetic-process oracle is a simulation calculation only; '+
                      'HTTP payload rates are not wire throughput, and data alone do not validate host/broker saturation.'}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('folder',type=Path)
    args=ap.parse_args()
    result=evaluate_run(args.folder)
    (args.folder/'summary_v2.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    main()
