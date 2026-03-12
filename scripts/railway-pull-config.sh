#!/usr/bin/env bash
set -euo pipefail

out_dir="${1:-tmp/railway-config}"

mkdir -p "${out_dir}"

railway service status -a --json > "${out_dir}/services.json"

for service in web cron Postgres; do
  file_name="$(printf '%s' "${service}" | tr '[:upper:]' '[:lower:]')"
  railway variable list --service "${service}" -k > "${out_dir}/${file_name}.env"
done

printf 'wrote Railway config snapshots to %s\n' "${out_dir}"
