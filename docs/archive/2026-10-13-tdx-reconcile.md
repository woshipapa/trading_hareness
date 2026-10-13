# TDX line of work: reconciliation of 2026-10-13
Read-only reconciliation from the plans, the catalog, the code, the release log and the branches; main at b73e6e1b; clock about 13:40 Asia/Shanghai. Facts, no recommendation.
Probe outputs cited: docs/work/probes/2026-10-13-tdx-shadow.out.txt and docs/work/probes/2026-10-13-tdx-shadow-by-gap.out.txt.

Paths below are under /Users/papa/codebase/n8n. No tests or network commands were run.

1. IN MAIN AND RELEASED
- Catalog: 40 TDX bindings, counted by parsing `_bind` calls in quant-service/app/datasources/catalog.py. tdx_public 25 (24 declared, bars.minute unsupported); tdx_mac 7 (6 declared, bars.minute unsupported); derived_tdx_limits 3 declared (:557-565); tdx_local 2 declared; tdx_ext 3 unsupported (:799-803, note "A 股决策路径不得使用"). `live_verified` = 0 and `decision_eligible=True` = 0 among all 40. The one catalog rule is "eligible implies live_verified" (:1125).
- Releases (docs/RELEASE_SYNC_47.md):
  - -15 `7ba2412e`, 10-10 (:718): six TDX lanes (security list, all-A snapshot and index overview, F10/gpcw, 7727 ext, microstructure, MAC), all unsupported.
  - -16 `5088e844`, 10-11 (:719): round 4 (legacy bars, index breadth, tdxstat, blocks, calendar/IPO, order book, IOPV, derived limit pools, `tdx-promote.py`).
  - -17 `68564e8e`, 10-11 (:720): 26 bindings moved to declared.
  - -18 `a13870fc`, 10-12 (:721): bars.daily declared.
  - -19 `004e3f88`, 10-12 (:722): the 3 limit pools declared.
  - -20 `dff8acee`, 10-12 night (:723): `tdx_all_a_shadow`.
  - -21 `b0bea637` (:723): auction pool; its TDX curve ran first on 10-13.
- Running production paths that touch TDX:
  - Shadow loop `tdx_shadow_snapshot_runtime.py`: every 60 s from 09:15 (platform/runtime_task_registry.py:92-96). It stores `tdx_public/a_share_minute_cross_section_shadow` plus `local_derived/all_a_snapshot_source_agreement`; nothing else reads them. Slot of 60/min (public_provider_rate_limits.py:39-40); own executor, 1 worker, queue 0 (runtime_executors.py:149-150). Flag `TDX_SHADOW_SNAPSHOT_ENABLED`, default true (settings.py:119, main.py:4192).
  - Auction pool `auction_pool_runtime.py:25`: one `microstructure.auction_curve` call per pool stock, at most 200, one a second. Flag `AUCTION_POOL_CAPTURE_ENABLED`, default true (settings.py:120). 78 rows for 10-13 (docs/work/probes/2026-10-13-owner-history.out.txt:11).
  - Older post-close jobs `tick_flow` and `capital_changes`: TDX first, Tencent as fallback (collectors/post_close.py:274-280, :303; catalog :482/:485/:740). On by default (not in `opt_in_keys`) behind `POST_CLOSE_PUBLIC_ARCHIVE_ENABLED`, default true.
  - Research read API of the TDX adapters: `DATASOURCE_RESEARCH_READ_ENABLED`, default true (adapter_calls.py:35, settings.py:143). It is on-demand, bounded and writes nothing.

2. IN MAIN BUT NOT SWITCHED ON OR NOT USED
- Ten post-close TDX archive jobs (security list, tipinfo, gpcw, index bars, MAC boards, limit pools, host probe, tdxstat, files membership, calendar/IPO): default off, enabled by `PUBLIC_ARCHIVE_<KEY>_ENABLED` (runtime.py:46-52, :165-167; docs/DATA_SOURCE_LAYER.md:321). Whether the owner host sets any of them is not in the repo.
- The intraday snapshot is Fuyao only: `_fetch_intraday_all_a_snapshot_rows` at main.py:1746-1748. The fallback chain (phase B) is not coded and no branch for it exists.
- Resolver (datasources/resolver.py) walks bindings by ascending `(priority, source)`; it skips `unsupported` and `retired` and applies the live_verified and decision_eligible gates only when the request asks. bindings.py:115-121 binds every declared TDX adapter. The only production `resolver.fetch` call is collectors/intraday.py:209 (`limits.anomaly_tape`, not TDX). `platform/strategy_data_context.py:211` is not wired into main.py (grep). So no live path chooses TDX through the resolver.
- Where TDX sits in catalog order (lower number first). It is last in most multi-source capabilities:
  - quote.all_a_snapshot: fuyao_ths 12 (live_verified) > akshare 60 > tdx_public 80.
  - limits.limit_up_pool: fuyao 12 > eastmoney_ztb 50 > akshare 60 > derived_tdx_limits 90.
  - bars.daily: tushare, fuyao 20, longhu 25 ... tdx_public 65 > tdx_mac 70.
  - bars.minute: longhuvip 10 (live) > tdx_local 35 > tencent 50 (live) > tdx_public 65 and tdx_mac 70 (both unsupported).
  - limits.prices: tencent 12 > longhuvip_composite 25 (live) > tdx_mac 70.
  - quote.watch_snapshot: longhuvip 10 and tencent 50, both live_verified and eligible > tdx_mac 70.
  - ticks.session: tdx_public 20 > tencent 30. fundamentals.capital_changes: tdx_public 20 > eastmoney 50.
