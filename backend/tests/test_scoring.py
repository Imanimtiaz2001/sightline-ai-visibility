"""Opportunity scoring: bounded, monotonic and explainable from its factors."""

from __future__ import annotations

from typing import Any

from app.pipeline.contracts import (
    AIEngineChannel,
    AIOverviewChannel,
    KeywordCandidate,
    OrganicChannel,
    QueryMeasurement,
)
from app.scoring.opportunity import score_query


def measurement(
    *,
    volume: int | None = 5000,
    position: int | None = None,
    competitors: dict[str, int] | None = None,
    overview: dict[str, Any] | None = None,
    mentions: tuple[bool, ...] = (False, False),
    kd: int | None = 40,
    intent: str = "commercial",
) -> QueryMeasurement:
    return QueryMeasurement(
        ref="q1",
        candidate=KeywordCandidate(
            keyword="crm",
            search_volume=volume,
            keyword_difficulty=kd,
            intent=intent,  # type: ignore[arg-type]
            volume_known=volume is not None,
        ),
        prompt="What is the best CRM?",
        organic=OrganicChannel(status="ok", brand_position=position, competitor_positions=competitors or {}),
        ai_overview=AIOverviewChannel(status="ok", **(overview or {"present": False})),
        engines=[
            AIEngineChannel(
                engine=f"e{i}",
                model="m",
                status="ok",
                brand_mentioned=m,
                brand_cited=m,
                competitors_mentioned=[] if m else ["Rival"],
            )
            for i, m in enumerate(mentions)
        ],
    )


def test_score_is_the_product_of_bounded_factors() -> None:
    b = score_query(measurement(competitors={"Rival": 1}))
    factors = {f.name: f.value for f in b.factors}
    assert all(0 <= v <= 1 for v in factors.values())
    assert b.score == round(
        100
        * factors["demand"]
        * factors["gap"]
        * factors["urgency"]
        * factors["winnability"]
        * factors["intent_value"],
        1,
    )
    assert all(f.explanation for f in b.factors)


def test_fully_visible_brand_has_no_opportunity() -> None:
    b = score_query(measurement(position=1, overview={"present": True, "brand_cited": True}, mentions=(True, True)))
    assert b.visibility == 1.0 and b.score == 0


def test_monotonic_in_demand_gap_and_competitor_pressure() -> None:
    base = score_query(measurement()).score
    assert score_query(measurement(volume=50_000)).score > base  # more demand
    assert score_query(measurement(position=2)).score < base  # less gap
    assert score_query(measurement(competitors={"Rival": 1})).score >= base  # competitors filling the gap
    assert score_query(measurement(kd=90)).score < base  # harder to win
    assert score_query(measurement(intent="navigational")).score < base


def test_missing_ai_overview_is_excluded_not_counted_as_a_miss() -> None:
    without = score_query(measurement(position=1, mentions=(True, True)))
    assert without.channel_visibility["ai_overview"] is None
    assert without.visibility == 1.0  # weights renormalise over the channels that exist


def test_failed_channels_are_excluded_and_fully_failed_queries_score_zero() -> None:
    m = measurement(position=1).model_copy(
        update={"engines": [AIEngineChannel(engine="e", model="m", status="failed")]}
    )
    assert score_query(m).channel_visibility["ai_answers"] is None
    dead = m.model_copy(
        update={"organic": OrganicChannel(status="failed"), "ai_overview": AIOverviewChannel(status="failed")}
    )
    b = score_query(dead)
    assert b.visibility is None and b.score == 0 and "excluded" in b.summary


def test_unknown_volume_is_flagged_not_invented() -> None:
    b = score_query(measurement(volume=None))
    demand = next(f for f in b.factors if f.name == "demand")
    assert demand.value == 0.25 and "unknown" in demand.explanation
