# One place to run everything, two ways to run the app.
#
#   Local  -- app and worker in your venv, Postgres in a container. Fast reload,
#             a debugger you can attach, `make dev`.
#   Docker -- the whole stack in containers, exactly as it deploys. `make up`.
#
# `make` on its own prints this list.

.DEFAULT_GOAL := help
.PHONY: help install env db-up db-down migrate dev worker send \
        up up-scale down logs test test-unit lint types check fmt

# Compose reads .env automatically; the local targets rely on Settings doing the
# same (config.py: env_file=".env").
COMPOSE := docker compose

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# --- Setup -------------------------------------------------------------------

install: ## Sync the virtualenv from uv.lock
	uv sync

env: ## Create .env from .env.example if it does not exist
	@test -f .env && echo ".env already exists, leaving it alone" \
		|| { cp .env.example .env && echo "created .env -- edit the secrets before running"; }

# === Local: app in your venv, Postgres in a container ========================

db-up: ## Start only Postgres (for the local app to talk to)
	$(COMPOSE) up -d postgres

db-down: ## Stop Postgres and delete its data volume
	$(COMPOSE) down -v

migrate: ## Apply migrations to the database in DATABASE_URL
	uv run alembic upgrade head

dev: ## Run the API locally with auto-reload (needs db-up + migrate first)
	uv run uvicorn webhook_receiver.api.app:create_app --factory --reload --port 8000

worker: ## Run the worker locally (a second terminal; needs db-up + migrate)
	uv run python -m webhook_receiver.worker.main

send: ## POST a signed demo event to the running app (make send [ARGS="--count 2"])
	uv run python scripts/send_webhook.py $(ARGS)

# === Docker: the whole stack in containers ===================================

up: ## Build and start postgres + migrate + app + worker
	$(COMPOSE) up --build

up-scale: ## Same, with 4 workers (SKIP LOCKED keeps them from colliding)
	$(COMPOSE) up --build --scale worker=4

down: ## Stop the stack and delete volumes
	$(COMPOSE) down -v

logs: ## Tail logs from the running stack
	$(COMPOSE) logs -f

# --- Checks ------------------------------------------------------------------

test: ## Run every test (starts a real Postgres via Testcontainers)
	uv run pytest

test-unit: ## Run only the fast tests (no Docker needed)
	uv run pytest tests/unit

lint: ## ruff check + format check
	uv run ruff check . && uv run ruff format --check .

types: ## mypy --strict
	uv run mypy

fmt: ## Auto-format
	uv run ruff format . && uv run ruff check --fix .

check: lint types test-unit ## The pre-push gate, minus the Docker suite
