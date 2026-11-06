def comm_cost(latency_ms: float, bandwidth_mbps: float) -> float:
    return max(0.0, latency_ms / 100.0) * (1.0 + 50.0 / max(bandwidth_mbps, 1.0))
def energy_cost(energy_w: float, e_max: float) -> float:
    return max(0.0, energy_w / max(e_max, 1.0))
def drift_cost(drift: float) -> float:
    return drift * drift
def composite_cost(lat_ms, bw, e_w, drift, wL, wE, wD, e_max):
    return (wL * comm_cost(lat_ms, bw) + wE * energy_cost(e_w, e_max) + wD * drift_cost(drift))
