FROM node:22-bookworm-slim AS assets

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

# Tailwind v4 source detection needs the template and source files present
# in the build stage, otherwise the generated stylesheet is incomplete.
COPY src ./src
RUN npm run css:build


FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PORT=8000

COPY pyproject.toml uv.lock alembic.ini README.md ./
COPY alembic ./alembic
COPY src ./src
COPY main.py ./
COPY scripts/railway-entrypoint.sh ./scripts/railway-entrypoint.sh
COPY --from=assets /app/src/web/static/styles.css ./src/web/static/styles.css

RUN uv sync --locked --no-dev

EXPOSE 8000

CMD ["./scripts/railway-entrypoint.sh"]
