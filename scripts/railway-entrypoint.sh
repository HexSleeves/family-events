#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[railway] %s\n' "$*"
}

run_migrations() {
  local max_attempts="${MIGRATION_MAX_ATTEMPTS:-10}"
  local sleep_seconds="${MIGRATION_RETRY_SECONDS:-3}"
  local attempt=1

  until uv run python -m alembic upgrade head; do
    if (( attempt >= max_attempts )); then
      log "database migrations failed after ${attempt} attempts"
      return 1
    fi

    log "migration attempt ${attempt}/${max_attempts} failed; retrying in ${sleep_seconds}s"
    attempt=$((attempt + 1))
    sleep "${sleep_seconds}"
  done
}

role="${APP_ROLE:-web}"

case "${role}" in
  web)
    log "starting web service"
    run_migrations
    exec uv run python -m src.main serve
    ;;
  cron)
    log "starting scheduler worker"
    exec uv run python -m src.cron
    ;;
  *)
    log "unsupported APP_ROLE='${role}'; expected 'web' or 'cron'"
    exit 1
    ;;
esac