- Tushare `tdx_index/tdx_daily/tdx_member` (tushare_catalog.py:44-45) are Tushare's own interfaces, not the TDX protocol.
- Decision 0015 steps not in main: `quote.all_a_snapshot` has no `agreement` in its BindingSpec (catalog.py:319-326). `tdx-promote.py` has no `--decision-eligible` flag; it says "decision_eligible is not touched" (:408), so no tool path sets it True.
- Evidence (scripts/data):
  - Limit pools: 3 files dated 10-12 10:50 Beijing (group `b78d7e71`) pass owner_egress, agreement and intraday. Their `catalog_status` field says unsupported while the catalog now says declared. I found no commit moving any TDX binding to live_verified.
  - bars.minute, untracked files `..._tdx_mac_2026-10-12_owner_97a98005.json` and `..._tdx_public_...`: owner_egress pass (240 rows), intraday pass, agreement FAIL. Common rows are 0.329 (mac) and 0.325 (public) against the 0.95 minimum (tdx-promote.py:71), because the Tencent reference returned only 80 and 79 rows at 10:50. All 78 and 79 matched close values agree (max relative deviation 4.6e-8 for mac, 0 for public). MAC volume: 1 of 79 mismatched, at the 09:31 bar (17,000 shares off against a tolerance of 100). Catalog status stays unsupported.

3. WHAT THE PLANS SAY IS LEFT
- Gates (.claude/plan/tdx-integration.md:78-82): fixture contract test; owner-egress probe; cross-source agreement on a sample day within the spec tolerance; "intraday probe on a trading day... Without it nothing is LIVE_VERIFIED". `tdx-promote.py` (header :17-28): unsupported to declared needs owner_egress and agreement; declared to live_verified also needs intraday (every probe inside an XSHG session). Agreement means each declared tolerance holds and at least 95% of rows match. Decision 0015 adds operator consent for `decision_eligible`.
- No document gives a number of sessions or a numeric threshold. tdx-shadow-snapshot.md:77: "no threshold: this is the measurement the thresholds will be chosen from". tdx-live-snapshot-fallback.md:152-153: "No binding's status or eligibility changes in this plan. The planner runs the in-session check on the next trading day and applies the promotion."
- Phase A (shadow): done and released. Phase B (fallback plan rev. 2, 3 steps), none coded:
  - Step 1: chain `read(fuyao, tdx)`: on `FuyaoProviderError` read TDX once. Snapshot metadata gains `provider_key`, `decision_eligible`, `fallback_for`. At most one TDX thread at a time.
  - Step 2: ten or so consumers follow the snapshot's own provenance. Examples: `quote_from_fuyao`, the `fuyao_ths_derived` flow label accepted by signal rules (intraday_signal_rules.py:81-108), `live_policy.py`, paper execution, limit-up anchor, health.
  - Step 3: agreement against Fuyao with an in-session tolerance, and `--decision-eligible` in the promote tool. While the binding is declared, a TDX-filled minute feeds only the minute document, radar and temperature; signals treat it as not eligible.
  - docs/archive/2026-10-12-level1-snapshot-readers.md concludes no stored-row reader needs changing.
- Decision 0015 item 2 makes TDX a minute-level filler for Fuyao failures. No document makes TDX the primary for any capability.
- B020 (docs/work/BACKLOG.md:198-206) says the in-session promotion check, the day summary and the phase-B switch are not done. next-plan.md §4/§9 lists Monday probes: cadence, ext delay, q-flow, disclosure timing, limit pools vs Eastmoney, `fund.iopv` intraday. scripts/data has no result files for any of them. Only `tdx_realtime_load_2026-10-11_owner.json` exists: 1,118 calls (286+494+338 across 30 s, 10 s and 5 s cycles), 0 failed, on a Sunday.
- First shadow session, 10-13 until 13:38 (docs/work/probes/2026-10-13-tdx-shadow*.out.txt; untracked, produced by probe scripts, not independently re-run):
  - TDX has 162-163 minute documents against Fuyao's 166-167, of 173 expected; TDX is missing 13, Fuyao 8.
  - The two reads are a median 25.8 s apart (max 61.3 s). Price within one tick: median 0.505. Within 0.5%: median 0.970, min 0.348.
  - With a gap under 5 s (n 15): median 0.58 and 0.986.
  - Largest relative price gaps are 9.5-10.8% at 09:30-09:54.
  - Last comparison: 5,560 symbols in common; at most 33 symbols appear only in TDX.
  - After the 10-12 close, nine fields agreed on 5,560 symbols (scripts/data/tdx_fuyao_all_a_snapshot_agreement_2026-10-12_owner.json).

