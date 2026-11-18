"""DataForSEO HTTP client.

Responsibilities, in call order:

    cache lookup (single-flight) -> budget reservation -> retry loop
        -> circuit breaker -> concurrency gate -> HTTP attempt -> classification

Everything about *what* to ask lives in `tools.py`; this module only knows
*how* to ask DataForSEO safely and how to read its two-level status codes
(HTTP status + `status_code` at response and task level).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

import httpx

from app.core.cache import ResponseCache, cache_key
from app.core.call_context import CallContext
from app.core.errors import (
    AuthError,
    ExternalError,
    PermanentError,
    QuotaError,
    RateLimitedError,
    TransientError,
)
from app.core.resilience import BreakerRegistry, RetryOutcome, RetryPolicy, retry_call
from app.observability.logging import get_logger
from app.observability.metrics import EXTERNAL_CALLS, EXTERNAL_LATENCY, PROVIDER_COST

log = get_logger(__name__)
DEPENDENCY = "dataforseo"

OK = 20000
# Task-level codes that are *answers*, not failures.
NO_RESULTS = 40102  # "no search results" – a legitimate empty answer
PARTIAL_RESULTS = 40106  # some pages missing (not charged for them)

AUTH_CODES = {40100, 40104, 40207}
QUOTA_CODES = {40200, 40201, 40203, 40210}
RATE_CODES = {40202, 40209}
TRANSIENT_CODES = {40101, 40103, 40107, 50000, 50001, 50301, 50303, 50304, 50401, 50402}


def classify_status(code: int, message: str, operation: str) -> ExternalError:
    """Map a DataForSEO internal status code to our error taxonomy."""
    kwargs: dict[str, Any] = {"dependency": DEPENDENCY, "operation": operation, "provider_code": code}
    text = f"DataForSEO {code}: {message}"
    if code in AUTH_CODES:
        return AuthError(text, **kwargs)
    if code in QUOTA_CODES:
        return QuotaError(text, **kwargs)
    if code in RATE_CODES:
        return RateLimitedError(text, retry_after_s=2.0, **kwargs)
    if code in TRANSIENT_CODES or code >= 50000:
        return TransientError(text, **kwargs)
    return PermanentError(text, **kwargs)


def classify_http(response: httpx.Response, operation: str) -> ExternalError | None:
    status = response.status_code
    if status < 400:
        return None
    kwargs: dict[str, Any] = {"dependency": DEPENDENCY, "operation": operation, "status": status}
    if status == 401:
        return AuthError("DataForSEO rejected the credentials (HTTP 401)", **kwargs)
    if status == 402:
        return QuotaError("DataForSEO account needs funds (HTTP 402)", **kwargs)
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        return RateLimitedError(
            "DataForSEO rate limit (HTTP 429)",
            retry_after_s=float(retry_after) if retry_after and retry_after.isdigit() else None,
            **kwargs,
        )
    if status >= 500 or status == 408:
        return TransientError(f"DataForSEO server error (HTTP {status})", **kwargs)
    return PermanentError(f"DataForSEO rejected the request (HTTP {status})", **kwargs)


class Transport(Protocol):
    def post(self, path: str, payload: list[dict[str, Any]], timeout: httpx.Timeout) -> httpx.Response: ...


class HttpTransport:
    def __init__(self, base_url: str, login: str, password: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            auth=(login, password),
            headers={"Content-Type": "application/json", "User-Agent": "ai-visibility-platform/1.0"},
        )

    def post(self, path: str, payload: list[dict[str, Any]], timeout: httpx.Timeout) -> httpx.Response:
        return self._client.post(path.lstrip("/"), json=payload, timeout=timeout)


@dataclass
class ProviderResult:
    operation: str
    result: list[dict[str, Any]]
    cost_usd: float
    cached: bool
    attempts: int
    task_status: int
    notes: list[str] = field(default_factory=list)


class DataForSEOClient:
    def __init__(
        self,
        transport: Transport,
        *,
        cache: ResponseCache,
        breakers: BreakerRegistry,
        policy: RetryPolicy,
        connect_timeout_s: float,
        read_timeout_s: float,
        max_concurrency: int,
        sleep: Any = time.sleep,
    ) -> None:
        self._transport = transport
        self._cache = cache
        # One breaker per endpoint family: DataForSEO's SERP, Labs and each AI-answer
        # backend fail independently, so a Perplexity outage must not block SERP calls.
        self._breakers = breakers
        self._policy = policy
        self._connect_timeout_s = connect_timeout_s
        self._default_read_timeout_s = read_timeout_s
        self._gate = threading.BoundedSemaphore(max_concurrency)
        self._sleep = sleep

    def call(
        self,
        operation: str,
        path: str,
        task: dict[str, Any],
        *,
        ctx: CallContext,
        ttl: timedelta,
        estimate_usd: float,
        read_timeout_s: float | None = None,
        read_cache: bool = True,
    ) -> ProviderResult:
        key = cache_key(operation, task)
        with self._cache.single_flight(key):
            cached = self._cache.get(key, operation) if read_cache else None
            if cached is not None:
                ctx.metrics.cache_hit()
                ctx.metrics.api_call(operation, "cache_hit")
                log.info("provider_cache_hit", operation=operation, cache_key=key[:12])
                return ProviderResult(
                    operation, cached["result"], 0.0, True, 0, cached.get("task_status", OK), ["served from cache"]
                )

            ctx.budget.reserve(estimate_usd, operation=operation)
            timeout = httpx.Timeout(
                connect=self._connect_timeout_s,
                read=read_timeout_s or self._default_read_timeout_s,
                write=10.0,
                pool=10.0,
            )
            outcome = RetryOutcome()
            actual_cost = 0.0
            try:
                result, actual_cost, task_status = retry_call(
                    lambda: self._breakers.get(f"{DEPENDENCY}:{operation}").call(
                        lambda: self._attempt(operation, path, task, timeout, ctx), operation=operation
                    ),
                    policy=self._policy,
                    dependency=DEPENDENCY,
                    operation=operation,
                    on_retry=lambda *_: ctx.metrics.retry(),
                    sleep=self._sleep,
                    outcome=outcome,
                )
            except ExternalError as exc:
                exc.attempts = outcome.attempts  # type: ignore[attr-defined]
                raise
            finally:
                ctx.budget.settle(estimate_usd, actual_cost)
            ctx.metrics.spend(actual_cost)

            self._cache.put(
                key,
                operation,
                task,
                {"result": result, "task_status": task_status},
                ttl=ttl,
                cost_usd=actual_cost,
            )
            notes = ["provider returned partial results"] if task_status == PARTIAL_RESULTS else []
            return ProviderResult(operation, result, actual_cost, False, outcome.attempts, task_status, notes)

    def _attempt(
        self, operation: str, path: str, task: dict[str, Any], timeout: httpx.Timeout, ctx: CallContext
    ) -> tuple[list[dict[str, Any]], float, int]:
        started = time.perf_counter()
        outcome_label = "error"
        try:
            with self._gate:
                try:
                    response = self._transport.post(path, [task], timeout)
                except httpx.TimeoutException as exc:
                    raise TransientError(
                        f"DataForSEO timed out ({type(exc).__name__})", dependency=DEPENDENCY, operation=operation
                    ) from exc
                except httpx.TransportError as exc:
                    raise TransientError(
                        f"DataForSEO connection failed ({type(exc).__name__})",
                        dependency=DEPENDENCY,
                        operation=operation,
                    ) from exc
            http_error = classify_http(response, operation)
            if http_error:
                raise http_error
            result, cost, task_status = self._parse(response, operation)
            outcome_label = "ok"
            return result, cost, task_status
        except ExternalError as exc:
            outcome_label = exc.code
            raise
        finally:
            elapsed = time.perf_counter() - started
            EXTERNAL_LATENCY.labels(DEPENDENCY, operation).observe(elapsed)
            EXTERNAL_CALLS.labels(DEPENDENCY, operation, outcome_label).inc()
            ctx.metrics.api_call(operation, outcome_label)
            log.info(
                "external_call",
                dependency=DEPENDENCY,
                operation=operation,
                outcome=outcome_label,
                duration_ms=round(elapsed * 1000, 1),
            )

    @staticmethod
    def _parse(response: httpx.Response, operation: str) -> tuple[list[dict[str, Any]], float, int]:
        """Read the envelope defensively: providers return junk sometimes."""
        try:
            body = response.json()
        except ValueError as exc:
            raise TransientError(
                "DataForSEO returned a non-JSON body", dependency=DEPENDENCY, operation=operation
            ) from exc
        if not isinstance(body, dict):
            raise TransientError("DataForSEO returned an unexpected body", dependency=DEPENDENCY, operation=operation)

        top_code = int(body.get("status_code") or 0)
        if top_code != OK:
            raise classify_status(top_code, str(body.get("status_message", "")), operation)

        tasks = body.get("tasks")
        if not isinstance(tasks, list) or not tasks or not isinstance(tasks[0], dict):
            raise TransientError("DataForSEO response had no task", dependency=DEPENDENCY, operation=operation)
        task = tasks[0]
        task_code = int(task.get("status_code") or 0)
        cost = float(task.get("cost") or 0.0)
        if task_code not in {OK, NO_RESULTS, PARTIAL_RESULTS}:
            raise classify_status(task_code, str(task.get("status_message", "")), operation)

        PROVIDER_COST.labels(operation).inc(cost)
        raw_result = task.get("result")
        result = [r for r in raw_result if isinstance(r, dict)] if isinstance(raw_result, list) else []
        return result, cost, task_code
