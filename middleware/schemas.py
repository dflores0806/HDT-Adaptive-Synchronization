from pydantic import BaseModel
from typing import Dict
class SLOUpdate(BaseModel):
    A_min: float
    L_max: float
    E_max: float
class WeightsUpdate(BaseModel):
    wL: float
    wE: float
    wD: float
    eta: float
    alpha: float
    beta: float
class PolicySnapshot(BaseModel):
    delta_t: float
    weights: Dict[str, float]
    slo: Dict[str, float]
    set_S: Dict[str, float]
    cost: float
