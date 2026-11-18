"""Deterministic offline LLM – tests and offline demos ONLY (`LLM_PROVIDER=fixtures`).

It answers through the same tool-calling interface as a real model, so its
output is validated, repaired and persisted by exactly the same code path.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from app.pipeline.nodes.planning import default_prompt


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"call_{uuid.uuid4().hex[:8]}", "name": name, "args": args, "type": "tool_call"}


def _phrase(text: str, words: int = 4) -> str:
    return " ".join(re.sub(r"[^\w\s-]", " ", text.lower()).split()[:words])


class FixtureBackend:
    model = "fixture-llm"

    def complete(
        self,
        *,
        agent: str,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str,
        data: dict[str, Any],
    ) -> AIMessage:
        handler = getattr(self, f"_{agent}", None)
        calls = handler(data, [t["function"]["name"] for t in tools]) if handler else []
        return AIMessage(
            content="",
            tool_calls=calls,
            usage_metadata={"input_tokens": 900, "output_tokens": 350, "total_tokens": 1250},
        )

    # -- planners --------------------------------------------------------------------------------

    def _discovery_planner(self, data: dict[str, Any], _: list[str]) -> list[dict[str, Any]]:
        calls = [_call("keyword_suggestions", {"seed_keyword": _phrase(data["market"], 3), "limit": 50})]
        topics = [_phrase(t) for t in data.get("seed_topics") or []]
        if topics:
            calls.append(_call("related_keywords", {"seed_keyword": topics[0], "depth": 1, "limit": 50}))
        explicit = topics[1:]
        question = data.get("research_question") or ""
        quoted = re.findall(r"['\"“‘]([^'\"”’]{3,80})['\"”’]", question)
        explicit += [_phrase(q, 8) for q in quoted]
        if explicit:
            calls.append(_call("search_volume", {"keywords": list(dict.fromkeys(explicit))[:20]}))
        return calls

    def _measurement_planner(self, data: dict[str, Any], tools: list[str]) -> list[dict[str, Any]]:
        engines = [t for t in tools if t.endswith("_answer")]
        ranked = sorted(
            data["candidates"],
            key=lambda c: (
                "search_volume" not in c["found_by"],
                c["intent"] not in ("commercial", "transactional"),
                -(c["monthly_volume"] or 0),
            ),
        )[: data["max_queries"]]
        calls: list[dict[str, Any]] = []
        for c in ranked:
            calls.append(_call("google_serp", {"query_ref": c["id"], "keyword": c["keyword"], "depth": 10}))
            calls += [_call(e, {"query_ref": c["id"], "prompt": default_prompt(c["keyword"])}) for e in engines]
        return calls

    # -- analyst -----------------------------------------------------------------------------------

    def _gap_analyst(self, data: dict[str, Any], _: list[str]) -> list[dict[str, Any]]:
        judgements = []
        for q in data["queries"]:
            cv = q["channel_visibility"]
            vis = q["visibility"] or 0
            answers = [a for a in q["ai_answers"] if a["status"] == "ok"]
            mentioned = sum(1 for a in answers if a["brand_mentioned"])
            winners = sorted(
                {n for a in answers for n in a["competitors_mentioned"]}
                | set(q["organic"]["competitor_positions"])
                | set(q["ai_overview"]["competitors_cited"])
            )
            pos = q["organic"]["brand_position"]
            if vis >= 0.85:
                gap, worth = "already_strong", False
            elif vis == 0 and winners:
                gap, worth = "competitor_dominated", True
            elif cv.get("ai_answers") == 0:
                gap, worth = "missing_in_ai_answers", True
            elif cv.get("ai_overview") == 0:
                gap, worth = "missing_in_ai_overview", True
            else:
                gap, worth = "ranking_low_organic", True
            worth = worth and q["opportunity_score"] >= 12 and q["intent"] != "navigational"
            where = f"ranks #{pos}" if pos else "is not in the top results"
            reason = (
                f"{data['brand']} {where} and is mentioned in {mentioned} of {len(answers)} AI answers"
                + (f" while {', '.join(winners[:3])} appear" if winners else "")
                + f"; {q['monthly_volume']} monthly searches."
            )
            judgements.append(
                {
                    "query_ref": q["id"],
                    "worth_closing": worth,
                    "gap_type": gap,
                    "reason": reason[:400],
                    "competitors_winning": winners[:5],
                    "confidence": "high" if q["measurement_status"] == "measured" else "low",
                }
            )
        top = max(data["queries"], key=lambda q: q["opportunity_score"])
        total = sum(1 for q in data["queries"] for a in q["ai_answers"] if a["status"] == "ok")
        hits = sum(1 for q in data["queries"] for a in q["ai_answers"] if a["status"] == "ok" and a["brand_mentioned"])
        insights = [
            f"{data['brand']} appears in {hits} of {total} AI assistant answers; competitors dominate the high-volume category queries.",
            f"The biggest opening is '{top['keyword']}' (score {top['opportunity_score']:.0f}), where the brand has "
            f"{(top['visibility'] or 0):.0%} visibility.",
            "Where the brand ranks on page one it is usually also cited by AI answers, so organic wins carry over to AI visibility.",
        ]
        return [_call("GapAnalysisOutput", {"judgements": judgements, "headline_insights": insights})]

    def _recommender(self, data: dict[str, Any], _: list[str]) -> list[dict[str, Any]]:
        recs = []
        templates = {
            "competitor_dominated": ("comparison_page", "{b} vs {c}: an honest comparison for {k}"),
            "absent_everywhere": ("buyers_guide", "The {y} buyer's guide to {k}"),
            "missing_in_ai_answers": ("third_party_listing", "Earn reviews and listings that AI cites for {k}"),
            "missing_in_ai_overview": ("faq_hub", "{K}: straight answers to the questions buyers ask"),
            "ranking_low_organic": ("buyers_guide", "How to choose {k} (with a side-by-side table)"),
            "already_strong": ("landing_page_update", "Keep {b} ahead for {k}"),
        }
        for g in data["gaps"]:
            ctype, title = templates.get(g["gap_type"], templates["ranking_low_organic"])
            rival = (g["competitors_winning"] or ["alternatives"])[0]
            k = g["keyword"]
            recs.append(
                {
                    "query_ref": g["id"],
                    "title": title.format(b=data["brand"], c=rival, k=k, K=k[:1].upper() + k[1:], y="2026")[:120],
                    "content_type": ctype,
                    "target_channels": g["channels_missing"] or ["organic"],
                    "rationale": (
                        f"{g['analyst_reason']} A page that answers “{g['buyer_question']}” directly, with concrete "
                        f"pricing and feature facts, gives Google and AI assistants a source to cite instead of {rival}."
                    ),
                    "outline": [
                        f"Quick answer: {g['buyer_question']}",
                        *list(g["people_also_ask"][:3]),
                        f"Side-by-side: {data['brand']} vs {rival}",
                        "Pricing, limits and integrations",
                        "Who should choose what",
                    ][:8],
                    "key_points": [
                        "Lead with a one-paragraph direct answer",
                        "Publish verifiable numbers (pricing tiers, seat limits)",
                        f"Explain where {data['brand']} is the better fit and where it is not",
                    ],
                    "sources_to_earn": [d for d in g["cited_sources"] if d not in (data["brand_domain"],)][:3],
                    "effort": "medium" if ctype in ("comparison_page", "faq_hub") else "high",
                }
            )
        return [_call("RecommendationOutput", {"recommendations": recs})]
