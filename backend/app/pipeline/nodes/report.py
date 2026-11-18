"""Report agent: assembles the final structured report + human-readable summary.

Deterministic on purpose: the report is the user-facing contract and must
be produced even when every LLM stage fell back. It only arranges what the
other stages produced; it does not analyse.
"""

from __future__ import annotations

from typing import Any

from app.db.base import utcnow
from app.db.models import StepStatus
from app.pipeline.contracts import Degradation
from app.pipeline.nodes.base import NodeResult
from app.pipeline.state import PipelineDeps, PipelineState
from app.scoring.aggregates import channel_coverage, share_of_voice, visibility_score

CHANNEL_LABELS = {"organic": "Organic search", "ai_overview": "Google AI Overview", "ai_answers": "AI assistants"}


def reporter(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    brand = state["brand"]
    scored = state.get("scored") or []
    measurable = [s for s in scored if s.breakdown.visibility is not None]
    analysis = state.get("analysis")
    judgements = {j.query_ref: j for j in analysis.judgements} if analysis else {}
    recs = state.get("recommendations") or []
    degradations: list[Degradation] = state.get("degradations") or []
    keyword_by_ref = {s.ref: s.measurement.candidate.keyword for s in scored}

    summary = {
        "visibility_score": visibility_score(measurable),
        "queries_measured": sum(1 for s in scored if s.measurement.status == "measured"),
        "queries_partial": sum(1 for s in scored if s.measurement.status == "partial"),
        "queries_failed": sum(1 for s in scored if s.measurement.status == "failed"),
        "gaps_worth_closing": sum(1 for j in judgements.values() if j.worth_closing),
        "channel_coverage": channel_coverage(measurable),
        "share_of_voice": share_of_voice(measurable, brand.name, [c.name for c in brand.competitors]),
        "top_opportunity": (
            {"query": measurable[0].measurement.candidate.keyword, "score": measurable[0].breakdown.score}
            if measurable
            else None
        ),
    }
    insights = list(analysis.headline_insights) if analysis else []
    report: dict[str, Any] = {
        "generated_at": utcnow().isoformat(),
        "brand": {"name": brand.name, "domain": brand.domain, "market": brand.market, "location": brand.location_name},
        "question": brand.question,
        "summary": summary,
        "insights": insights,
        "opportunities": [
            {
                "ref": s.ref,
                "query": s.measurement.candidate.keyword,
                "score": s.breakdown.score,
                "visibility": s.breakdown.visibility,
                "summary": s.breakdown.summary,
                "worth_closing": judgements[s.ref].worth_closing if s.ref in judgements else None,
                "reason": judgements[s.ref].reason if s.ref in judgements else None,
            }
            for s in measurable[:10]
        ],
        "recommendations": [
            {
                "query": keyword_by_ref.get(r.query_ref),
                "title": r.title,
                "content_type": r.content_type,
                "target_channels": r.target_channels,
                "effort": r.effort,
            }
            for r in recs
        ],
        "data_quality": {
            "complete": not degradations and all(s.measurement.status == "measured" for s in scored),
            "plan_source": state.get("discovery_plan_source"),
            "analysis_source": state.get("analysis_source"),
            "recommendations_source": state.get("recommendations_source"),
            "degradations": [d.model_dump() for d in degradations],
        },
    }
    report["markdown"] = render_markdown(report)
    return NodeResult(
        update={"report": report},
        status=StepStatus.OK,
        summary={
            "visibility_score": summary["visibility_score"],
            "opportunities": len(report["opportunities"]),
            "recommendations": len(recs),
            "complete": report["data_quality"]["complete"],
        },
    )


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    brand = report["brand"]
    score = f"{s['visibility_score']:.0f}/100" if s["visibility_score"] is not None else "not available"
    lines = [
        f"# AI visibility report – {brand['name']}",
        f"_{brand['market']} · {brand['location']} · generated {report['generated_at'][:16].replace('T', ' ')} UTC_",
        "",
    ]
    if report.get("question"):
        lines += [f"**Research question:** {report['question']}", ""]
    lines += [
        f"**Visibility score:** {score} across {s['queries_measured'] + s['queries_partial']} measured queries"
        + (
            f" ({s['queries_partial']} partial, {s['queries_failed']} failed)"
            if s["queries_partial"] or s["queries_failed"]
            else ""
        ),
        "",
        "## Where the brand is visible",
    ]
    for channel, cov in s["channel_coverage"].items():
        lines.append(f"- {CHANNEL_LABELS[channel]}: visible in {cov['visible']} of {cov['measured']} queries")
    if report["insights"]:
        lines += ["", "## Key insights", *[f"- {i}" for i in report["insights"]]]
    if report["opportunities"]:
        lines += ["", "## Top opportunities", "| # | Query | Score | Visibility |", "|---|---|---|---|"]
        for i, o in enumerate(report["opportunities"][:5], 1):
            vis = f"{o['visibility']:.0%}" if o["visibility"] is not None else "–"
            lines.append(f"| {i} | {o['query']} | {o['score']:.0f} | {vis} |")
    if report["recommendations"]:
        lines += ["", "## Recommended content"]
        for r in report["recommendations"]:
            lines.append(
                f"- **{r['title']}** ({r['content_type'].replace('_', ' ')}, {r['effort']} effort) – for “{r['query']}”"
            )
    dq = report["data_quality"]
    if dq["degradations"]:
        lines += ["", "## Data quality notes", *[f"- {d['message']}" for d in dq["degradations"]]]
    return "\n".join(lines) + "\n"
