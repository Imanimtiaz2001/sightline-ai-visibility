"""Correlation context shared by logs, traces and error reports.

`trace_id` follows one analysis run end to end (it equals the run id), while
`request_id` follows one HTTP request. Both are bound into every log line via
structlog's contextvars integration, including lines emitted from worker
threads, because each worker re-binds the context it was handed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog


def new_id() -> str:
    return uuid.uuid4().hex


def bind(**values: Any) -> None:
    structlog.contextvars.bind_contextvars(**values)


def current() -> dict[str, Any]:
    return dict(structlog.contextvars.get_contextvars())


@contextmanager
def bound(**values: Any) -> Iterator[None]:
    """Bind values for the duration of a block and restore the previous ones."""
    with structlog.contextvars.bound_contextvars(**values):
        yield
