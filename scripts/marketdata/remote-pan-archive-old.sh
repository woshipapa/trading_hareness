#!/usr/bin/env bash
set -euo pipefail

# Edge-only, archive-first migration for bounded historical partitions.  It
# deliberately does not delete PostgreSQL rows; deletion is a separate,
# operator-approved step after restore verification.
BASE=/opt/feishu-relay-edge/adapter-ingestion/cold-export
UPLOAD=/tmp/remote-pan-upload.mjs
mkdir -p "$BASE"

archive_day() {
  local table="$1" column="$2" day="$3"
  local file="$BASE/${table}_${day}.csv.gz"
  local remote_base="/apps/股票paper存储/market-realtime/history/postgres/${table}/exchange_date=${day}"
  sudo -u postgres psql -d quant_intraday_edge -v ON_ERROR_STOP=1 -c \
    "COPY (SELECT * FROM quant.${table} WHERE (${column} AT TIME ZONE 'Asia/Shanghai')::date = DATE '${day}') TO STDOUT WITH (FORMAT csv, HEADER true)" \
    | gzip -n -1 > "$file"
  local rows bytes sha
  rows="$(sudo -u postgres psql -d quant_intraday_edge -Atc "SELECT count(*) FROM quant.${table} WHERE (${column} AT TIME ZONE 'Asia/Shanghai')::date = DATE '${day}'")"
  bytes="$(stat -c %s "$file")"
  sha="$(sha256sum "$file" | awk '{print $1}')"
  local remote="${remote_base}/data-${sha:0:12}.csv.gz"
  # The adapter reconciler removes untracked files under its ingestion mount.
  # Copy the immutable helper to container /tmp immediately before each pair
  # of uploads so a long archive run can safely resume after reconciliation.
  docker cp /opt/feishu-relay-edge/remote-pan-upload.mjs "feishu-relay-edge-adapter:${UPLOAD}"
  docker exec feishu-relay-edge-adapter node "$UPLOAD" "/var/lib/adapter-ingestion/cold-export/${table}_${day}.csv.gz" "$remote"
  printf '{"schema":"postgres-cold-archive-v1","table":"%s","exchange_date":"%s","rows":%s,"bytes":%s,"sha256":"%s","data_path":"%s"}\n' "$table" "$day" "$rows" "$bytes" "$sha" "$remote" > "$file.manifest.json"
  docker exec feishu-relay-edge-adapter node "$UPLOAD" "/var/lib/adapter-ingestion/cold-export/${table}_${day}.csv.gz.manifest.json" "${remote%.csv.gz}.manifest.json"
  rm -f "$file" "$file.manifest.json"
  echo "archived ${table}/${day} rows=${rows} bytes=${bytes}"
}

for day in 2026-08-24 2026-08-25 2026-08-26 2026-08-27 2026-08-28; do
  archive_day raw_market_observations available_at "$day"
done
for day in 2026-08-25 2026-08-26 2026-08-27 2026-08-28; do
  archive_day intraday_quote_observations observed_at "$day"
done
