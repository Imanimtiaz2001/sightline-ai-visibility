"""OpenAPI 3.1 document generated from the registered operations + a docs page."""

from __future__ import annotations

import re
import typing
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from app.api.framework import REGISTRY, Operation

_PARAM = re.compile(r"<(?:\w+:)?(\w+)>")

PROBLEM_SCHEMA = {
    "type": "object",
    "description": "RFC 9457 problem details",
    "properties": {
        "type": {"type": "string"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string"},
        "request_id": {"type": "string"},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "enum": ["body", "query"]},
                    "field": {"type": ["string", "null"]},
                    "message": {"type": "string"},
                    "type": {"type": "string"},
                },
            },
        },
    },
    "required": ["title", "status"],
}
STATUS_TEXT = {
    400: "Malformed request",
    404: "Not found",
    409: "Conflict with current state",
    413: "Body too large",
    415: "Body must be JSON",
    422: "Validation failed",
    429: "Too many requests / queue full",
    503: "Service not configured or dependency unavailable",
}


def _models(op: Operation) -> list[type[BaseModel]]:
    out: list[type[BaseModel]] = []
    for candidate in (op.body, op.response):
        if candidate is None:
            continue
        args = typing.get_args(candidate)
        model = args[0] if args else candidate
        if isinstance(model, type) and issubclass(model, BaseModel):
            out.append(model)
    return out


def _ref(model: Any) -> dict[str, Any]:
    args = typing.get_args(model)
    if args:
        return {"type": "array", "items": {"$ref": f"#/components/schemas/{args[0].__name__}"}}
    return {"$ref": f"#/components/schemas/{model.__name__}"}


def build_spec(version: str) -> dict[str, Any]:
    models = {m for op in REGISTRY for m in _models(op)}
    _, defs = models_json_schema(
        [(m, "serialization") for m in sorted(models, key=lambda m: m.__name__)],
        ref_template="#/components/schemas/{model}",
    )
    schemas = {**defs.get("$defs", {}), "Problem": PROBLEM_SCHEMA}
    paths: dict[str, Any] = {}
    for op in REGISTRY:
        path = _PARAM.sub(r"{\1}", op.path)
        parameters: list[dict[str, Any]] = [
            {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
            for name in _PARAM.findall(op.path)
        ]
        if op.params is not None:
            for name, field in op.params.model_fields.items():
                schema = op.params.model_json_schema()["properties"].get(name, {})
                parameters.append(
                    {
                        "name": name,
                        "in": "query",
                        "required": field.is_required(),
                        "description": field.description or "",
                        "schema": schema,
                    }
                )
        responses: dict[str, Any] = {}
        if op.response is None:
            responses[str(op.status)] = {"description": "No content" if op.status == 204 else "OK"}
        elif op.response_media != "application/json":
            responses[str(op.status)] = {
                "description": "OK",
                "content": {op.response_media: {"schema": {"type": "string"}}},
            }
        else:
            responses[str(op.status)] = {
                "description": "OK",
                "content": {"application/json": {"schema": _ref(op.response)}},
            }
        for code, text in op.extra_responses.items():
            responses[str(code)] = {"description": text}
        for code in op.errors:
            responses[str(code)] = {
                "description": STATUS_TEXT.get(code, "Error"),
                "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}},
            }
        operation: dict[str, Any] = {
            "summary": op.summary,
            "tags": [op.tag],
            "operationId": f"{op.method}_{re.sub(r'[^a-z0-9]+', '_', path.lower()).strip('_')}",
            "parameters": parameters,
            "responses": responses,
        }
        if op.description:
            operation["description"] = op.description
        if op.body is not None:
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": _ref(op.body)}}}
        paths.setdefault(path, {})[op.method] = operation
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "AI Visibility Platform API",
            "version": version,
            "description": (
                "Measure how a brand shows up in Google search, Google AI Overviews and AI assistants, rank the "
                "opportunities and get content recommendations.\n\n"
                "**Conventions** – JSON everywhere; errors are `application/problem+json`; every response carries "
                "`X-Request-ID`; analyses are asynchronous (`POST` returns `202` + `Location`, follow progress via "
                "`GET /runs/{id}` or the SSE stream `GET /runs/{id}/events`); `Idempotency-Key` makes run creation safe to retry."
            ),
        },
        "servers": [{"url": "/"}],
        "paths": paths,
        "components": {"schemas": schemas},
    }


DOCS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>AI Visibility Platform API</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"></head>
<body>
<noscript><p style="font-family:system-ui;padding:2rem">This page renders the specification with JavaScript.
The raw OpenAPI document is at <a href="/api/openapi.json">/api/openapi.json</a>.</p></noscript>
<div id="ui"><p style="font-family:system-ui;padding:2rem;color:#555">Loading the API explorer…
If it does not appear (no network, or a strict content policy), the raw OpenAPI document is at
<a href="/api/openapi.json">/api/openapi.json</a>.</p></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js" crossorigin></script>
<script>window.ui = SwaggerUIBundle({url: '/api/openapi.json', dom_id: '#ui', deepLinking: true});</script>
</body></html>"""
