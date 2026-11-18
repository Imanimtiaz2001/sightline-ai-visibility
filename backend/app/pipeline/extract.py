"""Pure parsing of raw DataForSEO payloads into stage contracts.

Provider payloads are treated as untrusted: every access is defensive,
unknown shapes yield empty results instead of exceptions, and nothing here
performs I/O. That keeps the normalizer nodes trivial and this module easy
to test against recorded payloads.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

from app.pipeline.contracts import (
    AIEngineChannel,
    AIOverviewChannel,
    BrandContext,
    KeywordCandidate,
    OrganicChannel,
    SerpResult,
    SourceRef,
)

_INTENTS = {"informational", "navigational", "commercial", "transactional"}
_COMPETITION_LEVEL = {"LOW": 0.2, "MEDIUM": 0.5, "HIGH": 0.85}


def normalize_domain(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip().lower()
    host = urlparse(value if "//" in value else f"//{value}").hostname or ""
    return host.removeprefix("www.")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def short_phrase(text: str, words: int = 5) -> str:
    """First `words` words of free text, punctuation stripped (for seeds built from profile text)."""
    return " ".join(re.sub(r"[^\w\s-]", " ", text).split()[:words])


def domain_matches(candidate: str, owned: str) -> bool:
    candidate, owned = normalize_domain(candidate), normalize_domain(owned)
    return bool(owned) and (candidate == owned or candidate.endswith("." + owned))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# --- keywords ------------------------------------------------------------------------


def _labs_keyword(data: dict[str, Any], source: str) -> KeywordCandidate | None:
    keyword = data.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        return None
    info = _as_dict(data.get("keyword_info"))
    props = _as_dict(data.get("keyword_properties"))
    intent = _as_dict(data.get("search_intent_info")).get("main_intent")
    volume = _int(info.get("search_volume"))
    return KeywordCandidate(
        keyword=normalize_text(keyword),
        search_volume=volume,
        competition=_float(info.get("competition")),
        cpc=_float(info.get("cpc")),
        keyword_difficulty=_int(props.get("keyword_difficulty")),
        intent=intent if intent in _INTENTS else None,
        sources=[source],
        volume_known=volume is not None,
    )


def extract_keywords(tool: str, result: list[dict[str, Any]]) -> list[KeywordCandidate]:
    out: list[KeywordCandidate] = []
    if tool == "search_volume":
        for item in result:
            keyword = item.get("keyword")
            if not isinstance(keyword, str):
                continue
            volume = _int(item.get("search_volume"))
            index = _float(item.get("competition_index"))
            competition = index / 100 if index is not None else _COMPETITION_LEVEL.get(str(item.get("competition")))
            out.append(
                KeywordCandidate(
                    keyword=normalize_text(keyword),
                    search_volume=volume,
                    competition=competition,
                    cpc=_float(item.get("cpc")),
                    sources=[tool],
                    volume_known=volume is not None,
                )
            )
        return out
    for block in result:
        if tool == "keyword_suggestions":
            seed = _as_dict(block.get("seed_keyword_data"))
            if seed and (c := _labs_keyword(seed, tool)):
                out.append(c)
        for item in _as_list(block.get("items")):
            data = _as_dict(item).get("keyword_data") if tool == "related_keywords" else item
            if c := _labs_keyword(_as_dict(data), tool):
                out.append(c)
    return out


def merge_candidates(candidates: list[KeywordCandidate]) -> list[KeywordCandidate]:
    """Merge duplicates from different tools, keeping the most complete metrics."""
    merged: dict[str, KeywordCandidate] = {}
    for c in candidates:
        existing = merged.get(c.keyword)
        if existing is None:
            merged[c.keyword] = c
            continue
        merged[c.keyword] = KeywordCandidate(
            keyword=c.keyword,
            search_volume=existing.search_volume if existing.search_volume is not None else c.search_volume,
            competition=existing.competition if existing.competition is not None else c.competition,
            cpc=existing.cpc if existing.cpc is not None else c.cpc,
            keyword_difficulty=existing.keyword_difficulty
            if existing.keyword_difficulty is not None
            else c.keyword_difficulty,
            intent=existing.intent or c.intent,
            sources=sorted(set(existing.sources) | set(c.sources)),
            volume_known=existing.volume_known or c.volume_known,
        )
    return list(merged.values())


# --- mention detection -------------------------------------------------------------------


def _mention_pattern(name: str, domain: str) -> re.Pattern[str]:
    aliases = {re.escape(name.strip()), re.escape(normalize_domain(domain))}
    return re.compile(r"(?<![\w.])(" + "|".join(sorted(a for a in aliases if a)) + r")(?![\w])", re.IGNORECASE)


class EntityMatcher:
    """Decides whether a brand/competitor is mentioned in text or cited by domain."""

    def __init__(self, brand: BrandContext) -> None:
        self.brand = brand
        self._brand_re = _mention_pattern(brand.name, brand.domain)
        self._competitors = [(c.name, c.domain, _mention_pattern(c.name, c.domain)) for c in brand.competitors]

    def brand_in_text(self, text: str) -> bool:
        return bool(text and self._brand_re.search(text))

    def brand_cited(self, domains: list[str]) -> bool:
        return any(domain_matches(d, self.brand.domain) for d in domains)

    def competitors_in(self, text: str, domains: list[str]) -> list[str]:
        found = []
        for name, domain, pattern in self._competitors:
            if (text and pattern.search(text)) or any(domain_matches(d, domain) for d in domains):
                found.append(name)
        return found

    def competitor_for_domain(self, domain: str) -> str | None:
        for name, owned, _ in self._competitors:
            if domain_matches(domain, owned):
                return name
        return None

    def brand_sentence(self, text: str) -> str | None:
        match = self._brand_re.search(text or "")
        if not match:
            return None
        start = max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start())) + 1
        end_candidates = [i for i in (text.find(".", match.end()), text.find("\n", match.end())) if i != -1]
        end = min(end_candidates) + 1 if end_candidates else len(text)
        return text[start:end].strip()[:300]


# --- SERP --------------------------------------------------------------------------------


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def extract_serp(result: list[dict[str, Any]], matcher: EntityMatcher) -> tuple[OrganicChannel, AIOverviewChannel]:
    items = [i for i in _as_list(_as_dict(result[0] if result else {}).get("items")) if isinstance(i, dict)]

    organic_items = sorted(
        (i for i in items if i.get("type") == "organic" and _int(i.get("rank_group"))),
        key=lambda i: _int(i.get("rank_group")) or 999,
    )
    brand_position: int | None = None
    brand_url: str | None = None
    competitor_positions: dict[str, int] = {}
    top: list[SerpResult] = []
    for item in organic_items:
        position = _int(item.get("rank_group")) or 0
        domain = normalize_domain(item.get("domain") or item.get("url"))
        if len(top) < 10:
            top.append(
                SerpResult(
                    position=position,
                    domain=domain,
                    title=str(item.get("title") or "")[:200],
                    url=str(item.get("url") or "")[:500],
                )
            )
        if brand_position is None and domain_matches(domain, matcher.brand.domain):
            brand_position, brand_url = position, item.get("url")
        if (name := matcher.competitor_for_domain(domain)) and name not in competitor_positions:
            competitor_positions[name] = position

    paa = [
        str(el.get("title"))
        for i in items
        if i.get("type") == "people_also_ask"
        for el in _as_list(i.get("items"))
        if isinstance(el, dict) and el.get("title")
    ][:8]
    organic = OrganicChannel(
        status="ok",
        brand_position=brand_position,
        brand_url=brand_url,
        competitor_positions=competitor_positions,
        top_results=top,
        people_also_ask=paa,
    )

    overview_items = [i for i in items if "ai_overview" in str(i.get("type", ""))]
    if not overview_items:
        return organic, AIOverviewChannel(status="ok", present=False)
    texts: list[str] = []
    refs: dict[str, SourceRef] = {}
    for node in _walk(overview_items):
        for key in ("markdown", "text"):
            if isinstance(node.get(key), str) and node.get("type") != "ai_overview_reference":
                texts.append(node[key])
                break
        if node.get("type") == "ai_overview_reference" or ("url" in node and "domain" in node and "source" in node):
            url = str(node.get("url") or "")
            domain = normalize_domain(node.get("domain") or url)
            if domain and url not in refs:
                refs[url] = SourceRef(domain=domain, url=url[:500], title=str(node.get("title") or "")[:200])
    text = "\n".join(texts)
    domains = [r.domain for r in refs.values()]
    overview = AIOverviewChannel(
        status="ok",
        present=True,
        brand_cited=matcher.brand_cited(domains),
        brand_mentioned=matcher.brand_in_text(text),
        competitors_cited=matcher.competitors_in(text, domains),
        references=list(refs.values())[:15],
    )
    return organic, overview


# --- AI answers ---------------------------------------------------------------------------


def extract_ai_answer(
    result: list[dict[str, Any]], *, engine: str, model: str, matcher: EntityMatcher
) -> AIEngineChannel:
    block = _as_dict(result[0] if result else {})
    texts: list[str] = []
    for raw_item in _as_list(block.get("items")):
        item = _as_dict(raw_item)
        if item.get("type") not in (None, "message"):
            continue
        for section in _as_list(item.get("sections")):
            text = _as_dict(section).get("text")
            if isinstance(text, str):
                texts.append(text)
    urls: list[str] = []
    for node in _walk([block.get("items"), block.get("annotations"), block.get("search_results")]):
        for key in ("direct_url", "url"):
            if isinstance(node.get(key), str) and node[key].startswith("http"):
                urls.append(node[key])
                break
    domains = list(dict.fromkeys(d for d in (normalize_domain(u) for u in urls) if d))
    text = "\n".join(texts).strip()
    return AIEngineChannel(
        engine=engine,
        model=str(block.get("model_name") or model),
        status="ok",
        brand_mentioned=matcher.brand_in_text(text),
        brand_cited=matcher.brand_cited(domains),
        competitors_mentioned=matcher.competitors_in(text, domains),
        cited_domains=domains[:20],
        excerpt=text[:700],
        brand_context=matcher.brand_sentence(text),
    )
