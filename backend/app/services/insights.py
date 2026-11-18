"""Read-side queries: opportunities, history, comparison, portfolio overview, export."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import (
    BrandOverview,
    Overview,
    QueryDelta,
    QueryDetail,
    QueryFilter,
    QueryHistoryPoint,
    QueryOut,
    QueryPage,
    RunComparison,
)
from app.core.errors import ApiError, NotFound
from app.db.models import Brand, Query, Recommendation, RecommendationStatus, Run, RunStatus
from app.services.presenters import brand_out, query_out, run_brief

COMPLETED = (RunStatus.SUCCEEDED.value, RunStatus.PARTIAL.value)
SIGNIFICANT = 0.05  # visibility change (5 points) treated as a real move rather than noise


def get_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id, options=[selectinload(Run.steps)])
    if run is None:
        raise NotFound("run", run_id)
    return run


def _ai_mentions(q: Query) -> tuple[int, int]:
    engines = [e for e in (q.channels or {}).get("ai_engines", []) if e.get("status") == "ok"]
    return sum(1 for e in engines if e.get("brand_mentioned")), len(engines)


def _channel_missing(q: Query, channel: str) -> bool:
    value = (q.score_breakdown or {}).get("channel_visibility", {}).get(channel)
    return value is not None and value == 0


def _matches(q: Query, f: QueryFilter) -> bool:
    if f.search and f.search.lower() not in q.text:
        return False
    if f.intent and q.intent not in f.intent:
        return False
    if f.missing_in and not _channel_missing(q, f.missing_in):
        return False
    if f.worth_closing is not None and bool((q.analysis or {}).get("worth_closing")) != f.worth_closing:
        return False
    if f.min_score is not None and q.opportunity_score < f.min_score:
        return False
    return not (f.min_volume is not None and (q.search_volume or 0) < f.min_volume)


_SORTS = {
    "score": lambda q: q.opportunity_score,
    "volume": lambda q: q.search_volume or 0,
    "visibility": lambda q: q.visibility if q.visibility is not None else -1,
}


def list_queries(session: Session, run_id: str, f: QueryFilter) -> QueryPage:
    get_run(session, run_id)
    queries = list(session.scalars(select(Query).where(Query.run_id == run_id)))
    recs: dict[str, list[str]] = {}
    for rec_id, query_id in session.execute(
        select(Recommendation.id, Recommendation.query_id).where(Recommendation.run_id == run_id)
    ):
        recs.setdefault(query_id, []).append(rec_id)
    facets = {
        "intent": dict(Counter(q.intent or "unknown" for q in queries)),
        "missing_in": {
            c: sum(1 for q in queries if _channel_missing(q, c)) for c in ("organic", "ai_overview", "ai_answers")
        },
        "status": dict(Counter(q.status for q in queries)),
    }
    matched = [q for q in queries if _matches(q, f)]
    key = f.sort.lstrip("-")
    matched.sort(key=_SORTS[key], reverse=f.sort.startswith("-"))
    page = matched[f.offset : f.offset + f.limit]
    return QueryPage(
        items=[query_out(q, recs.get(q.id)) for q in page],
        total=len(matched),
        limit=f.limit,
        offset=f.offset,
        facets=facets,
    )


def query_detail(session: Session, query_id: str) -> QueryDetail:
    q = session.get(Query, query_id)
    if q is None:
        raise NotFound("query", query_id)
    history_rows = session.execute(
        select(Query, Run.finished_at)
        .join(Run, Run.id == Query.run_id)
        .where(Query.brand_id == q.brand_id, Query.normalized_text == q.normalized_text, Run.status.in_(COMPLETED))
        .order_by(Run.created_at)
    ).all()
    history = []
    for hq, finished in history_rows:
        mentions, measured = _ai_mentions(hq)
        history.append(
            QueryHistoryPoint(
                run_id=hq.run_id,
                measured_at=finished,
                visibility=hq.visibility,
                opportunity_score=hq.opportunity_score,
                organic_position=(hq.channels or {}).get("organic", {}).get("brand_position"),
                ai_mentions=mentions,
                ai_measured=measured,
            )
        )
    rec_ids = list(session.scalars(select(Recommendation.id).where(Recommendation.query_id == q.id)))
    base: QueryOut = query_out(q, rec_ids)
    return QueryDetail(**base.model_dump(), history=history)


def _previous_completed(session: Session, run: Run) -> Run | None:
    return session.scalar(
        select(Run)
        .where(Run.brand_id == run.brand_id, Run.status.in_(COMPLETED), Run.created_at < run.created_at)
        .order_by(Run.created_at.desc())
        .limit(1)
    )


def compare_runs(session: Session, run_id: str, baseline_id: str | None) -> RunComparison:
    current = get_run(session, run_id)
    baseline: Run | None
    if baseline_id:
        baseline = get_run(session, baseline_id)
        if baseline.brand_id != current.brand_id:
            raise ApiError(422, "Validation failed", "baseline must belong to the same brand")
    else:
        baseline = _previous_completed(session, current)
    cur_q = {q.normalized_text: q for q in session.scalars(select(Query).where(Query.run_id == current.id))}
    base_q = (
        {q.normalized_text: q for q in session.scalars(select(Query).where(Query.run_id == baseline.id))}
        if baseline
        else {}
    )

    deltas: list[QueryDelta] = []
    for text in sorted(set(cur_q) | set(base_q)):
        c, b = cur_q.get(text), base_q.get(text)
        cv, bv = (c.visibility if c else None), (b.visibility if b else None)
        if c and not b:
            change = "new"
        elif b and not c:
            change = "dropped"
        elif cv is None or bv is None or abs(cv - bv) < SIGNIFICANT:
            change = "unchanged"
        else:
            change = "improved" if cv > bv else "declined"
        cm = _ai_mentions(c)[0] if c else None
        bm = _ai_mentions(b)[0] if b else None
        deltas.append(
            QueryDelta(
                text=text,
                change=change,
                baseline_visibility=bv,
                current_visibility=cv,
                visibility_delta=round(cv - bv, 3) if cv is not None and bv is not None else None,
                baseline_score=b.opportunity_score if b else None,
                current_score=c.opportunity_score if c else None,
                baseline_position=(b.channels or {}).get("organic", {}).get("brand_position") if b else None,
                current_position=(c.channels or {}).get("organic", {}).get("brand_position") if c else None,
                baseline_ai_mentions=bm,
                current_ai_mentions=cm,
            )
        )
    order = {"improved": 0, "declined": 1, "new": 2, "dropped": 3, "unchanged": 4}
    deltas.sort(key=lambda d: (order[d.change], -abs(d.visibility_delta or 0)))
    counts = Counter(d.change for d in deltas)

    def coverage(run: Run | None) -> dict[str, dict[str, int]] | None:
        return ((run.report or {}).get("summary") or {}).get("channel_coverage") if run else None

    delta = (
        round(current.visibility_score - baseline.visibility_score, 1)
        if baseline and current.visibility_score is not None and baseline.visibility_score is not None
        else None
    )
    cur_cov, base_cov = coverage(current) or {}, coverage(baseline) or {}
    return RunComparison(
        current=run_brief(current),
        baseline=run_brief(baseline) if baseline else None,
        visibility_delta=delta,
        improved=counts["improved"],
        declined=counts["declined"],
        unchanged=counts["unchanged"],
        new=counts["new"],
        dropped=counts["dropped"],
        queries=deltas,
        channel_coverage={
            ch: {"current": cur_cov.get(ch), "baseline": base_cov.get(ch)}
            for ch in ("organic", "ai_overview", "ai_answers")
        },
    )


def overview(session: Session) -> Overview:
    brands = list(
        session.scalars(
            select(Brand).options(selectinload(Brand.competitors), selectinload(Brand.runs)).order_by(Brand.name)
        )
    )
    open_counts = dict(
        session.execute(
            select(Recommendation.brand_id, func.count())
            .join(Run, Run.id == Recommendation.run_id)
            .where(Recommendation.status.in_([RecommendationStatus.OPEN.value, RecommendationStatus.IN_PROGRESS.value]))
            .group_by(Recommendation.brand_id)
        ).all()
    )
    items: list[BrandOverview] = []
    for brand in brands:
        out = brand_out(brand)
        latest = next(
            (r for r in sorted(brand.runs, key=lambda r: r.created_at, reverse=True) if r.status in COMPLETED), None
        )
        summary = ((latest.report or {}).get("summary") or {}) if latest else {}
        trend = [p.visibility_score for p in out.trend if p.visibility_score is not None]
        items.append(
            BrandOverview(
                brand=out,
                open_recommendations=int(open_counts.get(brand.id, 0)),
                gaps_worth_closing=summary.get("gaps_worth_closing"),
                top_opportunity=summary.get("top_opportunity"),
                visibility_delta=round(trend[-1] - trend[-2], 1) if len(trend) >= 2 else None,
            )
        )
    scores = [
        i.brand.latest_completed_run.visibility_score
        for i in items
        if i.brand.latest_completed_run and i.brand.latest_completed_run.visibility_score is not None
    ]
    totals: dict[str, Any] = {
        "brands": len(items),
        "average_visibility": round(sum(scores) / len(scores), 1) if scores else None,
        "open_recommendations": sum(i.open_recommendations for i in items),
        "active_runs": sum(1 for i in items if i.brand.active_run_id),
        "never_measured": sum(1 for i in items if i.brand.latest_completed_run is None),
    }
    return Overview(brands=items, totals=totals)


# --- export --------------------------------------------------------------------------------------

# A leading =, +, - or @ makes a spreadsheet treat the cell as a formula, so text
# coming from the provider or the model is prefixed before it is written out.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


CSV_COLUMNS = [
    "rank",
    "query",
    "opportunity_score",
    "visibility",
    "search_volume",
    "intent",
    "keyword_difficulty",
    "organic_position",
    "ai_overview_present",
    "ai_overview_cited",
    "ai_mentions",
    "ai_measured",
    "worth_closing",
    "gap_type",
    "reason",
    "status",
]


def export_run(session: Session, run_id: str, fmt: str) -> tuple[str, str, str]:
    run = get_run(session, run_id)
    if not RunStatus(run.status).is_terminal:
        raise ApiError(409, "Conflict", "the run has not finished yet")
    queries = list(session.scalars(select(Query).where(Query.run_id == run_id).order_by(Query.rank)))
    stem = (
        f"ai-visibility-{run.profile_snapshot.get('name', 'brand').lower().replace(' ', '-')}-{run.created_at:%Y%m%d}"
    )
    if fmt == "md":
        return (
            (run.report or {}).get("markdown", "# Report unavailable\n"),
            "text/markdown; charset=utf-8",
            f"{stem}.md",
        )
    if fmt == "json":
        recs = list(
            session.scalars(
                select(Recommendation).where(Recommendation.run_id == run_id).order_by(Recommendation.priority)
            )
        )
        payload = {
            "run": {
                "id": run.id,
                "status": run.status,
                "created_at": run.created_at.isoformat(),
                "profile": run.profile_snapshot,
            },
            "report": run.report,
            "queries": [query_out(q).model_dump(mode="json") for q in queries],
            "recommendations": [
                {
                    "title": r.title,
                    "query_id": r.query_id,
                    "content_type": r.content_type,
                    "outline": r.outline,
                    "key_points": r.key_points,
                    "rationale": r.rationale,
                    "status": r.status,
                }
                for r in recs
            ],
        }
        return json.dumps(payload, indent=2), "application/json", f"{stem}.json"
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for q in queries:
        ch = q.channels or {}
        mentions, measured = _ai_mentions(q)
        analysis = q.analysis or {}
        writer.writerow(
            [
                q.rank,
                q.text,
                q.opportunity_score,
                q.visibility,
                q.search_volume,
                q.intent,
                q.keyword_difficulty,
                ch.get("organic", {}).get("brand_position"),
                ch.get("ai_overview", {}).get("present"),
                ch.get("ai_overview", {}).get("brand_cited"),
                mentions,
                measured,
                analysis.get("worth_closing"),
                analysis.get("gap_type"),
                analysis.get("reason"),
                q.status,
            ]
        )
    return buffer.getvalue(), "text/csv; charset=utf-8", f"{stem}.csv"
