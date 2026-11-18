"""Graph state and the dependencies injected into nodes."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from app.config import Settings
from app.core.call_context import CallContext
from app.pipeline.contracts import (
    BrandContext,
    Degradation,
    GapAnalysisOutput,
    KeywordCandidate,
    MeasurementTarget,
    PlannedCall,
    QueryMeasurement,
    RecommendationDraft,
    ScoredQuery,
)
from app.pipeline.recorder import StepRecorder
from app.providers.dataforseo.tools import ToolExecutor, ToolRegistry, ToolResult
from app.providers.llm.gateway import LLMGateway


class PipelineState(TypedDict, total=False):
    brand: BrandContext
    discovery_plan: list[PlannedCall]
    discovery_plan_source: str
    discovery_results: list[ToolResult]
    candidates: list[KeywordCandidate]
    measurement_targets: list[MeasurementTarget]
    measurement_results: Annotated[list[tuple[MeasurementTarget, list[ToolResult]]], operator.add]
    measurements: list[QueryMeasurement]
    scored: list[ScoredQuery]
    analysis: GapAnalysisOutput
    analysis_source: str
    recommendations: list[RecommendationDraft]
    recommendations_source: str
    report: dict[str, Any]
    degradations: Annotated[list[Degradation], operator.add]


class BranchState(TypedDict):
    """State handed to one parallel measurement branch via `Send`."""

    brand: BrandContext
    target: MeasurementTarget


@dataclass(frozen=True)
class PipelineDeps:
    settings: Settings
    llm: LLMGateway
    tools: ToolRegistry
    executor: ToolExecutor
    ctx: CallContext
    recorder: StepRecorder
