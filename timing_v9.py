"""Monotonic fixed-epoch pacing for measurement/control loops.

The clock is process-local; use UTC timestamps only for cross-container trace
alignment. A skipped slot is reported, never replayed as a burst.
"""
import math
import time


class PeriodicDeadline:
    def __init__(self, period_s, start=None):
        self.period_s = float(period_s)
        if not math.isfinite(self.period_s) or self.period_s <= 0:
            raise ValueError('period_s must be positive and finite')
        self.due = time.monotonic() if start is None else float(start)
        self.skipped_total = 0

    def lag_s(self, actual):
        return max(0.0, float(actual) - self.due)

    def advance(self, completed):
        """Advance to the first future slot, reporting how many were missed."""
        self.due += self.period_s
        completed = float(completed)
        missed = 0
        if completed >= self.due:
            missed = 1 + int((completed-self.due)//self.period_s)
            self.due += missed*self.period_s
        self.skipped_total += missed
        return missed

    def delay_s(self, now):
        return max(0.0, self.due - float(now))
