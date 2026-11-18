"""Run lifecycle: admission control, background execution, persistence of results.

Slow work is handled asynchronously: `POST /runs` returns 202 immediately
with the run resource; the client follows progress through
`GET /runs/{id}` or the server-sent-event stream `GET /runs/{id}/events`.
"""

from __future__ import annotations

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.container import Container
from app.core.budget import CostBudget
from app.core.call_context import CallContext
from app.core.errors import ApiError, Conflict, NotFound
from app.db.base import get_engine, utcnow
from app.db.models import Brand, Query, Recommendation, Run, RunStatus
from app.observability.context import bound
from app.observability.error_reporting import capture_exception
from app.observability.logging import get_logger
from app.observability.metrics import RUN_OUTCOMES, RunMetrics
from app.pipeline.contracts import BrandContext, CompetitorRef
from app.pipeline.extract import normalize_text
from app.pipeline.graph import run_pipeline
from app.pipeline.recorder import DbStepRecorder
from app.pipeline.state import PipelineDeps, PipelineState
from app.reference import get_location

log = get_logger(__name__)
ACTIVE = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)

# When a run measures nothing at all, the dominant provider failure is the useful
# thing to tell the user: "no results" sends them looking in the wrong place, while
# "the provider rejected the credentials" is immediately actionable.
FAILURE_EXPLANATIONS = {
    "auth_failed": "The data provider rejected the credentials. Check DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.",
    "quota_exhausted": "The data provider account is out of credit, so no data could be fetched.",
    "rate_limited": "The data provider rate-limited every request. Try again in a few minutes.",
    "circuit_open": "The data provider had been failing repeatedly, so calls were paused to let it recover.",
    "transient": "The data provider could not be reached after several attempts.",
    "permanent": "The data provider rejected the requests, so nothing could be measured.",
    "budget_exceeded": "The run reached its cost limit before anything could be measured.",
}


def failure_reason(metrics: RunMetrics) -> dict[str, Any]:
    """Explain a failed run from the dominant external failure, not just 'no results'."""
    counts: Counter[str] = Counter()
    for outcomes in metrics.summary()["api_calls"].values():
        for code, times in outcomes.items():
            if code not in ("ok", "cache_hit"):
                counts[code] += times
    if not counts:
        return {"code": "no_measurements", "message": "No query could be measured. See the data-quality notes."}
    code, _ = counts.most_common(1)[0]
    fallback = "Nothing could be measured. See the data-quality notes for what failed."
    return {"code": code, "message": FAILURE_EXPLANATIONS.get(code, fallback)}


def snapshot(brand: Brand, question: str | None) -> dict[str, Any]:
    location = get_location(brand.location_code)
    return BrandContext(
        brand_id=brand.id,
        name=brand.name,
        domain=brand.domain,
        market=brand.market,
        description=brand.description,
        location_code=brand.location_code,
        location_name=location["name"] if location else str(brand.location_code),
        language_code=brand.language_code,
        seed_topics=list(brand.seed_topics or []),
        competitors=[CompetitorRef(name=c.name, domain=c.domain) for c in brand.competitors],
        question=question,
    ).model_dump()


