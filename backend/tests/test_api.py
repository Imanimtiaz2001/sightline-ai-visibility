"""API contract: validation, status codes, idempotency, concurrency guards, observability hooks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask.testing import FlaskClient

from app.config import Settings
from tests.conftest import BRAND


def test_validation_errors_are_problem_json_with_field_paths(client: FlaskClient) -> None:
    bad = {
        **BRAND,
        "domain": "not a domain",
        "competitors": [{"name": "X", "domain": "tasklane.io"}],
        "location_code": 1,
    }
    response = client.post("/api/v1/brands", json=bad)
    assert response.status_code == 422
    assert response.mimetype == "application/problem+json"
    body = response.json
    assert body["title"] == "Validation failed" and body["request_id"]
    assert any(e["field"] == "domain" for e in body["errors"])


def test_cross_field_rules(client: FlaskClient) -> None:
    same_domain = {**BRAND, "competitors": [{"name": "Clone", "domain": "https://www.tasklane.io/pricing"}]}
    assert client.post("/api/v1/brands", json=same_domain).status_code == 422
    wrong_language = {**BRAND, "location_code": 2276, "language_code": "ur"}
    assert client.post("/api/v1/brands", json=wrong_language).status_code == 422
    assert client.post("/api/v1/brands", json={**BRAND, "unexpected": True}).status_code == 422


def test_media_type_and_not_found(client: FlaskClient) -> None:
    assert client.post("/api/v1/brands", data="name=x").status_code == 415
    assert client.get("/api/v1/brands/nope").status_code == 404
    assert client.get("/api/v1/runs/nope").json["status"] == 404


def test_duplicate_brand_conflicts(client: FlaskClient, brand_id: str) -> None:
    assert client.post("/api/v1/brands", json=BRAND).status_code == 409


def test_run_creation_is_idempotent_and_single_flight_per_brand(client: FlaskClient, brand_id: str) -> None:
    headers = {"Idempotency-Key": "launch-123"}
    first = client.post(f"/api/v1/brands/{brand_id}/runs", json={}, headers=headers)
    replay = client.post(f"/api/v1/brands/{brand_id}/runs", json={}, headers=headers)
    assert first.status_code == 202 and first.headers["Location"].endswith(first.json["id"])
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json["id"] == first.json["id"]
    if first.json["status"] in ("queued", "running"):
        assert client.post(f"/api/v1/brands/{brand_id}/runs", json={}).status_code in (202, 409)


def test_request_id_is_propagated(client: FlaskClient) -> None:
    response = client.get("/api/v1/brands", headers={"X-Request-ID": "browser-req-0001"})
    assert response.headers["X-Request-ID"] == "browser-req-0001"
    assert client.get("/api/v1/brands", headers={"X-Request-ID": "bad id!"}).headers["X-Request-ID"] != "bad id!"


def test_filters_exports_and_recommendation_workflow(
    client: FlaskClient, run_analysis: Callable[..., dict[str, Any]]
) -> None:
    run = run_analysis()
    page = client.get(f"/api/v1/runs/{run['id']}/queries?missing_in=ai_answers&sort=-volume&limit=2").json
    assert page["limit"] == 2 and page["facets"]["missing_in"]["ai_answers"] == page["total"]
    volumes = [q["search_volume"] or 0 for q in page["items"]]
    assert volumes == sorted(volumes, reverse=True)
    assert client.get(f"/api/v1/runs/{run['id']}/queries?sort=sideways").status_code == 422

    csv = client.get(f"/api/v1/runs/{run['id']}/export?format=csv")
    assert csv.status_code == 200 and csv.data.decode().startswith("rank,query,opportunity_score")
    assert "attachment" in csv.headers["Content-Disposition"]

    rec = client.get(f"/api/v1/runs/{run['id']}/recommendations").json[0]
    patched = client.patch(f"/api/v1/recommendations/{rec['id']}", json={"status": "in_progress"})
    assert patched.status_code == 200 and patched.json["status_updated_at"]
    assert client.patch(f"/api/v1/recommendations/{rec['id']}", json={"status": "shipped"}).status_code == 422
    assert client.get(f"/api/v1/runs/{run['id']}/recommendations?status=in_progress").json[0]["id"] == rec["id"]

    detail = client.get(f"/api/v1/queries/{page['items'][0]['id']}").json
    assert detail["history"] and detail["channels"]["organic"]["status"] == "ok"

    overview = client.get("/api/v1/overview").json
    assert overview["totals"]["brands"] == 1 and overview["brands"][0]["open_recommendations"] >= 1


def test_client_error_reports_are_accepted_and_rate_limited(client: FlaskClient) -> None:
    report = {
        "kind": "error",
        "message": "TypeError: x is undefined",
        "request_id": "browser-req-0001",
        "route": "/brands",
    }
    assert client.post("/api/v1/client-errors", json=report).status_code == 204
    statuses = {client.post("/api/v1/client-errors", json=report).status_code for _ in range(40)}
    assert 429 in statuses


def test_readiness_reports_missing_configuration(tmp_path: Any) -> None:
    from app import create_app

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'r.db'}",
        dataforseo_login="",  # type: ignore[call-arg]
        anthropic_api_key="",
    )  # type: ignore[arg-type]
    client = create_app(settings).test_client()
    ready = client.get("/health/ready")
    assert ready.status_code == 503 and ready.json["checks"]["configuration"]
    assert client.get("/health/live").status_code == 200
    brand = client.post("/api/v1/brands", json=BRAND).json["id"]
    assert client.post(f"/api/v1/brands/{brand}/runs", json={}).status_code == 503


def test_openapi_document_covers_every_operation(client: FlaskClient) -> None:
    spec = client.get("/api/openapi.json").json
    assert spec["openapi"] == "3.1.0"
    assert {"/api/v1/brands", "/api/v1/brands/{brand_id}/runs", "/api/v1/runs/{run_id}/events"} <= set(spec["paths"])
    create_run = spec["paths"]["/api/v1/brands/{brand_id}/runs"]["post"]
    assert {"202", "409", "422", "429"} <= set(create_run["responses"])
    assert "BrandIn" in spec["components"]["schemas"]
    assert "pipeline_node_duration_seconds" in client.get("/metrics").data.decode()
