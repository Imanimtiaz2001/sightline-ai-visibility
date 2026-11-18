"""Happy path: brand -> run -> every DAG node executes -> ranked, explained, persisted results."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask.testing import FlaskClient

from app.pipeline.graph import NODES

EXPECTED_NODES = set(NODES) - {"seed_fallback"}  # fallback only runs when discovery finds nothing


def test_full_run_produces_ranked_explained_results(
    client: FlaskClient, run_analysis: Callable[..., dict[str, Any]]
) -> None:
    run = run_analysis("How does Tasklane show up for 'best project management software'?")

    assert run["status"] == "succeeded", run["degradations"]
    assert run["degradations"] == []
    assert 0 <= run["visibility_score"] <= 100
    assert run["cost_usd"] > 0
    assert all(stage["status"] in ("ok", "skipped") for stage in run["stages"])

    # Trace: every node ran exactly once, except the parallel measurement branches (one per query).
    trace = client.get(f"/api/v1/runs/{run['id']}/trace").json
    nodes = [s["node"] for s in trace["steps"]]
    assert set(nodes) == EXPECTED_NODES
    queries = client.get(f"/api/v1/runs/{run['id']}/queries").json
    assert nodes.count("measurement_retriever") == queries["total"] == 6
    assert all(s["status"] == "ok" and s["duration_ms"] is not None for s in trace["steps"])

    # The research question's phrase was planned explicitly and measured.
    texts = [q["text"] for q in queries["items"]]
    assert "best project management software" in texts

    # Ranking is by opportunity score and every score carries its factors.
    scores = [q["opportunity_score"] for q in queries["items"]]
    assert scores == sorted(scores, reverse=True)
    for q in queries["items"]:
        factors = {f["name"]: f["value"] for f in q["score_breakdown"]["factors"]}
        product = (
            100
            * factors["demand"]
            * factors["gap"]
            * factors["urgency"]
            * factors["winnability"]
            * factors["intent_value"]
        )
        assert abs(product - q["opportunity_score"]) < 0.6
        assert q["analysis"]["source"] == "llm"

    # Recommendations target the top gaps and link back to their query.
    recs = client.get(f"/api/v1/runs/{run['id']}/recommendations").json
    assert recs and [r["priority"] for r in recs] == list(range(1, len(recs) + 1))
    query_ids = {q["id"] for q in queries["items"]}
    assert all(r["query_id"] in query_ids and r["source"] == "llm" for r in recs)

    # Run-level metrics summary: per-node latency/success and API call counts.
    metrics = run["metrics"]
    assert metrics["api_calls_total"] >= 6 * 3 + 2
    assert metrics["nodes"]["scorer"]["success_rate"] == 1.0
    assert "# AI visibility report" in run["report"]["markdown"]


def test_brand_profile_edits_do_not_rewrite_history(
    client: FlaskClient, brand_id: str, run_analysis: Callable[..., dict[str, Any]]
) -> None:
    first = run_analysis()
    brand = client.get(f"/api/v1/brands/{brand_id}").json
    edited = {
        k: brand[k]
        for k in ("name", "domain", "market", "description", "location_code", "language_code", "seed_topics")
    }
    edited["competitors"] = [{"name": "Boardwise", "domain": "boardwise.com"}]
    assert client.put(f"/api/v1/brands/{brand_id}", json=edited).status_code == 200

    second = run_analysis()
    assert len(client.get(f"/api/v1/runs/{first['id']}").json["profile"]["competitors"]) == 3
    assert len(second["profile"]["competitors"]) == 1
    history = client.get(f"/api/v1/brands/{brand_id}/runs").json
    assert [r["id"] for r in history["items"]] == [second["id"], first["id"]]


def test_remeasure_shows_what_changed(client: FlaskClient, run_analysis: Callable[..., dict[str, Any]]) -> None:
    run_analysis()
    second = run_analysis()  # the synthetic scenario improves a few queries on re-measure
    comparison = client.get(f"/api/v1/runs/{second['id']}/comparison").json
    assert comparison["baseline"] is not None
    assert comparison["improved"] >= 1
    improved = [q for q in comparison["queries"] if q["change"] == "improved"]
    assert all(q["current_visibility"] > q["baseline_visibility"] for q in improved)
