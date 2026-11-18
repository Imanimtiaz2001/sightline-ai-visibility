"""Per-run context handed to every external call."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.budget import CostBudget
from app.observability.metrics import RunMetrics


@dataclass(frozen=True)
class CallContext:
    run_id: str
    budget: CostBudget
    metrics: RunMetrics
    location_code: int
    language_code: str
    # A re-measure must observe the market *now*, so measurement calls skip cached reads
    # (they still write, so a retry of this run reuses them). Retries of a failed or
    # partial run set this to False and pay only for the calls that failed.
    fresh_measurements: bool = True
