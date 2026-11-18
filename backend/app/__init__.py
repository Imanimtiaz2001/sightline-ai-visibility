"""Flask application factory."""

from __future__ import annotations

import re
import time

import structlog
from flask import Flask, Response, g, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from app.api.framework import problem
from app.config import Settings, get_settings
from app.container import Container
from app.core.errors import ApiError
from app.db.base import SessionLocal, create_schema, init_engine
from app.observability.context import bind, new_id
from app.observability.error_reporting import capture_exception, init_error_reporting
from app.observability.logging import configure_logging, get_logger
from app.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
log = get_logger("http")


def create_app(settings: Settings | None = None, container: Container | None = None) -> Flask:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    init_engine(settings.database_url)
    create_schema()
    from app.api.routes import VERSION, bp
    from app.api.system import system
    from app.services.runs import RunService

    init_error_reporting(
        settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else None, settings.app_env, VERSION
    )
    container = container or Container.build(settings)
    run_service = RunService(container)
    interrupted = run_service.recover_interrupted()
    if interrupted:
        log.warning("interrupted_runs_marked_failed", count=interrupted)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_request_bytes
    app.json.sort_keys = False  # type: ignore[attr-defined]
    app.extensions["container"] = container
    app.extensions["run_service"] = run_service
    CORS(
        app,
        resources={r"/api/*": {"origins": settings.cors_origins}},
        expose_headers=["X-Request-ID", "Location", "Idempotent-Replayed", "Retry-After", "Content-Disposition"],
        allow_headers=["Content-Type", "X-Request-ID", "Idempotency-Key", "X-Client-Session"],
    )
    app.register_blueprint(bp)
    app.register_blueprint(system)
    _install_request_hooks(app)
    _install_error_handlers(app)
    log.info(
        "service_started",
        env=settings.app_env,
        data_mode=settings.dataforseo_mode.value,
        llm_provider=settings.llm_provider.value,
        llm_model=settings.resolved_llm_model,
        configuration_problems=settings.configuration_problems() or None,
    )
    return app


def _install_request_hooks(app: Flask) -> None:
    @app.before_request
    def _start() -> None:
        structlog.contextvars.clear_contextvars()
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _REQUEST_ID.match(incoming) else new_id()
        g.started = time.perf_counter()
        bind(request_id=g.request_id, client_session=request.headers.get("X-Client-Session"))

    @app.after_request
    def _finish(response: Response) -> Response:
        response.headers["X-Request-ID"] = g.get("request_id", "")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.path.startswith("/api/v1"):
            response.headers.setdefault("Cache-Control", "no-store")
        route = request.url_rule.rule if request.url_rule else "unmatched"
        elapsed = time.perf_counter() - g.get("started", time.perf_counter())
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, route).observe(elapsed)
        if route not in ("/metrics", "/health/live"):
            log.info(
                "http_request",
                method=request.method,
                route=route,
                path=request.path,
                status=response.status_code,
                duration_ms=round(elapsed * 1000, 1),
            )
        return response

    @app.teardown_request
    def _cleanup(_: BaseException | None) -> None:
        SessionLocal.remove()


def _install_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError) -> Response:
        return problem(exc.status, exc.title, exc.detail, errors=exc.errors, headers=exc.headers)

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException) -> Response:
        return problem(exc.code or 500, exc.name, exc.description)

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception) -> Response:
        log.exception("unhandled_exception", error=str(exc))
        capture_exception(exc)
        SessionLocal.rollback()
        return problem(
            500, "Internal Server Error", "Something went wrong on our side. Quote the request id when reporting this."
        )
