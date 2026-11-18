"""Analysis / Synthesis agent: judges which gaps are worth closing, and why.

The LLM receives normalised measurements and the deterministic score and
returns a `GapAnalysisOutput`. It cannot change the ranking. Its output is
validated (schema + "only ids/competitors we gave you"); queries it skipped
are filled in by the rule-based judge, and if the LLM is unavailable the
rule-based judge handles everything so the run still produces a result.
"""

from __future__ import annotations

from app.core.errors import ExternalError, InvalidOutputError
from app.db.models import StepStatus
from app.pipeline import prompts
from app.pipeline.contracts import BrandContext, Degradation, GapAnalysisOutput, GapJudgement, GapType, ScoredQuery
from app.pipeline.nodes.base import NodeResult
from app.pipeline.state import PipelineDeps, PipelineState

MAX_JUDGED = 15
WORTH_CLOSING_MIN_SCORE = 12.0


def _query_payload(s: ScoredQuery) -> dict[str, object]:
    m, b = s.measurement, s.breakdown
    return {
        "id": s.ref,
        "keyword": m.candidate.keyword,
        "monthly_volume": m.candidate.search_volume if m.candidate.volume_known else "unknown",
        "intent": m.candidate.intent,
        "keyword_difficulty": m.candidate.keyword_difficulty,
        "opportunity_score": b.score,
        "visibility": b.visibility,
        "channel_visibility": b.channel_visibility,
        "score_factors": {f.name: f.value for f in b.factors},
        "measurement_status": m.status,
        "organic": {
            "brand_position": m.organic.brand_position,
            "competitor_positions": m.organic.competitor_positions,
            "status": m.organic.status,
        },
        "ai_overview": {
            "status": m.ai_overview.status,
            "present": m.ai_overview.present,
            "brand_cited": m.ai_overview.brand_cited,
            "competitors_cited": m.ai_overview.competitors_cited,
        },
        "ai_answers": [
            {
                "engine": e.engine,
                "status": e.status,
                "brand_mentioned": e.brand_mentioned,
                "brand_cited": e.brand_cited,
                "competitors_mentioned": e.competitors_mentioned,
            }
            for e in m.engines
        ],
    }


def rule_judgement(s: ScoredQuery) -> GapJudgement:
    """Deterministic judge used as fallback and to fill queries the LLM skipped."""
    b, m = s.breakdown, s.measurement
    cv = b.channel_visibility
    winners = sorted(
        set(m.organic.competitor_positions)
        | set(m.ai_overview.competitors_cited)
        | {n for e in m.engines for n in e.competitors_mentioned}
    )[:5]
    vis = b.visibility or 0.0
    gap_type: GapType
    if vis >= 0.85:
        gap_type, reason = "already_strong", f"Brand is already {vis:.0%} visible across measured channels."
    elif vis == 0 and winners:
        gap_type, reason = (
            "competitor_dominated",
            f"Brand is absent from every measured channel while {', '.join(winners[:3])} appear.",
        )
    elif vis == 0:
        gap_type, reason = (
            "absent_everywhere",
            "Brand does not appear in search results or any AI answer for this query.",
        )
    elif cv.get("ai_answers") == 0:
        gap_type, reason = "missing_in_ai_answers", "Brand ranks organically but no AI assistant mentions it."
    elif cv.get("ai_overview") == 0:
        gap_type, reason = (
            "missing_in_ai_overview",
            "Google shows an AI Overview for this query but does not cite the brand.",
        )
    else:
        gap_type, reason = "ranking_low_organic", f"Brand is only {vis:.0%} visible; organic position is weak."
    worth = gap_type != "already_strong" and m.candidate.intent != "navigational" and b.score >= WORTH_CLOSING_MIN_SCORE
    if not worth and gap_type != "already_strong":
        reason += f" Opportunity score {b.score:.0f} is below the action threshold."
    return GapJudgement(
        query_ref=s.ref,
        worth_closing=worth,
        gap_type=gap_type,
        reason=reason[:400],
        competitors_winning=winners,
        confidence="medium" if m.status == "measured" else "low",
    )


