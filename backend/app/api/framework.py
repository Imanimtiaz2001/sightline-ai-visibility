"""Tiny API framework on top of Flask blueprints.

`@bp.operation(...)` does three things in one declaration, so they cannot
drift apart:

1. registers the Flask route;
2. validates the JSON body / query string with Pydantic and injects the
   parsed models as `body=` / `params=` keyword arguments;
3. records the operation for the generated OpenAPI 3.1 document.

Errors are rendered as RFC 9457 `application/problem+json` everywhere.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from flask import Blueprint, Response, g, jsonify, request
from pydantic import BaseModel, ValidationError

from app.core.errors import ApiError


@dataclass
class Operation:
    method: str
    path: str
    summary: str
    tag: str
    description: str = ""
    body: type[BaseModel] | None = None
    params: type[BaseModel] | None = None
    response: Any = None  # Pydantic model, list[Model], or None
    status: int = 200
    errors: tuple[int, ...] = ()
    response_media: str = "application/json"
    extra_responses: dict[int, str] = field(default_factory=dict)


REGISTRY: list[Operation] = []


def problem(
    status: int,
    title: str,
    detail: str | None = None,
    *,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    payload: dict[str, Any] = {"type": "about:blank", "title": title, "status": status}
    if detail:
        payload["detail"] = detail
    if errors:
        payload["errors"] = errors
    request_id = getattr(g, "request_id", None)
    if request_id:
        payload["request_id"] = request_id
    response = jsonify(payload)
    response.status_code = status
    response.mimetype = "application/problem+json"
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def _validation_errors(exc: ValidationError, location: str) -> list[dict[str, Any]]:
    return [
        {
            "location": location,
            "field": ".".join(str(p) for p in e["loc"]) or None,
            "message": e["msg"],
            "type": e["type"],
        }
        for e in exc.errors(include_url=False)
    ]


def _parse_query(model: type[BaseModel]) -> BaseModel:
    raw: dict[str, Any] = {}
    for key in request.args:
        values = request.args.getlist(key)
        annotation = model.model_fields.get(key)
        is_list = annotation is not None and "list" in str(annotation.annotation)
        raw[key] = values if is_list else values[-1]
    return model.model_validate(raw)


def json_response(payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    elif isinstance(payload, list):
        payload = [p.model_dump(mode="json") if isinstance(p, BaseModel) else p for p in payload]
    response = jsonify(payload)
    response.status_code = status
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


class ApiBlueprint(Blueprint):
    def operation(
        self,
        rule: str,
        method: str,
        *,
        summary: str,
        tag: str,
        description: str = "",
        body: type[BaseModel] | None = None,
        params: type[BaseModel] | None = None,
        response: Any = None,
        status: int = 200,
        errors: tuple[int, ...] = (),
        response_media: str = "application/json",
        extra_responses: dict[int, str] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        prefix = self.url_prefix or ""
        REGISTRY.append(
            Operation(
                method.lower(),
                prefix + rule,
                summary,
                tag,
                description,
                body,
                params,
                response,
                status,
                tuple(sorted(set(errors) | ({422} if body or params else set()))),
                response_media,
                extra_responses or {},
            )
        )

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                if body is not None:
                    if not request.is_json:
                        raise ApiError(
                            415, "Unsupported Media Type", "send a JSON body with Content-Type: application/json"
                        )
                    try:
                        kwargs["body"] = body.model_validate(request.get_json(silent=False))
                    except ValidationError as exc:
                        raise ApiError(
                            422,
                            "Validation failed",
                            "the request body is invalid",
                            errors=_validation_errors(exc, "body"),
                        ) from exc
                if params is not None:
                    try:
                        kwargs["params"] = _parse_query(params)
                    except ValidationError as exc:
                        raise ApiError(
                            422,
                            "Validation failed",
                            "the query string is invalid",
                            errors=_validation_errors(exc, "query"),
                        ) from exc
                return fn(*args, **kwargs)

            flask_rule = rule
            self.add_url_rule(flask_rule, endpoint=fn.__name__, view_func=wrapper, methods=[method.upper()])
            return wrapper

        return decorator
