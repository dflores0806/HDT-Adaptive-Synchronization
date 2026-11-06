#!/usr/bin/env python3
"""Run-isolated, stable-identity Docker Compose campaign (requires Docker).

Outputs are application-level measurements, NOT end-to-end wire-byte or
physical-energy measurements. Does not remove database volumes.
"""
import argparse
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
TABLES = {
    'heartbeats': 'node_heartbeats_v2',
    'updates': 'state_updates_v2',
    'reference': 'reference_samples_v2',
    'decisions': 'decision_trace_v2',
}


def docker(*args, input=None, override=None):
    files = ['-p', os.getenv('HDT_COMPOSE_PROJECT', 'hdt_iotj26'),
             '-f', str(ROOT / 'docker-compose.yml')]
    if override is not None:
        files += ['-f', str(override)]
    return subprocess.run(['docker', 'compose', *files, *args], cwd=ROOT,
                          input=input, text=True, capture_output=True, check=True)


def now_utc():
    return datetime.now(timezone.utc)


def build_node_override(n, physical_epoch, run_id="dev", scenario_id=0,
                        impairment=None):
    """Return valid minimal Compose YAML; IDs and physical phase stable across modes.

    Base image is built from the `edge` service; each per-node service uses it.
    Generated config is retained as experiment provenance.
    """
    if not 1 <= n <= 128:
        raise ValueError('n must be between 1 and 128')
    if not 0 <= scenario_id <= 99:
        raise ValueError('scenario_id must be in 0..99')
    impairment = impairment or {}
    profile = impairment.get('profile', 'none')
    if profile not in ('none','delay','delay_loss'):
        raise ValueError('Invalid impairment profile')
    services = [f'edge_{i:03d}' for i in range(n)]
    lines = ['services:']
    for i, service in enumerate(services):
        lines += [f'  {service}:', '    image: hdt-edge:revision',
                  '    environment:', f'      NODE_ID: node-{i:03d}',
                  f'      PHYSICAL_EPOCH: "{physical_epoch:.6f}"',
                  f'      RUN_ID: "{run_id}"',
                  f'      SYNTH_SCENARIO_ID: "{scenario_id}"',
                  f'      HDT_IMPAIR_PROFILE: "{profile}"',
                  f'      HDT_IMPAIR_START_S: "{float(impairment.get("start_s", 65.)):.3f}"',
                  f'      HDT_IMPAIR_DURATION_S: "{float(impairment.get("duration_s", 35.)):.3f}"',
                  f'      HDT_IMPAIR_DELAY_MS: "{int(impairment.get("delay_ms", 0))}"',
                  f'      HDT_IMPAIR_DROP_EVERY: "{int(impairment.get("drop_every", 0))}"',
                  '      API_URL: http://middleware:8000',
                  '      MQTT_BROKER: mosquitto', '      MQTT_PORT: "1883"',
                  '    depends_on:', '      middleware:',
                  '        condition: service_healthy',
                  '      mosquitto:', '        condition: service_started',
                  '    restart: "no"']
    return '\n'.join(lines) + '\n', services


def export_csv(table, run_id, start, end, path, override=None):
    if table not in TABLES.values() or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
        raise ValueError('Unrecognized table or run identifier')
    sql = (f"COPY (SELECT * FROM {table} WHERE run_id='{run_id}' "
           f"AND ts>='{start}' AND ts<'{end}' ORDER BY ts,id) TO STDOUT WITH CSV HEADER;")
    output = docker('exec', '-T', 'postgres', 'psql', '-U', 'hdt', '-d', 'hdt',
                    '-X', '-q', '-v', 'ON_ERROR_STOP=1', input=sql+'\n', override=override)
    path.write_text(output.stdout, encoding='utf-8')


