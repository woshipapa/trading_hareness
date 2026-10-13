"""Wait for a Codex turn to end in a session log. Usage: wait_codex.py <thread-id-prefix> [stall-minutes]
Exits when a task_complete newer than the last user_message appears, when an error or abort event appears after it,
or when the log has not been written for the stall time. Prints the outcome and the last agent message."""
import glob, json, os, sys, time

prefix, stall = sys.argv[1], float(sys.argv[2]) * 60 if len(sys.argv) > 2 else 600
path = sorted(glob.glob(os.path.expanduser(f"~/.codex-teleai-mcp/sessions/2026/10/1[23]/*{prefix}*")))[-1]


def state():
    last_user = -1; events = []
    for index, line in enumerate(open(path, encoding="utf-8")):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        payload = item.get("payload") or {}
        kind = payload.get("type")
        if kind == "user_message":
            last_user = index; events = []
        elif kind in ("task_complete", "turn_aborted", "error", "stream_error", "agent_message"):
            events.append((kind, payload))
    return last_user, events


while True:
    _, events = state()
    kinds = [kind for kind, _ in events]
    ended = next((kind for kind in kinds if kind in ("task_complete", "turn_aborted", "error", "stream_error")), None)
    idle = time.time() - os.path.getmtime(path)
    if ended or idle > stall:
        message = next((payload.get("message") or payload.get("last_agent_message") or "" for kind, payload in reversed(events) if kind in ("agent_message", "task_complete")), "")
        detail = next((json.dumps(payload, ensure_ascii=False)[:300] for kind, payload in events if kind in ("turn_aborted", "error", "stream_error")), "")
        print(f"{prefix}: {ended or 'STALLED'} (log idle {idle:.0f}s) {detail}")
        print(str(message)[:3500])
        break
    time.sleep(15)
