"""HTTP API v1. Handlers are thin: validate (framework) -> service -> presenter."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from typing import Any, Literal

from flask import Response, current_app, g, request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.framework import ApiBlueprint, json_response
from app.api.schemas import (
    BrandIn,
    BrandList,
    BrandOut,
    ClientErrorIn,
    ComparisonParams,
    LocationOut,
    MetaOut,
    Overview,
    QueryDetail,
    QueryFilter,
    QueryPage,
    RecommendationFilter,
    RecommendationOut,
    RecommendationPatch,
    RunComparison,
    RunCreate,
    RunOut,
    RunPage,
    RunTrace,
)
from app.container import Container
from app.core.errors import ApiError, NotFound
from app.core.ratelimit import SlidingWindowLimiter
from app.db.base import SessionLocal, get_engine, utcnow
from app.db.models import ClientErrorReport, Recommendation, Run, RunStatus
from app.observability.logging import get_logger
from app.observability.metrics import CLIENT_ERRORS
from app.reference import LANGUAGE_NAMES, LOCATIONS
from app.services import brands as brand_service
from app.services import insights
from app.services.presenters import brand_out, recommendation_out, run_brief, run_out, step_out
from app.services.runs import RunService

bp = ApiBlueprint("api", __name__, url_prefix="/api/v1")
log = get_logger("api")
_client_error_limiter = SlidingWindowLimiter(limit=30, window_s=60)
VERSION = "1.0.0"


def _db() -> Session:
    return SessionLocal()


def _container() -> Container:
    return current_app.extensions["container"]  # type: ignore[no-any-return]


def _runs() -> RunService:
    return current_app.extensions["run_service"]  # type: ignore[no-any-return]


# --- reference & meta -------------------------------------------------------------------------


@bp.operation("/meta", "GET", summary="Service capabilities and configuration status", tag="System", response=MetaOut)
def get_meta() -> Response:
    s = _container().settings
    problems = s.configuration_problems()
    return json_response(
        MetaOut(
            version=VERSION,
            environment=s.app_env,
            data_mode=s.dataforseo_mode.value,
            synthetic_data=s.uses_synthetic_data,
            llm_provider=s.llm_provider.value,
            llm_model=s.resolved_llm_model,
            ai_engines=[{"engine": e, "model": m} for e, m in s.engine_models.items()],
            configured=not problems,
            configuration_problems=problems,
            limits={
                "max_queries_per_run": s.max_queries_per_run,
                "max_cost_per_run_usd": s.max_cost_per_run_usd,
                "max_competitors": 6,
                "max_seed_topics": 10,
            },
        )
    )


@bp.operation(
    "/reference/locations",
    "GET",
    summary="Markets a brand can be measured in",
    tag="System",
    response=list[LocationOut],
)
def get_locations() -> Response:
    return json_response(
        [
            LocationOut(
                code=loc["code"],
                name=loc["name"],
                iso=loc["iso"],
                languages=[{"code": c, "name": LANGUAGE_NAMES.get(c, c)} for c in loc["languages"]],
            )
            for loc in LOCATIONS
        ]
    )


# --- brands -----------------------------------------------------------------------------------


@bp.operation("/brands", "GET", summary="List tracked brands", tag="Brands", response=BrandList)
def list_brands() -> Response:
    items = [brand_out(b) for b in brand_service.list_brands(_db())]
    return json_response(BrandList(items=items, total=len(items)))


@bp.operation(
    "/brands",
    "POST",
    summary="Register a brand and its competitors",
    tag="Brands",
    body=BrandIn,
    response=BrandOut,
    status=201,
    errors=(409,),
)
def create_brand(body: BrandIn) -> Response:
    brand = brand_service.create_brand(_db(), body)
    return json_response(brand_out(brand), 201, {"Location": f"/api/v1/brands/{brand.id}"})


@bp.operation("/brands/<brand_id>", "GET", summary="Get a brand", tag="Brands", response=BrandOut, errors=(404,))
def get_brand(brand_id: str) -> Response:
    return json_response(brand_out(brand_service.get_brand(_db(), brand_id)))


@bp.operation(
    "/brands/<brand_id>",
    "PUT",
    summary="Replace a brand profile",
    tag="Brands",
    body=BrandIn,
    response=BrandOut,
    errors=(404, 409),
    description="Past runs keep the profile snapshot they measured, so editing never rewrites history.",
)
def update_brand(brand_id: str, body: BrandIn) -> Response:
    return json_response(brand_out(brand_service.update_brand(_db(), brand_id, body)))


@bp.operation(
    "/brands/<brand_id>",
    "DELETE",
    summary="Delete a brand and its history",
    tag="Brands",
    status=204,
    errors=(404, 409),
)
def delete_brand(brand_id: str) -> Response:
    brand_service.delete_brand(_db(), brand_id)
    return Response(status=204)


# --- runs ---------------------------------------------------------------------------------------


class Paging(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


@bp.operation(
    "/brands/<brand_id>/runs",
    "GET",
    summary="Run history for a brand (newest first)",
    tag="Runs",
    params=Paging,
    response=RunPage,
    errors=(404,),
)
def list_runs(brand_id: str, params: Paging) -> Response:
    session = _db()
    brand_service.get_brand(session, brand_id)
    total = session.scalar(select(func.count()).select_from(Run).where(Run.brand_id == brand_id)) or 0
    runs = session.scalars(
        select(Run)
        .where(Run.brand_id == brand_id)
        .order_by(Run.created_at.desc())
        .limit(params.limit)
        .offset(params.offset)
    )
    return json_response(
        RunPage(items=[run_brief(r) for r in runs], total=total, limit=params.limit, offset=params.offset)
    )


@bp.operation(
    "/brands/<brand_id>/runs",
    "POST",
    summary="Start an analysis",
    tag="Runs",
    body=RunCreate,
    response=RunOut,
    status=202,
    errors=(404, 409, 429, 503),
    description="Returns immediately with the queued run. Send an `Idempotency-Key` header to make retries safe: "
    "the same key returns the original run (200) instead of starting another. Only one run per brand at a time (409).",
    extra_responses={200: "Idempotent replay – the run created earlier with this Idempotency-Key"},
)
def create_run(brand_id: str, body: RunCreate) -> Response:
    key = request.headers.get("Idempotency-Key")
    if key is not None and not (1 <= len(key) <= 128):
        raise ApiError(422, "Validation failed", "Idempotency-Key must be 1-128 characters")
    session = _db()
    run, created = _runs().create(session, brand_id, question=body.question, idempotency_key=key)
    run = insights.get_run(session, run.id)
    headers = {"Location": f"/api/v1/runs/{run.id}"}
    if not created:
        headers["Idempotent-Replayed"] = "true"
    return json_response(run_out(run), 202 if created else 200, headers)


@bp.operation(
    "/runs/<run_id>",
    "GET",
    summary="Get a run with live stage progress and its report",
    tag="Runs",
    response=RunOut,
    errors=(404,),
)
def get_run(run_id: str) -> Response:
    return json_response(run_out(insights.get_run(_db(), run_id)))


@bp.operation(
    "/runs/<run_id>/retry",
    "POST",
    summary="Re-run with the same inputs (cached calls are free)",
    tag="Runs",
    response=RunOut,
    status=202,
    errors=(404, 409, 429, 503),
)
def retry_run(run_id: str) -> Response:
    session = _db()
    run = _runs().retry(session, run_id)
    return json_response(run_out(insights.get_run(session, run.id)), 202, {"Location": f"/api/v1/runs/{run.id}"})


@bp.operation(
    "/runs/<run_id>/events",
    "GET",
    summary="Server-sent events stream of run progress",
    tag="Runs",
    response_media="text/event-stream",
    errors=(404,),
    description="Emits `event: run` with the full RunOut JSON whenever progress changes, a `: keep-alive` comment "
    "every 15 s, and closes after the run reaches a terminal status. Clients may fall back to polling GET /runs/{id}.",
)
def run_events(run_id: str) -> Response:
    insights.get_run(_db(), run_id)
    request_id = g.get("request_id")

    def stream() -> Iterator[str]:
        last_digest, last_beat, started = "", time.monotonic(), time.monotonic()
        yield "retry: 3000\n\n"
        while time.monotonic() - started < 900:
            with Session(get_engine()) as session:
                run = session.get(Run, run_id, options=[selectinload(Run.steps)])
                if run is None:
                    return
                payload = run_out(run).model_dump_json()
                terminal = RunStatus(run.status).is_terminal
            digest = hashlib.sha256(payload.encode()).hexdigest()
            if digest != last_digest:
                last_digest = digest
                yield f"event: run\ndata: {payload}\n\n"
            if terminal:
                yield "event: end\ndata: {}\n\n"
                return
            if time.monotonic() - last_beat > 15:
                last_beat = time.monotonic()
                yield ": keep-alive\n\n"
            time.sleep(1.0)
        log.info("sse_stream_timeout", run_id=run_id, request_id=request_id)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@bp.operation(
    "/runs/<run_id>/trace",
    "GET",
    summary="Node-by-node trace of a run (for engineers)",
    tag="Observability",
    response=RunTrace,
    errors=(404,),
)
def run_trace(run_id: str) -> Response:
    run = insights.get_run(_db(), run_id)
    return json_response(
        RunTrace(
            run_id=run.id,
            trace_id=run.id,
            status=RunStatus(run.status),
            steps=[step_out(s) for s in run.steps],
            metrics=run.metrics,
            log_query=f'trace_id="{run.id}"',
        )
    )


@bp.operation(
    "/runs/<run_id>/queries",
    "GET",
    summary="Measured queries (opportunities) with filters",
    tag="Opportunities",
    params=QueryFilter,
    response=QueryPage,
    errors=(404,),
)
def list_queries(run_id: str, params: QueryFilter) -> Response:
    return json_response(insights.list_queries(_db(), run_id, params))


@bp.operation(
    "/queries/<query_id>",
    "GET",
    summary="One query with its full evidence and history across runs",
    tag="Opportunities",
    response=QueryDetail,
    errors=(404,),
)
def get_query(query_id: str) -> Response:
    return json_response(insights.query_detail(_db(), query_id))


@bp.operation(
    "/runs/<run_id>/comparison",
    "GET",
    summary="What changed versus an earlier run",
    tag="Runs",
    params=ComparisonParams,
    response=RunComparison,
    errors=(404,),
)
def compare(run_id: str, params: ComparisonParams) -> Response:
    return json_response(insights.compare_runs(_db(), run_id, params.baseline))


class ExportParams(BaseModel):
    format: Literal["csv", "json", "md"] = "csv"


@bp.operation(
    "/runs/<run_id>/export",
    "GET",
    summary="Download a run as CSV, JSON or Markdown",
    tag="Runs",
    params=ExportParams,
    response_media="text/csv",
    errors=(404, 409),
)
def export(run_id: str, params: ExportParams) -> Response:
    body, mimetype, filename = insights.export_run(_db(), run_id, params.format)
    return Response(body, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --- recommendations --------------------------------------------------------------------------------


@bp.operation(
    "/runs/<run_id>/recommendations",
    "GET",
    summary="Content recommendations of a run, by priority",
    tag="Recommendations",
    params=RecommendationFilter,
    response=list[RecommendationOut],
    errors=(404,),
)
def list_recommendations(run_id: str, params: RecommendationFilter) -> Response:
    session = _db()
    insights.get_run(session, run_id)
    stmt = (
        select(Recommendation)
        .where(Recommendation.run_id == run_id)
        .options(selectinload(Recommendation.query))
        .order_by(Recommendation.priority)
    )
    if params.status:
        stmt = stmt.where(Recommendation.status.in_([s.value for s in params.status]))
    return json_response([recommendation_out(r) for r in session.scalars(stmt)])


@bp.operation(
    "/recommendations/<rec_id>",
    "PATCH",
    summary="Track work on a recommendation",
    tag="Recommendations",
    body=RecommendationPatch,
    response=RecommendationOut,
    errors=(404,),
)
def patch_recommendation(rec_id: str, body: RecommendationPatch) -> Response:
    session = _db()
    rec = session.get(Recommendation, rec_id, options=[selectinload(Recommendation.query)])
    if rec is None:
        raise NotFound("recommendation", rec_id)
    if rec.status != body.status.value:
        rec.status, rec.status_updated_at = body.status.value, utcnow()
        session.commit()
        log.info("recommendation_status_changed", recommendation_id=rec_id, status=body.status.value)
    return json_response(recommendation_out(rec))


# --- portfolio ---------------------------------------------------------------------------------------


@bp.operation(
    "/overview", "GET", summary="Portfolio overview across every tracked brand", tag="Brands", response=Overview
)
def get_overview() -> Response:
    return json_response(insights.overview(_db()))


# --- browser error reports ---------------------------------------------------------------------------


@bp.operation(
    "/client-errors",
    "POST",
    summary="Report a browser-side error",
    tag="Observability",
    body=ClientErrorIn,
    status=204,
    errors=(429,),
)
def report_client_error(body: ClientErrorIn) -> Response:
    if not _client_error_limiter.allow(request.remote_addr or "unknown"):
        raise ApiError(429, "Too Many Requests", "error reporting rate limit reached", headers={"Retry-After": "60"})
    session = _db()
    session.add(
        ClientErrorReport(
            kind=body.kind,
            message=body.message,
            stack=body.stack,
            url=body.url,
            request_id=body.request_id,
            trace_id=body.trace_id,
            session_id=body.session_id,
            user_agent=(request.user_agent.string or "")[:400],
            release=body.release,
            context={**(body.context or {}), "route": body.route},
        )
    )
    session.commit()
    CLIENT_ERRORS.labels(body.kind).inc()
    payload: dict[str, Any] = body.model_dump(exclude={"stack"})
    log.error(
        "client_error",
        **{f"client_{k}": v for k, v in payload.items()},
        client_stack_head=(body.stack or "")[:500] or None,
    )
    return Response(status=204)
