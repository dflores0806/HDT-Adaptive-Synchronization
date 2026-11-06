"""Experiment modes are persisted in PostgreSQL, shared by API and policy worker."""
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ..db import get_session
from ..models import ExperimentConfig
router = APIRouter(prefix='/admin', tags=['admin'])

class ExperimentUpdate(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    mode: str
    fixed_interval_s: float = Field(gt=0, le=60)
    aoi_threshold_s: float = Field(gt=0, le=120)

@router.get('/experiment')
def get_experiment():
    with get_session() as s:
        cfg = s.get(ExperimentConfig, 1)
        return ({'run_id': cfg.run_id, 'mode': cfg.mode,
                 'fixed_interval_s': cfg.fixed_interval_s, 'aoi_threshold_s': cfg.aoi_threshold_s}
                if cfg else {'run_id': 'dev', 'mode': 'A1', 'fixed_interval_s': 2., 'aoi_threshold_s': 6.})

@router.post('/experiment')
def set_experiment(update: ExperimentUpdate):
    if update.mode not in ('B0', 'B1', 'B1_guard', 'A1', 'A1_interval', 'A1_selection'):
        raise HTTPException(status_code=422, detail='Unsupported policy mode')
    if not re.fullmatch('[A-Za-z0-9_-]+', update.run_id):
        raise HTTPException(status_code=422, detail='run_id must be alphanumeric, dash or underscore')
    with get_session() as s:
        cfg = s.get(ExperimentConfig, 1)
        if cfg is None:
            cfg = ExperimentConfig(id=1)
            s.add(cfg)
        cfg.run_id = update.run_id
        cfg.mode = update.mode
        cfg.fixed_interval_s = update.fixed_interval_s
        cfg.aoi_threshold_s = update.aoi_threshold_s
        s.commit()
    return {'ok': True, **update.model_dump()}
