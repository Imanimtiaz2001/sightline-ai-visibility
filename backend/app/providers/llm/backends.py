"""Chat backends: Anthropic or OpenAI through LangChain, plus the factory."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from app.config import LLMProvider, Settings


class LangChainBackend:
    """Wraps a LangChain chat model. SDK-level retries are disabled (`max_retries=0`)
    because retries, backoff and circuit breaking are owned by `LLMGateway`."""

    def __init__(self, chat: BaseChatModel, model: str) -> None:
        self._chat = chat
        self.model = model

    def complete(
        self,
        *,
        agent: str,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str,
        data: dict[str, Any],
    ) -> AIMessage:
        bound = self._chat.bind_tools(tools, tool_choice=tool_choice) if tools else self._chat
        reply = bound.invoke(messages, config={"run_name": agent, "tags": [agent]})
        if not isinstance(reply, AIMessage):  # pragma: no cover - defensive
            raise TypeError(f"unexpected reply type {type(reply).__name__}")
        return reply


def build_backend(settings: Settings) -> Any:
    model = settings.resolved_llm_model
    if settings.llm_provider is LLMProvider.ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        return LangChainBackend(
            ChatAnthropic(
                model=model,
                api_key=settings.anthropic_api_key,
                timeout=settings.llm_timeout_s,
                max_retries=0,
                max_tokens=settings.llm_max_output_tokens,
            ),
            model,
        )
    if settings.llm_provider is LLMProvider.OPENAI:
        from langchain_openai import ChatOpenAI

        return LangChainBackend(
            ChatOpenAI(
                model=model,
                api_key=settings.openai_api_key,
                timeout=settings.llm_timeout_s,
                max_retries=0,
                max_completion_tokens=settings.llm_max_output_tokens,
            ),
            model,
        )
    from app.providers.llm.fixture import FixtureBackend

    return FixtureBackend()
