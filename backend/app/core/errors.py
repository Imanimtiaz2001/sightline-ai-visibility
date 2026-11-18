"""Error taxonomy.

Two families:

* `ExternalError` – something outside the process failed. The `retryable`
  flag is the single decision point the retry loop consults, so error
  classification lives in exactly one place per provider.
* `ApiError` – an error we deliberately return to an API client; rendered
  as RFC 9457 problem+json by the API layer.
"""

from __future__ import annotations

from typing import Any


class ExternalError(Exception):
    """A failed call to an external dependency (DataForSEO, LLM)."""

    retryable: bool = False
    code: str = "external_error"

    def __init__(
        self,
        message: str,
        *,
        dependency: str,
        operation: str,
        status: int | None = None,
        provider_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.dependency = dependency
        self.operation = operation
        self.status = status
        self.provider_code = provider_code
        self.retry_after_s = retry_after_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "dependency": self.dependency,
            "operation": self.operation,
            "retryable": self.retryable,
            "http_status": self.status,
            "provider_code": self.provider_code,
        }


class TransientError(ExternalError):
    """Timeouts, connection resets, 429s, 5xx – worth retrying."""

    retryable = True
    code = "transient"


class RateLimitedError(TransientError):
    code = "rate_limited"


class PermanentError(ExternalError):
    """Bad request, validation failure – retrying cannot help."""

    code = "permanent"


class AuthError(PermanentError):
    code = "auth_failed"


class QuotaError(PermanentError):
    """Payment required / insufficient funds / cost limit."""

    code = "quota_exhausted"


class CircuitOpenError(ExternalError):
    """Fast-fail because the dependency has been failing repeatedly."""

    code = "circuit_open"


class BudgetExceededError(ExternalError):
    """The run's cost ceiling would be exceeded; the call is not made."""

    code = "budget_exceeded"


class InvalidOutputError(Exception):
    """An LLM (or provider) returned output that does not satisfy its contract."""

    def __init__(self, message: str, *, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.title = title
        self.detail = detail
        self.errors = errors or []
        self.headers = headers or {}


class NotFound(ApiError):
    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(404, "Not Found", f"{resource} '{identifier}' does not exist")


class Conflict(ApiError):
    def __init__(self, detail: str) -> None:
        super().__init__(409, "Conflict", detail)
