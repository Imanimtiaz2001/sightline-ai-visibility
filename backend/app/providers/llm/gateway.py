"""LLM gateway: the only place that talks to a language model.

Everything the model returns is treated as untrusted input:

* tool calls are returned raw and validated by the caller against the tool's
  Pydantic schema (see `validate_tool_call`);
* structured answers are produced through a *forced* tool call whose schema
  is the expected Pydantic model, then re-validated here; on failure the
  model gets one targeted repair turn quoting the validation errors, and if
  that fails too the caller receives `InvalidOutputError` and falls back.

Transport failures go through the same retry/backoff + circuit breaker as
DataForSEO, with LLM-specific error classification.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.core.call_context import CallContext
from app.core.errors import (
    AuthError,
    ExternalError,
    InvalidOutputError,
    PermanentError,
    QuotaError,
    RateLimitedError,
    TransientError,
)
from app.core.resilience import BreakerRegistry, RetryOutcome, RetryPolicy, retry_call
from app.observability.logging import get_logger
from app.observability.metrics import EXTERNAL_CALLS, EXTERNAL_LATENCY, LLM_TOKENS

log = get_logger(__name__)
DEPENDENCY = "llm"
T = TypeVar("T", bound=BaseModel)


class ChatBackend(Protocol):
    """A chat model. `data` is the structured payload already rendered into the prompt;
    real backends ignore it, the offline fixture backend reasons over it directly."""

    model: str

    def complete(
        self,
        *,
        agent: str,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str,
        data: dict[str, Any],
    ) -> AIMessage: ...


@dataclass
class ToolCallProposal:
    calls: list[dict[str, Any]]  # raw: {"id", "name", "args"} – NOT yet validated
    text: str
    attempts: int
    usage: dict[str, int] = field(default_factory=dict)


def classify_llm_exception(exc: Exception) -> ExternalError:
    """Map SDK exceptions (Anthropic/OpenAI via LangChain) onto our taxonomy.

    Duck-typed on class name + status code so it works for both SDKs without
    importing either.
    """
    name = type(exc).__name__
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    kwargs: dict[str, Any] = {"dependency": DEPENDENCY, "operation": "chat", "status": status}
    message = f"{name}: {str(exc)[:300]}"
    if isinstance(exc, ValueError | TypeError | KeyError | AttributeError):
        # Raised client-side (bad parameter for this model, SDK misuse): retrying cannot help.
        return PermanentError(message, **kwargs)
    if "Timeout" in name or "Connection" in name:
        return TransientError(message, **kwargs)
    if status == 429 or "RateLimit" in name:
        return RateLimitedError(message, **kwargs)
    if status in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return AuthError(message, **kwargs)
    if status == 402 or "insufficient_quota" in str(exc):
        return QuotaError(message, **kwargs)
    if status is not None and (status >= 500 or status in (408, 409, 529)):
        return TransientError(message, **kwargs)
    if status is not None and 400 <= status < 500:
        return PermanentError(message, **kwargs)
    return TransientError(message, **kwargs)


class LLMGateway:
    def __init__(
        self,
        backend: ChatBackend,
        *,
        breakers: BreakerRegistry,
        policy: RetryPolicy,
        repair_attempts: int = 1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend = backend
        self._breaker = breakers.get(DEPENDENCY)
        self._policy = policy
        self._repair_attempts = repair_attempts
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self._backend.model

    # -- public API -----------------------------------------------------------

    def propose_tool_calls(
        self,
        *,
        agent: str,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        data: dict[str, Any],
        ctx: CallContext,
        feedback: str | None = None,
    ) -> ToolCallProposal:
        """Ask the model to plan tool calls. Malformed JSON arguments are surfaced as
        raw calls (with a string `args`) so the validator rejects them explicitly."""
        messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(user)]
        if feedback:
            messages.append(HumanMessage(feedback))
        reply, attempts = self._invoke(agent, messages, tools=tools, tool_choice="any", data=data, ctx=ctx)
        calls: list[dict[str, Any]] = [
            {"id": c.get("id") or f"call_{i}", "name": c.get("name"), "args": c.get("args")}
            for i, c in enumerate(reply.tool_calls)
        ]
        calls += [
            {"id": c.get("id") or f"invalid_{i}", "name": c.get("name"), "args": c.get("args")}
            for i, c in enumerate(getattr(reply, "invalid_tool_calls", []) or [])
        ]
        return ToolCallProposal(calls, _text_of(reply), attempts, _usage(reply))

    def structured(
        self,
        *,
        agent: str,
        system: str,
        user: str,
        schema: type[T],
        data: dict[str, Any],
        ctx: CallContext,
        semantic_check: Callable[[T], list[str]] | None = None,
    ) -> tuple[T, int]:
        """Return a validated `schema` instance, or raise `InvalidOutputError`.

        `semantic_check` catches what a schema cannot express (e.g. "every id
        must be one we gave you"); its complaints get the same repair turn.
        Returns (value, repair_turns_used).
        """
        tool = {
            "type": "function",
            "function": {
                "name": schema.__name__,
                "description": (schema.__doc__ or "Return the result.").strip(),
                "parameters": _schema_for_llm(schema),
            },
        }
        messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(user)]
        problems: list[str] = []
        for turn in range(self._repair_attempts + 1):
            reply, _ = self._invoke(agent, messages, tools=[tool], tool_choice=schema.__name__, data=data, ctx=ctx)
            raw = _first_tool_args(reply, schema.__name__)
            problems = []
            if raw is None:
                problems = [f"you must call the {schema.__name__} tool exactly once"]
            else:
                try:
                    value = schema.model_validate(raw)
                    problems = semantic_check(value) if semantic_check else []
                    if not problems:
                        return value, turn
                except ValidationError as exc:
                    problems = [
                        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()[:15]
                    ]
            log.warning("llm_output_invalid", agent=agent, turn=turn, problems=problems[:10])
            messages = [
                *messages,
                reply,
                HumanMessage(
                    "Your previous answer was rejected by validation:\n- "
                    + "\n- ".join(problems)
                    + f"\nCall {schema.__name__} again with a corrected, complete answer."
                ),
            ]
            # Anthropic requires a tool_result after a tool_use; strip the tool call from the history.
            messages[-2] = AIMessage(content=_text_of(reply) or "(previous attempt)")
        raise InvalidOutputError(
            f"{agent} output failed validation after {self._repair_attempts + 1} attempt(s)",
            details=[{"problem": p} for p in problems],
        )

    # -- internals ------------------------------------------------------------

    def _invoke(
        self,
        agent: str,
        messages: list[BaseMessage],
        *,
        tools: list[dict[str, Any]],
        tool_choice: str,
        data: dict[str, Any],
        ctx: CallContext,
    ) -> tuple[AIMessage, int]:
        outcome = RetryOutcome()

        def attempt() -> AIMessage:
            started = time.perf_counter()
            label = "error"
            try:
                try:
                    reply = self._backend.complete(
                        agent=agent, messages=messages, tools=tools, tool_choice=tool_choice, data=data
                    )
                except ExternalError:
                    raise
                except Exception as exc:  # SDK errors -> taxonomy
                    raise classify_llm_exception(exc) from exc
                label = "ok"
                return reply
            except ExternalError as exc:
                label = exc.code
                raise
            finally:
                elapsed = time.perf_counter() - started
                EXTERNAL_LATENCY.labels(DEPENDENCY, agent).observe(elapsed)
                EXTERNAL_CALLS.labels(DEPENDENCY, agent, label).inc()
                ctx.metrics.api_call(f"llm.{agent}", label)
                log.info(
                    "external_call",
                    dependency=DEPENDENCY,
                    operation=agent,
                    outcome=label,
                    duration_ms=round(elapsed * 1000, 1),
                    model=self.model,
                )

        try:
            reply = retry_call(
                lambda: self._breaker.call(attempt, operation=agent),
                policy=self._policy,
                dependency=DEPENDENCY,
                operation=agent,
                on_retry=lambda *_: ctx.metrics.retry(),
                sleep=self._sleep,
                outcome=outcome,
            )
        except ExternalError as exc:
            exc.attempts = outcome.attempts  # type: ignore[attr-defined]
            raise
        for kind, count in _usage(reply).items():
            LLM_TOKENS.labels(agent, kind).inc(count)
            ctx.metrics.tokens(kind, count)
        return reply, outcome.attempts


def _schema_for_llm(schema: type[BaseModel]) -> dict[str, Any]:
    js = schema.model_json_schema()
    js.pop("title", None)
    js.pop("description", None)
    return js


def _first_tool_args(reply: AIMessage, name: str) -> dict[str, Any] | None:
    for call in reply.tool_calls:
        args = call.get("args")
        if call.get("name") == name and isinstance(args, dict):
            return cast(dict[str, Any], args)
    # Some models answer with JSON in text despite tool_choice; accept it if it parses.
    text = _text_of(reply).strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _text_of(reply: AIMessage) -> str:
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def _usage(reply: AIMessage) -> dict[str, int]:
    usage = getattr(reply, "usage_metadata", None) or {}
    return {k: int(usage[k]) for k in ("input_tokens", "output_tokens") if k in usage}
