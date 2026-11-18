"""Scorer: deterministic opportunity ranking (see `app.scoring.opportunity`)."""

from __future__ import annotations

from app.db.models import StepStatus
from app.pipeline.contracts import ScoredQuery
from app.pipeline.nodes.base import NodeResult
from app.pipeline.state import PipelineDeps, PipelineState
from app.scoring.opportunity import score_query


def scorer(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    scored = [ScoredQuery(measurement=m, breakdown=score_query(m)) for m in state.get("measurements") or []]
    scored.sort(key=lambda s: (-s.breakdown.score, -(s.measurement.candidate.search_volume or 0)))
    measurable = sum(1 for s in scored if s.breakdown.visibility is not None)
    return NodeResult(
        update={"scored": scored},
        status=StepStatus.OK if measurable else StepStatus.FAILED,
        summary={
            "measurable": measurable,
            "ranking": [(s.ref, s.measurement.candidate.keyword, s.breakdown.score) for s in scored[:10]],
        },
    )
