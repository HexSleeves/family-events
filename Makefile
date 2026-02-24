.PHONY: help install lint format typecheck check fix clean run dev scrape test deploy restart logs

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

lint: ## Lint with ruff
	uv run ruff check src/ main.py tests/

format: ## Format with ruff
	uv run ruff format src/ main.py tests/

format-check: ## Check formatting without writing changes
	uv run ruff format --check src/ main.py tests/

typecheck: ## Type check with ty
	uv run ty check src/ main.py

fix: ## Auto-fix linting issues with ruff
	uv run ruff check --fix src/ main.py tests/

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

run: ## Run the application
	uv run python main.py

dev: ## Run the application in development mode
	uv run python main.py --reload

scrape: ## Run the scraper/cron job manually
	uv run python -m src.cron

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

# ──────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────

clean: ## Remove caches and compiled files
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .ruff_cache .pytest_cache .mypy_cache htmlcov .coverage
