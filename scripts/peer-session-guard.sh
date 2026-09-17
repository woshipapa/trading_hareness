#!/usr/bin/env bash
# Keep the peer's intraday collection usable, and say so before the session.
#
# Docker marks a container unhealthy but never restarts it, so on 2026-09-17 a
# wedged service sat "healthy"-looking and idle for 45 minutes with nobody
# watching. This guard is what acts on that, and what says something when it
# cannot fix it before the market opens.
#
#   heal     - restart what is unhealthy; run often, stay quiet when it works
#   preopen  - heal, then verify and report; run before the session
#   selftest - send one clearly-marked test message, so the alert path is known
#              to work before it is needed rather than after
#
# Alerts go straight to Feishu, deliberately not through the quant service: the
# thing being checked is the thing that would otherwise have to deliver the
# message. PEER_GUARD_FEISHU_WEBHOOK (a custom-bot hook for 盘中股票提醒群) is
# the normal path; the app credentials remain a fallback for when the hook is
# rotated or revoked, so a rotation cannot silence the alarm.
set -uo pipefail

MODE="${1:-heal}"
COMPOSE_DIR="${PEER_COMPOSE_DIR:-$HOME/trading_hareness/deploy/shared-peer}"
STATE_DIR="${PEER_GUARD_STATE_DIR:-$HOME/.local/state/peer-session-guard}"
RESTART_COOLDOWN_SECONDS="${PEER_GUARD_RESTART_COOLDOWN_SECONDS:-600}"
MAIN_PORT="${PEER_QUANT_PORT:-15682}"
SCHEDULER_PORT="${PEER_SCHEDULER_PORT:-15683}"
EXPECTED_MAIN_PROFILE="${PEER_EXPECTED_MAIN_PROFILE:-intraday_edge}"

MAIN=trading-hareness-peer-quant-research-1
SCHEDULER=trading-hareness-peer-quant-research-scheduler-1
TUNNEL=trading-hareness-peer-db-tunnel-1

mkdir -p "$STATE_DIR"
problems=()
actions=()

note() { printf '%s %s\n' "$(date -Is)" "$*"; }
problem() { problems+=("$1"); note "PROBLEM: $1"; }
acted() { actions+=("$1"); note "ACTION: $1"; }

# Both compose files, always. Using compose.yaml alone silently downgrades the
# main container from intraday_edge to research - it is the same file set the
# release pipeline uses, and half of it is not a valid deployment.
compose() {
  if [ ! -f "$COMPOSE_DIR/compose.intraday-owner.yaml" ]; then
    problem "compose.intraday-owner.yaml is missing from $COMPOSE_DIR; refusing to act with a partial file set"
    return 1
  fi
  (cd "$COMPOSE_DIR" && docker compose -f compose.yaml -f compose.intraday-owner.yaml "$@")
}

container_state() { docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null || echo missing; }

container_health() {
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null || echo missing
}

cooled_down() {
  local name=$1 stamp="$STATE_DIR/restart-$1"
  [ -f "$stamp" ] || return 0
  local last now
  last=$(cat "$stamp" 2>/dev/null || echo 0)
  now=$(date +%s)
  [ $((now - last)) -ge "$RESTART_COOLDOWN_SECONDS" ]
}

mark_restarted() { date +%s > "$STATE_DIR/restart-$1"; }

wait_healthy() {
  local name=$1 limit=${2:-90} waited=0
  while [ "$waited" -lt "$limit" ]; do
    [ "$(container_health "$name")" = healthy ] && return 0
    sleep 5; waited=$((waited + 5))
  done
  return 1
}

restart_service() {
  local service=$1 name=$2
  if ! cooled_down "$service"; then
    problem "$service is unhealthy but was restarted less than ${RESTART_COOLDOWN_SECONDS}s ago; not restarting again"
    return 1
  fi
  mark_restarted "$service"
  if compose restart "$service" >/dev/null 2>&1 && wait_healthy "$name"; then
    acted "restarted $service and it became healthy"
    return 0
  fi
  problem "restarted $service but it did not become healthy"
  return 1
}

health_json() { curl -fsS -m 15 "http://127.0.0.1:$1/health" 2>/dev/null; }

text_payload() {
  python3 -c '
import json, sys
print(json.dumps({"msg_type": "text", "content": {"text": sys.argv[1]}}, ensure_ascii=False))
' "$1"
}

alert_via_webhook() {
  local text=$1 response
  [ -n "${PEER_GUARD_FEISHU_WEBHOOK:-}" ] || return 1
  response=$(curl -fsS -m 15 -X POST "$PEER_GUARD_FEISHU_WEBHOOK" \
    -H 'Content-Type: application/json' -d "$(text_payload "$text")" 2>/dev/null) || return 1
  # The hook answers 200 with a body even when it rejects the message - a
  # missing keyword or a bad signature shows up only in "code".
  printf '%s' "$response" | python3 -c '
import json, sys
body = json.load(sys.stdin)
sys.exit(0 if body.get("code", 0) == 0 else 1)
' 2>/dev/null || { note "webhook rejected the message: $(printf '%s' "$response" | head -c 200)"; return 1; }
}

alert_via_app_credentials() {
  local text=$1 token
  [ -n "${FEISHU_APP_ID:-}" ] && [ -n "${FEISHU_APP_SECRET:-}" ] && [ -n "${FEISHU_ALERT_RECEIVE_ID:-}" ] || return 1
  token=$(curl -fsS -m 15 -X POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal \
    -H 'Content-Type: application/json' \
    -d "{\"app_id\":\"$FEISHU_APP_ID\",\"app_secret\":\"$FEISHU_APP_SECRET\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tenant_access_token",""))' 2>/dev/null)
  [ -n "$token" ] || return 1
  curl -fsS -m 15 -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=${FEISHU_ALERT_RECEIVE_ID_TYPE:-chat_id}" \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
    -d "$(python3 -c '
