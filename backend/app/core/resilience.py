"""Retry with exponential backoff + full jitter, and a circuit breaker.

Kept dependency-free and small on purpose: the behaviour is a graded part of
the design, so it should be readable in one sitting and unit-testable with a
fake clock.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

from app.core.errors import CircuitOpenError, ExternalError
from app.observability.logging import get_logger
from app.observability.metrics import CIRCUIT_STATE, RETRIES

T = TypeVar("T")
log = get_logger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0

    def backoff(self, attempt: int, rng: Callable[[], float] = random.random) -> float:
        """Full-jitter backoff (AWS architecture blog): U(0, min(cap, base·2^n)).

        Full jitter spreads synchronized clients (e.g. the parallel fan-out
        after a shared 429) better than equal or decorrelated jitter.
        """
        ceiling = min(self.max_delay_s, self.base_delay_s * (2.0 ** (attempt - 1)))
        return ceiling * rng()


@dataclass
class RetryOutcome:
    attempts: int = 0
    delays: list[float] = field(default_factory=list)


def retry_call[T](
    fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    dependency: str,
    operation: str,
    on_retry: Callable[[int, ExternalError, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    outcome: RetryOutcome | None = None,
) -> T:
    """Call `fn`, retrying only `ExternalError`s that are marked retryable.

    Non-retryable errors (auth, payment, bad request, open circuit) propagate
    immediately; anything that is not an `ExternalError` is a bug and also
    propagates immediately.
    """
    outcome = outcome or RetryOutcome()
    for attempt in range(1, policy.max_attempts + 1):
        outcome.attempts = attempt
        try:
            return fn()
        except ExternalError as exc:
            if not exc.retryable or attempt == policy.max_attempts:
                raise
            delay = policy.backoff(attempt)
            if exc.retry_after_s is not None:  # honour server hints, within our cap
                delay = min(policy.max_delay_s, max(delay, exc.retry_after_s))
            outcome.delays.append(delay)
            RETRIES.labels(dependency, operation).inc()
            log.warning(
                "external_call_retry",
                dependency=dependency,
                operation=operation,
                attempt=attempt,
                next_delay_s=round(delay, 3),
                error_code=exc.code,
                error=exc.message,
            )
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Classic three-state breaker.

    * CLOSED: calls flow; consecutive failures are counted.
    * OPEN: calls fail fast with `CircuitOpenError` until `reset_timeout_s`.
    * HALF_OPEN: one trial call is let through; success closes, failure re-opens.

    Only *retryable* failures (i.e. the dependency is unhealthy) trip the
    breaker. A 400 caused by our own bad request says nothing about the
    dependency's health and must not open the circuit for every other run.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if self._state is BreakerState.OPEN and self._clock() - self._opened_at >= self.reset_timeout_s:
            self._state = BreakerState.HALF_OPEN
            self._trial_in_flight = False

    def call(self, fn: Callable[[], T], *, operation: str) -> T:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN or (self._state is BreakerState.HALF_OPEN and self._trial_in_flight):
                raise CircuitOpenError(
                    f"{self.name} circuit is open after repeated failures; failing fast",
                    dependency=self.name,
                    operation=operation,
                )
            if self._state is BreakerState.HALF_OPEN:
                self._trial_in_flight = True
        try:
            result = fn()
        except ExternalError as exc:
            self._record_failure(exc)
            raise
        self._record_success()
        return result

    def _record_success(self) -> None:
        with self._lock:
            if self._state is not BreakerState.CLOSED:
                log.info("circuit_closed", dependency=self.name)
            self._state = BreakerState.CLOSED
            self._failures = 0
            self._trial_in_flight = False
            CIRCUIT_STATE.labels(self.name).set(0)

    def _record_failure(self, exc: ExternalError) -> None:
        if not exc.retryable:
            with self._lock:
                self._trial_in_flight = False
            return
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                if self._state is not BreakerState.OPEN:
                    log.error("circuit_opened", dependency=self.name, consecutive_failures=self._failures)
                self._state = BreakerState.OPEN
                self._opened_at = self._clock()
                self._trial_in_flight = False
                CIRCUIT_STATE.labels(self.name).set(1)

    def snapshot(self) -> dict[str, object]:
        return {"dependency": self.name, "state": self.state.value, "consecutive_failures": self._failures}


class BreakerRegistry:
    """One breaker per dependency, shared across all runs in the process."""

    def __init__(self, failure_threshold: int, reset_timeout_s: float) -> None:
        self._threshold = failure_threshold
        self._reset = reset_timeout_s
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name, failure_threshold=self._threshold, reset_timeout_s=self._reset
                )
            return self._breakers[name]

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            breakers = list(self._breakers.values())
        return [b.snapshot() for b in breakers]
