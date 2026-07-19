#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

fail() {
  printf 'demo-db-up: %s\n' "$*" >&2
  exit 2
}

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_directory}/.." && pwd -P)"
postgres_bin="${VITAL_RELAY_POSTGRES_BIN:-/Applications/Postgres.app/Contents/Versions/latest/bin}"
database_host="${VITAL_RELAY_DEMO_DB_HOST:-127.0.0.1}"
database_port="${VITAL_RELAY_DEMO_DB_PORT:-5432}"
database_user="${VITAL_RELAY_DEMO_DB_USER:-$(id -un)}"
database_name="${VITAL_RELAY_DEMO_DB_NAME:-vital_relay}"
scope_id="${VITAL_RELAY_DEMO_SCOPE_ID:-11111111-1111-4111-8111-111111111111}"
retention_hours="${VITAL_RELAY_DEMO_RETENTION_HOURS:-24}"
api_base_url="${VITAL_RELAY_API_BASE_URL:-http://127.0.0.1:8000}"
env_file="${VITAL_RELAY_DEMO_ENV_FILE:-${repository_root}/.env.demo}"
startup_timeout_seconds="${VITAL_RELAY_DEMO_DB_START_TIMEOUT_SECONDS:-30}"

[[ "${database_host}" == "127.0.0.1" || "${database_host}" == "localhost" ]] || \
  fail "VITAL_RELAY_DEMO_DB_HOST must be 127.0.0.1 or localhost for Postgres.app"
[[ "${database_port}" =~ ^[0-9]+$ ]] && (( database_port >= 1 && database_port <= 65535 )) || \
  fail "VITAL_RELAY_DEMO_DB_PORT must be an integer from 1 through 65535"
