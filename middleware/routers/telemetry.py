"""Operational measurements and independent evaluation reference."""
from datetime import datetime, timezone
import json
import logging
import time
from typing import Dict
from fastapi import APIRouter, Request, HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select, desc
from ..db import get_session
from ..models import (TelemetryEvent, NodeHeartbeat, StateUpdate, ReferenceSample,
                      ExperimentConfig)

router = APIRouter(prefix='/telemetry', tags=['telemetry'])

log = logging.getLogger('hdt.capacity.db')

def timed_store(kind, fn, *args):
    t0=time.perf_counter()
    try:
        return fn(*args)
    finally:
        ms=(time.perf_counter()-t0)*1000.
        if ms >= 250.:
            log.warning('HDT_DB_SLOW %s', json.dumps({'operation':kind,'wall_ms':round(ms,3)},separators=(',',':')))

RANGES = {'temperature_c': 40., 'load_fraction': 1., 'cooling_fraction': 1.}


class LegacyTelemetry(BaseModel):
    node_id: str
    latency_ms: float
    bandwidth_mbps: float
    energy_w: float
    loss: float = 0.0
    drift: float = 0.0


class StatePayload(BaseModel):
    run_id: str | None = Field(default=None, min_length=1, max_length=64)
    node_id: str = Field(min_length=1, max_length=64)
    sample_time: datetime
    seq: int = Field(ge=0)
    state: Dict[str, float]


class HeartbeatPayload(BaseModel):
    run_id: str | None = Field(default=None, min_length=1, max_length=64)
    node_id: str = Field(min_length=1, max_length=64)
    subsystem: str = Field(min_length=1, max_length=32)
    latency_ms: float = Field(ge=0)
    energy_w: float = Field(ge=0)
    drift_rate_norm_s: float = Field(ge=0)
    bytes_per_update: int = Field(gt=0)
    updates_offered: int = Field(ge=0, default=0)
    updates_failed: int = Field(ge=0, default=0)


def current_run(s):
    cfg = s.get(ExperimentConfig, 1)
    if cfg is None:
        cfg = ExperimentConfig(id=1, run_id='dev', mode='A1')
        s.add(cfg)
        s.flush()
    return cfg.run_id


def state_valid(p):
    # Require explicit state dimensions and finite values.
    if set(p.state) != set(RANGES):
        return False
    return all(float('-inf') < value < float('inf') for value in p.state.values())


@router.post('')
def legacy_ingest(tp: LegacyTelemetry):
    with get_session() as s:
        s.add(TelemetryEvent(**tp.model_dump()))
        s.commit()
    return {'ok': True, 'legacy': True}


# SQLAlchemy sessions are synchronous. Keep the ASGI event loop free of
# blocking database commits and SELECTs when many edge nodes post at once.
# Each helper owns a session for its entire lifetime within one worker thread.
# This changes execution scheduling only, not the experiment's state semantics.

def _store_heartbeat(tp: HeartbeatPayload, data_len: int):
    with get_session() as s:
        run_id = current_run(s)
        if tp.run_id is not None and tp.run_id != run_id:
            raise HTTPException(status_code=409, detail='stale_run_id')
        s.add(NodeHeartbeat(run_id=run_id, heartbeat_bytes=data_len,
                            **tp.model_dump(exclude={'run_id'})))
        s.commit()
    return {'ok': True, 'run_id': run_id}


@router.post('/heartbeat')
async def heartbeat(tp: HeartbeatPayload, request: Request):
    data_len = len(await request.body())
    return await run_in_threadpool(timed_store, 'heartbeat', _store_heartbeat, tp, data_len)


def _store_state(tp: StatePayload, data_len: int):
    with get_session() as s:
        run_id = current_run(s)
        if tp.run_id is not None and tp.run_id != run_id:
            raise HTTPException(status_code=409, detail='stale_run_id')
        latest = s.execute(select(StateUpdate).where(
            StateUpdate.node_id == tp.node_id, StateUpdate.run_id == run_id,
            StateUpdate.accepted.is_(True)).order_by(
                desc(StateUpdate.sample_time), desc(StateUpdate.seq))
        ).scalars().first()
        accepted = latest is None or (tp.sample_time, tp.seq) > (
            latest.sample_time.replace(tzinfo=timezone.utc)
            if latest.sample_time.tzinfo is None else latest.sample_time,
            latest.seq)
        reason = '' if accepted else 'stale_or_duplicate'
        s.add(StateUpdate(run_id=run_id, node_id=tp.node_id,
                          sample_time=tp.sample_time, seq=tp.seq,
                          state=tp.state, payload_bytes=data_len,
                          accepted=accepted, reject_reason=reason))
        s.commit()
    return {'ok': accepted, 'reason': reason, 'run_id': run_id}


@router.post('/state')
async def state(tp: StatePayload, request: Request):
    if not state_valid(tp):
        return {'ok': False, 'reason': 'invalid_state_dimensions'}
    if tp.sample_time.tzinfo is None:
        return {'ok': False, 'reason': 'timestamp_requires_timezone'}
    data_len = len(await request.body())
    return await run_in_threadpool(timed_store, 'state', _store_state, tp, data_len)


def _store_reference(tp: StatePayload, data_len: int):
    with get_session() as s:
        run_id = current_run(s)
        if tp.run_id is not None and tp.run_id != run_id:
            raise HTTPException(status_code=409, detail='stale_run_id')
        s.add(ReferenceSample(run_id=run_id, node_id=tp.node_id,
                              sample_time=tp.sample_time, seq=tp.seq,
                              state=tp.state, payload_bytes=data_len))
        s.commit()
    return {'ok': True, 'run_id': run_id}


@router.post('/reference')
async def reference(tp: StatePayload, request: Request):
    """Evaluation-only reference; never passed to the online policy."""
    if not state_valid(tp):
        return {'ok': False, 'reason': 'invalid_state_dimensions'}
    if tp.sample_time.tzinfo is None:
        return {'ok': False, 'reason': 'timestamp_requires_timezone'}
    data_len = len(await request.body())
    return await run_in_threadpool(timed_store, 'reference', _store_reference, tp, data_len)