4. UNMERGED BRANCHES
- 41 `codex/tdx-*` branches: 19 merged into main (incl. `codex/tdx-integration` and `-integration-2`, both fully merged), 22 not. `lsworktrees.sh` marks `tdx-handshake` and `tdx-other-clients` patch-equivalent. All 22 are exploration branches from 10-09/10-10. final-adoption.md says to take files by `git checkout <branch> -- <paths>` and never merge them whole.
- Counts are commits not in main; most were harvested already (their modules and reports are in main; branches kept):
  - Code, tests and report:
    - ext-market 5 (+670 lines): 7727 ext market.
    - f10-finance 2: F10 and finance units.
    - files-stats 1: file parsers.
    - fin-history 1: gpcw history.
    - instruments 2: instruments.
    - legacy-misc 4: 0x054b and 0x051d.
    - local-files 3: local gbbq/tdxhy files.
    - mac 2 and mac-fields 1: MAC protocol.
    - microstructure 1.
    - official-bridge 3: TdxQuant exporter.
    - zhb-extras 1.
  - Probes and reports only:
    - hostpool 1 (+2858 lines, host matrix), handshake 1, other-clients 1.
    - q-breadth 1, q-disclosure 8, q-extcodes 2, q-flow 1, q-limitfields 3, q-misc 1, q-stats 2, q-units 1.
  - The adopted code is in main: quant-service/app/datasources/sources/tdx_*.py and scripts/tdx-quant-export.py. The reports are in main as docs/archive/tdx-route-*.md and tdx-q-*.md (that archive listing has no tdx-q-misc.md; `git ls-tree` finds it).
  - `claude/tdx-p0-followup`: 1 commit (43057c1e), 10-09, a P0 review fix; not checked for supersession.
  - No branch holds the fallback (phase B).

5. RISKS AND CONSTRAINTS IN THE DOCUMENTS
- Handshake: the third setup packet (LOGIN_TWO, 招商证券 identity) puts the session in restricted mode (0 rows). LOGIN_ONE is the default, with per-host fallback to `legacy_3` and the profile recorded in provenance. "Server policy can flip again" (integration.md:95).
- Access:
  - Hosts are unofficial. tdx_hosts.py has 20 hosts ranked from the owner probe of 10-10; tdx_mac.py has 3 MAC hosts, and decision 0015 says the owner egress was used.
  - Rules: 1 connection per host, 5 s socket timeout, `FAILOVER_SECONDS` = 15 s (tdx_protocol.py:83), a started operation is never cut, small batches.
  - A lane-M mutation run once reached live hosts (~1,000 requests on 10-10 23:54; next-plan.md:116).
  - Never log in with broker credentials (tdx-access.md:101).
- Legal and access: tdx-access.md:103 and integration.md:93 say the terms are "research/learning only, 学习交流/non-commercial -> research evidence only"; both predate decision 0015 and its text has no licence review.
- Units and fields:
  - Legacy volume is in lots (x100 to shares); legacy daily volume is whole lots, and MAC daily volume is float32 shares.
  - Quote scale (0x053e) is wrong for ETFs (10x) and CBs (100x).
  - Money in F10 is 千元 and shares are 万股.
  - 880xxx board codes carry non-book numbers, and 880005 and 880001-880079 are statistics rows.
  - ST must come from the security list, not from 0x054b names.
  - The all-A snapshot coverage denominator is None.
  - TDX includes stocks Fuyao lacks (e.g. 600293.SH).
- Time: TDX `server_time_raw` is separate from collection time; there is no per-symbol exchange timestamp. The first TDX minute bar holds the opening auction, so minute checks must start after 09:31. Tipinfo dates cover only the latest report period.
- Listed UNKNOWN fields (final-adoption.md:56): MAC 0x5d/0x5e/0x16/0x1d/0x7a/0x6c-0x72/0x74-0x76, ext-market K-line and index volume units, delay minutes, most of the tipinfo columns, parts of tdxstat.
- The `tdx_public` rate slot (60/min) is documented as reserved for the shadow loop; the fallback plan wanted a per-minute limit for the same key.

6. CONTRADICTIONS AND UNKNOWNS
- Decision 0015 says the catalog declares the Fuyao comparison and the promote tool can set eligibility; neither is in main (see section 2).
- Older plans restrict TDX to research evidence; decision 0015 (2026-10-12) lifts it. Decision 0015 counts 33 declared bindings; the catalog has 35 declared plus 5 unsupported (the 2 minute-bar bindings and the 3 tdx_ext ones).
- Unsettled:
  - Why one or more shadow minutes show 0 common symbols and up to 5,444 only in Fuyao.
  - Whether the owner has the ten archive flags on.
  - Whether `apply` would take the 10-12 limit-pool evidence for declared to live_verified, since its `catalog_status` field reads unsupported.
  - The Monday probe results are not in the repo, so TDX rate behaviour under a full session is measured only through the shadow loop.
  - The shadow day summary (3e6ef727) has not been run in-session per B020.
