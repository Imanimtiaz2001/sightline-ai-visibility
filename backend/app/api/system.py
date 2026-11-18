"""Health checks, Prometheus metrics and API documentation."""

from __future__ import annotations

from flask import Response, current_app
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.framework import ApiBlueprint, json_response
from app.api.openapi import DOCS_HTML, build_spec
from app.api.routes import VERSION
from app.api.schemas import Health
from app.container import Container
from app.db.base import get_engine
from app.observability.metrics import REGISTRY

system = ApiBlueprint("system", __name__)


@system.operation("/health/live", "GET", summary="Liveness: the process is up", tag="System", response=Health)
def live() -> Response:
    return json_response(Health(status="ok", checks={}))


@system.operation(
    "/health/ready",
    "GET",
    summary="Readiness: database reachable, configuration present, dependency breakers",
    tag="System",
    response=Health,
    errors=(503,),
    description="503 when the database is unreachable or required configuration is missing. An open circuit breaker "
    "reports `degraded` (200): the service still serves history and can run fallbacks.",
)
def ready() -> Response:
    container: Container = current_app.extensions["container"]
    checks: dict[str, object] = {}
    status = "ok"
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - exercised manually
        checks["database"] = f"error: {type(exc).__name__}"
        status = "unavailable"
    problems = container.settings.configuration_problems()
    checks["configuration"] = problems or "ok"
    if problems:
        status = "unavailable"
    breakers = container.breakers.snapshot()
    checks["dependencies"] = breakers
    if status == "ok" and any(b["state"] != "closed" for b in breakers):
        status = "degraded"
    checks["data_mode"] = container.settings.dataforseo_mode.value
    return json_response(Health(status=status, checks=checks), 503 if status == "unavailable" else 200)


@system.route("/metrics")
def metrics() -> Response:
    return Response(generate_latest(REGISTRY), mimetype=CONTENT_TYPE_LATEST)


@system.route("/api/openapi.json")
def openapi() -> Response:
    return json_response(build_spec(VERSION))


@system.route("/api/docs")
def docs() -> Response:
    return Response(DOCS_HTML, mimetype="text/html")