import json, sys
print(json.dumps({"receive_id": sys.argv[1], "msg_type": "text",
                  "content": json.dumps({"text": sys.argv[2]}, ensure_ascii=False)}, ensure_ascii=False))
' "$FEISHU_ALERT_RECEIVE_ID" "$text")" >/dev/null
}

alert() {
  local text=$1
  if alert_via_webhook "$text"; then
    note "alert sent via webhook"; return 0
  fi
  if alert_via_app_credentials "$text"; then
    note "alert sent via app credentials (webhook unavailable)"; return 0
  fi
  note "ALERT NOT DELIVERED - no working Feishu channel"
  return 1
}

if [ "$MODE" = selftest ]; then
  alert "✅ 盘中抓取守护自检 @ $(date '+%F %T %Z') — 这条是测试消息，收到即表示告警通道可用。"
  exit $?
fi

# --- heal -------------------------------------------------------------------
# The tunnel goes first: the application containers cannot become healthy while
# their database path is down, and restarting them ahead of it just burns the
# cooldown on a container that was never the problem.
for pair in "db-tunnel:$TUNNEL" "quant-research:$MAIN" "quant-research-scheduler:$SCHEDULER"; do
  service=${pair%%:*}; name=${pair##*:}
  state=$(container_state "$name")
  health=$(container_health "$name")
  if [ "$state" = missing ]; then
    problem "$name does not exist"
    continue
  fi
  if [ "$state" != running ]; then
    problem "$name is $state"
    restart_service "$service" "$name"
    continue
  fi
  if [ "$health" = unhealthy ]; then
    problem "$name is unhealthy"
    restart_service "$service" "$name"
  fi
done

# --- the async pool, in every mode ------------------------------------------
# Checked here rather than under preopen because a stalled pool is invisible to
# Docker on any build whose health probe only pings the sync pool: the
# container reports healthy while every collector fails. Reading the counters
# directly is what notices it within one heal interval.
for port_pair in "$MAIN_PORT:quant-research:$MAIN" "$SCHEDULER_PORT:quant-research-scheduler:$SCHEDULER"; do
  port=${port_pair%%:*}; rest=${port_pair#*:}; service=${rest%%:*}; name=${rest##*:}
  body=$(health_json "$port")
  if [ -z "$body" ]; then
    # Already reported above if the container itself is down; a running
    # container that will not answer is its own problem.
    [ "$(container_state "$name")" = running ] && {
      problem "$name is running but /health did not answer on $port"
      restart_service "$service" "$name"
    }
    continue
  fi
  read -r pool_size waiting <<<"$(printf '%s' "$body" | python3 -c '
import json, sys
pool = json.load(sys.stdin).get("async_database_pool") or {}
print(pool.get("pool_size", -1), pool.get("waiting", -1))
' 2>/dev/null || echo "-1 -1")"
  if [ "$pool_size" = "0" ] && [ "$waiting" != "0" ] && [ "$waiting" != "-1" ]; then
    problem "$name: async pool holds no connections while $waiting requests wait"
    restart_service "$service" "$name"
  fi
done

# --- verify -----------------------------------------------------------------
if [ "$MODE" = preopen ]; then
  [ -n "$(health_json "$MAIN_PORT")" ] || problem "main service /health did not answer on $MAIN_PORT"

  actual_profile=$(docker inspect "$MAIN" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | sed -n 's/^QUANT_RUNTIME_PROFILE=//p')
  # Catches a redeploy that used compose.yaml alone: the container comes up
  # healthy, with every intraday collector silently switched off.
  [ "$actual_profile" = "$EXPECTED_MAIN_PROFILE" ] \
    || problem "main container runs profile '${actual_profile:-unset}', expected '$EXPECTED_MAIN_PROFILE' - intraday collection is off"

  watchlist=$(docker exec "$MAIN" python -c '
import os, psycopg
conn = psycopg.connect(host=os.environ["PGHOST"], port=os.environ["PGPORT"], dbname=os.environ["PGDATABASE"],
                       user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"])
with conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM quant.intraday_watchlists WHERE enabled")
    print(cur.fetchone()[0])
' 2>/dev/null)
  case "$watchlist" in
    ''|*[!0-9]*) problem "could not read the intraday watchlist through the tunnel" ;;
    0) problem "the intraday watchlist is empty; nothing would be scanned" ;;
    *) note "watchlist: $watchlist symbols" ;;
  esac
fi

# --- report -----------------------------------------------------------------
if [ ${#problems[@]} -eq 0 ]; then
  note "ok (${MODE})"
  exit 0
fi

summary="⚠️ 盘中抓取服务未就绪 (${MODE}) @ $(date '+%F %T %Z')"
for item in "${problems[@]}"; do summary+=$'\n· '"$item"; done
if [ ${#actions[@]} -gt 0 ]; then
  summary+=$'\n已自动处理:'
  for item in "${actions[@]}"; do summary+=$'\n· '"$item"; done
fi

# A heal run that fixed everything it found is a normal recovery, not an
# incident: the point of the guard is that it does not need attention. Only an
# unfixed problem is worth a message and a failed unit.
unresolved=0
for item in "${problems[@]}"; do
  case "$item" in *"did not become healthy"*|*"not restarting again"*|*"does not exist"*|*"refusing to act"*) unresolved=1 ;; esac
done
if [ "$MODE" = preopen ]; then
  alert "$summary"
  exit 1
fi
if [ "$unresolved" = 1 ]; then
  alert "$summary"
  exit 1
fi
note "recovered without intervention (${MODE})"
exit 0
