from fastapi import APIRouter
from sqlalchemy import select, desc
from ..db import get_session
from ..models import PolicyDecision
router = APIRouter(prefix="/decisions", tags=["decisions"])
@router.get("/last")
def last_decision():
    with get_session() as s:
        row = s.execute(select(PolicyDecision).order_by(desc(PolicyDecision.ts))).scalars().first()
        if not row:
            return {"ok": False, "message": "No decisions recorded yet"}
        return {
            "ok": True,
            "delta_t": row.delta_t,
            "weights": row.weights,
            "slo": row.slo,
            "set_S": row.set_S,
            "cost": row.cost,
            "ts": str(row.ts),
        }
