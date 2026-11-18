"""ORM -> API model mapping (one place, so every endpoint renders resources identically)."""

from __future__ import annotations

from typing import Any

from app.api.schemas import (
    BrandOut,
    CompetitorOut,
    QueryOut,
    RecommendationOut,
    RunBrief,
    RunOut,
    StageProgress,
    StepOut,
    TrendPoint,
)
from app.db.models import Brand, Query, Recommendation, Run, RunStatus, RunStep
from app.reference import get_location

STAGES: list[tuple[str, str, tuple[str, ...]]] = [
    ("plan", "Planning the research", ("discovery_planner",)),
    ("discover", "Researching the market", ("discovery_retriever",)),
    ("questions", "Finding the questions buyers ask", ("discovery_normalizer", "seed_fallback")),
    ("select", "Choosing what to measure", ("measurement_planner",)),
    ("measure", "Measuring search and AI answers", ("measurement_retriever",)),
    ("read", "Reading the results", ("measurement_normalizer",)),
    ("score", "Scoring opportunities", ("scorer",)),
    ("judge", "Judging which gaps matter", ("gap_analyst",)),
    ("recommend", "Writing content recommendations", ("recommender",)),
    ("report", "Assembling the report", ("reporter",)),
]
_SEVERITY = {"failed": 3, "degraded": 2, "ok": 1, "skipped": 0}


def run_brief(run: Run) -> RunBrief:
    return RunBrief(
        id=run.id,
        status=RunStatus(run.status),
        created_at=run.created_at,
        finished_at=run.finished_at,
        visibility_score=run.visibility_score,
        question=run.question,
    )


def brand_out(brand: Brand, runs: list[Run] | None = None) -> BrandOut:
    runs = sorted(runs if runs is not None else brand.runs, key=lambda r: r.created_at)
    completed = [r for r in runs if r.status in (RunStatus.SUCCEEDED.value, RunStatus.PARTIAL.value)]
    active = next((r for r in reversed(runs) if not RunStatus(r.status).is_terminal), None)
    location = get_location(brand.location_code)
    return BrandOut(
        id=brand.id,
        name=brand.name,
        domain=brand.domain,
        market=brand.market,
        description=brand.description,
        location_code=brand.location_code,
        location_name=location["name"] if location else str(brand.location_code),
        language_code=brand.language_code,
        seed_topics=list(brand.seed_topics or []),
        competitors=[CompetitorOut(id=c.id, name=c.name, domain=c.domain) for c in brand.competitors],
        created_at=brand.created_at,
        updated_at=brand.updated_at,
        latest_run=run_brief(runs[-1]) if runs else None,
        latest_completed_run=run_brief(completed[-1]) if completed else None,
        active_run_id=active.id if active else None,
        runs_count=len(runs),
        trend=[
            TrendPoint(run_id=r.id, finished_at=r.finished_at, visibility_score=r.visibility_score, status=r.status)
            for r in completed[-12:]
        ],
    )


def _stage_detail(stage: str, steps: list[RunStep]) -> str | None:
    done = [s for s in steps if s.output_summary]
    if not done:
        return None
    out: dict[str, Any] = done[-1].output_summary or {}
    if stage == "discover":
        return f"{out.get('ok', 0)} of {out.get('calls', 0)} research calls succeeded" + (
            f", {out['cache_hits']} from cache" if out.get("cache_hits") else ""
        )
    if stage == "questions":
        if any(s.node == "seed_fallback" for s in steps):
            return "No research results; using your profile topics"
        return f"{out.get('kept', 0)} candidate questions found"
    if stage == "select":
        queries = out.get("queries") or []
        return f"{len(queries)} questions selected" + (" (rule-based)" if out.get("source") == "fallback" else "")
    if stage == "read":
        return f"{out.get('measured', 0)} measured, {out.get('partial', 0)} partial, {out.get('failed', 0)} failed"
    if stage in ("judge", "recommend") and out.get("source") == "rule":
        return "AI unavailable – used rule-based fallback"
    if stage == "recommend" and "count" in out:
        return f"{out['count']} recommendations"
    return None


def stage_progress(run: Run) -> list[StageProgress]:
    terminal = RunStatus(run.status).is_terminal
    by_node: dict[str, list[RunStep]] = {}
    for step in run.steps:
        by_node.setdefault(step.node, []).append(step)
    planned_queries = 0
    for step in by_node.get("measurement_planner", []):
        queries = (step.output_summary or {}).get("queries")
        planned_queries = len(queries) if queries else 0
    out: list[StageProgress] = []
    for key, label, nodes in STAGES:
        steps = [s for n in nodes for s in by_node.get(n, [])]
        total = planned_queries if key == "measure" else len(steps)
        completed = sum(1 for s in steps if s.status != "running")
        if not steps:
            status = "skipped" if terminal else "pending"
        elif any(s.status == "running" for s in steps) or (key == "measure" and not terminal and completed < total):
            status = "running"
        else:
            status = max((s.status for s in steps), key=lambda v: _SEVERITY.get(v, 0))
        detail = _stage_detail(key, steps)
        if key == "measure" and total:
            detail = f"{completed} of {total} questions measured"
        out.append(
            StageProgress(stage=key, label=label, status=status, detail=detail, completed=completed, total=total)
        )
    return out


def run_out(run: Run) -> RunOut:
    duration = (run.finished_at - run.started_at).total_seconds() if run.finished_at and run.started_at else None
    return RunOut(
        id=run.id,
        brand_id=run.brand_id,
        status=RunStatus(run.status),
        question=run.question,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_s=round(duration, 1) if duration is not None else None,
        current_node=run.current_node,
        retry_of_run_id=run.retry_of_run_id,
        data_mode=run.data_mode,
        llm_model=run.llm_model,
        visibility_score=run.visibility_score,
        cost_usd=run.cost_usd,
        degradations=run.degradations or [],
        error=run.error,
        stages=stage_progress(run),
        report=run.report,
        metrics=run.metrics,
        profile=run.profile_snapshot,
    )


def step_out(step: RunStep) -> StepOut:
    return StepOut.model_validate(step)


def query_out(q: Query, rec_ids: list[str] | None = None) -> QueryOut:
    out = QueryOut.model_validate(q)
    out.recommendation_ids = rec_ids or []
    return out


def recommendation_out(r: Recommendation) -> RecommendationOut:
    return RecommendationOut(
        id=r.id,
        run_id=r.run_id,
        brand_id=r.brand_id,
        query_id=r.query_id,
        query_text=r.query.text,
        priority=r.priority,
        title=r.title,
        content_type=r.content_type,
        effort=r.effort,
        target_channels=r.target_channels,
        rationale=r.rationale,
        outline=r.outline,
        key_points=r.key_points,
        sources_to_earn=r.sources_to_earn,
        source=r.source,
        status=r.status,
        status_updated_at=r.status_updated_at,
        created_at=r.created_at,
        opportunity_score=r.query.opportunity_score,
    )
