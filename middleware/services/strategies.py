"""Exactly the same action evaluator is used by A1, ablations, and baselines."""
from dataclasses import replace
from itertools import combinations
from .action_controller import ControlLimits, NodeEstimate, decide, evaluate


def choose_action(nodes, limits, mode='A1', fixed_interval_s=2., aoi_threshold_s=6.):
    nodes = tuple(nodes)
    available = {n.node_id for n in nodes if n.available}
    if mode == 'A1':
        return decide(nodes, limits)
    if mode == 'B0':
        local = replace(limits, candidate_intervals_s=(float(fixed_interval_s),))
        return evaluate(nodes, local, float(fixed_interval_s), available, search='B0_fixed')
    if mode == 'B1':
        local = replace(limits, candidate_intervals_s=(float(fixed_interval_s),))
        selected = {n.node_id for n in nodes if n.available and n.age_s + fixed_interval_s >= aoi_threshold_s}
        return evaluate(nodes, local, float(fixed_interval_s), selected, search='B1_threshold')
    if mode == 'B1_guard':
        # Supplementary baseline: AoI threshold with deterministic minimum
        # per-subsystem coverage. This is NOT the original B1 result.
        local = replace(limits, candidate_intervals_s=(float(fixed_interval_s),))
        selected = {n.node_id for n in nodes if n.available and
                    n.age_s + fixed_interval_s >= aoi_threshold_s}
        for group in sorted({n.subsystem for n in nodes if n.available}):
            if not any(n.node_id in selected for n in nodes if n.available and n.subsystem == group):
                candidates = [n for n in nodes if n.available and n.subsystem == group]
                # Prefer the stalest member, then its predicted error growth.
                chosen = max(candidates, key=lambda n: (n.age_s,
                    n.error_norm + n.drift_rate_norm_per_s * limits.decision_horizon_s,
                    n.node_id))
                selected.add(chosen.node_id)
        return evaluate(nodes, local, float(fixed_interval_s), selected,
                        search='B1_threshold_coverage_guard')
    if mode == 'A1_selection':
        local = replace(limits, candidate_intervals_s=(float(fixed_interval_s),))
        return decide(nodes, local)
    if mode == 'A1_interval':
        # Fixed full participation with adaptive interval. Mark infeasible constraints honestly.
        candidates = [evaluate(nodes, limits, dt, available, search='A1_interval')
                      for dt in limits.candidate_intervals_s]
        feasible = [d for d in candidates if d.feasible]
        return min(feasible or candidates, key=lambda d: (0 if d.feasible else len(d.violations), d.objective))
    raise ValueError(f'unknown mode {mode}')
