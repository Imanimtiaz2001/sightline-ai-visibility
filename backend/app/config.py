"""Application settings.

Every setting comes from the environment (12-factor). `Settings` is the single
source of truth; nothing else in the codebase reads `os.environ` directly.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ProviderMode(StrEnum):
    """How the DataForSEO provider is reached.

    LIVE is the default and the only mode used in normal operation.
    FIXTURES replays synthetic responses from disk; it exists for automated
    tests and offline demos and is surfaced loudly in the API and the UI.
    """

    LIVE = "live"
    FIXTURES = "fixtures"


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    FIXTURES = "fixtures"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- service -----------------------------------------------------------
    app_env: str = "development"
    log_level: str = "INFO"
    log_format: str = Field(default="json", pattern="^(json|console)$")
    database_url: str = "sqlite:///./data/app.db"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:4200", "http://localhost:8080"]
    max_request_bytes: int = 64 * 1024
    sentry_dsn: SecretStr | None = None

    # --- DataForSEO ----------------------------------------------------------
    dataforseo_mode: ProviderMode = ProviderMode.LIVE
    dataforseo_login: str = ""
    dataforseo_password: SecretStr = SecretStr("")
    dataforseo_base_url: str = "https://api.dataforseo.com/v3"
    dataforseo_connect_timeout_s: float = 5.0
    dataforseo_read_timeout_s: float = 45.0
    dataforseo_llm_read_timeout_s: float = 125.0  # Live LLM tasks may run for 120 s
    dataforseo_max_concurrency: int = Field(default=6, ge=1, le=30)  # provider hard limit is 30
    # Fixtures mode only: simulated provider latency so an offline demo shows live progress.
    fixture_latency_ms: int = Field(default=0, ge=0, le=10_000)
    ai_engines: Annotated[list[str], NoDecode] = ["chat_gpt:gpt-4.1-mini", "perplexity:sonar"]

    # --- LLM -------------------------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.ANTHROPIC
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    llm_model: str = ""  # empty -> provider default below
    llm_timeout_s: float = 60.0
    llm_max_output_tokens: int = 4096
    llm_repair_attempts: int = Field(default=1, ge=0, le=3)

    # --- resilience ------------------------------------------------------------
    retry_max_attempts: int = Field(default=4, ge=1, le=10)
    retry_base_delay_s: float = 0.5
    retry_max_delay_s: float = 8.0
    circuit_failure_threshold: int = Field(default=5, ge=1)
    circuit_reset_timeout_s: float = 30.0

    # --- cost & scope ------------------------------------------------------------
    cache_ttl_keywords_h: int = 24 * 7
    cache_ttl_serp_h: int = 12
    cache_ttl_llm_h: int = 12
    max_cost_per_run_usd: float = 1.50
    max_discovery_calls: int = Field(default=4, ge=1, le=10)
    max_queries_per_run: int = Field(default=8, ge=1, le=25)
    max_recommendations: int = Field(default=5, ge=1, le=15)
    max_concurrent_runs: int = Field(default=3, ge=1, le=20)
    run_worker_threads: int = Field(default=3, ge=1, le=20)

    @field_validator("cors_origins", "ai_engines", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def _check_engines(self) -> Settings:
        for spec in self.ai_engines:
            engine, _, model = spec.partition(":")
            if engine not in {"chat_gpt", "perplexity", "gemini", "claude"} or not model:
                raise ValueError(f"AI_ENGINES entry '{spec}' must look like 'chat_gpt:gpt-4.1-mini'")
        return self

    @property
    def resolved_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return {
            LLMProvider.ANTHROPIC: "claude-sonnet-5-5",
            LLMProvider.OPENAI: "gpt-4.1",
            LLMProvider.FIXTURES: "fixture-llm",
        }[self.llm_provider]

    @property
    def engine_models(self) -> dict[str, str]:
        return dict(spec.split(":", 1) for spec in self.ai_engines)

    @property
    def uses_synthetic_data(self) -> bool:
        return self.dataforseo_mode is ProviderMode.FIXTURES or self.llm_provider is LLMProvider.FIXTURES

    def configuration_problems(self) -> list[str]:
        """Missing configuration that makes the service unable to do real work."""
        problems: list[str] = []
        if self.dataforseo_mode is ProviderMode.LIVE and (
            not self.dataforseo_login or not self.dataforseo_password.get_secret_value()
        ):
            problems.append("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are not set")
        if self.llm_provider is LLMProvider.ANTHROPIC and not self.anthropic_api_key.get_secret_value():
            problems.append("ANTHROPIC_API_KEY is not set")
        if self.llm_provider is LLMProvider.OPENAI and not self.openai_api_key.get_secret_value():
            problems.append("OPENAI_API_KEY is not set")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
