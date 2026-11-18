"""Composition root: builds the process-wide singletons once.

Breakers, the cache and HTTP connection pools are shared by every run in
the process (a breaker that resets per run would never protect anything).
Per-run objects – budget, metrics, recorder – are created by `RunService`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import ProviderMode, Settings
from app.core.cache import ResponseCache
from app.core.resilience import BreakerRegistry, RetryPolicy
from app.providers.dataforseo.client import DataForSEOClient, HttpTransport, Transport
from app.providers.dataforseo.tools import ToolExecutor, ToolRegistry
from app.providers.llm.backends import build_backend
from app.providers.llm.gateway import ChatBackend, LLMGateway

FIXTURE_DIR = Path(__file__).parent / "providers" / "dataforseo" / "fixtures"


@dataclass
class Container:
    settings: Settings
    breakers: BreakerRegistry
    cache: ResponseCache
    tools: ToolRegistry
    executor: ToolExecutor
    llm: LLMGateway

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        transport: Transport | None = None,
        backend: ChatBackend | None = None,
        sleep: object | None = None,
    ) -> Container:
        breakers = BreakerRegistry(settings.circuit_failure_threshold, settings.circuit_reset_timeout_s)
        cache = ResponseCache()
        policy = RetryPolicy(settings.retry_max_attempts, settings.retry_base_delay_s, settings.retry_max_delay_s)
        if transport is None:
            if settings.dataforseo_mode is ProviderMode.FIXTURES:
                from app.providers.dataforseo.fixtures import FixtureTransport

                transport = FixtureTransport(
                    FIXTURE_DIR / "scenario.json", latency_s=settings.fixture_latency_ms / 1000
                )
            else:
                transport = HttpTransport(
                    settings.dataforseo_base_url,
                    settings.dataforseo_login,
                    settings.dataforseo_password.get_secret_value(),
                )
        extra = {"sleep": sleep} if sleep is not None else {}
        client = DataForSEOClient(
            transport,
            cache=cache,
            breakers=breakers,
            policy=policy,
            connect_timeout_s=settings.dataforseo_connect_timeout_s,
            read_timeout_s=settings.dataforseo_read_timeout_s,
            max_concurrency=settings.dataforseo_max_concurrency,
            **extra,
        )
        llm = LLMGateway(
            backend or build_backend(settings),
            breakers=breakers,
            policy=policy,
            repair_attempts=settings.llm_repair_attempts,
            **extra,  # type: ignore[arg-type]
        )
        return cls(settings, breakers, cache, ToolRegistry(settings), ToolExecutor(client), llm)
