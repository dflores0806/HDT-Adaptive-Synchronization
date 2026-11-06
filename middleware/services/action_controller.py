"""Research prototype of an action-dependent, auditable synchronization policy.

Wired to the revision policy loop; ground-truth fidelity must be measured
separately, and all predictive coefficients require calibration before publication.
The offline exhaustive oracle is exact only over the specified *discrete* grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import FrozenSet, Iterable, List, Tuple
import math


@dataclass(frozen=True)
class NodeEstimate:
    node_id: str
    subsystem: str
    age_s: float
    error_norm: float
    drift_rate_norm_per_s: float
    bytes_per_update: int = 256
    joules_per_update: float = 0.01
    available: bool = True
    latency_ms: float = 0.
    monitoring_Bps: float = 0.

    def __post_init__(self):
        if not self.node_id or not self.subsystem:
            raise ValueError('node_id and subsystem must be nonempty')
        for name in ('age_s', 'error_norm', 'drift_rate_norm_per_s', 'joules_per_update', 'latency_ms', 'monitoring_Bps'):
            v = getattr(self, name)
            if not math.isfinite(v) or v < 0:
                raise ValueError(f'{name} must be nonnegative and finite')
        if self.bytes_per_update <= 0:
            raise ValueError('bytes_per_update must be positive')


@dataclass(frozen=True)
class ControlLimits:
    candidate_intervals_s: Tuple[float, ...] = (0.5, 1., 2., 4., 8.)
    aoi_max_s: float = 10.
    decision_horizon_s: float = 2.  # controller observation period, not selected reporting interval
    error_max_norm: float = 0.1
    comm_budget_Bps: float = 800.
    energy_budget_Jps: float = 0.06
    w_comm: float = 0.35
    w_energy: float = 0.15
    w_error: float = 0.35
    w_aoi: float = 0.15
    require_subsystem_coverage: bool = True
    latency_max_ms: float = 120.

    def __post_init__(self):
        positives = (*self.candidate_intervals_s, self.aoi_max_s, self.decision_horizon_s,
                     self.error_max_norm, self.comm_budget_Bps,
                     self.energy_budget_Jps, self.latency_max_ms)
        if not self.candidate_intervals_s or any(not math.isfinite(v) or v <= 0 for v in positives):
            raise ValueError('candidate intervals and limits must be positive and finite')
        weights = (self.w_comm, self.w_energy, self.w_error, self.w_aoi)
        if any(not math.isfinite(v) or v < 0 for v in weights) or sum(weights) <= 0:
            raise ValueError('weights must be nonnegative with positive sum')


@dataclass(frozen=True)
class Decision:
    interval_s: float
    selected: FrozenSet[str]
    objective: float
    feasible: bool
    violations: Tuple[str, ...]
    comm_Bps: float
    energy_Jps: float
    max_aoi_s: float
    max_error_norm: float
    search: str


def evaluate(nodes: Iterable[NodeEstimate], limits: ControlLimits,
             interval_s: float, selected: Iterable[str], search: str = 'evaluation') -> Decision:
    """Explicit action-dependent surrogate, not a measured fidelity guarantee.

    Selected nodes are assumed to synchronize successfully over the candidate
    reporting interval (best-case actuation assumption). Unselected or unavailable
    nodes age until the NEXT CONTROLLER DECISION, i.e., decision_horizon_s,
    independently of the chosen reporting interval. The model is a surrogate;
    neither MQTT delivery nor future physical error is guaranteed.
    Drift is an *estimated error growth rate*, not the synthetic random drift
    field of the original repository.
    """
    nodes = tuple(nodes)
    ids = {n.node_id for n in nodes}
    if len(ids) != len(nodes):
        raise ValueError('duplicate node_id')
    if interval_s not in limits.candidate_intervals_s:
        raise ValueError('interval must be a configured candidate')
    selected = frozenset(selected)
    if not selected.issubset({n.node_id for n in nodes if n.available}):
        raise ValueError('cannot synchronize unavailable or unknown node')
    comm = (sum(n.bytes_per_update for n in nodes if n.node_id in selected) / interval_s +
            sum(n.monitoring_Bps for n in nodes if n.available))
    energy = sum(n.joules_per_update for n in nodes if n.node_id in selected) / interval_s
    ages = [interval_s if n.node_id in selected else n.age_s + limits.decision_horizon_s for n in nodes]
    errors = [(n.drift_rate_norm_per_s * interval_s if n.node_id in selected
               else n.error_norm + n.drift_rate_norm_per_s * limits.decision_horizon_s)
              for n in nodes]
    age_max = max(ages, default=0.)
    err_max = max(errors, default=0.)
    violations = []
    if comm > limits.comm_budget_Bps + 1e-9:
        violations.append('communication_budget')
    if energy > limits.energy_budget_Jps + 1e-9:
        violations.append('energy_budget')
    if age_max > limits.aoi_max_s + 1e-9:
        violations.append('aoi_limit')
    if err_max > limits.error_max_norm + 1e-9:
        violations.append('error_limit')
    if any(n.latency_ms > limits.latency_max_ms for n in nodes if n.node_id in selected):
        violations.append('latency_limit')
    if limits.require_subsystem_coverage:
        groups = {n.subsystem for n in nodes if n.available}
        covered = {n.subsystem for n in nodes if n.node_id in selected}
        if groups - covered:
            violations.append('subsystem_coverage')
    cost = (limits.w_comm * comm / limits.comm_budget_Bps +
            limits.w_energy * energy / limits.energy_budget_Jps +
            limits.w_error * (sum(errors) / len(errors) / limits.error_max_norm if errors else 0.) +
            limits.w_aoi * (sum(ages) / len(ages) / limits.aoi_max_s if ages else 0.))
    return Decision(interval_s, selected, cost, not violations, tuple(violations),
                    comm, energy, age_max, err_max, search)


def _all_subsets(items: Tuple[str, ...]):
    for r in range(len(items) + 1):
        for c in combinations(items, r):
            yield c


def _safety_rank(d: Decision, lim: ControlLimits) -> Tuple[float, float]:
    """Best-effort ranking for *infeasible* states; never marks SLO as met."""
    penalty = (max(0., d.comm_Bps / lim.comm_budget_Bps - 1.) +
               max(0., d.energy_Jps / lim.energy_budget_Jps - 1.) +
               max(0., d.max_aoi_s / lim.aoi_max_s - 1.) +
               max(0., d.max_error_norm / lim.error_max_norm - 1.) +
               (1. if 'subsystem_coverage' in d.violations else 0.) +
               (1. if 'latency_limit' in d.violations else 0.))
    return (penalty, d.objective)


def discrete_oracle(nodes: Iterable[NodeEstimate], limits: ControlLimits,
                    max_nodes: int = 10) -> Decision:
    """Exhaustive finite-grid reference, *not* continuous or global optimum."""
    nodes = tuple(nodes)
    active = tuple(n.node_id for n in nodes if n.available)
    if len(active) > max_nodes:
        raise ValueError('oracle restricted to small N')
    decisions = (evaluate(nodes, limits, dt, subset, search='discrete_oracle')
                 for dt in limits.candidate_intervals_s for subset in _all_subsets(active))
    feasible, best_effort = None, None
    for d in decisions:
        if d.feasible and (feasible is None or d.objective < feasible.objective):
            feasible = d
        if best_effort is None or _safety_rank(d, limits) < _safety_rank(best_effort, limits):
            best_effort = d
    assert best_effort is not None
    return feasible if feasible is not None else best_effort


def greedy_controller(nodes: Iterable[NodeEstimate], limits: ControlLimits) -> Decision:
    """Bounded heuristic for N>10. Records infeasibility rather than hiding it."""
    nodes = tuple(nodes)
    available = tuple(n for n in nodes if n.available)
    best_feasible, best_effort = None, None
    for dt in limits.candidate_intervals_s:
        selected = set()
        # Inclusion is mandatory when not synchronizing would break predicted
        # AoI/error constraints; the apparent benefit is a model assumption.
        for n in available:
            unsynced_age = n.age_s + limits.decision_horizon_s
            unsynced_error = n.error_norm + n.drift_rate_norm_per_s * limits.decision_horizon_s
            if unsynced_age > limits.aoi_max_s or unsynced_error > limits.error_max_norm:
                selected.add(n.node_id)
        if limits.require_subsystem_coverage:
            for group in sorted({n.subsystem for n in available}):
                if not any(n.node_id in selected for n in available if n.subsystem == group):
                    candidates = [n for n in available if n.subsystem == group]
                    # Smallest measured transmission cost (only available proxy).
                    chosen = min(candidates, key=lambda n: (n.bytes_per_update, n.joules_per_update, n.node_id))
                    selected.add(chosen.node_id)
        current = evaluate(nodes, limits, dt, selected, search='greedy')
        # Add optional participants if doing so improves the explicitly modeled
        # objective without breaking any enforced constraints.
        while True:
            improvements = []
            for n in available:
                if n.node_id not in selected:
                    candidate = evaluate(nodes, limits, dt, selected | {n.node_id}, search='greedy')
                    if candidate.feasible and candidate.objective < current.objective - 1e-12:
                        improvements.append(candidate)
            if not improvements:
                break
            current = min(improvements, key=lambda d: d.objective)
            selected = set(current.selected)
        if current.feasible and (best_feasible is None or current.objective < best_feasible.objective):
            best_feasible = current
        if best_effort is None or _safety_rank(current, limits) < _safety_rank(best_effort, limits):
            best_effort = current
    return best_feasible if best_feasible is not None else best_effort


def decide(nodes: Iterable[NodeEstimate], limits: ControlLimits) -> Decision:
    nodes = tuple(nodes)
    if sum(n.available for n in nodes) <= 10:
        return discrete_oracle(nodes, limits)
    return greedy_controller(nodes, limits)
