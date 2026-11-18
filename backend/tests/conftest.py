from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.config import Settings
from app.container import FIXTURE_DIR, Container
from app.providers.dataforseo.fixtures import FixtureTransport
from tests.fakes import ScriptedBackend

BRAND = {
    "name": "Tasklane",
    "domain": "tasklane.io",
    "market": "project management software for agencies",
    "description": "Project management for creative and digital agencies.",
    "seed_topics": ["agency project management", "resource planning software"],
    "competitors": [
        {"name": "Boardwise", "domain": "boardwise.com"},
        {"name": "Sprintloop", "domain": "sprintloop.io"},
        {"name": "Planfield", "domain": "planfield.com"},
    ],
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        dataforseo_mode="fixtures",  # type: ignore[arg-type]
        llm_provider="fixtures",  # type: ignore[arg-type]
        log_level="WARNING",
        retry_base_delay_s=0.001,
        retry_max_delay_s=0.002,
        max_queries_per_run=6,
    )


@pytest.fixture
def backend() -> ScriptedBackend:
    return ScriptedBackend()


@pytest.fixture
def transport() -> FixtureTransport:
    return FixtureTransport(FIXTURE_DIR / "scenario.json", inject_faults=False)


@pytest.fixture
def app(settings: Settings, transport: Any, backend: ScriptedBackend) -> Iterator[Flask]:
    container = Container.build(settings, transport=transport, backend=backend, sleep=lambda _: None)
    flask_app = create_app(settings, container)
    flask_app.config["TESTING"] = True
    yield flask_app
    flask_app.extensions["run_service"].shutdown(wait=True)


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def brand_id(client: FlaskClient) -> str:
    response = client.post("/api/v1/brands", json=BRAND)
    assert response.status_code == 201, response.json
    return str(response.json["id"])  # type: ignore[index]


def wait_for_run(client: FlaskClient, run_id: str, timeout_s: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/runs/{run_id}").json
        assert body is not None
        if body["status"] in ("succeeded", "partial", "failed"):
            return body  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


@pytest.fixture
def run_analysis(client: FlaskClient, brand_id: str) -> Callable[..., dict[str, Any]]:
    def _run(question: str | None = None) -> dict[str, Any]:
        response = client.post(f"/api/v1/brands/{brand_id}/runs", json={"question": question} if question else {})
        assert response.status_code == 202, response.json
        return wait_for_run(client, response.json["id"])  # type: ignore[index]

    return _run
