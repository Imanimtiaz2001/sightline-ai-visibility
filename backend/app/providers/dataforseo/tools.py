"""DataForSEO tools exposed to the LLM.

One tool per logical API call. Each tool has:

* a Pydantic argument model – the JSON schema the LLM sees *and* the
  validator its arguments must pass before anything is sent to DataForSEO;
* a payload builder that merges the validated arguments with trusted run
  context (location, language, model names). The LLM never chooses those:
  fewer free parameters means fewer ways for it to be wrong or to be steered.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import Settings
from app.core.call_context import CallContext
from app.core.errors import ExternalError
from app.providers.dataforseo.client import DataForSEOClient, ProviderResult
from app.reference import country_iso

Phase = Literal["discovery", "measurement"]
_OPERATORS = re.compile(r"\b(site|inurl|intitle|filetype|related|cache):", re.IGNORECASE)


def _clean_keyword(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip().lower()
    if _OPERATORS.search(value) or any(ch in value for ch in '"<>{}[]'):
        # Operators multiply SERP cost 5x and change semantics; reject rather than "fix".
        raise ValueError("search operators and special characters are not allowed")
    return value


SearchPhrase = Annotated[str, Field(min_length=2, max_length=80), AfterValidator(_clean_keyword)]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class KeywordSuggestionsArgs(ToolArgs):
    """Find real search queries that contain a seed phrase, with volume and competition."""

    seed_keyword: SearchPhrase = Field(
        description="A short seed phrase (2-5 words) describing a topic buyers search for, "
        "e.g. 'project management software'. Not a brand name of a competitor."
    )
    limit: int = Field(default=50, ge=10, le=100, description="How many suggestions to return (10-100).")


class RelatedKeywordsArgs(ToolArgs):
    """Find queries Google considers related to a seed phrase (from 'related searches')."""

    seed_keyword: SearchPhrase = Field(description="A short seed phrase (2-5 words).")
    depth: int = Field(default=1, ge=1, le=2, description="Related-search depth; 1 is usually enough.")
    limit: int = Field(default=50, ge=10, le=100, description="How many related keywords to return (10-100).")


class SearchVolumeArgs(ToolArgs):
    """Get Google Ads search volume, competition and CPC for an explicit list of keywords."""

    keywords: list[SearchPhrase] = Field(min_length=1, max_length=20, description="Exact keywords to look up (1-20).")

    @field_validator("keywords")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class MeasurementArgs(ToolArgs):
    query_ref: str = Field(
        pattern=r"^q\d{1,3}$",
        description="ID of the candidate query this measurement belongs to, exactly as given (e.g. 'q3').",
    )


class GoogleSerpArgs(MeasurementArgs):
    """Fetch the live Google results page for a query, including any AI Overview and its cited sources."""

    keyword: SearchPhrase = Field(description="The search query exactly as a user would type it.")
    depth: int = Field(default=10, ge=10, le=30, description="Number of organic results to read (10-30).")


class AIAnswerArgs(MeasurementArgs):
    """Ask an AI assistant the question a buyer would ask and capture its answer and citations."""

    prompt: str = Field(
        min_length=10,
        max_length=480,
        description="A natural question a buyer would ask, phrased neutrally. Must not mention the "
        "brand being measured, so the answer reflects organic visibility.",
    )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[ToolArgs]
    phase: Phase
    operation: str
    path: str
    ttl: timedelta
    estimate_usd: float
    build_task: Callable[[Any, CallContext], dict[str, Any]]
    read_timeout_s: float | None = None

    def llm_schema(self) -> dict[str, Any]:
        """OpenAI-style function schema; LangChain converts it for Anthropic too."""
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        schema.pop("description", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }


@dataclass(frozen=True)
class ValidatedCall:
    id: str
    spec: ToolSpec
    args: ToolArgs


@dataclass(frozen=True)
class RejectedCall:
    id: str
    name: str
    raw_args: Any
    errors: list[dict[str, Any]]

    def feedback(self) -> str:
        reasons = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ())) or 'args'}: {e['msg']}" for e in self.errors)
        return f"Tool call '{self.name}' was rejected: {reasons}"


@dataclass
class ToolResult:
    call_id: str
    tool: str
    args: dict[str, Any]
    ok: bool
    data: list[dict[str, Any]]
    cost_usd: float = 0.0
    cached: bool = False
    attempts: int = 0
    error: dict[str, Any] | None = None
    notes: list[str] | None = None


ENGINE_LABELS = {"chat_gpt": "ChatGPT", "perplexity": "Perplexity", "gemini": "Gemini", "claude": "Claude"}


class ToolRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        kw_ttl = timedelta(hours=settings.cache_ttl_keywords_h)
        specs: list[ToolSpec] = [
            ToolSpec(
                name="keyword_suggestions",
                description=KeywordSuggestionsArgs.__doc__ or "",
                args_model=KeywordSuggestionsArgs,
                phase="discovery",
                operation="labs.keyword_suggestions",
                path="dataforseo_labs/google/keyword_suggestions/live",
                ttl=kw_ttl,
                estimate_usd=0.02,
                build_task=lambda a, c: {
                    "keyword": a.seed_keyword,
                    "location_code": c.location_code,
                    "language_code": c.language_code,
                    "limit": a.limit,
                    "include_seed_keyword": True,
                    "order_by": ["keyword_info.search_volume,desc"],
                },
            ),
            ToolSpec(
                name="related_keywords",
                description=RelatedKeywordsArgs.__doc__ or "",
                args_model=RelatedKeywordsArgs,
                phase="discovery",
                operation="labs.related_keywords",
                path="dataforseo_labs/google/related_keywords/live",
                ttl=kw_ttl,
                estimate_usd=0.02,
                build_task=lambda a, c: {
                    "keyword": a.seed_keyword,
                    "location_code": c.location_code,
                    "language_code": c.language_code,
                    "depth": a.depth,
                    "limit": a.limit,
                },
            ),
            ToolSpec(
                name="search_volume",
                description=SearchVolumeArgs.__doc__ or "",
                args_model=SearchVolumeArgs,
                phase="discovery",
                operation="keywords.search_volume",
                path="keywords_data/google_ads/search_volume/live",
                ttl=kw_ttl,
                estimate_usd=0.08,
                build_task=lambda a, c: {
                    "keywords": a.keywords,
                    "location_code": c.location_code,
                    "language_code": c.language_code,
                },
            ),
            ToolSpec(
                name="google_serp",
                description=GoogleSerpArgs.__doc__ or "",
                args_model=GoogleSerpArgs,
                phase="measurement",
                operation="serp.google_organic",
                path="serp/google/organic/live/advanced",
                ttl=timedelta(hours=settings.cache_ttl_serp_h),
                estimate_usd=0.006,
                build_task=lambda a, c: {
                    "keyword": a.keyword,
                    "location_code": c.location_code,
                    "language_code": c.language_code,
                    "depth": a.depth,
                    "device": "desktop",
                    "load_async_ai_overview": True,
                },
            ),
        ]
        for engine, model in settings.engine_models.items():
            specs.append(self._engine_spec(engine, model))
        self._specs = {s.name: s for s in specs}

    def _engine_spec(self, engine: str, model: str) -> ToolSpec:
        label = ENGINE_LABELS[engine]

        def build(a: AIAnswerArgs, c: CallContext) -> dict[str, Any]:
            task: dict[str, Any] = {"user_prompt": a.prompt, "model_name": model, "max_output_tokens": 1200}
            iso = country_iso(c.location_code)
            if engine in {"chat_gpt", "gemini", "claude"}:
                task["web_search"] = True
                if engine == "chat_gpt" and iso:
                    task["web_search_country_iso_code"] = iso
            elif engine == "perplexity" and iso:
                task["web_search_country_iso_code"] = iso
            return task

        return ToolSpec(
            name=f"{engine}_answer",
            description=f"Ask {label} ({model}) a buyer question and capture its answer and cited sources.",
            args_model=AIAnswerArgs,
            phase="measurement",
            operation=f"ai.{engine}",
            path=f"ai_optimization/{engine}/llm_responses/live",
            ttl=timedelta(hours=self._settings.cache_ttl_llm_h),
            estimate_usd=0.05,
            build_task=build,
            read_timeout_s=self._settings.dataforseo_llm_read_timeout_s,
        )

    def by_phase(self, phase: Phase) -> list[ToolSpec]:
        return [s for s in self._specs.values() if s.phase == phase]

    @property
    def engine_tools(self) -> list[ToolSpec]:
        return [s for s in self._specs.values() if s.name.endswith("_answer")]

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)


def validate_tool_call(raw: dict[str, Any], allowed: dict[str, ToolSpec]) -> ValidatedCall | RejectedCall:
    """Validate one LLM tool call. Never raises: bad calls become `RejectedCall`s."""
    call_id = str(raw.get("id") or "")
    name = str(raw.get("name") or "")
    args: Any = raw.get("args", raw.get("arguments"))
    spec = allowed.get(name)
    if spec is None:
        return RejectedCall(call_id, name, args, [{"loc": ("name",), "msg": f"unknown tool '{name}'"}])
    if isinstance(args, str):  # some providers stream arguments as a JSON string
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return RejectedCall(call_id, name, args, [{"loc": (), "msg": "arguments are not valid JSON"}])
    if not isinstance(args, dict):
        return RejectedCall(call_id, name, args, [{"loc": (), "msg": "arguments must be an object"}])
    try:
        return ValidatedCall(call_id, spec, spec.args_model.model_validate(args))
    except ValidationError as exc:
        errors = [{"loc": tuple(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return RejectedCall(call_id, name, args, errors)


class ToolExecutor:
    """Runs validated tool calls. External failures become failed `ToolResult`s, never exceptions."""

    def __init__(self, client: DataForSEOClient) -> None:
        self._client = client

    def execute(self, call: ValidatedCall, ctx: CallContext) -> ToolResult:
        spec = call.spec
        args = call.args.model_dump()
        try:
            result: ProviderResult = self._client.call(
                spec.operation,
                spec.path,
                spec.build_task(call.args, ctx),
                ctx=ctx,
                ttl=spec.ttl,
                estimate_usd=spec.estimate_usd,
                read_timeout_s=spec.read_timeout_s,
                read_cache=spec.phase == "discovery" or not ctx.fresh_measurements,
            )
        except ExternalError as exc:
            return ToolResult(
                call.id, spec.name, args, False, [], attempts=getattr(exc, "attempts", 1), error=exc.to_dict()
            )
        return ToolResult(
            call.id,
            spec.name,
            args,
            True,
            result.result,
            cost_usd=result.cost_usd,
            cached=result.cached,
            attempts=result.attempts,
            notes=result.notes,
        )
