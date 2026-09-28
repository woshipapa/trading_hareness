#!/usr/bin/env bash
# Deepen the all-A daily history the post-close screens need.
#
# The 30-day base screen had nothing to judge against: full-market coverage
# reached back about two weeks and only 32 symbols carried 30 sessions. This
# walks back one trading day at a time and stops as soon as a day is already
# complete, so a second run costs almost nothing.
#
# Run it in the owner's 04:00-08:00 batch window through the research
# scheduler. The scheduler is pinned to db-tunnel:5433; the intraday service
# and its 5432 lane are never used for historical writes.
set -uo pipefail

DAYS="${1:-40}"
MIN_ROWS="${BACKFILL_MIN_ROWS:-5000}"
PAUSE="${BACKFILL_PAUSE_SECONDS:-12}"
CONTAINER=trading-hareness-peer-quant-research-scheduler-1
PORT="${PEER_RESEARCH_QUANT_PORT:-15683}"

if ! python3 - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
hour = datetime.now(ZoneInfo("Asia/Shanghai")).hour
raise SystemExit(0 if 4 <= hour < 8 else 1)
PY
then
  printf 'batch backfill is restricted to 04:00-08:00 Asia/Shanghai\n' >&2
  exit 2
fi

lane=$(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null || true)
printf '%s\n' "$lane" | grep -qx 'PGHOST=db-tunnel' || { printf 'scheduler is not on db-tunnel\n' >&2; exit 2; }
printf '%s\n' "$lane" | grep -qx 'PGPORT=5433' || { printf 'scheduler is not on batch port 5433\n' >&2; exit 2; }

KEY=$(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep '^QUANT_WRITE_API_KEY=' | cut -d= -f2-)

have_rows() {
  docker exec "$CONTAINER" python -c "
import os, sys, psycopg
conn = psycopg.connect(host=os.environ['PGHOST'], port=os.environ['PGPORT'], dbname=os.environ['PGDATABASE'],
                       user=os.environ['PGUSER'], password=os.environ['PGPASSWORD'])
with conn.cursor() as cur:
    cur.execute('SELECT count(*) FROM quant.canonical_bars_daily WHERE trading_date=%s', (sys.argv[1],))
    print(cur.fetchone()[0])
" "$1" 2>/dev/null
}

trading_days() {
  docker exec "$CONTAINER" python -c "
import os, sys, psycopg
conn = psycopg.connect(host=os.environ['PGHOST'], port=os.environ['PGPORT'], dbname=os.environ['PGDATABASE'],
                       user=os.environ['PGUSER'], password=os.environ['PGPASSWORD'])
with conn.cursor() as cur:
    # The exchange calendar, not a weekday guess: a holiday asked for as a
    # session returns nothing and reads exactly like a failed fetch.
    cur.execute('''SELECT DISTINCT calendar_date FROM quant.market_trade_calendar
                    WHERE is_open AND calendar_date <= (now() AT TIME ZONE 'Asia/Shanghai')::date
                    ORDER BY calendar_date DESC LIMIT %s''', (int(sys.argv[1]),))
    for row in cur.fetchall():
        print(row[0])
" "$1" 2>/dev/null
}

filled=0; skipped=0; failed=0
for day in $(trading_days "$DAYS"); do
  rows=$(have_rows "$day")
  if [ "${rows:-0}" -ge "$MIN_ROWS" ] 2>/dev/null; then
    skipped=$((skipped + 1)); continue
  fi
  body=$(curl -s -m 900 -X POST "http://127.0.0.1:${PORT}/api/v1/market/sync/full-daily" \
    -H 'Content-Type: application/json' -H "X-Quant-Write-Key: $KEY" \
    -d "{\"trade_date\":\"$day\",\"provider\":\"auto\",\"minimum_rows\":$MIN_ROWS}")
  case "$body" in
    *'"status":"completed"'*)
      filled=$((filled + 1))
      printf '%s %s filled (%s rows before)\n' "$(date +%T)" "$day" "${rows:-0}" ;;
    *rate-limit*|*saturated*)
      printf '%s %s deferred: provider queue is busy\n' "$(date +%T)" "$day"
      sleep 60 ;;
    *)
      failed=$((failed + 1))
      printf '%s %s FAILED %s\n' "$(date +%T)" "$day" "$(printf '%s' "$body" | head -c 160)" ;;
  esac
  sleep "$PAUSE"
done
printf 'filled=%s already_complete=%s failed=%s\n' "$filled" "$skipped" "$failed"

docker exec "$CONTAINER" python -c "
import os, psycopg
conn = psycopg.connect(host=os.environ['PGHOST'], port=os.environ['PGPORT'], dbname=os.environ['PGDATABASE'],
                       user=os.environ['PGUSER'], password=os.environ['PGPASSWORD'])
with conn.cursor() as cur:
    cur.execute('''SELECT count(*) FROM (SELECT symbol FROM quant.canonical_bars_daily
                     GROUP BY symbol HAVING count(DISTINCT trading_date) >= 30) t''')
    deep = cur.fetchone()[0]
    cur.execute('SELECT count(DISTINCT trading_date) FROM quant.canonical_bars_daily')
    print('symbols with >=30 sessions: %s  distinct sessions: %s' % (deep, cur.fetchone()[0]))
"

# A partial batch is not a successful systemd run.  Returning non-zero makes
# the failure visible to the timer/monitoring lane instead of silently waiting
# for the next morning while the research gate remains stale.
if (( failed > 0 )); then
  printf 'batch backfill finished with %s failed date(s)\n' "$failed" >&2
  exit 1
fi
