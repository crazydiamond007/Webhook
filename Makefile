# One place to run everything, two ways to run the app.
#
#   Local  -- app and worker in your venv, Postgres in a container. Fast reload,
#             a debugger you can attach, `make dev`.
#   Docker -- the whole stack in containers, exactly as it deploys. `make up`.
#
# `make` on its own prints this list.

.DEFAULT_GOAL := help
.PHONY: help install env db-up db-down migrate dev worker send balance demo \
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

balance: ## Show the account balances, the ledger, and the attempt log
	@$(COMPOSE) exec -T postgres psql -U webhook -d webhook_receiver -c \
		"SELECT external_ref, balance_minor, version FROM account ORDER BY external_ref;" -c \
		"SELECT count(*) AS ledger_rows, coalesce(sum(amount_minor), 0) AS ledger_sum \
		 FROM ledger_entry;" -c \
		"SELECT e.external_id, a.attempt_number, a.outcome FROM processing_attempt a \
		 JOIN webhook_event e ON e.id = a.event_id ORDER BY a.id;"

demo: ## The Day 2 story: a duplicate delivery and a reordered one, on one account
	@echo "== two deliveries of ONE credit -- the second must move no money (FR-6)"
	uv run python scripts/send_webhook.py --amount 500 --count 2
	@echo "== a snapshot at sequence 2, then a STALE one at sequence 1 (FR-10)"
	uv run python scripts/send_webhook.py --event-type balance.snapshot --balance 1000 --sequence 2
	uv run python scripts/send_webhook.py --event-type balance.snapshot --balance 50 --sequence 1
	@echo "== give the worker a moment, then look at the damage"
	@sleep 3
	@$(MAKE) --no-print-directory balance
	@echo "balance should be 1000, NOT 50 and NOT 1500;"
	@echo "ledger_rows should be 2 (one credit, one snapshot delta); the stale snapshot is superseded."

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
