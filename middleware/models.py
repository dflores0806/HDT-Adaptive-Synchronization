"""DB schema. Existing legacy tables are preserved for backwards compatibility."""
from datetime import datetime
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TelemetryEvent(Base):
    __tablename__ = 'telemetry_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    latency_ms: Mapped[float] = mapped_column(Float)
    bandwidth_mbps: Mapped[float] = mapped_column(Float)
    energy_w: Mapped[float] = mapped_column(Float)
    loss: Mapped[float] = mapped_column(Float, default=0.0)
    drift: Mapped[float] = mapped_column(Float, default=0.0)


class PolicyDecision(Base):
    __tablename__ = 'policy_decisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delta_t: Mapped[float] = mapped_column(Float)
    weights: Mapped[dict] = mapped_column(JSON)
    slo: Mapped[dict] = mapped_column(JSON)
    set_S: Mapped[dict] = mapped_column(JSON)
    cost: Mapped[float] = mapped_column(Float, default=0.0)


class NodeHeartbeat(Base):
    __tablename__ = 'node_heartbeats_v2'
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    subsystem: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[float] = mapped_column(Float)
    energy_w: Mapped[float] = mapped_column(Float)
    drift_rate_norm_s: Mapped[float] = mapped_column(Float)
    bytes_per_update: Mapped[int] = mapped_column(Integer)
    heartbeat_bytes: Mapped[int] = mapped_column(Integer)
    updates_offered: Mapped[int] = mapped_column(Integer, default=0)
    updates_failed: Mapped[int] = mapped_column(Integer, default=0)


class StateUpdate(Base):
    __tablename__ = 'state_updates_v2'
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    sample_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seq: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict] = mapped_column(JSON)
    payload_bytes: Mapped[int] = mapped_column(Integer)
    accepted: Mapped[bool] = mapped_column(Boolean, default=True)
    reject_reason: Mapped[str] = mapped_column(String(32), default='')


class ReferenceSample(Base):
    """Evaluation-only ground truth. NEVER read by the policy worker."""
    __tablename__ = 'reference_samples_v2'
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    sample_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seq: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict] = mapped_column(JSON)
    payload_bytes: Mapped[int] = mapped_column(Integer)


class ExperimentConfig(Base):
    __tablename__ = 'experiment_config_v2'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), default='dev')
    mode: Mapped[str] = mapped_column(String(16), default='A1')
    fixed_interval_s: Mapped[float] = mapped_column(Float, default=2.)
    aoi_threshold_s: Mapped[float] = mapped_column(Float, default=6.)


class DecisionTrace(Base):
    __tablename__ = 'decision_trace_v2'
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict] = mapped_column(JSON)
    runtime_ms: Mapped[float] = mapped_column(Float)
    selected_count: Mapped[int] = mapped_column(Integer)
    objective: Mapped[float] = mapped_column(Float)
    feasible: Mapped[bool] = mapped_column(Boolean)
