"""Process-wide Prometheus metrics plus a per-run summary collector.

Prometheus series answer fleet questions ("is DataForSEO failing for
everyone?"); `RunMetrics` answers the single-run question ("what happened in
run X?") and is persisted on the run row and logged when the run ends.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

NODE_LATENCY = Histogram(
    "pipeline_node_duration_seconds",
    "Wall-clock duration of a pipeline node",
    ["node"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)
NODE_OUTCOMES = Counter(
    "pipeline_node_outcomes_total", "Node executions by outcome", ["node", "outcome"], registry=REGISTRY
)
RUN_OUTCOMES = Counter("pipeline_runs_total", "Finished runs by final status", ["status"], registry=REGISTRY)
EXTERNAL_CALLS = Counter(
    "external_calls_total",
    "Calls to external dependencies (one per attempt)",
    ["dependency", "operation", "outcome"],
    registry=REGISTRY,
)
EXTERNAL_LATENCY = Histogram(
    "external_call_duration_seconds",
    "Latency of a single external attempt",
    ["dependency", "operation"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
    registry=REGISTRY,
)
RETRIES = Counter("external_retries_total", "Retries scheduled", ["dependency", "operation"], registry=REGISTRY)
CACHE_LOOKUPS = Counter("provider_cache_lookups_total", "Cache lookups", ["operation", "result"], registry=REGISTRY)
CIRCUIT_STATE = Gauge(
    "circuit_breaker_open", "1 when the breaker for a dependency is open", ["dependency"], registry=REGISTRY
)
PROVIDER_COST = Counter("provider_cost_usd_total", "Money spent at DataForSEO", ["operation"], registry=REGISTRY)
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["agent", "kind"], registry=REGISTRY)
CLIENT_ERRORS = Counter("client_errors_total", "Errors reported by browsers", ["kind"], registry=REGISTRY)
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP latency", ["method", "route"], registry=REGISTRY)


@dataclass
class RunMetrics:
    """Thread-safe accumulator for one run; safe under the parallel fan-out."""

    node_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    node_outcomes: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    api_calls: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    retries: int = 0
    cache_hits: int = 0
    cost_usd: float = 0.0
    llm_tokens: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def node(self, name: str, duration_ms: float, outcome: str) -> None:
        with self._lock:
            self.node_ms[name].append(duration_ms)
            self.node_outcomes[name][outcome] += 1

    def api_call(self, operation: str, outcome: str) -> None:
        with self._lock:
            self.api_calls[operation][outcome] += 1

    def retry(self) -> None:
        with self._lock:
            self.retries += 1

    def cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def spend(self, usd: float) -> None:
        with self._lock:
            self.cost_usd += usd

    def tokens(self, kind: str, count: int) -> None:
        with self._lock:
            self.llm_tokens[kind] += count

    def summary(self) -> dict[str, Any]:
        with self._lock:
            nodes = {
                name: {
                    "executions": len(samples),
                    "total_ms": round(sum(samples), 1),
                    "max_ms": round(max(samples), 1),
                    "outcomes": dict(self.node_outcomes[name]),
                    "success_rate": round(self.node_outcomes[name].get("ok", 0) / max(1, len(samples)), 3),
                }
                for name, samples in self.node_ms.items()
            }
            return {
                "nodes": nodes,
                "api_calls": {op: dict(outcomes) for op, outcomes in self.api_calls.items()},
                "api_calls_total": sum(sum(o.values()) for o in self.api_calls.values()),
                "retries": self.retries,
                "cache_hits": self.cache_hits,
                "cost_usd": round(self.cost_usd, 5),
                "llm_tokens": dict(self.llm_tokens),
            }