def rule_insights(scored: list[ScoredQuery], brand: BrandContext) -> list[str]:
    measurable = [s for s in scored if s.breakdown.visibility is not None]
    if not measurable:
        return ["No query could be measured, so no visibility insight is available for this run."]
    insights: list[str] = []
    engines_total = sum(1 for s in measurable for e in s.measurement.engines if e.status == "ok")
    engines_hit = sum(1 for s in measurable for e in s.measurement.engines if e.status == "ok" and e.brand_mentioned)
    if engines_total:
        insights.append(f"{brand.name} is mentioned in {engines_hit} of {engines_total} AI assistant answers measured.")
    top = measurable[0]
    insights.append(
        f"Largest opportunity: '{top.measurement.candidate.keyword}' (score {top.breakdown.score:.0f}) – {top.breakdown.summary}"
    )
    ranking = [
        s for s in measurable if s.measurement.organic.brand_position and s.measurement.organic.brand_position <= 10
    ]
    insights.append(f"{brand.name} ranks on page one for {len(ranking)} of {len(measurable)} measured queries.")
    return insights[:4]


def gap_analyst(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    brand = state["brand"]
    scored = [s for s in state.get("scored") or [] if s.breakdown.visibility is not None][:MAX_JUDGED]
    refs = {s.ref for s in scored}
    known_competitors = {c.name.lower(): c.name for c in brand.competitors}
    data = {
        "brand": brand.name,
        "competitors": [c.name for c in brand.competitors],
        "research_question": brand.question,
        "queries": [_query_payload(s) for s in scored],
    }

    def semantic(out: GapAnalysisOutput) -> list[str]:
        problems = [f"query id '{j.query_ref}' was not provided" for j in out.judgements if j.query_ref not in refs]
        ids = [j.query_ref for j in out.judgements]
        problems += [f"query id '{r}' judged more than once" for r in {r for r in ids if ids.count(r) > 1}]
        return problems

    def fallback(message: str, error: dict[str, object] | None, attempts: int) -> NodeResult:
        analysis = GapAnalysisOutput(
            judgements=[rule_judgement(s) for s in scored], headline_insights=rule_insights(scored, brand)
        )
        return NodeResult(
            update={"analysis": analysis, "analysis_source": "rule"},
            status=StepStatus.DEGRADED,
            summary={"source": "rule", "worth_closing": sum(j.worth_closing for j in analysis.judgements)},
            attempts=attempts,
            error=error,
            degradations=[Degradation(node="gap_analyst", kind="fallback", message=message)],
        )

    try:
        output, repairs = deps.llm.structured(
            agent="gap_analyst",
            system=prompts.GAP_ANALYST,
            user=prompts.render_data(data),
            schema=GapAnalysisOutput,
            data=data,
            ctx=deps.ctx,
            semantic_check=semantic,
        )
    except ExternalError as exc:
        return fallback(
            f"AI analyst unavailable ({exc.code}); gaps judged by rules", exc.to_dict(), getattr(exc, "attempts", 1)
        )
    except InvalidOutputError as exc:
        return fallback(
            "AI analyst output failed validation; gaps judged by rules",
            {"code": "invalid_llm_output", "message": exc.message, "details": exc.details},
            1,
        )

    # Sanitise: keep only competitor names we know (case-insensitive), fill skipped queries by rule.
    judged = {
        j.query_ref: j.model_copy(
            update={
                "competitors_winning": [
                    known_competitors[n.lower()] for n in j.competitors_winning if n.lower() in known_competitors
                ]
            }
        )
        for j in output.judgements
    }
    filled = [s.ref for s in scored if s.ref not in judged]
    for s in scored:
        judged.setdefault(s.ref, rule_judgement(s))
    analysis = GapAnalysisOutput(judgements=[judged[s.ref] for s in scored], headline_insights=output.headline_insights)
    degradations = (
        [
            Degradation(
                node="gap_analyst",
                kind="partial",
                message=f"{len(filled)} queries were judged by rules (AI skipped them)",
            )
        ]
        if filled
        else []
    )
    return NodeResult(
        update={"analysis": analysis, "analysis_source": "llm"},
        status=StepStatus.DEGRADED if filled else StepStatus.OK,
        summary={
            "source": "llm",
            "repair_turns": repairs,
            "filled_by_rule": filled,
            "worth_closing": sum(j.worth_closing for j in analysis.judgements),
        },
        attempts=1 + repairs,
        degradations=degradations,
    )
