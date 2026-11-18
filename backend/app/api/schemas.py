"""Request and response models of the public API (the frontend contract)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.db.models import RecommendationStatus, RunStatus, StepStatus
from app.pipeline.extract import normalize_domain
from app.reference import LOCATIONS, get_location

_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _domain(value: str) -> str:
    cleaned = normalize_domain(value)
    if not _DOMAIN.match(cleaned):
        raise ValueError("must be a domain like example.com")
    return cleaned


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


Domain = Annotated[str, AfterValidator(_domain)]
Name = Annotated[str, Field(min_length=1, max_length=80), AfterValidator(_clean_text)]
Topic = Annotated[str, Field(min_length=2, max_length=80), AfterValidator(_clean_text)]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- brands ---------------------------------------------------------------------------------


class CompetitorIn(In):
    name: Name
    domain: Domain


class BrandIn(In):
    name: Name = Field(description="Brand name as people write it, e.g. 'Acme'.")
    domain: Domain = Field(description="Primary website domain, e.g. 'acme.com'.")
    market: Annotated[str, Field(min_length=3, max_length=200), AfterValidator(_clean_text)] = Field(
        description="The category the brand competes in, e.g. 'project management software for agencies'."
    )
    description: Annotated[str, Field(max_length=1000)] = ""
    location_code: int = Field(
        default=2840, description="DataForSEO location code; see GET /api/v1/reference/locations."
    )
    language_code: str = Field(default="en", min_length=2, max_length=8)
    seed_topics: list[Topic] = Field(default_factory=list, max_length=10)
    competitors: list[CompetitorIn] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def _consistency(self) -> BrandIn:
        location = get_location(self.location_code)
        if location is None:
            raise ValueError(f"location_code must be one of {[loc['code'] for loc in LOCATIONS]}")
        if self.language_code not in location["languages"]:
            raise ValueError(f"language_code must be one of {location['languages']} for {location['name']}")
        domains = [c.domain for c in self.competitors]
        if self.domain in domains:
            raise ValueError("a competitor cannot use the brand's own domain")
        if len(set(domains)) != len(domains):
            raise ValueError("competitor domains must be unique")
        names = [c.name.lower() for c in self.competitors]
        if len(set(names)) != len(names) or self.name.lower() in names:
            raise ValueError("competitor names must be unique and differ from the brand name")
        self.seed_topics = list(dict.fromkeys(self.seed_topics))
        return self


class CompetitorOut(Out):
    id: str
    name: str
    domain: str


class TrendPoint(BaseModel):
    run_id: str
    finished_at: datetime | None
    visibility_score: float | None
    status: str


class RunBrief(Out):
    id: str
    status: RunStatus
    created_at: datetime
    finished_at: datetime | None
    visibility_score: float | None
    question: str | None


class BrandOut(Out):
    id: str
    name: str
    domain: str
    market: str
    description: str
    location_code: int
    location_name: str
    language_code: str
    seed_topics: list[str]
    competitors: list[CompetitorOut]
    created_at: datetime
    updated_at: datetime
    latest_run: RunBrief | None = None
    latest_completed_run: RunBrief | None = None
    active_run_id: str | None = None
    runs_count: int = 0
    trend: list[TrendPoint] = Field(default_factory=list)


class BrandList(BaseModel):
    items: list[BrandOut]
    total: int


# --- runs ---------------------------------------------------------------------------------------


class RunCreate(In):
    question: Annotated[str, Field(min_length=5, max_length=300), AfterValidator(_clean_text)] | None = Field(
        default=None,
        description="Optional research question, e.g. \"How does Acme show up for 'best project management software'?\"",
    )


class StepOut(Out):
    id: str
    node: str
    item_key: str | None
    status: StepStatus
    attempts: int
    started_at: datetime
    finished_at: datetime | None
    duration_ms: float | None
    output_summary: dict[str, Any] | None
    error: dict[str, Any] | None


class StageProgress(BaseModel):
    stage: str
    label: str
    status: Literal["pending", "running", "ok", "degraded", "failed", "skipped"]
    detail: str | None = None
    completed: int = 0
    total: int = 0


class RunOut(Out):
    id: str
    brand_id: str
    status: RunStatus
    question: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    current_node: str | None
    retry_of_run_id: str | None
    data_mode: str
    llm_model: str
    visibility_score: float | None
    cost_usd: float
    degradations: list[dict[str, Any]]
    error: dict[str, Any] | None
    stages: list[StageProgress]
    report: dict[str, Any] | None
    metrics: dict[str, Any] | None
    profile: dict[str, Any]


class RunPage(BaseModel):
    items: list[RunBrief]
    total: int
    limit: int
    offset: int


class RunTrace(BaseModel):
    run_id: str
    trace_id: str
    status: RunStatus
    steps: list[StepOut]
    metrics: dict[str, Any] | None
    log_query: str = Field(description="Filter to paste into your log store to see every log line of this run.")


# --- queries / opportunities --------------------------------------------------------------------

ChannelName = Literal["organic", "ai_overview", "ai_answers"]


class QueryFilter(In):
    search: str | None = Field(default=None, max_length=100, description="Substring match on the query text.")
    intent: list[Literal["informational", "navigational", "commercial", "transactional"]] | None = None
    missing_in: ChannelName | None = Field(
        default=None, description="Only queries where the brand is not visible in this channel."
    )
    worth_closing: bool | None = None
    min_score: float | None = Field(default=None, ge=0, le=100)
    min_volume: int | None = Field(default=None, ge=0)
    sort: Literal["-score", "score", "-volume", "volume", "visibility", "-visibility"] = "-score"
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class QueryOut(Out):
    id: str
    run_id: str
    rank: int
    text: str
    prompt: str
    source: str
    intent: str | None
    search_volume: int | None
    competition: float | None
    cpc: float | None
    keyword_difficulty: int | None
    status: Literal["measured", "partial", "failed"]
    visibility: float | None
    opportunity_score: float
    score_breakdown: dict[str, Any]
    channels: dict[str, Any]
    analysis: dict[str, Any] | None
    errors: list[dict[str, Any]]
    recommendation_ids: list[str] = Field(default_factory=list)


class QueryPage(BaseModel):
    items: list[QueryOut]
    total: int
    limit: int
    offset: int
    facets: dict[str, dict[str, int]]


class QueryHistoryPoint(BaseModel):
    run_id: str
    measured_at: datetime | None
    visibility: float | None
    opportunity_score: float
    organic_position: int | None
    ai_mentions: int
    ai_measured: int


class QueryDetail(QueryOut):
    history: list[QueryHistoryPoint]


# --- recommendations ------------------------------------------------------------------------------


class RecommendationOut(Out):
    id: str
    run_id: str
    brand_id: str
    query_id: str
    query_text: str
    priority: int
    title: str
    content_type: str
    effort: str
    target_channels: list[str]
    rationale: str
    outline: list[str]
    key_points: list[str]
    sources_to_earn: list[str]
    source: Literal["llm", "rule"]
    status: RecommendationStatus
    status_updated_at: datetime | None
    created_at: datetime
    opportunity_score: float


class RecommendationPatch(In):
    status: RecommendationStatus


class RecommendationFilter(In):
    status: list[RecommendationStatus] | None = None


# --- comparison / overview ---------------------------------------------------------------------------


class QueryDelta(BaseModel):
    text: str
    change: Literal["improved", "declined", "unchanged", "new", "dropped"]
    baseline_visibility: float | None
    current_visibility: float | None
    visibility_delta: float | None
    baseline_score: float | None
    current_score: float | None
    baseline_position: int | None
    current_position: int | None
    baseline_ai_mentions: int | None
    current_ai_mentions: int | None


class RunComparison(BaseModel):
    current: RunBrief
    baseline: RunBrief | None
    visibility_delta: float | None
    improved: int
    declined: int
    unchanged: int
    new: int
    dropped: int
    queries: list[QueryDelta]
    channel_coverage: dict[str, dict[str, dict[str, int] | None]]


class ComparisonParams(In):
    baseline: str | None = Field(
        default=None, description="Run id to compare against; defaults to the previous completed run."
    )


class BrandOverview(BaseModel):
    brand: BrandOut
    open_recommendations: int
    gaps_worth_closing: int | None
    top_opportunity: dict[str, Any] | None
    visibility_delta: float | None


class Overview(BaseModel):
    brands: list[BrandOverview]
    totals: dict[str, Any]


# --- misc ----------------------------------------------------------------------------------------------


class LocationOut(BaseModel):
    code: int
    name: str
    iso: str
    languages: list[dict[str, str]]


class MetaOut(BaseModel):
    version: str
    environment: str
    data_mode: Literal["live", "fixtures"]
    synthetic_data: bool
    llm_provider: str
    llm_model: str
    ai_engines: list[dict[str, str]]
    configured: bool
    configuration_problems: list[str]
    limits: dict[str, Any]


class ClientErrorIn(In):
    kind: Literal["error", "unhandled_rejection", "http", "resource"]
    message: Annotated[str, Field(min_length=1, max_length=1000)]
    stack: Annotated[str, Field(max_length=8000)] | None = None
    url: Annotated[str, Field(max_length=1000)] | None = None
    route: Annotated[str, Field(max_length=300)] | None = None
    request_id: Annotated[str, Field(max_length=64)] | None = None
    trace_id: Annotated[str, Field(max_length=64)] | None = None
    session_id: Annotated[str, Field(max_length=64)] | None = None
    release: Annotated[str, Field(max_length=64)] | None = None
    context: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _small_context(self) -> ClientErrorIn:
        if self.context is not None and len(str(self.context)) > 4000:
            raise ValueError("context is too large")
        return self


class Health(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    checks: dict[str, Any]
