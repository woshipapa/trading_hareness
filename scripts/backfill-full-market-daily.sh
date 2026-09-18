#!/usr/bin/env bash
# Deepen the all-A daily history the post-close screens need.
#
# The 30-day base screen had nothing to judge against: full-market coverage
# reached back about two weeks and only 32 symbols carried 30 sessions. This
# walks back one trading day at a time and stops as soon as a day is already
# complete, so a second run costs almost nothing.
#
# Run it after the close. The sync shares a 6-requests-per-minute provider with
# the live collectors, and during a session it loses that race - the request
# comes back "shared provider rate-limit queue is full".
set -uo pipefail

DAYS="${1:-40}"
MIN_ROWS="${BACKFILL_MIN_ROWS:-5000}"
PAUSE="${BACKFILL_PAUSE_SECONDS:-12}"
CONTAINER=trading-hareness-peer-quant-research-1
PORT="${PEER_QUANT_PORT:-15682}"

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
