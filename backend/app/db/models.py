"""Persistence model.

History is append-only at the run level: a re-measure creates a new `Run`
with its own `Query` and `Recommendation` rows and a frozen snapshot of the
brand profile it measured. Nothing a finished run produced is ever updated,
except the user-owned workflow state of a recommendation.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UtcDateTime, utcnow
from app.observability.context import new_id


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"  # finished, but some stages degraded or some queries failed
    FAILED = "failed"  # nothing usable was produced

    @property
    def is_terminal(self) -> bool:
        return self in {RunStatus.SUCCEEDED, RunStatus.PARTIAL, RunStatus.FAILED}


class StepStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    DEGRADED = "degraded"  # finished using a fallback or with partial data
    FAILED = "failed"
    SKIPPED = "skipped"


class RecommendationStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DISMISSED = "dismissed"


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    domain: Mapped[str] = mapped_column(String(253), unique=True)
    market: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    location_code: Mapped[int] = mapped_column(Integer, default=2840)
    language_code: Mapped[str] = mapped_column(String(8), default="en")
    seed_topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    competitors: Mapped[list[Competitor]] = relationship(
        back_populates="brand", cascade="all, delete-orphan", order_by="Competitor.position"
    )
    runs: Mapped[list[Run]] = relationship(back_populates="brand", cascade="all, delete-orphan")


class Competitor(Base):
    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("brand_id", "domain"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    domain: Mapped[str] = mapped_column(String(253))
    position: Mapped[int] = mapped_column(Integer, default=0)

    brand: Mapped[Brand] = relationship(back_populates="competitors")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("brand_id", "idempotency_key"),
        Index("ix_runs_brand_created", "brand_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.QUEUED.value, index=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retry_of_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    profile_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    data_mode: Mapped[str] = mapped_column(String(16))
    llm_model: Mapped[str] = mapped_column(String(80))
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    degradations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    brand: Mapped[Brand] = relationship(back_populates="runs")
    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunStep.started_at"
    )
    queries: Mapped[list[Query]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Query.rank"
    )
    recommendations: Mapped[list[Recommendation]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Recommendation.priority"
    )


class RunStep(Base):
    """One node execution – the span of the run's trace."""

    __tablename__ = "run_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    node: Mapped[str] = mapped_column(String(64))
    item_key: Mapped[str | None] = mapped_column(String(300), nullable=True)  # fan-out branch key
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[Run] = relationship(back_populates="steps")


class Query(Base):
    """A question measured in one run, with its per-channel visibility."""

    __tablename__ = "queries"
    __table_args__ = (Index("ix_queries_brand_text", "brand_id", "normalized_text"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    brand_id: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(300))
    normalized_text: Mapped[str] = mapped_column(String(300))
    prompt: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(32))
    intent: Mapped[str | None] = mapped_column(String(24), nullable=True)
    search_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    competition: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpc: Mapped[float | None] = mapped_column(Float, nullable=True)
    keyword_difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    channels: Mapped[dict[str, Any]] = mapped_column(JSON)
    visibility: Mapped[float | None] = mapped_column(Float, nullable=True)
    opportunity_score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    run: Mapped[Run] = relationship(back_populates="queries")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    brand_id: Mapped[str] = mapped_column(String(32), index=True)
    query_id: Mapped[str] = mapped_column(ForeignKey("queries.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer)  # 1 = do first
    title: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(32))
    effort: Mapped[str] = mapped_column(String(8))
    target_channels: Mapped[list[str]] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    outline: Mapped[list[str]] = mapped_column(JSON)
    key_points: Mapped[list[str]] = mapped_column(JSON)
    sources_to_earn: Mapped[list[str]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(8))  # "llm" | "rule"
    status: Mapped[str] = mapped_column(String(16), default=RecommendationStatus.OPEN.value)
    status_updated_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    run: Mapped[Run] = relationship(back_populates="recommendations")
    query: Mapped[Query] = relationship()


class ProviderCacheEntry(Base):
    """Content-addressed cache of paid provider responses (avoid paying twice)."""

    __tablename__ = "provider_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)


class ClientErrorReport(Base):
    __tablename__ = "client_errors"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(24))
    message: Mapped[str] = mapped_column(String(1000))
    stack: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    release: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
