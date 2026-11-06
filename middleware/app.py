"""HDT ingestion API with bounded, research-only HTTP concurrency telemetry.

In-flight requests measure application concurrency, NOT broker queue depth.
The metrics endpoint is for isolated lab use, not an authenticated service.
"""
import threading
import time
from collections import deque
from fastapi import FastAPI
from .db import engine, pool_kwargs
from .models import Base
from .routers import admin, telemetry, decisions

app = FastAPI(title="HDT Adaptive Middleware", version="0.2.1-capacity")
Base.metadata.create_all(bind=engine)

_lock = threading.Lock()
_ingress = {"inflight": 0, "inflight_high_water_since_start": 0,
            "requests_seen_since_start": 0, "completed_since_start": 0,
            "http_failures_since_start": 0}
_wall_ms = deque(maxlen=2048)

@app.middleware("http")
async def count_ingress(request, call_next):
    if not request.url.path.startswith('/telemetry/'):
        return await call_next(request)
    with _lock:
        _ingress['inflight'] += 1
        _ingress['requests_seen_since_start'] += 1
        _ingress['inflight_high_water_since_start'] = max(
            _ingress['inflight_high_water_since_start'],_ingress['inflight'])
    t0=time.perf_counter()
    status=500
    try:
        response=await call_next(request)
        status=response.status_code
        return response
    finally:
        with _lock:
            _ingress['inflight']-=1
            _ingress['completed_since_start']+=1
            if status>=400:_ingress['http_failures_since_start']+=1
            _wall_ms.append(round((time.perf_counter()-t0)*1000,3))

@app.get('/research/ingress-metrics')
async def ingress_metrics():
    with _lock:
        values=dict(_ingress)
        ms=sorted(_wall_ms)
        values['last_2048_ingress_wall_ms_p95']=(ms[min(len(ms)-1,int(.95*(len(ms)-1)))] if ms else None)
    values['sqlalchemy_pool_status']=engine.pool.status()
    values['sqlalchemy_pool_configuration']=dict(pool_kwargs)
    values['queue_depth_available']=False
    values['definition']='HTTP telemetry requests in application handler, not MQTT/ASGI queue depth'
    return values

app.include_router(admin.router)
app.include_router(telemetry.router)
app.include_router(decisions.router)

@app.get('/health')
def health():
    return {"status": "ok"}
