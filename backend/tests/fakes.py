"""Test doubles: scripted LLM and programmable HTTP transports."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage

from app.providers.llm.fixture import FixtureBackend


class ScriptedBackend:
    """Plays queued replies per agent, then falls back to the deterministic fixture model.

    A queued item may be an `AIMessage`, an exception instance (raised), or a
    callable `(data) -> AIMessage`.
    """

    model = "scripted-test-llm"

    def __init__(self) -> None:
        self.queues: dict[str, deque[Any]] = defaultdict(deque)
        self.calls: list[dict[str, Any]] = []
        self._fallback = FixtureBackend()

    def script(self, agent: str, *items: Any) -> ScriptedBackend:
        self.queues[agent].extend(items)
        return self

    def complete(
        self,
        *,
        agent: str,
        messages: list[BaseMessage],
        tools: list[dict[str, Any]],
        tool_choice: str,
        data: dict[str, Any],
    ) -> AIMessage:
        self.calls.append({"agent": agent, "messages": messages, "tools": [t["function"]["name"] for t in tools]})
        if self.queues[agent]:
            item = self.queues[agent].popleft()
            if isinstance(item, BaseException):
                raise item
            return item(data) if callable(item) else item
        return self._fallback.complete(agent=agent, messages=messages, tools=tools, tool_choice=tool_choice, data=data)


def tool_message(*calls: tuple[str, Any], invalid: list[dict[str, Any]] | None = None) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"id": f"c{i}", "name": name, "args": args, "type": "tool_call"} for i, (name, args) in enumerate(calls)
        ],
        invalid_tool_calls=invalid or [],
    )


class SequenceTransport:
    """Returns the next programmed response for each request (then repeats the last one)."""

    def __init__(
        self, *responses: httpx.Response | Exception | Callable[[str, dict[str, Any]], httpx.Response]
    ) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, payload: list[dict[str, Any]], timeout: httpx.Timeout) -> httpx.Response:
        self.requests.append((path, payload[0]))
        item = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(path, payload[0])
        return item


def ok_envelope(result: list[dict[str, Any]], cost: float = 0.01, task_status: int = 20000) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status_code": 20000,
            "status_message": "Ok.",
            "cost": cost,
            "tasks_count": 1,
            "tasks_error": 0,
            "tasks": [{"id": "t", "status_code": task_status, "status_message": "Ok.", "cost": cost, "result": result}],
        },
    )


def task_error(code: int, message: str = "error") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status_code": 20000,
            "status_message": "Ok.",
            "cost": 0,
            "tasks_count": 1,
            "tasks_error": 1,
            "tasks": [{"id": "t", "status_code": code, "status_message": message, "cost": 0, "result": None}],
        },
    )


class FailingTransport:
    """Wraps another transport and fails every request matching `predicate`."""

    def __init__(self, inner: Any, predicate: Callable[[str, dict[str, Any]], bool], response: httpx.Response) -> None:
        self.inner, self.predicate, self.response = inner, predicate, response
        self.failed: list[str] = []

    def post(self, path: str, payload: list[dict[str, Any]], timeout: httpx.Timeout) -> httpx.Response:
        if self.predicate(path, payload[0]):
            self.failed.append(path)
            return self.response
        return self.inner.post(path, payload, timeout)  # type: ignore[no-any-return]