def export_initial_state(run_id, start, path, override=None):
    """Snapshot before measurement: fidelity uses it, traffic window excludes it."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
        raise ValueError('Invalid run ID')
    sql = (f"COPY (SELECT DISTINCT ON (node_id) * FROM state_updates_v2 "
           f"WHERE run_id='{run_id}' AND accepted=true AND ts<'{start}' "
           f"ORDER BY node_id,sample_time DESC,id DESC) TO STDOUT WITH CSV HEADER;")
    output = docker('exec', '-T', 'postgres', 'psql', '-U', 'hdt', '-d', 'hdt',
                    '-X','-q','-v','ON_ERROR_STOP=1',input=sql+'\n',override=override)
    path.write_text(output.stdout,encoding='utf-8')



def monitor_containers(services, override, stop_event, output, cadence_s=10.):
    """Collect Docker CPU/memory/network counter snapshots during measurement.

    Not broker queue depth, not proof of offered network frames, and not a
    substitute for separate tcpdump or device-level power metering.
    """
    try:
        ids=docker('ps','-q',*services,override=override).stdout.split()
    except Exception as exc:
        output.append({'error':'docker_ps_failed','detail':str(exc)})
        return
    if not ids:
        output.append({'error':'no_containers_to_monitor'})
        return
    while not stop_event.is_set():
        stamp=now_utc().isoformat()
        try:
            out=subprocess.run(['docker','stats','--no-stream','--format','{{json .}}',*ids],
                               capture_output=True,text=True,check=True,timeout=8)
            for line in out.stdout.splitlines():
                try:
                    output.append({'ts_utc':stamp,'docker_stats':json.loads(line)})
                except json.JSONDecodeError:
                    output.append({'ts_utc':stamp,'parse_error':line[:200]})
        except Exception as exc:
            output.append({'ts_utc':stamp,'error':'docker_stats_failed','detail':str(exc)})
        # Bounded HTTP concurrency instrumentation is separate from Docker
        # stats and MUST NOT be described as queue occupancy.
        try:
            base=os.getenv('HDT_API_BASE','http://127.0.0.1:18000').rstrip('/')
            snap=requests.get(base+'/research/ingress-metrics',timeout=2)
            snap.raise_for_status()
            output.append({'ts_utc':stamp,'http_ingress_metrics':snap.json()})
        except Exception as exc:
            output.append({'ts_utc':stamp,'http_ingress_metrics_error':str(exc)[:250]})
        stop_event.wait(cadence_s)



def export_scheduler_logs(services, start, end, output, override=None):
    """Collect policy/agent logs after measurement; non-destructive, per-run.

    Container log lines may have a Compose prefix. The analysis parser looks
    for an explicit HDT_*_TIMING marker and validates the run identifier.
    """
    output.mkdir(parents=True, exist_ok=True)
    problems=[]
    for service in services:
        try:
            result=docker('logs', '--no-color', '--since',start.isoformat(),
                          '--until',end.isoformat(),service, override=override)
            (output/(service+'.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        except Exception as exc:
            problems.append({'service':service,'error':repr(exc)})
    (output/'capture_status.json').write_text(json.dumps({
        'services_requested':services,'failures':problems,
        'window_start':start.isoformat(),'window_end':end.isoformat()
    },indent=2),encoding='utf-8')
    return problems


def run_experiment(args):
    if not (1 <= args.n <= 128) or args.warmup < 0 or args.measure <= 0:
        raise ValueError('Require 1<=n<=128, warmup>=0, measure>0')
    if not 0 <= args.scenario_id <= 99:
        raise ValueError('scenario_id must be in 0..99')
    if not (0. <= args.churn_fraction < 1.):
        raise ValueError('Require 0 <= churn_fraction < 1')
    if args.impair_profile not in ('none','delay','delay_loss'):
        raise ValueError('Unknown impairment profile')
    if not 0 <= args.impair_delay_ms <= 1500 or not 0 <= args.impair_drop_every <= 100:
        raise ValueError('Invalid impairment parameters')
    if args.impair_profile == 'delay_loss' and args.impair_drop_every < 2:
        raise ValueError('delay_loss requires drop_every >= 2')
    if args.impair_profile != 'none' and not (args.impair_duration_s > 0 and
            args.impair_start_s >= args.warmup and
            args.impair_start_s + args.impair_duration_s < args.warmup + args.measure):
        raise ValueError('Injection window must fit inside planned warmup + measurement')
    if args.churn_fraction and (args.churn_at_s < 0 or args.churn_at_s + args.churn_down_s >= args.measure):
        raise ValueError('Churn schedule must fit into measurement window')
    run_id = args.run_id or f'{now_utc():%Y%m%dT%H%M%S}_{args.mode}_{args.n}_{uuid.uuid4().hex[:6]}'
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
        raise ValueError('Invalid run ID')
    out = (ROOT / args.results_dir / run_id)
    out.mkdir(parents=True, exist_ok=False)
    physical_epoch = None  # choose after Docker build, just before agent launch
    nodes = [f'edge_{i:03d}' for i in range(args.n)]
    override = out / 'nodes.compose.yml'
    events = []
    t_start = None
    t_end = None
    agents_launched = False
    error = None
    monitoring_stop = threading.Event()
    monitoring_thread = None
    resource_snapshots = []
    try:
        docker('up','-d','--build','postgres','redis','mosquitto','middleware','policy')
        # An explicit image keeps deterministic per-node services identical.
        docker('build','edge')
        # Prevent the unnumbered legacy edge service from interfering.
        docker('stop','edge')
        docker('rm','-f','edge')
        for attempt in range(30):
            try:
                r = requests.post(os.getenv('HDT_API_BASE', 'http://127.0.0.1:18000').rstrip('/') + '/admin/experiment', json={
                    'run_id':run_id,'mode':args.mode,'fixed_interval_s':args.fixed_interval,
                    'aoi_threshold_s':args.aoi_threshold},timeout=5)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 29:
                    raise
                time.sleep(2)
        # Source time origin must not include variable image-build latency.
        physical_epoch = time.time()
        text, nodes = build_node_override(args.n, physical_epoch, run_id, args.scenario_id,
            {'profile': args.impair_profile, 'start_s': args.impair_start_s,
             'duration_s': args.impair_duration_s,
             'delay_ms': args.impair_delay_ms, 'drop_every': args.impair_drop_every})
        override.write_text(text,encoding='utf8')
        docker('up','-d','--no-deps',*nodes,override=override)
        agents_launched = True
        print('Warmup:',run_id,flush=True)
        time.sleep(args.warmup)
        t_start = now_utc()
        monitoring_thread=threading.Thread(target=monitor_containers,
                 args=(['middleware','policy','mosquitto','postgres',*nodes],
                       override,monitoring_stop,resource_snapshots),daemon=True)
        monitoring_thread.start()
        if args.churn_fraction:
            time.sleep(args.churn_at_s)
            failed = nodes[:max(1,round(args.n*args.churn_fraction))]
            event_start = now_utc()
            docker('stop',*failed,override=override)
            events.append({'event':'failure_complete','ts_utc':now_utc().isoformat(),
                           'command_started_utc':event_start.isoformat(),
                           'node_ids':[f'node-{i:03d}' for i in range(len(failed))]})
            time.sleep(args.churn_down_s)
            event_start = now_utc()
            docker('start',*failed,override=override)
            events.append({'event':'recovery_restart_complete','ts_utc':now_utc().isoformat(),
                           'command_started_utc':event_start.isoformat(),
                           'node_ids':[f'node-{i:03d}' for i in range(len(failed))]})
        remaining = args.measure - (now_utc() - t_start).total_seconds()
        if remaining > 0:
            time.sleep(remaining)
        t_end = now_utc()
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        monitoring_stop.set()
        if monitoring_thread is not None:
            monitoring_thread.join(timeout=10.)
        (out/'docker_resource_samples.json').write_text(json.dumps(resource_snapshots,indent=2),encoding='utf8')
        if agents_launched and t_start is not None and t_end is not None:
            log_problems=export_scheduler_logs(['policy','middleware','postgres',*nodes], t_start, t_end,
                                               out/'scheduler_logs', override)
            if log_problems:
                events.append({'event':'timing_log_export_warning','detail':log_problems})
        if agents_launched:
            try:
                docker('stop',*nodes,override=override)
            except Exception as exc:
                events.append({'event':'cleanup_failed','detail':repr(exc)})
        exports_ok = False
        if t_start is not None and t_end is not None:
            try:
                export_initial_state(run_id,t_start.isoformat(),out/'initial_state.csv',override)
                for name,tbl in TABLES.items():
                    export_csv(tbl,run_id,t_start.isoformat(),t_end.isoformat(),out/f'{name}.csv',override)
                exports_ok = all((out / (name + '.csv')).is_file() for name in TABLES) and (out/'initial_state.csv').is_file()
            except Exception as exc:
                events.append({'event':'export_failed','detail':repr(exc)})
                if error is None:
                    error = 'export_failed: ' + repr(exc)
        policy_env_names=('ACTION_GRID_S','AOI_MAX_S','ERROR_MAX_NORM',
                          'COMM_BUDGET_BPS','ENERGY_BUDGET_JPS','LATENCY_MAX_MS',
                          'CONTROL_PERIOD_S','W_COMM','W_ENERGY','W_ERROR','W_AOI')
        effective_policy_env={key: os.getenv(key, '') for key in policy_env_names}
        meta = vars(args) | {'policy_env_overrides':effective_policy_env,
                'run_id':run_id, 'start_utc':t_start.isoformat() if t_start else None,
               'end_utc':t_end.isoformat() if t_end else None,
               'physical_epoch_unix_s':physical_epoch,
               'synthetic_scenario_id':args.scenario_id,
               'source_model_version':'phase11-deterministic-scenarios; 0 retains legacy exact function',
               'transport_perturbation': {
                 'layer': 'agent-side HTTP application requests, not IP packets',
                 'profile': args.impair_profile,
                 'start_offset_from_physical_epoch_s': args.impair_start_s,
                 'duration_s': args.impair_duration_s,
                 'delay_ms': args.impair_delay_ms,
                 'drop_every': args.impair_drop_every,
                 'start_utc': datetime.fromtimestamp(physical_epoch+args.impair_start_s,timezone.utc).isoformat(),
                 'end_utc': datetime.fromtimestamp(physical_epoch+args.impair_start_s+args.impair_duration_s,timezone.utc).isoformat(),
                 'references_unimpaired': True,
               },
               'node_ids':[f'node-{i:03d}' for i in range(args.n)],
               'timestamp_semantics':'PostgreSQL server receipt UTC',
               'reference_traffic':'evaluation-only, included in total recorded application bytes',
               'scheduler_version':'phase9-monotonic-fixed-epoch; missed slots logged, no catch-up bursts',
               'scheduler_logs':'scheduler_logs/ (best-effort; timing logs do not count as network payload)',
               'application_byte_scope':'HTTP POST bodies, not TCP/IP/MQTT overhead',
               'run_complete':t_end is not None and error is None and exports_ok,
               'failure':error}
        (out/'metadata.json').write_text(json.dumps(meta,indent=2),encoding='utf8')
        (out/'churn_events.json').write_text(json.dumps(events,indent=2),encoding='utf8')
    if error is not None:
        raise RuntimeError(error)
    print('Exported:',out)
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['B0','B1','B1_guard','A1','A1_interval','A1_selection'],required=True)
    p.add_argument('--n',type=int,default=8)
    p.add_argument('--scenario-id',type=int,default=0)
    p.add_argument('--warmup',type=int,default=60)
    p.add_argument('--measure',type=int,default=180)
    p.add_argument('--fixed-interval',type=float,default=2.)
    p.add_argument('--aoi-threshold',type=float,default=6.)
    p.add_argument('--run-id')
    p.add_argument('--churn-fraction',type=float,default=0.)
    p.add_argument('--churn-at-s',type=int,default=30)
    p.add_argument('--churn-down-s',type=int,default=30)
    p.add_argument('--impair-profile',choices=['none','delay','delay_loss'],default='none')
    p.add_argument('--impair-start-s',type=float,default=65.)
    p.add_argument('--impair-duration-s',type=float,default=35.)
    p.add_argument('--impair-delay-ms',type=int,default=0)
    p.add_argument('--impair-drop-every',type=int,default=0)
    p.add_argument('--results-dir',default='results_v2')
    args=p.parse_args()
    try:
        run_experiment(args)
    except (ValueError,RuntimeError) as ex:
        p.error(str(ex))

if __name__=='__main__':
    main()
