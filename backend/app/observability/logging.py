"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

import structlog

SENSITIVE_KEY = re.compile(r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|dsn)", re.IGNORECASE)
REDACTED = "[REDACTED]"
MAX_STRING = 2_000


def redact(value: Any, depth: int = 0) -> Any:
    """Recursively redact sensitive keys and truncate very long strings.

    Used both by the log processor and by node instrumentation, so node inputs
    can be logged without leaking credentials or whole provider payloads.
    """
    if depth > 6:
        return "[…]"
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and SENSITIVE_KEY.search(k) else redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        items = [redact(v, depth + 1) for v in value[:50]]
        if len(value) > 50:
            items.append(f"[… {len(value) - 50} more]")
        return items
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + f"[… {len(value) - MAX_STRING} chars]"
    return value


def _redaction_processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact(event_dict)  # type: ignore[no-any-return]


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redaction_processor,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer() if fmt == "json" else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    # Route stdlib logging (werkzeug, gunicorn, httpx) through the same pipeline.
    logging.basicConfig(level=level.upper(), stream=sys.stdout, format="%(message)s", force=True)
    for noisy in ("httpx", "httpcore", "anthropic", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
