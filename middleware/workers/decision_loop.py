"""Action-dependent and auditable v2 policy loop.

Consumes ONLY state updates and low-rate heartbeats. Evaluation-only references
must never be read here. An unavailable node remains in the expected state and
is flagged in global feasibility, not treated as having fresh measurements.
"""
import asyncio
import logging
import json
import os
import signal
import time
from contextlib import suppress
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, desc
from ..db import engine, get_session
from ..models import Base, NodeHeartbeat, StateUpdate, ExperimentConfig, PolicyDecision, DecisionTrace
from ..services.action_controller import NodeEstimate, ControlLimits
from ..services.strategies import choose_action
from ..services.publisher import PolicyPublisher
from timing_v9 import PeriodicDeadline

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
log = logging.getLogger('hdt.v2.policy')
shutdown = asyncio.Event()

HEARTBEAT_STALE_S = float(os.getenv('HEARTBEAT_STALE_S', '7'))
CONTROL_PERIOD_S = float(os.getenv('CONTROL_PERIOD_S', '2'))
# All values are research defaults, not experimentally calibrated constants.
LIMITS = ControlLimits(
    candidate_intervals_s=tuple(float(x) for x in os.getenv('ACTION_GRID_S', '1,2,4,8').split(',')),
    aoi_max_s=float(os.getenv('AOI_MAX_S', '12')),
    decision_horizon_s=CONTROL_PERIOD_S,
    error_max_norm=float(os.getenv('ERROR_MAX_NORM', '0.08')),
    comm_budget_Bps=float(os.getenv('COMM_BUDGET_BPS', '6000')),
    energy_budget_Jps=float(os.getenv('ENERGY_BUDGET_JPS', '6.0')),
    latency_max_ms=float(os.getenv('LATENCY_MAX_MS', '120')),
    w_comm=float(os.getenv('W_COMM', '.35')),
    w_energy=float(os.getenv('W_ENERGY', '.15')),
    w_error=float(os.getenv('W_ERROR', '.35')),
    w_aoi=float(os.getenv('W_AOI', '.15')),
)


