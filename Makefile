# Sightline – common tasks. `make run` is all a reviewer needs.
SHELL := /bin/bash
PY ?= python3

.PHONY: run demo stop logs dev backend frontend test test-backend test-frontend lint openapi clean

## run: build and start both halves on live data (http://localhost:8080)
run: .env
	docker compose up --build

## demo: start both halves on SYNTHETIC fixtures (no API keys needed; clearly bannered in the UI)
demo: .env
	DATAFORSEO_MODE=fixtures LLM_PROVIDER=fixtures FIXTURE_LATENCY_MS=700 docker compose up --build

stop:
	docker compose down

logs:
	docker compose logs -f backend

.env:
	@cp backend/.env.example .env
	@echo ">> Created .env from backend/.env.example – add your DataForSEO and LLM credentials, then re-run."

## dev: run without Docker (backend on :8000, frontend on :4200 with a proxy to the backend)
dev:
	$(MAKE) -j2 backend frontend

backend:
	cd backend && $(PY) -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt && \
	  set -a && { [ -f ../.env ] && source ../.env || true; } && set +a && \
	  .venv/bin/gunicorn -c gunicorn.conf.py wsgi:app

frontend:
	cd frontend && npm ci --no-audit --no-fund && npx ng serve --port 4200

## test: backend (ruff, mypy, pytest) and frontend (vitest) checks
test: test-backend test-frontend

test-backend:
	cd backend && $(PY) -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt && \
	  .venv/bin/ruff check app tests && .venv/bin/mypy app && .venv/bin/pytest

test-frontend:
	cd frontend && npm ci --no-audit --no-fund && npx ng test --watch=false && npx ng build

## openapi: regenerate docs/openapi.json from the code
openapi:
	cd backend && DATAFORSEO_MODE=fixtures LLM_PROVIDER=fixtures DATABASE_URL=sqlite:// $(PY) -c \
	  "import json; from app import create_app; c=create_app().test_client(); open('../docs/openapi.json','w').write(json.dumps(c.get('/api/openapi.json').json, indent=2))"

clean:
	docker compose down -v
