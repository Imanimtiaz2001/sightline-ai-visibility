"""Error reporting.

Unhandled exceptions are always logged as structured `error` events with the
trace/request ids (searchable in any log store). If `SENTRY_DSN` is set they
are also sent to Sentry, tagged with the same ids, so an alert links straight
to the run's trace.
"""

from __future__ import annotations

from typing import Any

from app.observability import context

_sentry: Any = None


def init_error_reporting(dsn: str | None, environment: str, release: str) -> None:
    global _sentry  # noqa: PLW0603 - optional SDK handle
    if not dsn:
        return
    import sentry_sdk

    sentry_sdk.init(dsn=dsn, environment=environment, release=release, traces_sample_rate=0.0, send_default_pii=False)
    _sentry = sentry_sdk


def capture_exception(exc: BaseException, **tags: Any) -> None:
    if _sentry is None:
        return
    with _sentry.new_scope() as scope:
        for key, value in {**context.current(), **tags}.items():
            scope.set_tag(key, str(value))
        _sentry.capture_exception(exc)
