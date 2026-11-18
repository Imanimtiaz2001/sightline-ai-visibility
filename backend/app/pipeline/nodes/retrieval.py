"""Search / Retrieval agents: execute approved tool calls. No LLM, no interpretation.

Arguments are re-validated against the tool schema immediately before
execution (defence in depth: plans may come from the LLM, the repair turn or
a fallback rule, and state could be tampered with between nodes).
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from pydantic import ValidationError

from app.db.models import StepStatus
from app.observability.context import bound, current
from app.pipeline.contracts import Degradation, PlannedCall
from app.pipeline.nodes.base import NodeResult
from app.pipeline.state import BranchState, PipelineDeps, PipelineState
from app.providers.dataforseo.tools import ToolResult, ValidatedCall


def _execute_all(calls: list[PlannedCall], deps: PipelineDeps, max_workers: int) -> list[ToolResult]:
    log_context = current()

    def run(planned: PlannedCall) -> ToolResult:
        with bound(**log_context, tool=planned.tool):
            spec = deps.tools.get(planned.tool)
            if spec is None:
                return ToolResult(
                    planned.id,
                    planned.tool,
                    dict(planned.args),
                    False,
                    [],
                    error={"code": "unknown_tool", "message": f"unknown tool {planned.tool}"},
                )
            try:
                args = spec.args_model.model_validate(planned.args)
            except ValidationError as exc:
                return ToolResult(
                    planned.id,
                    planned.tool,
                    dict(planned.args),
                    False,
                    [],
                    error={"code": "invalid_args", "message": str(exc.errors()[:3])},
                )
            return deps.executor.execute(ValidatedCall(planned.id, spec, args), deps.ctx)

    if not calls:
        return []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(calls)), thread_name_prefix="retrieval") as pool:
        return list(pool.map(run, calls))


TOOL_LABELS = {
    "keyword_suggestions": "Keyword research",
    "related_keywords": "Related-keyword research",
    "search_volume": "Search-volume lookup",
    "google_serp": "Google search results",
    "chat_gpt_answer": "ChatGPT",
    "perplexity_answer": "Perplexity",
    "gemini_answer": "Gemini",
    "claude_answer": "Claude",
}
REASONS = {
    "transient": "the provider kept failing after {attempts} attempts",
    "rate_limited": "the provider kept rate-limiting us after {attempts} attempts",
    "circuit_open": "the provider has been failing repeatedly, so calls were paused",
    "budget_exceeded": "the run's cost limit was reached",
    "auth_failed": "the provider rejected the credentials",
    "quota_exhausted": "the provider account is out of credit",
    "permanent": "the provider rejected the request",
}


def describe_failure(r: ToolResult, label: str | None) -> str:
    """User-facing sentence; the raw provider error stays in the step trace and logs."""
    code = str((r.error or {}).get("code"))
    reason = REASONS.get(code, "it failed").format(attempts=max(1, r.attempts))
    what = TOOL_LABELS.get(r.tool, r.tool)
    return f"{what} unavailable for '{label}': {reason}" if label else f"{what} unavailable: {reason}"


def _outcome(
    results: list[ToolResult], node: str, label: str | None = None
) -> tuple[StepStatus, list[Degradation], dict[str, object]]:
    failed = [r for r in results if not r.ok]
    codes = Counter(str((r.error or {}).get("code")) for r in failed)
    summary: dict[str, object] = {
        "calls": len(results),
        "ok": len(results) - len(failed),
        "failed": len(failed),
        "cache_hits": sum(1 for r in results if r.cached),
        "cost_usd": round(sum(r.cost_usd for r in results), 5),
        "failure_codes": dict(codes),
    }
    if not failed:
        return StepStatus.OK, [], summary
    degradations = []
    for r in failed:
        code = str((r.error or {}).get("code"))
        degradations.append(
            Degradation(
                node=node,
                kind="budget" if code == "budget_exceeded" else "partial",
                message=describe_failure(r, label)[:300],
            )
        )
    status = StepStatus.FAILED if len(failed) == len(results) else StepStatus.DEGRADED
    return status, degradations, summary


def discovery_retriever(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    plan = state.get("discovery_plan") or []
    results = _execute_all(plan, deps, max_workers=4)
    status, degradations, summary = _outcome(results, "discovery_retriever")
    return NodeResult(
        update={"discovery_results": results},
        status=status if plan else StepStatus.SKIPPED,
        summary=summary,
        attempts=max((r.attempts for r in results), default=0),
        degradations=degradations,
    )


def measurement_retriever(state: BranchState, deps: PipelineDeps) -> NodeResult:
    """One parallel branch per query (fan-out via LangGraph `Send`)."""
    target = state["target"]
    results = _execute_all(target.calls, deps, max_workers=len(target.calls) or 1)
    status, degradations, summary = _outcome(results, "measurement_retriever", target.candidate.keyword)
    return NodeResult(
        update={"measurement_results": [(target, results)]},
        status=status,
        summary={**summary, "query": target.candidate.keyword},
        attempts=max((r.attempts for r in results), default=0),
        degradations=degradations,
    )
