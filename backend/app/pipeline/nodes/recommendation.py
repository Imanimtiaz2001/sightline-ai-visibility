"""Recommendation agent: turns the gaps worth closing into specific content to publish."""

from __future__ import annotations

from app.core.errors import ExternalError, InvalidOutputError
from app.db.models import StepStatus
from app.pipeline import prompts
from app.pipeline.contracts import (
    BrandContext,
    Channel,
    ContentType,
    Degradation,
    GapJudgement,
    RecommendationDraft,
    RecommendationOutput,
    ScoredQuery,
)
from app.pipeline.extract import domain_matches
from app.pipeline.nodes.base import NodeResult
from app.pipeline.state import PipelineDeps, PipelineState


def selected_gaps(state: PipelineState, limit: int) -> list[tuple[ScoredQuery, GapJudgement]]:
    analysis = state.get("analysis")
    if analysis is None:
        return []
    judgements = {j.query_ref: j for j in analysis.judgements}
    gaps = [
        (s, judgements[s.ref])
        for s in state.get("scored") or []
        if s.ref in judgements and judgements[s.ref].worth_closing
    ]
    return gaps[:limit]


def _cited_domains(s: ScoredQuery) -> list[str]:
    m = s.measurement
    domains = [r.domain for r in m.ai_overview.references] + [d for e in m.engines for d in e.cited_domains]
    return list(dict.fromkeys(domains))


def _missing_channels(s: ScoredQuery) -> list[Channel]:
    return [c for c, v in s.breakdown.channel_visibility.items() if v is not None and v < 0.5]  # type: ignore[misc]


def _gap_payload(s: ScoredQuery, j: GapJudgement) -> dict[str, object]:
    m = s.measurement
    return {
        "id": s.ref,
        "keyword": m.candidate.keyword,
        "buyer_question": m.prompt,
        "monthly_volume": m.candidate.search_volume if m.candidate.volume_known else "unknown",
        "intent": m.candidate.intent,
        "gap_type": j.gap_type,
        "analyst_reason": j.reason,
        "channels_missing": _missing_channels(s),
        "brand_organic_position": m.organic.brand_position,
        "competitors_winning": j.competitors_winning,
        "top_organic_results": [
            {"position": r.position, "domain": r.domain, "title": r.title} for r in m.organic.top_results[:6]
        ],
        "cited_sources": _cited_domains(s)[:15],
        "people_also_ask": m.organic.people_also_ask[:5],
    }


_TEMPLATE: dict[str, tuple[ContentType, str]] = {
    "competitor_dominated": ("comparison_page", "{brand} vs alternatives: {keyword}"),
    "absent_everywhere": ("buyers_guide", "The complete guide to {keyword}"),
    "missing_in_ai_answers": ("third_party_listing", "Get {brand} cited for '{keyword}'"),
    "missing_in_ai_overview": ("faq_hub", "{keyword}: answers to the questions buyers ask"),
    "ranking_low_organic": ("how_to_guide", "How to choose: {keyword}"),
    "already_strong": ("landing_page_update", "Defend your position for '{keyword}'"),
}


def rule_recommendation(
    s: ScoredQuery, j: GapJudgement, brand: BrandContext, allowed_sources: set[str]
) -> RecommendationDraft:
    content_type, title = _TEMPLATE[j.gap_type]
    keyword = s.measurement.candidate.keyword
    paa = s.measurement.organic.people_also_ask[:3]
    sources = [d for d in _cited_domains(s) if d in allowed_sources][:4]
    competitors = ", ".join(j.competitors_winning[:3]) or "other vendors"
    return RecommendationDraft(
        query_ref=s.ref,
        title=title.format(brand=brand.name, keyword=keyword)[:120],
        content_type=content_type,
        target_channels=_missing_channels(s) or ["organic"],
        rationale=(
            f"{j.reason} The query has {s.measurement.candidate.search_volume or 'unknown'} monthly searches and an "
            f"opportunity score of {s.breakdown.score:.0f}; answering it directly gives search engines and AI "
            f"assistants a citable source alongside {competitors}."
        )[:600],
        outline=[
            f"What to look for in {keyword}",
            *(q.rstrip("?") + "?" for q in paa),
            f"How {brand.name} compares",
            "Pricing and next steps",
        ][:8],
        key_points=[
            f"Answer '{s.measurement.prompt}' in the first paragraph",
            "Include concrete facts: pricing, limits, integrations",
            "Compare options fairly with a summary table",
        ],
        sources_to_earn=sources,
        effort="medium",
    )


def recommender(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    brand = state["brand"]
    gaps = selected_gaps(state, deps.settings.max_recommendations)
    if not gaps:
        return NodeResult(
            update={"recommendations": [], "recommendations_source": "none"},
            status=StepStatus.SKIPPED,
            summary={"reason": "no gaps worth closing"},
        )
    refs = {s.ref for s, _ in gaps}
    owned = [brand.domain, *(c.domain for c in brand.competitors)]
    allowed_sources = {d for s, _ in gaps for d in _cited_domains(s) if not any(domain_matches(d, o) for o in owned)}
    data = {
        "brand": brand.name,
        "brand_domain": brand.domain,
        "competitors": [c.name for c in brand.competitors],
        "gaps": [_gap_payload(s, j) for s, j in gaps],
    }

    def semantic(out: RecommendationOutput) -> list[str]:
        problems = [
            f"query id '{r.query_ref}' is not one of the gaps provided"
            for r in out.recommendations
            if r.query_ref not in refs
        ]
        covered = {r.query_ref for r in out.recommendations}
        problems += [f"no recommendation for gap '{r}'" for r in sorted(refs - covered)]
        return problems

    def fallback(message: str, error: dict[str, object] | None, attempts: int) -> NodeResult:
        recs = [rule_recommendation(s, j, brand, allowed_sources) for s, j in gaps]
        return NodeResult(
            update={"recommendations": recs, "recommendations_source": "rule"},
            status=StepStatus.DEGRADED,
            summary={"source": "rule", "count": len(recs)},
            attempts=attempts,
            error=error,
            degradations=[Degradation(node="recommender", kind="fallback", message=message)],
        )

    try:
        output, repairs = deps.llm.structured(
            agent="recommender",
            system=prompts.RECOMMENDER.format(max_recs=deps.settings.max_recommendations),
            user=prompts.render_data(data),
            schema=RecommendationOutput,
            data=data,
            ctx=deps.ctx,
            semantic_check=semantic,
        )
    except ExternalError as exc:
        return fallback(
            f"AI strategist unavailable ({exc.code}); used rule-based recommendations",
            exc.to_dict(),
            getattr(exc, "attempts", 1),
        )
    except InvalidOutputError as exc:
        return fallback(
            "AI strategist output failed validation; used rule-based recommendations",
            {"code": "invalid_llm_output", "message": exc.message, "details": exc.details},
            1,
        )

    recs = [
        r.model_copy(
            update={"sources_to_earn": [d for d in dict.fromkeys(r.sources_to_earn) if d.lower() in allowed_sources]}
        )
        for r in output.recommendations
        if r.query_ref in refs
    ][: deps.settings.max_recommendations]
    return NodeResult(
        update={"recommendations": recs, "recommendations_source": "llm"},
        status=StepStatus.OK,
        summary={"source": "llm", "repair_turns": repairs, "count": len(recs)},
        attempts=1 + repairs,
    )
