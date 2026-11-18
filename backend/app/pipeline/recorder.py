"""Persists node executions (spans) as they happen.

Writing each step when it starts and finishes gives three things at once:
live progress for the UI, a durable trace for incident debugging, and the
guarantee that work finished before a crash is visible afterwards.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.base import get_engine, utcnow
from app.db.models import Run, RunStep
from app.observability.logging import get_logger, redact

log = get_logger(__name__)


class StepRecorder(Protocol):
    def start(self, node: str, item_key: str | None, input_summary: dict[str, Any]) -> str: ...

    def finish(
        self,
        step_id: str,
        *,
        status: str,
        duration_ms: float,
        attempts: int,
        output_summary: dict[str, Any],
        error: dict[str, Any] | None,
    ) -> None: ...


class DbStepRecorder:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def start(self, node: str, item_key: str | None, input_summary: dict[str, Any]) -> str:
        with Session(get_engine()) as session:
            step = RunStep(
                run_id=self.run_id,
                node=node,
                item_key=item_key,
                status="running",
                input_summary=redact(input_summary),
                started_at=utcnow(),
            )
            session.add(step)
            session.execute(update(Run).where(Run.id == self.run_id).values(current_node=node))
            session.commit()
            return step.id

    def finish(
        self,
        step_id: str,
        *,
        status: str,
        duration_ms: float,
        attempts: int,
        output_summary: dict[str, Any],
        error: dict[str, Any] | None,
    ) -> None:
        try:
            with Session(get_engine()) as session:
                session.execute(
                    update(RunStep)
                    .where(RunStep.id == step_id)
                    .values(
                        status=status,
                        finished_at=utcnow(),
                        duration_ms=round(duration_ms, 1),
                        attempts=attempts,
                        output_summary=redact(output_summary),
                        error=error,
                    )
                )
                session.commit()
        except Exception:  # recording must never break the pipeline
            log.exception("step_record_failed", step_id=step_id)


class NullRecorder:
    """For unit tests that exercise the graph without a database."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def start(self, node: str, item_key: str | None, input_summary: dict[str, Any]) -> str:
        self.steps.append({"node": node, "item_key": item_key, "input": input_summary})
        return str(len(self.steps) - 1)

    def finish(self, step_id: str, **kwargs: Any) -> None:
        self.steps[int(step_id)].update(kwargs)