class RunService:
    def __init__(self, container: Container) -> None:
        self._c = container
        self._pool = ThreadPoolExecutor(max_workers=container.settings.run_worker_threads, thread_name_prefix="run")
        self._admission = threading.Lock()

    # -- admission ----------------------------------------------------------------------

    def create(
        self,
        session: Session,
        brand_id: str,
        *,
        question: str | None,
        idempotency_key: str | None,
        retry_of: str | None = None,
    ) -> tuple[Run, bool]:
        """Create and enqueue a run. Returns (run, created); created=False on idempotent replay."""
        brand = session.get(Brand, brand_id, options=[selectinload(Brand.competitors)])
        if brand is None:
            raise NotFound("brand", brand_id)
        problems = self._c.settings.configuration_problems()
        if problems:
            raise ApiError(503, "Service not configured", "; ".join(problems))

        with self._admission:  # serialise the check-then-insert below within this process
            if idempotency_key:
                existing = session.scalar(
                    select(Run).where(Run.brand_id == brand_id, Run.idempotency_key == idempotency_key)
                )
                if existing:
                    return existing, False
            active = session.scalar(select(Run).where(Run.brand_id == brand_id, Run.status.in_(ACTIVE)))
            if active:
                raise Conflict(f"run {active.id} is already in progress for this brand")
            running = session.scalar(select(func.count()).select_from(Run).where(Run.status.in_(ACTIVE))) or 0
            if running >= self._c.settings.max_concurrent_runs:
                raise ApiError(
                    429,
                    "Too Many Requests",
                    "the analysis queue is full; try again shortly",
                    headers={"Retry-After": "30"},
                )
            run = Run(
                brand_id=brand_id,
                question=question,
                idempotency_key=idempotency_key,
                retry_of_run_id=retry_of,
                profile_snapshot=snapshot(brand, question),
                data_mode=self._c.settings.dataforseo_mode.value,
                llm_model=self._c.llm.model,
            )
            session.add(run)
            session.commit()
        self._pool.submit(self._execute, run.id)
        log.info("run_queued", run_id=run.id, brand_id=brand_id, trace_id=run.id, retry_of=retry_of)
        return run, True

    def retry(self, session: Session, run_id: str) -> Run:
        """Re-run a failed/partial run with the same inputs. Successful provider calls
        from the original run are served from the cache, so they are not paid twice."""
        original = session.get(Run, run_id)
        if original is None:
            raise NotFound("run", run_id)
        if not RunStatus(original.status).is_terminal:
            raise Conflict("run is still in progress")
        run, _ = self.create(
            session, original.brand_id, question=original.question, idempotency_key=None, retry_of=run_id
        )
        return run

    # -- execution ------------------------------------------------------------------------

    def _execute(self, run_id: str) -> None:
        with bound(trace_id=run_id, run_id=run_id):
            metrics = RunMetrics()
            try:
                with Session(get_engine()) as session:
                    run = session.get(Run, run_id)
                    if run is None:
                        return
                    run.status, run.started_at = RunStatus.RUNNING.value, utcnow()
                    brand = BrandContext.model_validate(run.profile_snapshot)
                    is_retry = run.retry_of_run_id is not None
                    session.commit()
                log.info("run_started", brand=brand.name, question=brand.question)

                ctx = CallContext(
                    run_id=run_id,
                    budget=CostBudget(self._c.settings.max_cost_per_run_usd),
                    metrics=metrics,
                    location_code=brand.location_code,
                    language_code=brand.language_code,
                    fresh_measurements=not is_retry,
                )
                deps = PipelineDeps(
                    settings=self._c.settings,
                    llm=self._c.llm,
                    tools=self._c.tools,
                    executor=self._c.executor,
                    ctx=ctx,
                    recorder=DbStepRecorder(run_id),
                )
                final = run_pipeline(deps, PipelineState(brand=brand, degradations=[]))
                status = self._persist(run_id, final, ctx, metrics)
                RUN_OUTCOMES.labels(status).inc()
                log.info("run_finished", status=status, metrics=metrics.summary())
            except Exception as exc:  # last line of defence: the run is marked failed, never left "running"
                log.exception("run_crashed")
                capture_exception(exc)
                RUN_OUTCOMES.labels(RunStatus.FAILED.value).inc()
                self._mark_failed(
                    run_id, {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"[:500]}, metrics
                )

    def _persist(self, run_id: str, state: PipelineState, ctx: CallContext, metrics: RunMetrics) -> str:
        scored = state.get("scored") or []
        analysis = state.get("analysis")
        judgements = {j.query_ref: j.model_dump() for j in analysis.judgements} if analysis else {}
        source = state.get("analysis_source")
        report = state.get("report") or {}
        degradations = [d.model_dump() for d in state.get("degradations") or []]
        measurable = [s for s in scored if s.breakdown.visibility is not None]

        if not measurable:
            status = RunStatus.FAILED
        elif degradations or any(s.measurement.status != "measured" for s in scored):
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.SUCCEEDED

        with Session(get_engine()) as session:
            run = session.get(Run, run_id)
            assert run is not None
            queries: dict[str, Query] = {}
            for rank, s in enumerate(scored, 1):
                m = s.measurement
                q = Query(
                    run_id=run_id,
                    brand_id=run.brand_id,
                    rank=rank,
                    text=m.candidate.keyword,
                    normalized_text=normalize_text(m.candidate.keyword),
                    prompt=m.prompt,
                    source=",".join(m.candidate.sources) or "unknown",
                    intent=m.candidate.intent,
                    search_volume=m.candidate.search_volume if m.candidate.volume_known else None,
                    competition=m.candidate.competition,
                    cpc=m.candidate.cpc,
                    keyword_difficulty=m.candidate.keyword_difficulty,
                    status=m.status,
                    channels={
                        "organic": m.organic.model_dump(),
                        "ai_overview": m.ai_overview.model_dump(),
                        "ai_engines": [e.model_dump() for e in m.engines],
                    },
                    visibility=s.breakdown.visibility,
                    opportunity_score=s.breakdown.score,
                    score_breakdown=s.breakdown.model_dump(),
                    analysis={**judgements[s.ref], "source": source} if s.ref in judgements else None,
                    errors=[e.model_dump() for e in m.errors],
                )
                session.add(q)
                queries[s.ref] = q
            session.flush()
            for priority, r in enumerate(state.get("recommendations") or [], 1):
                if r.query_ref not in queries:
                    continue
                session.add(
                    Recommendation(
                        run_id=run_id,
                        brand_id=run.brand_id,
                        query_id=queries[r.query_ref].id,
                        priority=priority,
                        title=r.title,
                        content_type=r.content_type,
                        effort=r.effort,
                        target_channels=list(r.target_channels),
                        rationale=r.rationale,
                        outline=r.outline,
                        key_points=r.key_points,
                        sources_to_earn=r.sources_to_earn,
                        source=state.get("recommendations_source") or "llm",
                    )
                )
            run.status = status.value
            run.report = report
            run.visibility_score = (report.get("summary") or {}).get("visibility_score")
            run.degradations = degradations
            run.cost_usd = round(ctx.budget.spent_usd, 5)
            run.metrics = metrics.summary()
            run.finished_at = utcnow()
            run.current_node = None
            if status is RunStatus.FAILED:
                run.error = failure_reason(metrics)
            session.commit()
        return status.value

    @staticmethod
    def _mark_failed(run_id: str, error: dict[str, Any], metrics: RunMetrics) -> None:
        with Session(get_engine()) as session:
            session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(
                    status=RunStatus.FAILED.value,
                    error=error,
                    finished_at=utcnow(),
                    current_node=None,
                    metrics=metrics.summary(),
                )
            )
            session.commit()

    @staticmethod
    def recover_interrupted() -> int:
        """Runs left queued/running by a previous process can never finish; say so explicitly."""
        with Session(get_engine()) as session:
            result = session.execute(
                update(Run)
                .where(Run.status.in_(ACTIVE))
                .values(
                    status=RunStatus.FAILED.value,
                    finished_at=utcnow(),
                    current_node=None,
                    error={
                        "code": "interrupted",
                        "message": "The service restarted while this run was in progress. Retry it; completed provider calls are cached.",
                    },
                )
            )
            session.commit()
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def shutdown(self, *, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)
