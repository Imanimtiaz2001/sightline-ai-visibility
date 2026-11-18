"""Node instrumentation and contract enforcement.

`instrumented()` wraps every node with the same cross-cutting behaviour so
the node bodies contain only their single responsibility:

* a span: start/finish persisted via the recorder, `node` + `span_id` bound
  into every log line the node emits;
* structured `node_started` / `node_finished` logs with redacted inputs,
  output summary, duration, outcome and retry count;
* Prometheus + per-run metrics;
* contract enforcement: a node may only write the state keys it declares;
* crash containment: an unexpected exception is recorded as a failed step
  and a degradation, and the graph continues – routing functions see the
  missing output and take the fallback path.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.db.models import StepStatus
from app.observability.context import bound, new_id
from app.observability.logging import get_logger
from app.observability.metrics import NODE_LATENCY, NODE_OUTCOMES
from app.pipeline.contracts import Degradation
from app.pipeline.state import PipelineDeps

log = get_logger("pipeline.node")


@dataclass
class NodeResult:
    update: dict[str, Any]
    status: StepStatus = StepStatus.OK
    summary: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    degradations: list[Degradation] = field(default_factory=list)
    error: dict[str, Any] | None = None


NodeFn = Callable[[Any, PipelineDeps], NodeResult]


def summarize(value: Any) -> Any:
    """Compact, log-safe description of a state value."""
    if isinstance(value, list):
        return {"count": len(value)}
    if isinstance(value, BaseModel):
        data = value.model_dump()
        return {k: (summarize(v) if isinstance(v, list | dict) else v) for k, v in list(data.items())[:8]}
    if isinstance(value, dict):
        return {"keys": sorted(value)[:10]}
    return value


def instrumented(
    name: str,
    fn: NodeFn,
    deps: PipelineDeps,
    *,
    reads: tuple[str, ...],
    writes: tuple[str, ...],
    item_key: Callable[[Any], str] | None = None,
) -> Callable[[Any], dict[str, Any]]:
    allowed = set(writes) | {"degradations"}

    def run(state: Any) -> dict[str, Any]:
        key = item_key(state) if item_key else None
        span_id = new_id()[:16]
        inputs = {k: summarize(state.get(k)) for k in reads}
        with bound(node=name, span_id=span_id, item=key):
            try:
                step_id: str | None = deps.recorder.start(name, key, inputs)
            except Exception:
                log.exception("step_start_record_failed")
                step_id = None
            log.info("node_started", inputs=inputs)
            started = time.perf_counter()
            try:
                result = fn(state, deps)
            except Exception as exc:  # contain bugs: one node must not kill the run
                log.exception("node_crashed", error=str(exc))
                result = NodeResult(
                    update={},
                    status=StepStatus.FAILED,
                    error={"code": "node_crashed", "message": f"{type(exc).__name__}: {exc}"[:500]},
                    degradations=[Degradation(node=name, kind="failed", message=f"{name} failed unexpectedly")],
                )
            duration_ms = (time.perf_counter() - started) * 1000

            illegal = set(result.update) - allowed
            if illegal:  # contract violation is a programming error – surface loudly, drop the keys
                log.error("node_contract_violation", illegal_keys=sorted(illegal))
                result.update = {k: v for k, v in result.update.items() if k in allowed}

            outcome = result.status.value
            NODE_LATENCY.labels(name).observe(duration_ms / 1000)
            NODE_OUTCOMES.labels(name, outcome).inc()
            deps.ctx.metrics.node(name, duration_ms, outcome)
            log_fn = log.warning if result.status is not StepStatus.OK else log.info
            log_fn(
                "node_finished",
                outcome=outcome,
                duration_ms=round(duration_ms, 1),
                retries=max(0, result.attempts - 1) if result.attempts else 0,
                attempts=result.attempts,
                output=result.summary,
                error=result.error,
                degradations=[d.model_dump() for d in result.degradations] or None,
            )
            if step_id is not None:
                deps.recorder.finish(
                    step_id,
                    status=outcome,
                    duration_ms=duration_ms,
                    attempts=result.attempts,
                    output_summary={**result.summary, "degradations": [d.message for d in result.degradations]},
                    error=result.error,
                )
        update = dict(result.update)
        if result.degradations:
            update["degradations"] = result.degradations
        return update

    run.__name__ = name
    return run
