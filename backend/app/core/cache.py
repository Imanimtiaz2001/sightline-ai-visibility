"""Content-addressed response cache with single-flight de-duplication.

Two mechanisms keep us from paying twice for the same provider work:

1. Persistent cache (DB table) keyed by sha256(operation + canonical request).
   A re-run, a retried run, or another brand asking the same question within
   the TTL reuses the stored response at zero cost.
2. Single-flight lock per key, so two parallel fan-out branches that need the
   identical request make one paid call, and the second reads the cache.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.base import get_engine, utcnow
from app.db.models import ProviderCacheEntry
from app.observability.metrics import CACHE_LOOKUPS


def cache_key(operation: str, request: dict[str, Any]) -> str:
    canonical = json.dumps({"op": operation, "req": request}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class ResponseCache:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @contextmanager
    def single_flight(self, key: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            yield

    def get(self, key: str, operation: str) -> dict[str, Any] | None:
        with Session(get_engine()) as session:
            entry = session.get(ProviderCacheEntry, key)
            if entry is None or entry.expires_at <= utcnow():
                CACHE_LOOKUPS.labels(operation, "miss").inc()
                return None
            CACHE_LOOKUPS.labels(operation, "hit").inc()
            return entry.response

    def put(
        self,
        key: str,
        operation: str,
        request: dict[str, Any],
        response: dict[str, Any],
        *,
        ttl: timedelta,
        cost_usd: float,
    ) -> None:
        with Session(get_engine()) as session:
            session.merge(
                ProviderCacheEntry(
                    key=key,
                    operation=operation,
                    request=request,
                    response=response,
                    cost_usd=cost_usd,
                    created_at=utcnow(),
                    expires_at=utcnow() + ttl,
                )
            )
            session.commit()

    def purge_expired(self) -> int:
        with Session(get_engine()) as session:
            result = session.execute(delete(ProviderCacheEntry).where(ProviderCacheEntry.expires_at <= utcnow()))
            session.commit()
            return int(result.rowcount or 0)  # type: ignore[attr-defined]
