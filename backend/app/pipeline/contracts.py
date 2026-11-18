"""Typed contracts between pipeline stages.

Every stage reads and writes only these models, so each stage can be tested
and replaced in isolation, and a failure in one stage leaves the outputs of
the others intact and well-typed.

The `*Output` models at the bottom are the *LLM-facing* schemas: they are
sent to the model as forced-tool schemas and every response is validated
against them before it can influence anything else.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal["informational", "navigational", "commercial", "transactional"]
Channel = Literal["organic", "ai_overview", "ai_answers"]
MeasureStatus = Literal["ok", "failed", "not_measured"]


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- run input -----------------------------------------------------------------


class CompetitorRef(Contract):
    name: str
    domain: str


class BrandContext(Contract):
    """Frozen snapshot of the brand profile a run measures."""

    brand_id: str
    name: str
    domain: str
    market: str
    description: str = ""
    location_code: int
    location_name: str
    language_code: str
    seed_topics: list[str] = Field(default_factory=list)
    competitors: list[CompetitorRef] = Field(default_factory=list)
    question: str | None = None


# --- discovery --------------------------------------------------------------------


class PlannedCall(Contract):
    """A tool call that passed validation and is approved for execution."""

    id: str
    tool: str
    args: dict[str, object]
    query_ref: str | None = None


class KeywordCandidate(Contract):
    keyword: str
    search_volume: int | None = None
    competition: float | None = None
    cpc: float | None = None
    keyword_difficulty: int | None = None
    intent: Intent | None = None
    sources: list[str] = Field(default_factory=list)
    volume_known: bool = True


# --- measurement --------------------------------------------------------------------


class MeasurementTarget(Contract):
    ref: str
    candidate: KeywordCandidate
    prompt: str
    calls: list[PlannedCall]


class SerpResult(Contract):
    position: int
    domain: str
    title: str
    url: str


class SourceRef(Contract):
    domain: str
    url: str
    title: str = ""


class OrganicChannel(Contract):
    status: MeasureStatus = "not_measured"
    brand_position: int | None = None
    brand_url: str | None = None
    competitor_positions: dict[str, int] = Field(default_factory=dict)
    top_results: list[SerpResult] = Field(default_factory=list)
    people_also_ask: list[str] = Field(default_factory=list)


class AIOverviewChannel(Contract):
    status: MeasureStatus = "not_measured"
    present: bool = False
    brand_cited: bool = False
    brand_mentioned: bool = False
    competitors_cited: list[str] = Field(default_factory=list)
    references: list[SourceRef] = Field(default_factory=list)


class AIEngineChannel(Contract):
    engine: str
    model: str
    status: MeasureStatus = "not_measured"
    brand_mentioned: bool = False
    brand_cited: bool = False
    competitors_mentioned: list[str] = Field(default_factory=list)
    cited_domains: list[str] = Field(default_factory=list)
    excerpt: str = ""
    brand_context: str | None = None


class StageError(Contract):
    stage: str
    code: str
    message: str
    tool: str | None = None
    retryable: bool | None = None


class QueryMeasurement(Contract):
    ref: str
    candidate: KeywordCandidate
    prompt: str
    organic: OrganicChannel
    ai_overview: AIOverviewChannel
    engines: list[AIEngineChannel]
    errors: list[StageError] = Field(default_factory=list)

    @property
    def status(self) -> Literal["measured", "partial", "failed"]:
        statuses = [self.organic.status, self.ai_overview.status, *(e.status for e in self.engines)]
        measured = [s for s in statuses if s != "not_measured"]
        ok = sum(1 for s in measured if s == "ok")
        if ok == 0:
            return "failed"
        return "measured" if ok == len(measured) else "partial"


# --- scoring -----------------------------------------------------------------------


class ScoreFactor(Contract):
    name: str
    value: float
    explanation: str


class ScoreBreakdown(Contract):
    score: float
    visibility: float | None
    factors: list[ScoreFactor]
    channel_visibility: dict[str, float | None]
    summary: str


class ScoredQuery(Contract):
    measurement: QueryMeasurement
    breakdown: ScoreBreakdown

    @property
    def ref(self) -> str:
        return self.measurement.ref


# --- LLM output schemas ----------------------------------------------------------------

GapType = Literal[
    "absent_everywhere",
    "missing_in_ai_answers",
    "missing_in_ai_overview",
    "ranking_low_organic",
    "competitor_dominated",
    "already_strong",
]


class GapJudgement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query_ref: str = Field(pattern=r"^q\d{1,3}$", description="The query id you are judging, e.g. 'q2'.")
    worth_closing: bool = Field(description="True if the brand should invest in closing this gap now.")
    gap_type: GapType = Field(description="The single best description of the gap.")
    reason: str = Field(
        min_length=20, max_length=400, description="One or two sentences grounded only in the provided data."
    )
    competitors_winning: list[str] = Field(
        default_factory=list, max_length=5, description="Competitor names (from the provided list) visible here."
    )
    confidence: Literal["low", "medium", "high"]


class GapAnalysisOutput(BaseModel):
    """Judge which of the measured visibility gaps are worth closing, and why."""

    model_config = ConfigDict(extra="ignore")

    judgements: list[GapJudgement] = Field(min_length=1, max_length=25)
    headline_insights: list[str] = Field(
        min_length=1, max_length=4, description="2-4 short, specific, data-backed insights for a marketer."
    )


ContentType = Literal[
    "comparison_page",
    "alternatives_page",
    "buyers_guide",
    "how_to_guide",
    "faq_hub",
    "use_case_page",
    "case_study",
    "landing_page_update",
    "third_party_listing",
]


class RecommendationDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query_ref: str = Field(pattern=r"^q\d{1,3}$")
    title: str = Field(min_length=10, max_length=120, description="Working title of the content to publish.")
    content_type: ContentType
    target_channels: list[Channel] = Field(min_length=1, max_length=3)
    rationale: str = Field(min_length=40, max_length=600, description="Why this closes the gap; cite the data.")
    outline: list[str] = Field(min_length=3, max_length=8, description="Section headings in order.")
    key_points: list[str] = Field(min_length=2, max_length=6, description="Claims/facts the piece must make.")
    sources_to_earn: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Domains (from the cited sources provided) where a mention would influence AI answers.",
    )
    effort: Literal["low", "medium", "high"]


class RecommendationOutput(BaseModel):
    """Content recommendations, one or more per gap you were given."""

    model_config = ConfigDict(extra="ignore")

    recommendations: list[RecommendationDraft] = Field(min_length=1, max_length=15)


class Degradation(Contract):
    """Recorded whenever a stage falls back or loses data, so the user is told."""

    node: str
    kind: Literal["fallback", "partial", "skipped", "failed", "budget"]
    message: str
