.PHONY: help install lint format format-check typecheck check fix clean run dev scrape test deploy restart logs db-up db-down db-logs db-reset db-migrate railway-up-web railway-up-cron railway-logs-web railway-logs-cron

DJLINT_FLAGS=--profile=jinja --indent 2 --max-line-length 100 --ignore H006,H023,H029,H031,J004,J018,T003,T028

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ──────────────────────────────────────────────
# Dependencies
# ──────────────────────────────────────────────

install: ## Install dependencies with uv
	uv sync

install-dev: ## Install dev dependencies with uv
	uv sync --dev

upgrade: ## Upgrade all dependencies
	uv lock --upgrade
	uv sync

# ──────────────────────────────────────────────
# Code Quality
# ──────────────────────────────────────────────

lint: ## Lint Python, templates, and TOML
	uv run ruff check src/ scripts/ main.py tests/
	uv run djlint $(DJLINT_FLAGS) --lint src/web/templates/
	npm run lint:toml

format: ## Format Python, templates, docs, config, and frontend assets
	uv run ruff format src/ scripts/ main.py tests/
	status=0; uv run djlint $(DJLINT_FLAGS) --reformat --quiet src/web/templates/ || status=$$?; [ $$status -le 1 ]
	npm run format:text
	npm run format:toml
	uv run python scripts/format_misc.py

format-check: ## Check formatting without writing changes
	uv run ruff format --check src/ scripts/ main.py tests/
	uv run djlint $(DJLINT_FLAGS) --check src/web/templates/
	npm run format:text:check
	npm run format:toml:check
	uv run python scripts/format_misc.py --check

typecheck: ## Type check with ty
	uv run ty check src/ scripts/ main.py

fix: ## Auto-fix linting issues with ruff
	uv run ruff check --fix src/ scripts/ main.py tests/

check: lint format-check typecheck ## Run all checks (lint, format, typecheck)

# ──────────────────────────────────────────────
# Testing
# ──────────────────────────────────────────────

test: ## Run tests
	uv run pytest tests/ -v

test-cov: ## Run tests with coverage
	uv run pytest tests/ -v --cov=src --cov-report=term-missing

# ──────────────────────────────────────────────
# Running
# ──────────────────────────────────────────────

run: ## Run the application in production mode
	uv run python -m src.main serve

dev: ## Run the application in development mode with autoreload
	uv run python -m src.main serve-dev

scrape: ## Run the scraper/cron job manually
	uv run python -m src.cron

db-up: ## Start local Postgres via docker compose
	docker compose up -d postgres

db-down: ## Stop local Postgres
	docker compose down

db-logs: ## Tail local Postgres logs
	docker compose logs -f postgres

db-reset: ## Destroy and recreate local Postgres data
	docker compose down -v
	docker compose up -d postgres

db-migrate: ## Apply Alembic migrations to DATABASE_URL
	uv run alembic upgrade head

# ──────────────────────────────────────────────
# Deployment / Systemd
# ──────────────────────────────────────────────

restart: ## Restart the systemd service and show logs
	sudo systemctl restart family-events && sleep 2 && journalctl -u family-events -n 3 --no-pager

restart-cron: ## Restart the cron service
	sudo systemctl restart family-events-cron

status: ## Show service status
	@systemctl status family-events --no-pager
	@echo ""
	@systemctl status family-events-cron --no-pager

logs: ## Tail service logs
	journalctl -u family-events -f

logs-cron: ## Tail cron service logs
	journalctl -u family-events-cron -f

deploy: check ## Run all checks then restart the service
	sudo systemctl restart family-events
	sudo systemctl restart family-events-cron
	@sleep 2
	@journalctl -u family-events -n 5 --no-pager

railway-up-web: ## Deploy the web service to Railway
	railway up --service web

railway-up-cron: ## Deploy the cron service to Railway
	railway up --service cron

railway-logs-web: ## Tail Railway logs for the web service
	railway logs --service web

railway-logs-cron: ## Tail Railway logs for the cron service
	railway logs --service cron

# ──────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────

clean: ## Remove caches and compiled files
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .ruff_cache .pytest_cache .mypy_cache htmlcov .coverage
