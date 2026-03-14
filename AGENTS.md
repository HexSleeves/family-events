# AGENTS.md

This file gives agentic coding tools the operating rules for this repository.
Follow these instructions unless a direct user request overrides them.

## Project Snapshot

- App type: FastAPI web app for discovering and ranking family-friendly events.
- Main stack: Python 3.12, FastAPI, SQLAlchemy, PostgreSQL, Jinja2, HTMX, Tailwind CSS.
- Python package/tooling: `uv`.
- Frontend/config tooling: `npm`, Prettier, Tailwind CLI, Taplo.
- Canonical docs: `README.md`, `docs/architecture.md`, `docs/frontend.md`, `docs/pipeline.md`.

## Rule Files Present / Missing

- There is currently no repo-local `.cursor/rules/` directory.
- There is currently no repo-local `.cursorrules` file.
- There is currently no repo-local `.github/copilot-instructions.md` file.
- If any of those files are added later, treat them as additional instructions and update this file.

## Setup Commands

- Install Python dependencies: `uv sync --dev`
- Install JS/dev tooling: `npm install`
- Copy env file: `cp .env.example .env`
- Start local Postgres: `make db-up`
- Apply migrations: `make db-migrate`
- Run dev server: `make dev`
- Run production-style server locally: `make run`

## Core Build, Lint, Test Commands

- Full lint: `make lint`
- Full format: `make format`
- Formatting check only: `make format-check`
- Type check: `make typecheck`
- Full verification: `make check`
- Full test suite: `make test`
- Test suite with coverage: `make test-cov`
- CSS build: `npm run css:build`
- CSS watch: `npm run css:watch`

## Single-Test and Targeted Test Commands

- Run one test function: `uv run pytest tests/test_main.py::test_pipeline_cli_runs_scrape_tag_then_notify -v`
- Run one test file: `uv run pytest tests/test_pipeline.py -v`
- Run tests matching a pattern: `uv run pytest tests/ -k pipeline -v`
- Stop on first failure: `uv run pytest tests/test_pipeline.py -x -v`
- Show local prints/logging while debugging: `uv run pytest tests/test_pipeline.py -s -v`

## Targeted Quality Commands

- Ruff lint a subset: `uv run ruff check src/web/routes/pages.py tests/test_pipeline.py`
- Ruff format a subset: `uv run ruff format src/web/routes/pages.py tests/test_pipeline.py`
- Type check Python sources: `uv run ty check src/ scripts/ main.py`
- Template lint: `uv run djlint --profile=jinja --indent 2 --max-line-length 100 --ignore H006,H023,H029,H031,J004,J018,T003,T028 --lint src/web/templates/`
- Template format check: `uv run djlint --profile=jinja --indent 2 --max-line-length 100 --ignore H006,H023,H029,H031,J004,J018,T003,T028 --check src/web/templates/`
- Prettier check: `npm run format:text:check`
- TOML lint: `npm run lint:toml`

## CLI and App Commands

- Pipeline: `uv run python -m src.main pipeline`
- Scrape only: `uv run python -m src.main scrape`
- Tag only: `uv run python -m src.main tag`
- Notify only: `uv run python -m src.main notify`
- Serve without reload: `uv run python -m src.main serve`
- Serve with reload: `uv run python -m src.main serve-dev`
- List events in terminal: `uv run python -m src.main events`
- Dedupe events: `uv run python -m src.main dedupe`
- Run scheduler worker: `uv run python -m src.cron`

## Agent Workflow Rules

- Read `README.md` and the relevant module before changing behavior.
- Prefer the narrowest verification that proves your change, then run broader checks if risk is high.
- For code changes, run at least the most relevant targeted test(s).
- Before finishing a substantial change, prefer `make check` and `make test` unless the user asked for a smaller scope.
- Do not claim success without citing the command(s) you actually ran.
- Do not invent commands; prefer `Makefile`, `pyproject.toml`, and `package.json` as the source of truth.

## Repository Layout

- `src/main.py`: CLI entry point and web server commands.
- `src/scheduler.py`: scrape/tag/notify orchestration.
- `src/db/`: database access, schema, migrations helpers, models.
- `src/web/`: FastAPI app, routes, middleware, auth, templates, UI helpers.
- `src/scrapers/`: built-in and generic scraper implementations.
- `src/tagger/`: taxonomy and LLM/heuristic tagging.
- `src/notifications/`: notification formatting and delivery.
- `tests/`: pytest suite, shared fixtures in `tests/conftest.py`.

