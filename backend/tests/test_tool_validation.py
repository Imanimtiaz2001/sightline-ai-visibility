"""LLM tool calls are validated (schema + semantics) before any paid API call is made."""

from __future__ import annotations

from typing import Any

import pytest
from flask.testing import FlaskClient

from app.config import Settings
from app.providers.dataforseo.tools import RejectedCall, ToolRegistry, ValidatedCall, validate_tool_call
from tests.conftest import wait_for_run
from tests.fakes import ScriptedBackend, tool_message


@pytest.fixture
def tools(settings: Settings) -> dict[str, Any]:
    registry = ToolRegistry(settings)
    return {s.name: s for s in [*registry.by_phase("discovery"), *registry.by_phase("measurement")]}


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"name": "google_serp", "args": {"query_ref": "q1"}}, "keyword"),  # missing required field
        ({"name": "google_serp", "args": {"query_ref": "q1", "keyword": "crm", "depth": 500}}, "depth"),  # out of range
        ({"name": "google_serp", "args": {"query_ref": "first", "keyword": "crm"}}, "query_ref"),  # bad pattern
        (
            {"name": "keyword_suggestions", "args": {"seed_keyword": "crm", "location_code": 2840}},
            "location_code",
        ),  # LLM may not choose market
        (
            {"name": "keyword_suggestions", "args": {"seed_keyword": "site:competitor.com crm"}},
            "seed_keyword",
        ),  # operators cost 5x
        ({"name": "search_volume", "args": {"keywords": []}}, "keywords"),
        ({"name": "chat_gpt_answer", "args": {"query_ref": "q1", "prompt": "hi"}}, "prompt"),
    ],
)
def test_invalid_arguments_are_rejected_with_field_level_reasons(
    tools: dict[str, Any], raw: dict[str, Any], field: str
) -> None:
    outcome = validate_tool_call({"id": "c1", **raw}, tools)
    assert isinstance(outcome, RejectedCall)
    assert any(field in ".".join(map(str, e["loc"])) for e in outcome.errors), outcome.errors
    assert field in outcome.feedback()


def test_unknown_tool_and_malformed_json_arguments_are_rejected(tools: dict[str, Any]) -> None:
    assert isinstance(validate_tool_call({"name": "call_dataforseo", "args": {}}, tools), RejectedCall)
    assert isinstance(
        validate_tool_call({"name": "google_serp", "args": '{"query_ref": "q1", "keyw'}, tools), RejectedCall
    )
    assert isinstance(validate_tool_call({"name": "google_serp", "args": ["q1"]}, tools), RejectedCall)


def test_valid_call_is_normalised(tools: dict[str, Any]) -> None:
    outcome = validate_tool_call(
        {"id": "c1", "name": "google_serp", "args": '{"query_ref": "q2", "keyword": "  Best   CRM  "}'}, tools
    )
    assert isinstance(outcome, ValidatedCall)
    assert outcome.args.model_dump() == {"query_ref": "q2", "keyword": "best crm", "depth": 10}


def test_planner_repairs_rejected_calls_and_never_executes_them(
    client: FlaskClient, brand_id: str, backend: ScriptedBackend, transport: Any
) -> None:
    backend.script(
        "discovery_planner",
        tool_message(
            ("keyword_suggestions", {"limit": 30}),  # missing seed_keyword
            ("keyword_suggestions", {"seed_keyword": "tasklane reviews"}),  # brand name: semantic rejection
            ("related_keywords", {"seed_keyword": "agency project management"}),
        ),
        tool_message(("keyword_suggestions", {"seed_keyword": "project management software", "limit": 30})),
    )
    run = wait_for_run(client, client.post(f"/api/v1/brands/{brand_id}/runs", json={}).json["id"])
    assert run["status"] == "succeeded"

    planner = next(
        s for s in client.get(f"/api/v1/runs/{run['id']}/trace").json["steps"] if s["node"] == "discovery_planner"
    )
    assert planner["output_summary"]["source"] == "llm_repaired"
    assert len(planner["output_summary"]["rejected"]) == 2
    # The repair turn told the model exactly what was wrong.
    repair_prompt = backend.calls[1]["messages"][-1].content
    assert "seed_keyword" in repair_prompt and "brand's own name" in repair_prompt

    sent = [task for path, task in transport.requests if "dataforseo_labs" in path]
    assert {t["keyword"] for t in sent} == {"agency project management", "project management software"}
    assert all(t["location_code"] == 2840 for t in sent)  # trusted context injected by code, not the LLM


def test_measurement_plan_is_checked_against_candidates_and_completed(
    client: FlaskClient, brand_id: str, backend: ScriptedBackend, transport: Any
) -> None:
    def plan(data: dict[str, Any]) -> Any:
        first = data["candidates"][0]
        return tool_message(
            ("google_serp", {"query_ref": "q99", "keyword": "anything"}),  # unknown candidate id
            (
                "chat_gpt_answer",
                {"query_ref": first["id"], "prompt": "Is Tasklane the best option for this?"},
            ),  # names brand
            ("google_serp", {"query_ref": first["id"], "keyword": first["keyword"]}),
        )

    backend.script("measurement_planner", plan, lambda data: tool_message())  # repair turn returns nothing
    run = wait_for_run(client, client.post(f"/api/v1/brands/{brand_id}/runs", json={}).json["id"])
    assert run["status"] == "succeeded"

    queries = client.get(f"/api/v1/runs/{run['id']}/queries").json["items"]
    assert len(queries) == 1
    assert "tasklane" not in queries[0]["prompt"].lower()
    engines = {e["engine"]: e["status"] for e in queries[0]["channels"]["ai_engines"]}
    assert engines == {"chat_gpt": "ok", "perplexity": "ok"}  # missing calls completed by rule
    ai_prompts = [t["user_prompt"] for p, t in transport.requests if "ai_optimization" in p]
    assert ai_prompts and all("tasklane" not in p.lower() for p in ai_prompts)
