"""Per-run cost ceiling for paid provider calls."""

from __future__ import annotations

import threading

from app.core.errors import BudgetExceededError


class CostBudget:
    """Refuses a call when its *estimated* cost would push the run over budget.

    Estimating before the call (rather than checking actual spend after it)
    is what makes the ceiling a ceiling under parallel fan-out.
    """

    def __init__(self, limit_usd: float) -> None:
        self.limit_usd = limit_usd
        self._committed = 0.0
        self._reserved = 0.0
        self._lock = threading.Lock()

    def reserve(self, estimate_usd: float, *, operation: str) -> None:
        with self._lock:
            if self._committed + self._reserved + estimate_usd > self.limit_usd:
                raise BudgetExceededError(
                    f"run budget of ${self.limit_usd:.2f} reached; skipped {operation}",
                    dependency="dataforseo",
                    operation=operation,
                )
            self._reserved += estimate_usd

    def settle(self, estimate_usd: float, actual_usd: float) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - estimate_usd)
            self._committed += actual_usd

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._committed