def _utc(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def build_snapshot(heartbeats, state_updates, now=None):
    """Pure function accepting newest rows per node. No reference ground truth.

    Heartbeats contain a rate proxy computed locally, not direct state vectors.
    The error proxy grows with observed state age and is NOT measured MAE.
    """
    now = now or datetime.now(timezone.utc)
    latest_h = {}
    latest_s = {}
    for h in heartbeats:
        if h.node_id not in latest_h or _utc(h.ts) > _utc(latest_h[h.node_id].ts):
            latest_h[h.node_id] = h
    for s in state_updates:
        if s.accepted and (s.node_id not in latest_s or _utc(s.sample_time) > _utc(latest_s[s.node_id].sample_time)):
            latest_s[s.node_id] = s
    nodes = []
    for node_id, h in sorted(latest_h.items()):
        last_state = latest_s.get(node_id)
        age = max(0., (now - _utc(last_state.sample_time)).total_seconds()) if last_state else 1e4
        available = (now - _utc(h.ts)).total_seconds() <= HEARTBEAT_STALE_S
        rate = float(h.drift_rate_norm_s)
        nodes.append(NodeEstimate(node_id=node_id, subsystem=h.subsystem, age_s=age,
                                  error_norm=age * rate, drift_rate_norm_per_s=rate,
                                  bytes_per_update=h.bytes_per_update,
                                  joules_per_update=0.02 + 0.001 * h.energy_w,
                                  available=available, latency_ms=h.latency_ms,
                                  monitoring_Bps=h.heartbeat_bytes/2.))
    return nodes


def read_snapshot(s, run_id):
    # Bounded by the current run, but keep stale node rows in this run so failures
    # stay visible. There is deliberately no cross-run reuse of node state.
    heartbeats = s.execute(select(NodeHeartbeat).where(NodeHeartbeat.run_id == run_id)
        .distinct(NodeHeartbeat.node_id).order_by(NodeHeartbeat.node_id, desc(NodeHeartbeat.ts), desc(NodeHeartbeat.id))).scalars().all()
    states = s.execute(select(StateUpdate).where(StateUpdate.run_id == run_id,
                       StateUpdate.accepted.is_(True))
        .distinct(StateUpdate.node_id).order_by(StateUpdate.node_id, desc(StateUpdate.sample_time), desc(StateUpdate.id))).scalars().all()
    return build_snapshot(heartbeats, states)


async def cycle(publisher, scheduling=None):
    t0 = time.perf_counter()
    with get_session() as s:
        cfg = s.get(ExperimentConfig, 1)
        if cfg is None:
            cfg = ExperimentConfig(id=1, run_id='dev', mode='A1', fixed_interval_s=2., aoi_threshold_s=6.)
            s.add(cfg)
            s.commit()
        run_id, mode, fixed_interval, aoi_threshold = cfg.run_id, cfg.mode, cfg.fixed_interval_s, cfg.aoi_threshold_s
        nodes = read_snapshot(s, run_id)
        if scheduling is not None:
            scheduling['run_id'] = run_id
    if not nodes:
        return None
    d = choose_action(nodes, LIMITS, mode, fixed_interval, aoi_threshold)
    selected = set(d.selected)
    issued_at = datetime.now(timezone.utc).isoformat()
    policies = {n.node_id: {'run_id': run_id, 'mode': mode,
                   'issued_at': issued_at, 'delta_t': float(d.interval_s),
                   'participate': n.node_id in selected and n.available}
                for n in nodes}
    publish_errors_dict = await publisher.send_many(policies)
    publish_errors = list(publish_errors_dict)
    for node_id, reason in publish_errors_dict.items():
        log.error('Policy publish failed for %s: %s', node_id, reason)
    latency_ms = (time.perf_counter() - t0) * 1000.
    metrics = {
        'run_id': run_id, 'mode': mode, 'selected': sorted(selected),
        'available': [n.node_id for n in nodes if n.available],
        'unavailable': [n.node_id for n in nodes if not n.available],
        'violations': list(d.violations), 'feasible': d.feasible,
        'search': d.search, 'projected_state_update_Bps': d.comm_Bps,
        'projected_update_Jps': d.energy_Jps,
        'predicted_max_aoi_s': d.max_aoi_s,
        'predicted_max_normalized_error': d.max_error_norm,
        'publish_errors': publish_errors,
        'runtime_ms': latency_ms,
        'scheduler': dict(scheduling or {}),
        'decision_horizon_s': LIMITS.decision_horizon_s,
        'controller_weights': {'w_comm': LIMITS.w_comm, 'w_energy': LIMITS.w_energy,
                               'w_error': LIMITS.w_error, 'w_aoi': LIMITS.w_aoi},
        'controller_limits': {'comm_budget_Bps': LIMITS.comm_budget_Bps,
                              'energy_budget_Jps': LIMITS.energy_budget_Jps,
                              'aoi_max_s': LIMITS.aoi_max_s,
                              'error_max_norm': LIMITS.error_max_norm},
        'N': len(nodes), 'interval_s': d.interval_s,
    }
    with get_session() as s:
        s.add(PolicyDecision(delta_t=d.interval_s,
            weights={'w_comm': LIMITS.w_comm, 'w_energy': LIMITS.w_energy,
                     'w_error': LIMITS.w_error, 'w_aoi': LIMITS.w_aoi},
            slo={'aoi_max_s': LIMITS.aoi_max_s, 'error_max_norm': LIMITS.error_max_norm,
                 'comm_budget_Bps': LIMITS.comm_budget_Bps, 'energy_budget_Jps': LIMITS.energy_budget_Jps,
                 'latency_max_ms': LIMITS.latency_max_ms},
            set_S=metrics, cost=d.objective))
        s.add(DecisionTrace(run_id=run_id, mode=mode, details=metrics,
            runtime_ms=latency_ms, selected_count=len(selected),
            objective=d.objective, feasible=d.feasible))
        s.commit()
    log.info('%s / %s: interval=%.2f selected=%d/%d feasible=%s objective=%.3f',
             run_id, mode, d.interval_s, len(selected), len(nodes), d.feasible, d.objective)
    return d


async def decision_loop():
    Base.metadata.create_all(bind=engine)
    publisher = PolicyPublisher()
    loop = asyncio.get_running_loop()
    schedule = PeriodicDeadline(CONTROL_PERIOD_S, loop.time())
    tick = 0
    previous_skipped = 0
    while not shutdown.is_set():
        if schedule.delay_s(loop.time()) > 0:
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=schedule.delay_s(loop.time()))
            except asyncio.TimeoutError:
                pass
        if shutdown.is_set():
            break
        actual = loop.time()
        schedule_meta = {
            'tick':tick, 'period_s':CONTROL_PERIOD_S,
            'schedule_lag_ms':round(1000*schedule.lag_s(actual),3),
            'previous_skipped_slots':previous_skipped,
        }
        failed = False
        try:
            await cycle(publisher, scheduling=schedule_meta)
        except Exception:
            failed = True
            log.exception('Decision failed; no implicit SLO-success assumed')
        finished = loop.time()
        skipped = schedule.advance(finished)
        log.info('HDT_POLICY_TIMING %s',json.dumps({
            **schedule_meta,
            'time_utc':datetime.now(timezone.utc).isoformat(),
            'cycle_wall_ms':round(1000*(finished-actual),3),
            'slots_skipped_after_tick':skipped,
            'cycle_failed':failed
        },separators=(',', ':')))
        previous_skipped=skipped
        tick += 1


def _stop(*_):
    shutdown.set()


if __name__ == '__main__':
    with suppress(Exception):
        signal.signal(signal.SIGTERM, _stop)
    with suppress(Exception):
        signal.signal(signal.SIGINT, _stop)
    asyncio.run(decision_loop())
