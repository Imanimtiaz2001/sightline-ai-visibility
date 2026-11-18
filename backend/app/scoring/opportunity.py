"""Visibility and opportunity scoring – pure, deterministic, explainable.

Definition (defended in the README):

    opportunity = 100 × demand × gap × urgency × winnability × intent_value

* demand       – log-scaled monthly search volume, on an absolute scale so
                 scores are comparable across runs and brands.
* gap          – 1 − visibility, where visibility is the channel-weighted share
                 of the answer surface the brand currently owns.
* urgency      – 0.6 + 0.4 × competitor pressure: a gap competitors are filling
                 is losing share now; a gap nobody fills is still a gap.
* winnability  – discounted by keyword difficulty.
* intent_value – commercial/transactional questions are worth more to a
                 brand than navigational ones.

Every factor is in [0, 1], so the score is in [0, 100] and the factors are
returned with the score. The LLM never sets or adjusts the score: it judges
gaps and writes recommendations, but the ranking is reproducible from data.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from app.pipeline.contracts import (
    AIEngineChannel,
    AIOverviewChannel,
    OrganicChannel,
    QueryMeasurement,
    ScoreBreakdown,
    ScoreFactor,
)

# Approximate organic CTR curve normalised to position 1 (public CTR studies).
CTR_BY_POSITION = {1: 1.0, 2: 0.55, 3: 0.38, 4: 0.28, 5: 0.21, 6: 0.16, 7: 0.12, 8: 0.10, 9: 0.08, 10: 0.07}
BEYOND_PAGE_ONE = 0.03
CHANNEL_WEIGHTS = {"organic": 0.40, "ai_overview": 0.25, "ai_answers": 0.35}
INTENT_VALUE = {"transactional": 1.0, "commercial": 1.0, "informational": 0.7, "navigational": 0.3}
UNKNOWN_INTENT_VALUE = 0.8
DEMAND_CEILING = 100_000  # volume treated as "maximum demand"
UNKNOWN_DEMAND = 0.25
UNKNOWN_DIFFICULTY_WINNABILITY = 0.75


def organic_visibility(ch: OrganicChannel) -> float | None:
    if ch.status != "ok":
        return None
    if ch.brand_position is None:
        return 0.0
    return CTR_BY_POSITION.get(ch.brand_position, BEYOND_PAGE_ONE)


def ai_overview_visibility(ch: AIOverviewChannel) -> float | None:
    # No AI Overview on this SERP means the channel does not exist for this query:
    # it is excluded (weights renormalise) rather than counted as a miss.
    if ch.status != "ok" or not ch.present:
        return None
    if ch.brand_cited:
        return 1.0
    return 0.6 if ch.brand_mentioned else 0.0


def _engine_visibility(e: AIEngineChannel) -> float:
    if e.brand_mentioned and e.brand_cited:
        return 1.0
    if e.brand_mentioned:
        return 0.8
    if e.brand_cited:
        return 0.5
    return 0.0


def ai_answers_visibility(engines: Iterable[AIEngineChannel]) -> float | None:
    values = [_engine_visibility(e) for e in engines if e.status == "ok"]
    return sum(values) / len(values) if values else None


def channel_visibility(m: QueryMeasurement) -> dict[str, float | None]:
    return {
        "organic": organic_visibility(m.organic),
        "ai_overview": ai_overview_visibility(m.ai_overview),
        "ai_answers": ai_answers_visibility(m.engines),
    }


def combined_visibility(channels: dict[str, float | None]) -> float | None:
    available = {k: v for k, v in channels.items() if v is not None}
    if not available:
        return None
    total_weight = sum(CHANNEL_WEIGHTS[k] for k in available)
    return sum(CHANNEL_WEIGHTS[k] * v for k, v in available.items()) / total_weight


def demand(volume: int | None, known: bool) -> float:
    if not known or volume is None:
        return UNKNOWN_DEMAND
    return min(1.0, math.log10(1 + max(0, volume)) / math.log10(1 + DEMAND_CEILING))


def competitor_pressure(m: QueryMeasurement) -> tuple[float, list[str]]:
    """Share of measured surfaces where a competitor is visible and the brand is not."""
    surfaces: list[tuple[bool, set[str]]] = []
    if m.organic.status == "ok":
        top10 = {name for name, pos in m.organic.competitor_positions.items() if pos <= 10}
        brand_top10 = m.organic.brand_position is not None and m.organic.brand_position <= 10
        surfaces.append((brand_top10, top10))
    if m.ai_overview.status == "ok" and m.ai_overview.present:
        surfaces.append(
            (m.ai_overview.brand_cited or m.ai_overview.brand_mentioned, set(m.ai_overview.competitors_cited))
        )
    for e in m.engines:
        if e.status == "ok":
            surfaces.append((e.brand_mentioned or e.brand_cited, set(e.competitors_mentioned)))
    if not surfaces:
        return 0.0, []
    lost = [names for brand_visible, names in surfaces if names and not brand_visible]
    winners = sorted({n for names in lost for n in names})
    return len(lost) / len(surfaces), winners


def winnability(difficulty: int | None) -> float:
    if difficulty is None:
        return UNKNOWN_DIFFICULTY_WINNABILITY
    return 1.0 - 0.5 * max(0, min(100, difficulty)) / 100


def score_query(m: QueryMeasurement) -> ScoreBreakdown:
    channels = channel_visibility(m)
    vis = combined_visibility(channels)
    c = m.candidate
    d = demand(c.search_volume, c.volume_known)
    gap = 1.0 - vis if vis is not None else 0.0
    pressure, winners = competitor_pressure(m)
    urgency = 0.6 + 0.4 * pressure
    win = winnability(c.keyword_difficulty)
    intent = INTENT_VALUE.get(c.intent or "", UNKNOWN_INTENT_VALUE)
    score = round(100 * d * gap * urgency * win * intent, 1) if vis is not None else 0.0

    volume_text = f"{c.search_volume:,}/mo" if c.volume_known and c.search_volume is not None else "unknown volume"
    factors = [
        ScoreFactor(
            name="demand",
            value=round(d, 3),
            explanation=f"Search demand from {volume_text} (log scale, 100k/mo = 1.0).",
        ),
        ScoreFactor(
            name="gap",
            value=round(gap, 3),
            explanation="Not measurable – every channel failed."
            if vis is None
            else f"Brand owns {vis:.0%} of the weighted answer surface, leaving {gap:.0%} open.",
        ),
        ScoreFactor(
            name="urgency",
            value=round(urgency, 3),
            explanation=(
                f"Competitors fill {pressure:.0%} of surfaces where the brand is missing: {', '.join(winners)}."
                if winners
                else "No competitor is filling the gap yet."
            ),
        ),
        ScoreFactor(
            name="winnability",
            value=round(win, 3),
            explanation=f"Keyword difficulty {c.keyword_difficulty}/100."
            if c.keyword_difficulty is not None
            else "Keyword difficulty unknown; assumed medium.",
        ),
        ScoreFactor(name="intent_value", value=intent, explanation=f"Search intent: {c.intent or 'unknown'}."),
    ]
    return ScoreBreakdown(
        score=score,
        visibility=round(vis, 3) if vis is not None else None,
        factors=factors,
        channel_visibility={k: (round(v, 3) if v is not None else None) for k, v in channels.items()},
        summary=_summary(score, vis, volume_text, winners),
    )


def _summary(score: float, vis: float | None, volume_text: str, winners: list[str]) -> str:
    if vis is None:
        return "Could not be measured; excluded from ranking."
    if vis >= 0.85:
        return f"Brand is already strong here ({vis:.0%} visible)."
    who = f" while {', '.join(winners[:3])} appear" if winners else ""
    return f"{volume_text}; brand {vis:.0%} visible{who}."
