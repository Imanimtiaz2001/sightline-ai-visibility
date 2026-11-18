"""Query Planner agents: decide which DataForSEO calls to make. They never execute them.

Two planners because there are two distinct planning decisions with
different inputs: *discovery* (what to research, from the brand profile) and
*measurement* (which discovered questions to measure and how to phrase them
for AI assistants, from the discovered keyword data).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable

from app.core.errors import ExternalError
from app.db.models import StepStatus
from app.pipeline import prompts
from app.pipeline.contracts import BrandContext, Degradation, KeywordCandidate, MeasurementTarget, PlannedCall
from app.pipeline.extract import normalize_text, short_phrase
from app.pipeline.nodes.base import NodeResult
from app.pipeline.state import PipelineDeps, PipelineState
from app.providers.dataforseo.tools import (
    AIAnswerArgs,
    GoogleSerpArgs,
    KeywordSuggestionsArgs,
    RejectedCall,
    SearchVolumeArgs,
    ToolSpec,
    ValidatedCall,
    validate_tool_call,
)

SemanticCheck = Callable[[ValidatedCall], str | None]


def _plan_with_repair(
    *,
    agent: str,
    system: str,
    data: dict[str, object],
    specs: list[ToolSpec],
    semantic_check: SemanticCheck,
    deps: PipelineDeps,
) -> tuple[list[ValidatedCall], list[RejectedCall], int, bool]:
    """Propose → validate (schema + semantics) → one repair turn for rejected calls.

    Returns (accepted, rejected, llm_attempts, repaired). Raises ExternalError
    only if the LLM itself is unavailable.
    """
    allowed = {s.name: s for s in specs}
    tools = [s.llm_schema() for s in specs]
    accepted: list[ValidatedCall] = []
    rejected: list[RejectedCall] = []
    seen: set[tuple[str, str]] = set()
    attempts = 0
    repaired = False
    feedback: str | None = None
    for turn in range(deps.settings.llm_repair_attempts + 1):
        proposal = deps.llm.propose_tool_calls(
            agent=agent,
            system=system,
            user=prompts.render_data(data),
            tools=tools,
            data=data,
            ctx=deps.ctx,
            feedback=feedback,
        )
        attempts += proposal.attempts
        turn_rejected: list[RejectedCall] = []
        for raw in proposal.calls:
            outcome = validate_tool_call(raw, allowed)
            if isinstance(outcome, ValidatedCall) and (problem := semantic_check(outcome)):
                outcome = RejectedCall(
                    outcome.id, outcome.spec.name, outcome.args.model_dump(), [{"loc": (), "msg": problem}]
                )
            if isinstance(outcome, RejectedCall):
                turn_rejected.append(outcome)
                continue
            fingerprint = (outcome.spec.name, outcome.args.model_dump_json())
            if fingerprint not in seen:
                seen.add(fingerprint)
                accepted.append(outcome)
        rejected.extend(turn_rejected)
        if not turn_rejected or turn == deps.settings.llm_repair_attempts:
            break
        repaired = True
        feedback = (
            "Some of your tool calls were rejected and NOT executed:\n- "
            + "\n- ".join(r.feedback() for r in turn_rejected)
            + "\nIssue corrected versions of only the rejected calls."
        )
    return accepted, rejected, attempts, repaired


def _planned(call: ValidatedCall, query_ref: str | None = None) -> PlannedCall:
    return PlannedCall(id=call.id, tool=call.spec.name, args=call.args.model_dump(), query_ref=query_ref)


def _brand_payload(brand: BrandContext) -> dict[str, object]:
    return {
        "brand": brand.name,
        "brand_domain": brand.domain,
        "market": brand.market,
        "description": brand.description,
        "seed_topics": brand.seed_topics,
        "competitors": [c.name for c in brand.competitors],
        "market_location": brand.location_name,
        "language": brand.language_code,
    }


def _mentions_brand(text: str, brand: BrandContext) -> bool:
    return bool(re.search(rf"\b{re.escape(brand.name.lower())}\b", text.lower())) or brand.domain in text.lower()


# --- discovery -------------------------------------------------------------------------------


def fallback_discovery_plan(brand: BrandContext, max_calls: int) -> list[PlannedCall]:
    """Deterministic plan from the profile alone – used when the LLM is unavailable."""
    plan: list[PlannedCall] = []
    seeds = [s for s in [short_phrase(brand.market), *(short_phrase(t) for t in brand.seed_topics)] if len(s) >= 2]
    for i, seed in enumerate(dict.fromkeys(seeds)):
        if len(plan) >= max(1, max_calls - 1):
            break
        try:
            args = KeywordSuggestionsArgs(seed_keyword=seed, limit=50)
        except ValueError:
            continue
        plan.append(PlannedCall(id=f"fallback_{i}", tool="keyword_suggestions", args=args.model_dump()))
    explicit = [t for t in [*brand.seed_topics, *([brand.question] if brand.question else [])] if t]
    phrases = [p for p in (short_phrase(t, 8).lower() for t in explicit) if 2 <= len(p) <= 80][:20]
    if phrases:
        with contextlib.suppress(ValueError):
            plan.append(
                PlannedCall(
                    id="fallback_sv", tool="search_volume", args=SearchVolumeArgs(keywords=phrases).model_dump()
                )
            )
    return plan


def discovery_planner(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    brand = state["brand"]
    max_calls = deps.settings.max_discovery_calls
    data = {**_brand_payload(brand), "research_question": brand.question, "max_calls": max_calls}

    def semantic(call: ValidatedCall) -> str | None:
        seeds = getattr(call.args, "keywords", None) or [getattr(call.args, "seed_keyword", "")]
        if any(_mentions_brand(s, brand) for s in seeds):
            return "seeds must not contain the brand's own name; research the market, not the brand"
        return None

    try:
        accepted, rejected, attempts, repaired = _plan_with_repair(
            agent="discovery_planner",
            system=prompts.DISCOVERY_PLANNER.format(max_calls=max_calls),
            data=data,
            specs=deps.tools.by_phase("discovery"),
            semantic_check=semantic,
            deps=deps,
        )
    except ExternalError as exc:
        plan = fallback_discovery_plan(brand, max_calls)
        return NodeResult(
            update={"discovery_plan": plan, "discovery_plan_source": "fallback"},
            status=StepStatus.DEGRADED,
            summary={"source": "fallback", "calls": [c.tool for c in plan]},
            attempts=getattr(exc, "attempts", 1),
            error=exc.to_dict(),
            degradations=[
                Degradation(
                    node="discovery_planner",
                    kind="fallback",
                    message=f"AI planner unavailable ({exc.code}); used a rule-based research plan",
                )
            ],
        )

    plan = [_planned(c) for c in accepted[:max_calls]]
    summary = {
        "source": "llm_repaired" if repaired else "llm",
        "calls": [f"{c.tool}({c.args})" for c in plan],
        "rejected": [r.feedback() for r in rejected],
    }
    if not plan:
        plan = fallback_discovery_plan(brand, max_calls)
        return NodeResult(
            update={"discovery_plan": plan, "discovery_plan_source": "fallback"},
            status=StepStatus.DEGRADED,
            summary={**summary, "source": "fallback"},
            attempts=attempts,
            degradations=[
                Degradation(
                    node="discovery_planner",
                    kind="fallback",
                    message="AI planner produced no valid tool calls; used a rule-based plan",
                )
            ],
        )
    return NodeResult(
        update={"discovery_plan": plan, "discovery_plan_source": summary["source"]},
        status=StepStatus.OK,
        summary=summary,
        attempts=attempts,
    )


# --- measurement -------------------------------------------------------------------------------

_QUESTION_START = re.compile(r"^(how|what|which|why|who|where|when|is|are|can|does|do|should)\b")


def default_prompt(keyword: str) -> str:
    """Rule-based buyer question for a keyword (used to fill gaps in, or replace, the LLM plan)."""
    if _QUESTION_START.match(keyword):
        return keyword[0].upper() + keyword[1:].rstrip("?") + "?"
    if " vs " in keyword or " versus " in keyword:
        return f"How do {keyword} compare, and which should I choose?"
    if keyword.startswith("best "):
        return f"What are the {keyword}?"
    return f"What is the best {keyword}?"


def _priority(candidate: KeywordCandidate) -> tuple[int, int, int]:
    explicit = 0 if "search_volume" in candidate.sources or "question" in candidate.sources else 1
    commercial = 0 if candidate.intent in {"commercial", "transactional"} else 1
    return (explicit, commercial, -(candidate.search_volume or 0))


def _complete_target(
    ref: str, candidate: KeywordCandidate, calls: list[PlannedCall], deps: PipelineDeps, brand: BrandContext
) -> MeasurementTarget:
    """Guarantee each target has exactly one SERP call and one call per configured AI engine."""
    by_tool: dict[str, PlannedCall] = {}
    for call in calls:
        by_tool.setdefault(call.tool, call)
    prompt = next(
        (str(c.args["prompt"]) for c in by_tool.values() if "prompt" in c.args), default_prompt(candidate.keyword)
    )
    if _mentions_brand(prompt, brand):
        prompt = default_prompt(candidate.keyword)
    if "google_serp" not in by_tool:
        args = GoogleSerpArgs(query_ref=ref, keyword=candidate.keyword)
        by_tool["google_serp"] = PlannedCall(
            id=f"{ref}_serp", tool="google_serp", args=args.model_dump(), query_ref=ref
        )
    for spec in deps.tools.engine_tools:
        if spec.name not in by_tool:
            answer_args = AIAnswerArgs(query_ref=ref, prompt=prompt)
            by_tool[spec.name] = PlannedCall(
                id=f"{ref}_{spec.name}", tool=spec.name, args=answer_args.model_dump(), query_ref=ref
            )
    return MeasurementTarget(ref=ref, candidate=candidate, prompt=prompt, calls=list(by_tool.values()))


def fallback_measurement_targets(
    candidates: list[KeywordCandidate], deps: PipelineDeps, brand: BrandContext
) -> list[MeasurementTarget]:
    ranked = sorted(candidates, key=_priority)[: deps.settings.max_queries_per_run]
    return [_complete_target(f"q{i + 1}", c, [], deps, brand) for i, c in enumerate(ranked)]


def measurement_planner(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    brand = state["brand"]
    candidates = state.get("candidates") or []
    if not candidates:
        return NodeResult(
            update={"measurement_targets": []}, status=StepStatus.SKIPPED, summary={"reason": "no candidates"}
        )

    shortlist = sorted(candidates, key=_priority)[:30]
    by_ref = {f"q{i + 1}": c for i, c in enumerate(shortlist)}
    max_queries = deps.settings.max_queries_per_run
    data = {
        **_brand_payload(brand),
        "research_question": brand.question,
        "max_queries": max_queries,
        "ai_answer_tools": [s.name for s in deps.tools.engine_tools],
        "candidates": [
            {
                "id": ref,
                "keyword": c.keyword,
                "monthly_volume": c.search_volume if c.volume_known else None,
                "intent": c.intent,
                "keyword_difficulty": c.keyword_difficulty,
                "found_by": c.sources,
            }
            for ref, c in by_ref.items()
        ],
    }

    def semantic(call: ValidatedCall) -> str | None:
        ref = getattr(call.args, "query_ref", None)
        if ref not in by_ref:
            return f"query_ref '{ref}' is not one of the candidate ids"
        if call.spec.name == "google_serp" and normalize_text(call.args.keyword) != by_ref[ref].keyword:  # type: ignore[attr-defined]
            return f"google_serp keyword must be the candidate keyword '{by_ref[ref].keyword}' unchanged"
        if isinstance(call.args, AIAnswerArgs) and _mentions_brand(call.args.prompt, brand):
            return "AI-answer prompts must not mention the brand being measured"
        return None

    def degraded(message: str, *, error: dict[str, object] | None = None, attempts: int = 0) -> NodeResult:
        targets = fallback_measurement_targets(candidates, deps, brand)
        return NodeResult(
            update={"measurement_targets": targets},
            status=StepStatus.DEGRADED,
            summary={"source": "fallback", "queries": [t.candidate.keyword for t in targets]},
            attempts=attempts,
            error=error,
            degradations=[Degradation(node="measurement_planner", kind="fallback", message=message)],
        )

    try:
        accepted, rejected, attempts, repaired = _plan_with_repair(
            agent="measurement_planner",
            system=prompts.MEASUREMENT_PLANNER.format(max_queries=max_queries),
            data=data,
            specs=deps.tools.by_phase("measurement"),
            semantic_check=semantic,
            deps=deps,
        )
    except ExternalError as exc:
        return degraded(
            f"AI planner unavailable ({exc.code}); measured the top queries by demand",
            error=exc.to_dict(),
            attempts=getattr(exc, "attempts", 1),
        )

    grouped: dict[str, list[PlannedCall]] = {}
    for call in accepted:
        ref = call.args.query_ref  # type: ignore[attr-defined]
        if ref in grouped or len(grouped) < max_queries:
            grouped.setdefault(ref, []).append(_planned(call, ref))
    if not grouped:
        return degraded("AI planner selected no valid queries; measured the top queries by demand", attempts=attempts)

    targets = [_complete_target(ref, by_ref[ref], calls, deps, brand) for ref, calls in grouped.items()]
    completed = sum(len(t.calls) for t in targets) - len(accepted)
    return NodeResult(
        update={"measurement_targets": targets},
        status=StepStatus.OK,
        summary={
            "source": "llm_repaired" if repaired else "llm",
            "queries": {t.ref: t.candidate.keyword for t in targets},
            "calls_planned": sum(len(t.calls) for t in targets),
            "calls_completed_by_rule": max(0, completed),
            "rejected": [r.feedback() for r in rejected],
        },
        attempts=attempts,
    )
