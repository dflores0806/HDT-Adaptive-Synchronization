def estimate_drift(loss: float, grad_norm: float) -> float:
    return 0.5 * loss + 0.5 * min(1.0, grad_norm / 10.0)
