"""LLM output is untrusted: malformed or hallucinated output never reaches the user unchecked."""

from __future__ import annotations

from typing import Any

import pytest
from flask.testing import FlaskClient

from app.core.budget import CostBudget
from app.core.call_context import CallContext
from app.core.errors import InvalidOutputError, PermanentError
from app.core.resilience import BreakerRegistry, RetryPolicy
from app.observability.metrics import RunMetrics
from app.pipeline.contracts import GapAnalysisOutput
from app.providers.llm.gateway import LLMGateway
from tests.conftest import wait_for_run
from tests.fakes import ScriptedBackend, tool_message

CTX = CallContext(run_id="r", budget=CostBudget(1), metrics=RunMetrics(), location_code=2840, language_code="en")
VALID = {
    "judgements": [
        {
            "query_ref": "q1",
            "worth_closing": True,
            "gap_type": "absent_everywhere",
            "reason": "Brand is absent from all channels for a 5,000/mo query.",
            "confidence": "high",
        }
    ],
    "headline_insights": ["Brand is invisible for its main category query."],
}


def gateway(backend: ScriptedBackend, repairs: int = 1) -> LLMGateway:
    return LLMGateway(
        backend,
        breakers=BreakerRegistry(5, 30),
        policy=RetryPolicy(2, 0, 0),
        repair_attempts=repairs,
        sleep=lambda _: None,
    )


def structured(gw: LLMGateway, **kw: Any) -> tuple[GapAnalysisOutput, int]:
    return gw.structured(agent="gap_analyst", system="s", user="u", schema=GapAnalysisOutput, data={}, ctx=CTX, **kw)


def test_malformed_output_gets_one_targeted_repair_turn() -> None:
    broken = {"judgements": [{"query_ref": "q1", "worth_closing": "maybe", "gap_type": "vibes", "reason": "short"}]}
    backend = ScriptedBackend().script(
        "gap_analyst", tool_message(("GapAnalysisOutput", broken)), tool_message(("GapAnalysisOutput", VALID))
    )
    value, repairs = structured(gateway(backend))
    assert repairs == 1 and value.judgements[0].gap_type == "absent_everywhere"
    feedback = backend.calls[1]["messages"][-1].content
    assert "gap_type" in feedback and "headline_insights" in feedback and "reason" in feedback


def test_text_instead_of_tool_call_is_accepted_only_if_it_validates() -> None:
    import json

    from langchain_core.messages import AIMessage

    backend = ScriptedBackend().script("gap_analyst", AIMessage(content="```json\n" + json.dumps(VALID) + "\n```"))
    value, _ = structured(gateway(backend))
    assert value.headline_insights


def test_persistently_invalid_output_raises_instead_of_leaking() -> None:
    backend = ScriptedBackend().script(
        "gap_analyst",
        tool_message(("GapAnalysisOutput", {"judgements": []})),
        tool_message(("GapAnalysisOutput", {"oops": 1})),
    )
    with pytest.raises(InvalidOutputError) as info:
        structured(gateway(backend))
    assert info.value.details


def test_semantic_check_rejects_ids_that_were_never_provided() -> None:
    hallucinated = {**VALID, "judgements": [{**VALID["judgements"][0], "query_ref": "q42"}]}
    backend = ScriptedBackend().script(
        "gap_analyst",
        tool_message(("GapAnalysisOutput", hallucinated)),
        tool_message(("GapAnalysisOutput", hallucinated)),
    )
    with pytest.raises(InvalidOutputError):
        structured(
            gateway(backend),
            semantic_check=lambda out: [f"{j.query_ref} unknown" for j in out.judgements if j.query_ref != "q1"],
        )


def test_invalid_analysis_falls_back_to_rules_and_hallucinations_are_stripped(
    client: FlaskClient, brand_id: str, backend: ScriptedBackend
) -> None:
    garbage = tool_message(("GapAnalysisOutput", {"judgements": "all good"}))
    backend.script("gap_analyst", garbage, garbage)

    def recs_with_hallucinated_sources(data: dict[str, Any]) -> Any:
        return tool_message(
            (
                "RecommendationOutput",
                {
                    "recommendations": [
                        {
                            "query_ref": g["id"],
                            "title": f"Guide to {g['keyword']}",
                            "content_type": "buyers_guide",
                            "target_channels": ["organic"],
                            "rationale": "x" * 60,
                            "outline": ["a", "b", "c"],
                            "key_points": ["a", "b"],
                            "sources_to_earn": ["made-up-site.example", *g["cited_sources"][:1]],
                            "effort": "low",
                        }
                        for g in data["gaps"]
                    ]
                },
            )
        )

    backend.script("recommender", recs_with_hallucinated_sources)
    run = wait_for_run(client, client.post(f"/api/v1/brands/{brand_id}/runs", json={}).json["id"])

    assert run["status"] == "partial"
    assert run["report"]["data_quality"]["analysis_source"] == "rule"
    assert any(d["node"] == "gap_analyst" and d["kind"] == "fallback" for d in run["degradations"])
    queries = client.get(f"/api/v1/runs/{run['id']}/queries").json["items"]
    assert all(q["analysis"]["source"] == "rule" for q in queries)
    recs = client.get(f"/api/v1/runs/{run['id']}/recommendations").json
    assert recs and all("made-up-site.example" not in r["sources_to_earn"] for r in recs)


def test_client_side_configuration_errors_are_not_retried() -> None:
    backend = ScriptedBackend().script("gap_analyst", ValueError("temperature is not supported for this model"))
    with pytest.raises(PermanentError):
        structured(gateway(backend))
    assert len(backend.calls) == 1
