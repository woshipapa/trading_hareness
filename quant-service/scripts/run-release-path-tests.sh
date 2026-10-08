#!/usr/bin/env bash
# Run the quant suite the way a release runs the code, against a database made for this run.
#
#   scripts/run-release-path-tests.sh                      the whole suite
#   QUANT_TESTS="tests.test_a tests.test_b" scripts/...    selected modules
#
# The working tree is mounted into the existing image, as deploy-code-only.sh
# ships it, and the schema is built from the working tree's migrations into a
# fresh database that is dropped afterwards. Against the long-lived development
# database the result depended on whatever earlier runs and manual sessions left
# behind: on 2026-10-08 three of the four failures were a schema two revisions
# behind head and rows dated 2099 from an earlier run.
set -euo pipefail
cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f compose.yaml)
database="quant_test_$(date +%Y%m%d%H%M%S)_$$"

admin_sql() {
  "${COMPOSE[@]}" exec -T postgres psql -qX -v ON_ERROR_STOP=1 -U n8n -d n8n -c "$1"
}
run_in_image() {
  "${COMPOSE[@]}" run --rm --no-deps -T -v "$PWD:/src:ro" \
    -e PYTHONPATH=/src/quant-service/tests:/src/quant-service -e PGDATABASE="$database" \
    -w /src/quant-service quant-research "$@"
}

"${COMPOSE[@]}" up -d --wait postgres >/dev/null
admin_sql "CREATE DATABASE $database"
trap 'admin_sql "DROP DATABASE IF EXISTS $database WITH (FORCE)" >/dev/null 2>&1 || true' EXIT

run_in_image python database_bootstrap.py >/dev/null
if [ -n "${QUANT_TESTS:-}" ]; then
  # shellcheck disable=SC2086 - a space-separated list of test modules
  run_in_image python -m unittest $QUANT_TESTS -q
else
  run_in_image python -m unittest discover -s tests -q
fi