## Python Style Rules

- Use Python 3.12 syntax.
- Keep lines within 100 columns.
- Use 4-space indentation.
- Use double quotes; Ruff formatter is the authority.
- Add `from __future__ import annotations` in Python modules, matching repo convention.
- Prefer small, focused functions over large monolithic blocks.
- Preserve existing module docstring style when editing files that already use it.

## Imports

- Let Ruff/isort order imports; do not hand-tune import ordering.
- Use absolute first-party imports from `src`, for example `from src.db.models import Event`.
- Group imports as stdlib, third-party, then first-party.
- Avoid unused imports; Ruff will flag them.
- Prefer importing concrete names over importing entire modules unless patching or namespacing makes that clearer.

## Types and Data Modeling

- Add type hints for new functions, methods, and important locals when helpful.
- Prefer built-in generics like `list[str]`, `dict[str, Any]`, and `tuple[int, str]`.
- Use Pydantic `BaseModel` for validated domain/data payloads when that matches existing patterns.
- Use `TypedDict` for lightweight structured dict payloads when the repo already follows that style.
- Use `@dataclass(slots=True)` for immutable-ish computed structures where Pydantic would be overkill.
- Use `Literal` for constrained string values that are part of a stable interface.
- Keep validation near the model via Pydantic validators when possible.

## Naming Conventions

- Use `snake_case` for functions, variables, and modules.
- Use `PascalCase` for classes and Pydantic models.
- Use `UPPER_SNAKE_CASE` for module-level constants.
- Name tests as `test_<behavior>()`.
- Prefer explicit names like `visible_city_slugs` over vague names like `data` or `value`.

## Error Handling and Logging

- Fail loudly on invalid internal state; do not silently swallow exceptions.
- Raise specific HTTP errors in route handlers when validating request input.
- In operational paths, log failures with structured context using the existing logger patterns.
- When catching broad exceptions, do it at boundaries such as health checks, scheduler loops, scraper/network edges, or delivery integrations.
- Preserve actionable error messages for users and operators.
- Do not replace existing logging with print statements unless the file already uses CLI-style printing.

## Testing Conventions

- Use `pytest`.
- Reuse fixtures from `tests/conftest.py` before creating new setup helpers.
- The `client` fixture creates a temporary DB and FastAPI `TestClient`; use it for route/UI tests.
- Use `create_user` from `tests/conftest.py` for auth/account setup when possible.
- For sync tests that need async helpers, follow the repo pattern of `asyncio.run(...)`.
- Use `monkeypatch` to isolate external services, scheduler calls, and env-sensitive behavior.
- Keep tests close to behavior; prefer extending an existing related test file over creating a scattered new one.

## Web, Templates, and Frontend Rules

- This is a server-rendered app, not a SPA.
- Preserve FastAPI + Jinja2 + HTMX patterns already in use.
- Do not introduce a frontend framework unless explicitly requested.
- When editing templates, keep Djlint-compatible formatting with 2-space indentation.
- When editing CSS or frontend assets, rebuild with `npm run css:build` if the output file changes.
- Preserve HTMX partial-response behavior and existing endpoint contracts.

## Database and Migration Rules

- Default local development is PostgreSQL, even though SQLite still appears in compatibility paths and tests.
- When changing persisted behavior, inspect `src/db/models.py`, `src/db/database.py`, and Alembic files together.
- Use Alembic for schema changes; do not rely on ad hoc manual DB edits.
- Keep compatibility with existing test helpers that create temporary SQLite databases unless the task explicitly removes that support.

## Practical Do / Don't

- Do prefer `make` targets when available.
- Do run targeted tests after changing logic.
- Do run `make check` before finishing broad changes.
- Do update docs when commands or developer workflow change.
- Don't add new tooling without a strong repo-specific reason.
- Don't bypass formatters or manually fight generated formatting.
- Don't rewrite unrelated code while touching a file.
- Don't assume production-only behavior; check `README.md` and `docs/runbook.md` first.
