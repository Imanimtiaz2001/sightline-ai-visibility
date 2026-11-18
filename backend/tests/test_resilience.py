"""Retries, error classification, circuit breaking, caching and graceful degradation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from flask.testing import FlaskClient

from app.config import Settings
from app.container import FIXTURE_DIR, Container
from app.core.budget import CostBudget
from app.core.cache import ResponseCache
from app.core.call_context import CallContext
from app.core.errors import AuthError, BudgetExceededError, CircuitOpenError, PermanentError, TransientError
from app.core.resilience import BreakerRegistry, BreakerState, CircuitBreaker, RetryPolicy, retry_call
from app.db.base import create_schema, init_engine
from app.observability.metrics import RunMetrics
from app.providers.dataforseo.client import DataForSEOClient
from app.providers.dataforseo.fixtures import FixtureTransport
from tests.conftest import wait_for_run
from tests.fakes import FailingTransport, SequenceTransport, ok_envelope, task_error

RESULT = [{"items": [{"keyword": "crm", "keyword_info": {"search_volume": 100}}]}]


@pytest.fixture
def ctx() -> CallContext:
    return CallContext(run_id="r", budget=CostBudget(1.0), metrics=RunMetrics(), location_code=2840, language_code="en")


@pytest.fixture
def make_client(tmp_path: Path) -> Callable[..., tuple[DataForSEOClient, list[float]]]:
    init_engine(f"sqlite:///{tmp_path / 'cache.db'}")
    create_schema()

    def build(transport: Any, *, threshold: int = 5) -> tuple[DataForSEOClient, list[float]]:
        sleeps: list[float] = []
        client = DataForSEOClient(
            transport,
            cache=ResponseCache(),
            breakers=BreakerRegistry(threshold, 30),
            policy=RetryPolicy(4, 0.5, 8),
            connect_timeout_s=1,
            read_timeout_s=1,
            max_concurrency=2,
            sleep=sleeps.append,
        )
        return client, sleeps

    return build


def call(client: DataForSEOClient, ctx: CallContext, task: dict[str, Any] | None = None, **kw: Any) -> Any:
    return client.call(
        "labs.keyword_suggestions",
        "x/live",
        task or {"keyword": "crm"},
        ctx=ctx,
        ttl=timedelta(hours=1),
        estimate_usd=0.02,
        **kw,
    )


def test_transient_failures_are_retried_with_capped_jittered_backoff(make_client: Any, ctx: CallContext) -> None:
    transport = SequenceTransport(
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.ReadTimeout("slow"),
        task_error(50301, "third-party API unavailable"),
        ok_envelope(RESULT, cost=0.03),
    )
    client, sleeps = make_client(transport)
    result = call(client, ctx)
    assert result.attempts == 4 and result.result == RESULT and not result.cached
    assert len(sleeps) == 3
    assert sleeps[0] >= 1.0  # Retry-After honoured
    assert all(0 <= s <= 8 for s in sleeps)
    assert ctx.metrics.retries == 3
    assert ctx.budget.spent_usd == pytest.approx(0.03)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401), AuthError),
        (httpx.Response(400), PermanentError),
        (task_error(40501, "Invalid Field"), PermanentError),
        (task_error(40210, "Insufficient funds"), PermanentError),
    ],
)
def test_non_retryable_errors_fail_immediately(
    make_client: Any, ctx: CallContext, response: httpx.Response, error: type
) -> None:
    transport = SequenceTransport(response)
    client, sleeps = make_client(transport)
    with pytest.raises(error):
        call(client, ctx)
    assert len(transport.requests) == 1 and sleeps == []


def test_junk_body_is_treated_as_transient(make_client: Any, ctx: CallContext) -> None:
    transport = SequenceTransport(httpx.Response(200, text="<html>proxy error</html>"), ok_envelope(RESULT))
    client, _ = make_client(transport)
    assert call(client, ctx).attempts == 2


def test_no_results_is_an_answer_not_an_error(make_client: Any, ctx: CallContext) -> None:
    client, _ = make_client(SequenceTransport(task_error(40102, "No Search Results")))
    assert call(client, ctx).result == []


def test_exhausted_retries_raise_transient_with_attempt_count(make_client: Any, ctx: CallContext) -> None:
    client, sleeps = make_client(SequenceTransport(httpx.Response(503)))
    with pytest.raises(TransientError) as info:
        call(client, ctx)
    assert info.value.attempts == 4 and len(sleeps) == 3  # type: ignore[attr-defined]


def test_cache_prevents_paying_twice(make_client: Any, ctx: CallContext) -> None:
    transport = SequenceTransport(ok_envelope(RESULT, cost=0.05))
    client, _ = make_client(transport)
    first, second = call(client, ctx), call(client, ctx)
    assert not first.cached and second.cached and second.cost_usd == 0
    assert len(transport.requests) == 1
    assert ctx.budget.spent_usd == pytest.approx(0.05)
    third = call(client, ctx, read_cache=False)  # a re-measure bypasses the cached read
    assert not third.cached and len(transport.requests) == 2


def test_budget_ceiling_blocks_call_before_it_is_made(make_client: Any) -> None:
    transport = SequenceTransport(ok_envelope(RESULT))
    client, _ = make_client(transport)
    tight = CallContext(
        run_id="r", budget=CostBudget(0.01), metrics=RunMetrics(), location_code=2840, language_code="en"
    )
    with pytest.raises(BudgetExceededError):
        call(client, tight)
    assert transport.requests == []


def test_circuit_breaker_opens_fails_fast_and_recovers() -> None:
    now = [0.0]
    breaker = CircuitBreaker("dep", failure_threshold=3, reset_timeout_s=10, clock=lambda: now[0])

    def boom() -> None:
        raise TransientError("down", dependency="dep", operation="op")

    def bad_request() -> None:
        raise PermanentError("400", dependency="dep", operation="op")

    for _ in range(5):  # our own bad requests say nothing about the dependency's health
        with pytest.raises(PermanentError):
            breaker.call(bad_request, operation="op")
    assert breaker.state is BreakerState.CLOSED

    for _ in range(3):
        with pytest.raises(TransientError):
            breaker.call(boom, operation="op")
    assert breaker.state is BreakerState.OPEN
    calls = []
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: calls.append(1), operation="op")
    assert calls == []

    now[0] = 11
    assert breaker.state is BreakerState.HALF_OPEN
    breaker.call(lambda: None, operation="op")
    assert breaker.state is BreakerState.CLOSED


def test_open_circuit_is_not_retried() -> None:
    sleeps: list[float] = []

    def open_circuit() -> None:
        raise CircuitOpenError("open", dependency="d", operation="o")

    with pytest.raises(CircuitOpenError):
        retry_call(open_circuit, policy=RetryPolicy(5, 1, 2), dependency="d", operation="o", sleep=sleeps.append)
    assert sleeps == []


# --- graceful degradation of the whole DAG -------------------------------------------------------


def _app_with(settings: Settings, transport: Any, backend: Any) -> Any:
    from app import create_app

    container = Container.build(settings, transport=transport, backend=backend, sleep=lambda _: None)
    return create_app(settings, container)


def test_ai_engine_outage_yields_partial_run_with_organic_results(settings: Settings, backend: Any) -> None:
    """Perplexity is down for the whole run: retries exhaust, the breaker opens, the run still delivers."""
    inner = FixtureTransport(FIXTURE_DIR / "scenario.json", inject_faults=False)
    transport = FailingTransport(
        inner, lambda path, _: "perplexity" in path, task_error(50301, "Third-party API unavailable")
    )
    app = _app_with(settings, transport, backend)
    client = app.test_client()
    brand_id = client.post("/api/v1/brands", json=_brand()).json["id"]
    run = wait_for_run(client, client.post(f"/api/v1/brands/{brand_id}/runs", json={}).json["id"])

    assert run["status"] == "partial"
    assert any("Perplexity unavailable" in d["message"] for d in run["degradations"])
    queries = client.get(f"/api/v1/runs/{run['id']}/queries").json["items"]
    assert queries and all(q["status"] == "partial" for q in queries)
    for q in queries:
        engines = {e["engine"]: e["status"] for e in q["channels"]["ai_engines"]}
        assert engines == {"chat_gpt": "ok", "perplexity": "failed"}
        assert q["channels"]["organic"]["status"] == "ok"
        assert q["errors"] and q["errors"][0]["tool"] == "perplexity_answer"
    assert client.get(f"/api/v1/runs/{run['id']}/recommendations").json  # downstream stages kept working
    app.extensions["run_service"].shutdown(wait=True)

    # Retrying the partial run re-uses every successful paid call from the cache.
    inner.requests.clear()
    transport.predicate = lambda *_: False
    app = _app_with(settings, transport, backend)
    client = app.test_client()
    retry = wait_for_run(client, client.post(f"/api/v1/runs/{run['id']}/retry").json["id"])
    assert retry["status"] == "succeeded"
    paid = [path for path, _ in inner.requests]
    assert paid and all("perplexity" in p for p in paid)
    app.extensions["run_service"].shutdown(wait=True)


def test_llm_outage_falls_back_to_rules_end_to_end(client: FlaskClient, brand_id: str, backend: Any) -> None:
    timeout = type("APITimeoutError", (Exception,), {})("request timed out")
    for agent in ("discovery_planner", "measurement_planner", "gap_analyst", "recommender"):
        backend.script(agent, *([timeout] * 8))
    run = wait_for_run(client, client.post(f"/api/v1/brands/{brand_id}/runs", json={}).json["id"])

    assert run["status"] == "partial"
    sources = run["report"]["data_quality"]
    assert (sources["plan_source"], sources["analysis_source"], sources["recommendations_source"]) == (
        "fallback",
        "rule",
        "rule",
    )
    kinds = {d["node"]: d["kind"] for d in run["degradations"]}
    assert {"discovery_planner", "measurement_planner", "gap_analyst", "recommender"} <= set(kinds)
    assert run["visibility_score"] is not None  # live measurement still happened
    assert client.get(f"/api/v1/runs/{run['id']}/recommendations").json


def test_auth_failure_is_explained_to_the_operator(settings: Settings, backend: Any) -> None:
    """A wrong API key must not read as 'no results found'."""
    app = _app_with(settings, SequenceTransport(httpx.Response(401)), backend)
    client = app.test_client()
    brand_id = client.post("/api/v1/brands", json=_brand()).json["id"]
    run = wait_for_run(client, client.post(f"/api/v1/brands/{brand_id}/runs", json={}).json["id"])
    assert run["status"] == "failed"
    assert run["error"]["code"] == "auth_failed"
    assert "DATAFORSEO_LOGIN" in run["error"]["message"]
    app.extensions["run_service"].shutdown(wait=True)


def test_total_provider_outage_fails_cleanly(settings: Settings, backend: Any) -> None:
    transport = SequenceTransport(httpx.Response(503))
    app = _app_with(settings, transport, backend)
    client = app.test_client()
    brand_id = client.post("/api/v1/brands", json=_brand()).json["id"]
    run = wait_for_run(client, client.post(f"/api/v1/brands/{brand_id}/runs", json={}).json["id"])
    assert run["status"] == "failed"
    assert run["error"]["code"] == "transient"  # the dominant provider failure, not a vague "no results"
    assert "could not be reached" in run["error"]["message"]
    assert any(d["kind"] == "fallback" for d in run["degradations"])  # discovery fell back to profile topics
    assert run["report"]["markdown"]  # the user still gets an explanation
    readiness = client.get("/health/ready").json
    assert any(dep["state"] == "open" for dep in readiness["checks"]["dependencies"])
    app.extensions["run_service"].shutdown(wait=True)


def _brand() -> dict[str, Any]:
    from tests.conftest import BRAND

    return BRAND
