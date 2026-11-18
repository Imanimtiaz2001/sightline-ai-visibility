"""Extraction / Normalization agents: raw provider payloads -> typed contracts.

No LLM and no I/O; parsing lives in `pipeline.extract`. A payload that
cannot be parsed marks that one channel as failed – it never fails the node.
"""

from __future__ import annotations

import re

from app.db.models import StepStatus
from app.pipeline.contracts import (
    AIEngineChannel,
    AIOverviewChannel,
    Degradation,
    KeywordCandidate,
    OrganicChannel,
    QueryMeasurement,
    StageError,
)
from app.pipeline.extract import (
    EntityMatcher,
    extract_ai_answer,
    extract_keywords,
    extract_serp,
    merge_candidates,
    short_phrase,
)
from app.pipeline.nodes.base import NodeResult
from app.pipeline.state import PipelineDeps, PipelineState
from app.providers.dataforseo.tools import ToolResult

MIN_VOLUME = 10
MAX_CANDIDATES = 40


def discovery_normalizer(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    brand = state["brand"]
    results: list[ToolResult] = state.get("discovery_results") or []
    raw: list[KeywordCandidate] = []
    unparsable = 0
    for r in results:
        if not r.ok:
            continue
        try:
            raw.extend(extract_keywords(r.tool, r.data))
        except Exception:  # junk payload: drop this result, keep the rest
            unparsable += 1
    merged = merge_candidates(raw)
    brand_re = re.compile(rf"\b{re.escape(brand.name.lower())}\b")

    def keep(c: KeywordCandidate) -> bool:
        if brand_re.search(c.keyword) or brand.domain in c.keyword:
            return False  # the brand's own navigational queries are not discovery
        explicitly_requested = "search_volume" in c.sources
        return explicitly_requested or (c.search_volume or 0) >= MIN_VOLUME

    kept = sorted(
        (c for c in merged if keep(c)),
        key=lambda c: ("search_volume" not in c.sources, -(c.search_volume or 0)),
    )[:MAX_CANDIDATES]
    degradations = (
        [
            Degradation(
                node="discovery_normalizer", kind="partial", message=f"{unparsable} keyword payload(s) were unreadable"
            )
        ]
        if unparsable
        else []
    )
    return NodeResult(
        update={"candidates": kept},
        status=StepStatus.DEGRADED if unparsable else StepStatus.OK,
        summary={"raw": len(raw), "unique": len(merged), "kept": len(kept), "top": [c.keyword for c in kept[:5]]},
        degradations=degradations,
    )


def seed_fallback(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    """Fallback when discovery yields nothing: measure the profile's own topics.

    Visibility is still measured live; only demand is unknown, and the scorer
    and UI flag those queries as 'volume unknown' instead of inventing numbers.
    """
    brand = state["brand"]
    phrases = [brand.question or "", *brand.seed_topics, brand.market]
    candidates = [
        KeywordCandidate(keyword=p, sources=["profile"], volume_known=False)
        for p in dict.fromkeys(short_phrase(x, 8).lower() for x in phrases if x)
        if 2 <= len(p) <= 80
    ]
    return NodeResult(
        update={"candidates": candidates},
        status=StepStatus.DEGRADED,
        summary={"candidates": [c.keyword for c in candidates]},
        degradations=[
            Degradation(
                node="seed_fallback",
                kind="fallback",
                message="Keyword research returned no usable queries; measuring your profile topics with unknown search volume",
            )
        ],
    )


def _error(stage: str, r: ToolResult) -> StageError:
    e = r.error or {}
    return StageError(
        stage=stage,
        code=str(e.get("code", "unknown")),
        message=str(e.get("message", ""))[:300],
        tool=r.tool,
        retryable=e.get("retryable"),
    )


def _measurement_gap_message(partial: int, failed: int) -> str:
    parts = []
    if partial:
        parts.append(f"{partial} question{'s were' if partial != 1 else ' was'} only partly measured")
    if failed:
        parts.append(f"{failed} question{'s' if failed != 1 else ''} could not be measured at all")
    return " and ".join(parts) + "; missing channels are excluded from their scores"


def measurement_normalizer(state: PipelineState, deps: PipelineDeps) -> NodeResult:
    brand = state["brand"]
    matcher = EntityMatcher(brand)
    engine_models = deps.settings.engine_models
    order = {t.ref: i for i, t in enumerate(state.get("measurement_targets") or [])}
    measurements: list[QueryMeasurement] = []
    parse_failures = 0

    for target, results in sorted(state.get("measurement_results") or [], key=lambda p: order.get(p[0].ref, 999)):
        organic, overview = OrganicChannel(), AIOverviewChannel()
        engines = {e: AIEngineChannel(engine=e, model=m) for e, m in engine_models.items()}
        errors: list[StageError] = []
        for r in results:
            engine = r.tool.removesuffix("_answer")
            if not r.ok:
                errors.append(_error("retrieval", r))
                if r.tool == "google_serp":
                    organic, overview = OrganicChannel(status="failed"), AIOverviewChannel(status="failed")
                elif engine in engines:
                    engines[engine] = AIEngineChannel(engine=engine, model=engine_models[engine], status="failed")
                continue
            try:
                if r.tool == "google_serp":
                    organic, overview = extract_serp(r.data, matcher)
                elif engine in engines:
                    engines[engine] = extract_ai_answer(
                        r.data, engine=engine, model=engine_models[engine], matcher=matcher
                    )
            except Exception as exc:
                parse_failures += 1
                errors.append(StageError(stage="normalization", code="unparsable", message=str(exc)[:300], tool=r.tool))
        measurements.append(
            QueryMeasurement(
                ref=target.ref,
                candidate=target.candidate,
                prompt=target.prompt,
                organic=organic,
                ai_overview=overview,
                engines=list(engines.values()),
                errors=errors,
            )
        )

    by_status = {s: sum(1 for m in measurements if m.status == s) for s in ("measured", "partial", "failed")}
    degradations = []
    if by_status["partial"] or by_status["failed"]:
        degradations.append(
            Degradation(
                node="measurement_normalizer",
                kind="partial",
                message=_measurement_gap_message(by_status["partial"], by_status["failed"]),
            )
        )
    status = StepStatus.OK
    if not measurements or by_status["failed"] == len(measurements):
        status = StepStatus.FAILED
    elif degradations or parse_failures:
        status = StepStatus.DEGRADED
    return NodeResult(
        update={"measurements": measurements},
        status=status,
        summary={**by_status, "parse_failures": parse_failures},
        degradations=degradations,
    )