[[ "${database_user}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || \
  fail "VITAL_RELAY_DEMO_DB_USER must be a simple PostgreSQL role name"
[[ "${database_name}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || \
  fail "VITAL_RELAY_DEMO_DB_NAME must be a simple PostgreSQL database name"
[[ "${scope_id}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || \
  fail "VITAL_RELAY_DEMO_SCOPE_ID must be an explicit RFC 4122 UUID"
[[ "${retention_hours}" =~ ^[0-9]+$ ]] && (( retention_hours >= 1 && retention_hours <= 168 )) || \
  fail "VITAL_RELAY_DEMO_RETENTION_HOURS must be an integer from 1 through 168"
[[ "${startup_timeout_seconds}" =~ ^[0-9]+$ ]] && (( startup_timeout_seconds >= 1 && startup_timeout_seconds <= 120 )) || \
  fail "VITAL_RELAY_DEMO_DB_START_TIMEOUT_SECONDS must be an integer from 1 through 120"
[[ "${api_base_url}" == http://127.0.0.1:* || "${api_base_url}" == http://localhost:* ]] || \
  fail "VITAL_RELAY_API_BASE_URL must be an HTTP loopback URL for the local demo"

for executable_name in pg_isready psql createdb; do
  [[ -x "${postgres_bin}/${executable_name}" ]] || \
    fail "missing Postgres.app executable: ${postgres_bin}/${executable_name}"
done

python_executable="${repository_root}/.venv/bin/python"
database_cli="${repository_root}/.venv/bin/vital-relay-db"
[[ -x "${python_executable}" ]] || fail "run 'make install' before demo database setup"
[[ -x "${database_cli}" ]] || fail "vital-relay-db is unavailable; run 'make install'"

postgres_ready() {
  "${postgres_bin}/pg_isready" \
    --host="${database_host}" \
    --port="${database_port}" \
    --username="${database_user}" \
    --timeout=2 >/dev/null 2>&1
}

if ! postgres_ready; then
  [[ "${VITAL_RELAY_DEMO_DB_AUTO_START:-true}" == "true" ]] || \
    fail "Postgres.app is not accepting connections and automatic start is disabled"
  [[ "$(uname -s)" == "Darwin" ]] || \
    fail "automatic Postgres.app startup is supported only on macOS"
  [[ -d /Applications/Postgres.app ]] || fail "Postgres.app is not installed in /Applications"
  /usr/bin/open -g -a Postgres || fail "could not launch Postgres.app"
  for (( attempt = 0; attempt < startup_timeout_seconds; attempt += 1 )); do
    if postgres_ready; then
      break
    fi
    sleep 1
  done
fi
postgres_ready || fail "Postgres.app did not become ready within ${startup_timeout_seconds}s"

psql_admin=(
  "${postgres_bin}/psql"
  --no-psqlrc
  --host="${database_host}"
  --port="${database_port}"
  --username="${database_user}"
  --dbname=postgres
  --no-align
  --tuples-only
  --set=ON_ERROR_STOP=1
)

database_exists="$("${psql_admin[@]}" \
  --command="SELECT 1 FROM pg_database WHERE datname = '${database_name}'")"
if [[ -z "${database_exists}" ]]; then
  "${postgres_bin}/createdb" \
    --host="${database_host}" \
    --port="${database_port}" \
    --username="${database_user}" \
    --maintenance-db=postgres \
    --owner="${database_user}" \
    "${database_name}"
elif [[ "${database_exists}" != "1" ]]; then
  fail "unexpected database existence result"
fi

psql_demo=(
  "${postgres_bin}/psql"
  --no-psqlrc
  --host="${database_host}"
  --port="${database_port}"
  --username="${database_user}"
  --dbname="${database_name}"
  --no-align
  --tuples-only
  --set=ON_ERROR_STOP=1
)

"${psql_demo[@]}" --command='CREATE EXTENSION IF NOT EXISTS postgis' >/dev/null
postgis_version="$("${psql_demo[@]}" --command='SELECT PostGIS_Version()')"
[[ "${postgis_version}" == 3.* ]] || \
  fail "PostGIS 3 is required; server reported '${postgis_version}'"

database_url="postgresql+psycopg://${database_user}@${database_host}:${database_port}/${database_name}"
(
  cd -- "${repository_root}"
  "${database_cli}" --database-url "${database_url}" upgrade
)

alembic_revision="$("${psql_demo[@]}" --command='SELECT version_num FROM alembic_version')"
[[ -n "${alembic_revision}" ]] || fail "Alembic did not record a database revision"

scope_state="$("${psql_demo[@]}" \
  --command="SELECT status || '|' || (expires_at > CURRENT_TIMESTAMP)::text || '|' || expires_at::text FROM demo_scopes WHERE scope_id = '${scope_id}'::uuid")"
if [[ -z "${scope_state}" ]]; then
  (
    cd -- "${repository_root}"
    "${database_cli}" --database-url "${database_url}" create-scope \
      --scope "${scope_id}" \
      --retention-hours "${retention_hours}"
  )
  scope_state="$("${psql_demo[@]}" \
    --command="SELECT status || '|' || (expires_at > CURRENT_TIMESTAMP)::text || '|' || expires_at::text FROM demo_scopes WHERE scope_id = '${scope_id}'::uuid")"
fi

IFS='|' read -r scope_status scope_unexpired scope_expires_at <<<"${scope_state}"
[[ "${scope_status}" == "active" && "${scope_unexpired}" == "true" ]] || \
  fail "scope ${scope_id} is not reusable (${scope_state}); choose a new explicit VITAL_RELAY_DEMO_SCOPE_ID"

written_env_file="$(
  "${python_executable}" "${script_directory}/demo-db-write-env.py" \
    --env-file "${env_file}" \
    --database-url "${database_url}" \
    --scope-id "${scope_id}" \
    --api-base-url "${api_base_url}"
)"

printf 'Postgres.app database: %s@%s:%s/%s\n' \
  "${database_user}" "${database_host}" "${database_port}" "${database_name}"
printf 'PostGIS: %s\n' "${postgis_version}"
printf 'Alembic revision: %s\n' "${alembic_revision}"
printf 'Demo scope: %s (active until %s)\n' "${scope_id}" "${scope_expires_at}"
printf 'Environment: %s (mode 0600)\n' "${written_env_file}"
printf 'Next: %s/demo-seed-personas.sh\n' "${script_directory}"
