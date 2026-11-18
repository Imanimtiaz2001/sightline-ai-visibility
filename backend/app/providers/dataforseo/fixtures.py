"""Synthetic DataForSEO transport – tests and offline demos ONLY.

Activated exclusively by `DATAFORSEO_MODE=fixtures`; the API (`GET /meta`)
and the UI then show a "synthetic data" banner. It returns responses in the
real DataForSEO envelope (status codes, costs, item shapes), so the whole
client stack – classification, retries, breaker, cache, parsing – runs
exactly as it does against the live API. Scenario faults let a demo show
retries (HTTP 500, 40202 rate limit) and partial runs (50301).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

OPERATION_BY_PATH = {
    "dataforseo_labs/google/keyword_suggestions/live": "labs.keyword_suggestions",
    "dataforseo_labs/google/related_keywords/live": "labs.related_keywords",
    "keywords_data/google_ads/search_volume/live": "keywords.search_volume",
    "serp/google/organic/live/advanced": "serp.google_organic",
}


def _stable(value: str, mod: int) -> int:
    return int(hashlib.sha256(value.encode()).hexdigest()[:8], 16) % mod


class FixtureTransport:
    def __init__(self, scenario_path: Path, *, inject_faults: bool = True, latency_s: float = 0.0) -> None:
        self._latency_s = latency_s
        self._s = json.loads(scenario_path.read_text())
        self._by_kw = {k["keyword"]: k for k in self._s["keywords"]}
        self._names = {c["name"]: c["domain"] for c in self._s["competitors"]}
        self._names[self._s["brand"]["name"]] = self._s["brand"]["domain"]
        self._lock = threading.Lock()
        self._measure_counts: dict[str, int] = defaultdict(int)
        self._faults: dict[tuple[str, str], list[str]] = {}
        if inject_faults:
            self._faults = {
                (op, k["keyword"]): list(f) for k in self._s["keywords"] for op, f in (k.get("faults") or {}).items()
            }
            self._faults.update({(op, "*"): list(f) for op, f in self._s.get("discovery_faults", {}).items()})
        self.requests: list[tuple[str, dict[str, Any]]] = []

    # -- transport protocol -----------------------------------------------------------------

    def post(self, path: str, payload: list[dict[str, Any]], timeout: httpx.Timeout) -> httpx.Response:
        path = path.strip("/")
        task = payload[0]
        self.requests.append((path, task))
        if self._latency_s:
            time.sleep(self._latency_s * (2 if path.startswith("ai_optimization") else 1))
        operation = OPERATION_BY_PATH.get(path) or (
            "ai." + path.split("/")[1] if path.startswith("ai_optimization") else path
        )
        keyword = str(task.get("keyword") or task.get("user_prompt") or "*")
        if fault := self._next_fault(operation, task):
            return fault
        handler = {
            "labs.keyword_suggestions": self._suggestions,
            "labs.related_keywords": self._related,
            "keywords.search_volume": self._volume,
            "serp.google_organic": self._serp,
        }.get(operation)
        if handler is not None:
            return self._envelope(path, task, handler(task), cost=0.0115 if "labs" in operation else 0.0035)
        if operation.startswith("ai."):
            return self._envelope(path, task, self._ai(operation.removeprefix("ai."), task), cost=0.0221)
        return httpx.Response(404, json={"status_code": 40402, "status_message": f"Invalid path {keyword}"})

    # -- faults -----------------------------------------------------------------------------

    def _next_fault(self, operation: str, task: dict[str, Any]) -> httpx.Response | None:
        keyword = task.get("keyword") or self._keyword_for_prompt(str(task.get("user_prompt", "")))
        with self._lock:
            for key in ((operation, keyword), (operation, "*")):
                queue = self._faults.get(key)
                if queue:
                    fault = queue.pop(0)
                    break
            else:
                return None
        if fault == "http_500":
            return httpx.Response(500, text="<html>Internal Server Error</html>")
        if fault == "timeout":
            raise httpx.ReadTimeout("fixture timeout")
        code = int(fault.removeprefix("dfs_"))
        return httpx.Response(
            200,
            json={
                "version": "0.1",
                "status_code": 20000,
                "status_message": "Ok.",
                "cost": 0,
                "tasks_count": 1,
                "tasks_error": 1,
                "tasks": [
                    {
                        "id": "fixture",
                        "status_code": code,
                        "status_message": "Synthetic fault",
                        "cost": 0,
                        "result": None,
                    }
                ],
            },
        )

    # -- builders ------------------------------------------------------------------------------

    @staticmethod
    def _envelope(path: str, task: dict[str, Any], result: list[dict[str, Any]], cost: float) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "version": "0.1.20260601",
                "status_code": 20000,
                "status_message": "Ok.",
                "time": "0.4 sec.",
                "cost": cost,
                "tasks_count": 1,
                "tasks_error": 0,
                "tasks": [
                    {
                        "id": hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(),
                        "status_code": 20000,
                        "status_message": "Ok.",
                        "time": "0.4 sec.",
                        "cost": cost,
                        "result_count": len(result),
                        "path": path.split("/"),
                        "data": task,
                        "result": result,
                    }
                ],
            },
        )

    def _labs_item(self, k: dict[str, Any]) -> dict[str, Any]:
        return {
            "se_type": "google",
            "keyword": k["keyword"],
            "keyword_info": {
                "search_volume": k["volume"],
                "competition": k["competition"],
                "competition_level": "HIGH" if k["competition"] > 0.6 else "MEDIUM",
                "cpc": k["cpc"],
            },
            "keyword_properties": {"keyword_difficulty": k["kd"]},
            "search_intent_info": {"main_intent": k["intent"]},
        }

    def _matching(self, seed: str) -> list[dict[str, Any]]:
        words = set(seed.lower().split()) - {"software", "tool", "tools", "best", "for"}
        hits = [k for k in self._s["keywords"] if words & set(k["keyword"].split())] or self._s["keywords"]
        return sorted(hits, key=lambda k: -k["volume"])

    def _suggestions(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        seed = str(task["keyword"])
        items = [self._labs_item(k) for k in self._matching(seed) if seed in k["keyword"] or len(seed.split()) <= 2]
        return [
            {
                "se_type": "google",
                "seed_keyword": seed,
                "items_count": len(items),
                "items": items[: task.get("limit", 50)],
            }
        ]

    def _related(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        items = [
            {"se_type": "google", "keyword_data": self._labs_item(k), "depth": 1}
            for k in self._matching(str(task["keyword"]))
        ]
        return [{"se_type": "google", "seed_keyword": task["keyword"], "items": items[: task.get("limit", 50)]}]

    def _volume(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for kw in task["keywords"]:
            k = self._by_kw.get(kw)
            volume = k["volume"] if k else 50 + _stable(kw, 900)
            out.append(
                {
                    "keyword": kw,
                    "search_volume": volume,
                    "competition": "MEDIUM",
                    "competition_index": int((k["competition"] if k else 0.4) * 100),
                    "cpc": k["cpc"] if k else 3.5,
                }
            )
        return out

    def _variant(self, keyword: str, kind: str) -> dict[str, Any] | None:
        k = self._by_kw.get(keyword)
        if not k:
            return None
        with self._lock:
            count = self._measure_counts[f"{kind}:{keyword}"]
            self._measure_counts[f"{kind}:{keyword}"] += 1
        variant: dict[str, Any] = k["after"] if count >= 1 and "after" in k else k
        return variant

    def _serp(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        keyword = str(task["keyword"])
        spec = (self._variant(keyword, "serp") or {}).get("serp") or {"brand": None, "competitors": {}, "aio": None}
        fillers = self._s["filler_domains"]
        slots: dict[int, tuple[str, str]] = {}
        if spec.get("brand"):
            slots[spec["brand"]] = (self._s["brand"]["name"], self._s["brand"]["domain"])
        for name, pos in spec.get("competitors", {}).items():
            slots[pos] = (name, self._names[name])
        items: list[dict[str, Any]] = []
        if spec.get("aio"):
            refs = [
                {
                    "type": "ai_overview_reference",
                    "source": d.split(".")[0].title(),
                    "domain": d,
                    "url": f"https://www.{d}/{keyword.replace(' ', '-')}",
                    "title": f"{keyword.title()} – {d}",
                    "text": f"Overview of {keyword}.",
                }
                for d in spec["aio"]["cites"]
            ]
            mentions = ", ".join(spec["aio"]["mentions"]) or "several vendors"
            items.append(
                {
                    "type": "ai_overview",
                    "rank_group": 1,
                    "rank_absolute": 1,
                    "asynchronous_ai_overview": True,
                    "markdown": f"Popular options for **{keyword}** include {mentions}.",
                    "items": [
                        {
                            "type": "ai_overview_element",
                            "title": None,
                            "text": f"Popular options for {keyword} include {mentions}.",
                            "references": refs,
                        }
                    ],
                    "references": refs,
                }
            )
        depth = int(task.get("depth", 10))
        for pos in range(1, depth + 1):
            name, domain = slots.get(pos) or (None, fillers[(pos + _stable(keyword, 7)) % len(fillers)])
            title = f"{name}: {keyword.title()}" if name else f"{keyword.title()} – reviewed by {domain}"
            items.append(
                {
                    "type": "organic",
                    "rank_group": pos,
                    "rank_absolute": pos + (1 if spec.get("aio") else 0),
                    "domain": f"www.{domain}",
                    "url": f"https://www.{domain}/{keyword.replace(' ', '-')}",
                    "title": title,
                    "description": f"Everything about {keyword}.",
                }
            )
        items.append(
            {
                "type": "people_also_ask",
                "rank_group": 1,
                "items": [
                    {"type": "people_also_ask_element", "title": q}
                    for q in (
                        f"What is the best {keyword}?",
                        f"How much does {keyword} cost?",
                        f"How do I choose {keyword}?",
                    )
                ],
            }
        )
        return [
            {
                "keyword": keyword,
                "type": "organic",
                "se_domain": "google.com",
                "location_code": task.get("location_code"),
                "language_code": task.get("language_code"),
                "item_types": sorted({i["type"] for i in items}),
                "items_count": len(items),
                "items": items,
            }
        ]

    def _keyword_for_prompt(self, prompt: str) -> str:
        prompt = prompt.lower()
        return max((k for k in self._by_kw if k in prompt), key=len, default=prompt)

    def _ai(self, engine: str, task: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = str(task["user_prompt"])
        keyword = self._keyword_for_prompt(prompt)
        spec = ((self._variant(keyword, f"ai.{engine}") or {}).get("ai") or {}).get(engine) or {
            "mentions": [],
            "cites": [],
        }
        names = spec["mentions"]
        if names:
            text = (
                f"For {keyword}, the most frequently recommended options are "
                + ", ".join(names[:-1])
                + (f" and {names[-1]}" if len(names) > 1 else names[0])
                + ". "
                + " ".join(
                    f"{n} is known for {['strong integrations', 'agency-friendly pricing', 'resource planning', 'ease of use'][i % 4]}."
                    for i, n in enumerate(names)
                )
            )
        else:
            text = f"When evaluating {keyword}, focus on your team's workflow, integrations and budget rather than a single vendor."
        annotations = [
            {"title": d, "url": f"https://www.{d}/{keyword.replace(' ', '-')}", "start_index": 0, "end_index": 10}
            for d in spec["cites"]
        ]
        return [
            {
                "model_name": task["model_name"],
                "input_tokens": 40,
                "output_tokens": 180,
                "web_search": True,
                "money_spent": 0.0021,
                "datetime": "2026-11-17 10:00:00 +00:00",
                "items": [
                    {"type": "message", "sections": [{"type": "text", "text": text, "annotations": annotations}]}
                ],
                "search_results": [{"title": d, "url": f"https://{d}/", "snippet": ""} for d in spec["cites"]],
                "fan_out_queries": [keyword],
            }
        ]
