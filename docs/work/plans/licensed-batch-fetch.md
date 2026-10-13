# Batch reads of the licensed interface: one tool, one concurrency rule

Workstream W013. Written 2026-10-13 (Asia/Shanghai).

## Goal

Every batch read of the licensed interface from the operator workstation goes through one tool that runs at the
concurrency measured on 2026-10-13, refuses the settings that the upstream refuses, and stays out of the trading
session. The rule is written where agents read it.

The user's words, 2026-10-13: "好的，你把代码和规范改一下以后都跑这个批量的并发".

## What was measured (docs/work/FACTS.md F027)

Read calls `GetStockTrend` through the owner read path, 22:45-23:00, outside the session:

| in flight | calls a second | seconds a call (median) |
|---|---|---|
| 1 | 1.2 (with a 0.3 s pause) | about 0.5 |
| 4 | 4.8-5.3 | 0.71 |
| 8 | 6.7-7.3 | 1.06 |
| 12 | 7.1-7.4 | 1.5-1.6 |
| 16 through the peer service | 7.2-7.8 (the rest queue in its 8-thread executor) | 2.0 |
| 16 straight at the path behind it | refused: 208 of 240 calls answered with an HTTP error at once | |

The ceiling is about 7.4 calls a second, reached at 8 in flight; it sits on the owner's gateway, not on the peer
host's bandwidth and not on the peer service's executor (`AKSHARE_MAX_WORKERS`, 8). That executor is shared with
the service's own public-source jobs.

## The rule

- Default 6 in flight. At most 8. More is refused by the tool: it buys nothing and 16 is refused upstream.
- Not between 09:00 and 15:10 Asia/Shanghai on an exchange session (the service's realtime jobs share the path
  and the executor). The tool refuses to start then and pauses when the time comes, unless told otherwise on the
  command line.
- Twenty failed calls in a row stop the run. An upstream error code other than "no data" counts as a failed call,
  so a refusal cannot pass as an empty day.
- Resumable by construction: what is on disk is not asked again.
- A batch above 5,000 calls still needs the user's explicit consent (unchanged).
- In-service settings (`LONGHU_MINUTE_BATCH_WORKERS`, `QUANT_LONGHU_GATEWAY_WORKERS`, `AKSHARE_MAX_WORKERS`) are not
  touched: they serve the live session, which was not measured.

## The tool: `scripts/licensed_minute_history_fetch.py` (operator workstation)

Minute history (`GetStockTrend`) of any number of codes over a range of sessions, one JSON line per code and
session in `<out>/<code>.jsonl`, a log `<out>/fetch.log`. It replaces the dated probe
`docs/work/probes/2026-10-13-index-minute-fetch.py`, whose loop it takes over.

- Arguments: codes on the command line and/or `--codes-file` (one a line); `--start` and `--end` (dates; the end
  defaults to the last closed session); `--out` (required); `--workers` (default 6; above 8 is an error that says
  why); `--until HH:MM` (stop cleanly then); `--allow-session` (lift the session guard, for a small run the user
  asked for); `--base-url` (default `http://127.0.0.1:15682`, the owner read path; the tool refuses
  `:5681`, which is the local service and not owner data).
- Sessions come from `exchange_calendars` (XSHG), as `scripts/tdx-promote.py` already does; no local research file.
- The read key is read in memory from `deploy/shared-peer/.env` and never printed, logged or put on the command
  line.
- Standard library plus `exchange_calendars`. No pandas.
- Each log line every 200 calls: calls so far, the code and day reached, calls a second, the median and the 90th
  percentile of seconds a call. The last line is `FINISHED`, `PAUSED` or `ABORT` with the reason.
- The pure parts are functions a test can call without a network: the bound on workers, the session guard at a
  given time, the classification of a payload (data, no data, refusal), the list of what is still to fetch.

Tests `scripts/test_licensed_minute_history_fetch.py` (`unittest.TestCase`, run by `pytest scripts`): workers 9
is refused and 8 accepted; 10:00 on a session is blocked, 16:00 allowed, a Saturday allowed; a payload with error
code 1020 and no data is "no data", another error code is a refusal; a day already in the file is not asked again
and results are written in session order although they return out of order; `:5681` is refused.

Also in the same change:
- `scripts/shared-peer/classify-owner-paths.sh`: the tool is a workstation tool (the rule the teacher-cycle scripts
  and `scripts/tdx-promote.py` have) with a case in `scripts/shared-peer/deploy-code-only.test.mjs`; its test file
  is already covered by the test rule.
- `scripts/generate_scripts_catalog.py` gets its rule and `scripts/CATALOG.md` is regenerated.

## Acceptance

```
/Users/papa/.venvs/svc/bin/python -m pytest scripts -q     # test_runtime_hosts.py fails on main already; nothing else may
node scripts/shared-peer/deploy-code-only.test.mjs
python3 scripts/generate_scripts_catalog.py --check
git diff --check
```

No request to any host in the dispatch: the tests use a fake call.

## Where the rule is written (the planner does this, not the dispatch)

`AGENTS.md` ("Time and data rules"), `docs/work/README.md` (commands and hazards), the operator's memory note.
