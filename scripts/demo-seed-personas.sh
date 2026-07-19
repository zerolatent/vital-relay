#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

fail() {
  printf 'demo-seed-personas: %s\n' "$*" >&2
  exit 2
}

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
env_file="${VITAL_RELAY_DEMO_ENV_FILE:-${repository_root}/.env.demo}"
credentials_file="${VITAL_RELAY_DEMO_CREDENTIALS_FILE:-${repository_root}/demo-credentials.txt}"

[[ -f "${env_file}" ]] || \
  fail "${env_file} does not exist; run ${script_directory}/demo-db-up.sh first"

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

database_url="${VITAL_RELAY_DATABASE_URL:-}"
scope_id="${VITAL_RELAY_DEMO_SCOPE_ID:-}"
api_base_url="${VITAL_RELAY_API_BASE_URL:-http://127.0.0.1:8000}"
community_user_id="${VITAL_RELAY_DEMO_COMMUNITY_USER_ID:-demo-community-person}"
community_display_name="${VITAL_RELAY_DEMO_COMMUNITY_DISPLAY_NAME:-Demo community member}"
command_display_name="${VITAL_RELAY_DEMO_COMMAND_DISPLAY_NAME:-Incident command}"

[[ "${database_url}" == postgresql+psycopg://* || "${database_url}" == postgresql://* ]] || \
  fail "VITAL_RELAY_DATABASE_URL must be a PostgreSQL URL"
[[ "${scope_id}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || \
  fail "VITAL_RELAY_DEMO_SCOPE_ID must be an explicit RFC 4122 UUID"
[[ -n "${community_user_id}" && "${community_user_id}" != *$'\n'* ]] || \
  fail "VITAL_RELAY_DEMO_COMMUNITY_USER_ID must be non-empty and single-line"
[[ -n "${community_display_name}" && "${community_display_name}" != *$'\n'* ]] || \
  fail "VITAL_RELAY_DEMO_COMMUNITY_DISPLAY_NAME must be non-empty and single-line"
[[ -n "${command_display_name}" && "${command_display_name}" != *$'\n'* ]] || \
  fail "VITAL_RELAY_DEMO_COMMAND_DISPLAY_NAME must be non-empty and single-line"

python_executable="${repository_root}/.venv/bin/python"
database_cli="${repository_root}/.venv/bin/vital-relay-db"
[[ -x "${python_executable}" ]] || fail "run 'make install' before seeding"
[[ -x "${database_cli}" ]] || fail "vital-relay-db is unavailable; run 'make install'"

temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/vital-relay-demo-seed.XXXXXX")"
cleanup() {
  rm -rf -- "${temporary_root}"
}
trap cleanup EXIT HUP INT TERM

network_receipt="${temporary_root}/response-network.json"
community_receipt="${temporary_root}/community.json"
command_receipt="${temporary_root}/command.json"

(
  cd -- "${repository_root}"
  "${database_cli}" --database-url "${database_url}" seed-response-network \
    --scope "${scope_id}" \
    --confirm "${scope_id}" >"${network_receipt}"
  "${database_cli}" --database-url "${database_url}" create-persona-account \
    --scope "${scope_id}" \
    --confirm "${scope_id}" \
    --persona community \
    --display-name "${community_display_name}" \
    --user-id "${community_user_id}" >"${community_receipt}"
  "${database_cli}" --database-url "${database_url}" create-persona-account \
    --scope "${scope_id}" \
    --confirm "${scope_id}" \
    --persona command \
    --display-name "${command_display_name}" >"${command_receipt}"
)

rendered_summary="$(
  "${python_executable}" "${script_directory}/demo-seed-render.py" \
    --network-receipt "${network_receipt}" \
    --community-receipt "${community_receipt}" \
    --command-receipt "${command_receipt}" \
    --scope-id "${scope_id}" \
    --api-base-url "${api_base_url}" \
    --env-file "${env_file}" \
    --credentials-file "${credentials_file}"
)"

printf '%s\n' "${rendered_summary}"
printf 'Enrollment codes were rotated and saved only to %s (mode 0600).\n' \
  "$(cd -- "$(dirname -- "${credentials_file}")" && pwd -P)/$(basename -- "${credentials_file}")"
printf 'Source %s before starting the backend or simulator orchestration.\n' \
  "$(cd -- "$(dirname -- "${env_file}")" && pwd -P)/$(basename -- "${env_file}")"
