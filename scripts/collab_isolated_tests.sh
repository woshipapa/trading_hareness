#!/usr/bin/env bash
# The quant suite for a collaboration-branch merge check: the tree's code, no secrets, no internet.
#
#   scripts/collab_isolated_tests.sh <tree>                       the whole suite
#   QUANT_TESTS="tests.test_a" scripts/collab_isolated_tests.sh <tree>   selected modules
#
# collab_branch.py runs this on the trial merge of collab/owner-peer, i.e. on code the owner
# side wrote, so it differs from quant-service/scripts/run-release-path-tests.sh on purpose:
#   * no compose env: the official runner starts quant-research with the main checkout's .env
#     (write key, Feishu secrets) in its environment; this container gets database settings only;
#   * an --internal Docker network: the test container and its throwaway Postgres reach each
#     other and nothing else;
#   * the tree is copied in with `docker cp`: colima shares only $HOME with its VM, so a
#     temporary worktree cannot be bind-mounted;
#   * the caller runs this copy from the main checkout, never the one inside the tree under test.
# The schema is built from the tree's own migrations into a fresh database dropped afterwards.
set -euo pipefail
TREE="${1:?usage: collab_isolated_tests.sh <tree>}"
NET=collab-check-net
PG=collab-check-pg
IMAGE="${COLLAB_TEST_IMAGE:-n8n-quant-research:local}"
database="quant_test_$(date +%Y%m%d%H%M%S)_$$"
docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal "$NET" >/dev/null
if ! docker inspect "$PG" >/dev/null 2>&1; then
  docker run -d --rm --name "$PG" --network "$NET" -e POSTGRES_USER=n8n -e POSTGRES_PASSWORD=collab-check \
    -e POSTGRES_DB=n8n postgres:16-alpine >/dev/null
fi
for _ in $(seq 1 60); do
  docker exec "$PG" pg_isready -U n8n -d n8n >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$PG" psql -qX -v ON_ERROR_STOP=1 -U n8n -d n8n -c "CREATE DATABASE $database" >/dev/null
if [ -n "${QUANT_TESTS:-}" ]; then
  tests="python -m unittest $QUANT_TESTS -q"
else
  tests="python -m unittest discover -s tests -q"
fi
cid=$(docker create --network "$NET" \
  -e PGHOST="$PG" -e PGPORT=5432 -e PGUSER=n8n -e PGPASSWORD=collab-check -e PGDATABASE="$database" \
  -e QUANT_DATA_DIR=/tmp/quant -e PYTHONPATH=/src/quant-service/tests:/src/quant-service \
  -w /src/quant-service "$IMAGE" sh -c "python database_bootstrap.py >/dev/null && $tests")
cleanup() {
  docker rm -f "$cid" >/dev/null 2>&1 || true
  docker exec "$PG" psql -qX -U n8n -d n8n -c "DROP DATABASE IF EXISTS $database WITH (FORCE)" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker cp "$TREE/." "$cid:/src" >/dev/null
docker start -a "$cid"
