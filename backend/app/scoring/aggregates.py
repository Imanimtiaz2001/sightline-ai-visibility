"""Brand-level aggregates computed from a run's scored queries."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.pipeline.contracts import ScoredQuery
from app.scoring.opportunity import demand


def visibility_score(scored: list[ScoredQuery]) -> float | None:
    """Demand-weighted mean visibility (0-100): high-volume questions count more."""
    pairs = [
        (demand(s.measurement.candidate.search_volume, s.measurement.candidate.volume_known), s.breakdown.visibility)
        for s in scored
        if s.breakdown.visibility is not None
    ]
    total = sum(w for w, _ in pairs)
    if not pairs or total == 0:
        return None
    return round(100 * sum(w * v for w, v in pairs if v is not None) / total, 1)


def channel_coverage(scored: list[ScoredQuery]) -> dict[str, dict[str, int]]:
    """For each channel: in how many measured queries the brand is visible at all."""
    out: dict[str, dict[str, int]] = {}
    for channel in ("organic", "ai_overview", "ai_answers"):
        values = [s.breakdown.channel_visibility.get(channel) for s in scored]
        measured = [v for v in values if v is not None]
        out[channel] = {"measured": len(measured), "visible": sum(1 for v in measured if v > 0)}
    return out


def share_of_voice(scored: list[ScoredQuery], brand_name: str, competitors: list[str]) -> list[dict[str, Any]]:
    """Presence counts across every measured surface (organic top 10, AI Overview, each AI engine)."""
    counts: Counter[str] = Counter()
    surfaces = 0
    for s in scored:
        m = s.measurement
        if m.organic.status == "ok":
            surfaces += 1
            if m.organic.brand_position is not None and m.organic.brand_position <= 10:
                counts[brand_name] += 1
            for name, pos in m.organic.competitor_positions.items():
                if pos <= 10:
                    counts[name] += 1
        if m.ai_overview.status == "ok" and m.ai_overview.present:
            surfaces += 1
            if m.ai_overview.brand_cited or m.ai_overview.brand_mentioned:
                counts[brand_name] += 1
            for name in m.ai_overview.competitors_cited:
                counts[name] += 1
        for e in m.engines:
            if e.status == "ok":
                surfaces += 1
                if e.brand_mentioned or e.brand_cited:
                    counts[brand_name] += 1
                for name in e.competitors_mentioned:
                    counts[name] += 1
    total = sum(counts.values())
    entities = [brand_name, *competitors]
    return [
        {
            "name": name,
            "is_brand": name == brand_name,
            "appearances": counts[name],
            "surfaces": surfaces,
            "share": round(counts[name] / total, 3) if total else 0.0,
        }
        for name in entities
    ]
