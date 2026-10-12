# Level-1 Snapshot Reader Audit

Date: 2026-10-12
Scope: every `FROM quant.raw_market_observations` site under `quant-service/app` plus the capability registries and request/configuration names called out by the level-1 plan.

## Classification rule

The class is assigned to the actual capability that reaches the query, not to a function signature in isolation.

- `document-first`: the minute document or the current process memory is preferred; per-symbol rows are only a fallback for data from before the document switch.
- `rows-only, current day`: the query needs today's per-symbol `a_share_prices_snapshot` rows and would become empty or stale when those rows stop.
- `rows-only, history`: the query intentionally reaches past sessions or a retention/replay window.
- `names the capability, reads nothing`: a registry, health label, contract, or pure in-memory computation names the capability but does not query its rows.
- `excluded: other capability`: the site reads `raw_market_observations`, but tracing its literal/constant/argument proves that it cannot read `a_share_prices_snapshot`.

The class counts below count SQL sites that can target `a_share_prices_snapshot`; the excluded sites are still listed so the 52-site inventory is auditable.

## Target-capability SQL sites

| Site | Capability reach and caller trace | Class | Path |
| --- | --- | --- | --- |
| `quant-service/app/async_market_result_read_repository.py:48` | Literal `a_share_prices_snapshot`; `latest_all_a_level1` is called by `routers/market_result_reads.py` for the async latest-result API. It computes the newest legacy row timestamp after checking process memory and the minute document. | `document-first` | Read API; not a scan, signal, or gate input |
| `quant-service/app/async_market_result_read_repository.py:62` | Same literal and same caller. It reads per-symbol rows only after the document is absent or older than the newest legacy capture. | `document-first` | Read API; not a decision path |
| `quant-service/app/async_market_result_read_repository.py:68` | Same literal and same caller; count accompanies the fallback rows. | `document-first` | Read API; not a decision path |
| `quant-service/app/market_result_read_model.py:104` | Literal `a_share_prices_snapshot`; sync `latest_all_a_level1` is the fallback implementation selected by `routers/market_result_reads.py`. Memory/document are checked first. | `document-first` | Read API; not a scan, signal, or gate input |
| `quant-service/app/market_result_read_model.py:113` | Same literal/caller; per-symbol fallback rows at the selected legacy timestamp. | `document-first` | Read API; not a decision path |
| `quant-service/app/market_result_read_model.py:118` | Same literal/caller; fallback row count. | `document-first` | Read API; not a decision path |
| `quant-service/app/strategy_cards_read_model.py:157` | Parameter is the module constant `SNAPSHOT_CAPABILITY = "a_share_prices_snapshot"`, passed by `session_quotes`. `strategy_cards` calls `session_quotes` after `_document_quotes`; the raw query runs only when no document exists for the requested day. | `document-first` | Signal/recommendation presentation (`/api/v1/strategies/cards`); the separate direction gate does not consume these rows |
| `quant-service/app/storage_tiering_mover.py:110` | `rule.capability` parameter. `StorageTieringMover.run` iterates `storage_tiering_policy.RULES`, whose `a_share_prices_snapshot` rule supplies this value. The query enumerates symbols for a tiering pass. | `rows-only, history` | Storage/retention, outside scan/signal/gate |
| `quant-service/app/storage_tiering_mover.py:112` | Same `rule.capability` and caller; recursive keyset continuation over the target stream. | `rows-only, history` | Storage/retention |
| `quant-service/app/storage_tiering_mover.py:117` | Same `rule.capability`; null-symbol probe is bounded by `effective_at < before` before a cold-tier move. | `rows-only, history` | Storage/retention |
| `quant-service/app/raw_overflow_archive.py:237` | `capability` comes from `capability_from_stream(stream, config)`. `a_share_prices_snapshot` is in `DEFAULT_CAPABILITIES` and the edge adapter's allowlist; the authenticated `/raw-overflow/next` caller supplies the stream key. `effective_at < now - hot_window_hours` excludes the live window. | `rows-only, history` | Archive hand-off, outside scan/signal/gate |
| `quant-service/app/raw_overflow_archive.py:310` | Same stream-derived parameter and cutoff in the ACK verification's first-row lookup. | `rows-only, history` | Archive hand-off |
| `quant-service/app/raw_overflow_archive.py:317` | Same stream-derived parameter and cutoff in the durable range count. | `rows-only, history` | Archive hand-off |
| `quant-service/app/raw_overflow_archive.py:343` | Same stream-derived parameter and cutoff in deletion after the remote ACK; this is a retention operation, not a read used by a decision. | `rows-only, history` | Archive hand-off |
| `quant-service/app/minute_cross_section_backfill.py:59` | `mcs.LEGACY_CAPABILITY`, defined as `a_share_prices_snapshot`; `convert_day`, `verify_day`, and `delete_day` pass an explicit historical `day`. | `rows-only, history` | Legacy backfill/maintenance |
| `quant-service/app/minute_cross_section_backfill.py:77` | Same constant, with an explicit historical minute selected by `convert_day`, `verify_day`, or `delete_day`. | `rows-only, history` | Legacy backfill/maintenance |
| `quant-service/app/minute_cross_section_backfill.py:137` | Same constant in the outer delete statement; only a requested historical day is eligible. | `rows-only, history` | Legacy deletion/maintenance |
| `quant-service/app/minute_cross_section_backfill.py:138` | Same constant in the delete statement's subquery; it is the second `FROM` site in that SQL statement. | `rows-only, history` | Legacy deletion/maintenance |

The two latest-result implementations deliberately compare the document timestamp with the newest legacy timestamp. After the switch, the legacy branch is reachable only on a database with no document at all; it is not a current-day reader that needs moving. The strategy-card fallback has the same document-first behavior for the requested day.

## The other 34 SQL sites

These sites are included to account for all 52 `FROM` occurrences. Their traced capability cannot be `a_share_prices_snapshot`.

| Site | Capability reach and caller trace | Class | Path |
| --- | --- | --- | --- |
| `quant-service/app/market_history_backfill.py:55` | `item.capability`; `walk_source` constructs `DayList` only from `SOURCES` (`limit_performance_history`, `limit_pool_history`, `morning_bidding_history`) and `market_breadth_history`. | `excluded: other capability` | Historical backfill |
| `quant-service/app/watch_review_patterns.py:166` | Literals `provider_key='quant_scan'` and `capability='watch_daily_review'`. | `excluded: other capability` | Post-close pattern research |
| `quant-service/app/xiaojie_message_features.py:186` | Parameter `capability` is the module's Xiaojie message capability at `cursor()` callers; no caller passes the level-1 capability. | `excluded: other capability` | Feature cursor |
| `quant-service/app/xiaojie_message_features.py:198` | Same `PROVIDER_KEY`/`CAPABILITY` parameters from `features`; this is the Xiaojie message ledger. | `excluded: other capability` | Feature read |
| `quant-service/app/limit_detail_read_model.py:48` | `capabilities` is called only with `REVIEW_CAPABILITY` or the Xuangubao pool prefix. | `excluded: other capability` | Post-close limit detail |
| `quant-service/app/datasources/storage.py:42` | Literal `market_sentiment_close` with provider `derived_market_sentiment`. | `excluded: other capability` | Previous-session context |
| `quant-service/app/longhu_schema_profile_repository.py:23` | Provider is literal `longhuvip`; the query intentionally profiles Longhu payload shape and does not filter the Fuyao snapshot capability. | `excluded: other capability` | Schema/readiness research |
| `quant-service/app/daily_valuation_repository.py:37` | Literals `provider_key='fuyao_ths'` and `capability='a_share_valuations_snapshot'`. | `excluded: other capability` | Valuation projection |
| `quant-service/app/storage_tiering_mover.py:280` | Literal/constant `RUN_CAPABILITY` (`storage_tiering_run`) for the mover report, not a source stream. | `excluded: other capability` | Storage status |
| `quant-service/app/watch_scan_tape.py:130` | `TAPE_PROVIDER`/`TAPE_CAPABILITY` are `quant_scan`/`watch_scan_tape`; the scan stores and later replays its own tape. | `excluded: other capability` | Scan replay, but not the level-1 stream |
| `quant-service/app/derived_daily_readings.py:28` | `capability` is passed by daily-temperature/flow/timing callers; all traced callers use local-derived daily capabilities, never the level-1 capability. | `excluded: other capability` | Daily derived history |
| `quant-service/app/minute_cross_section.py:188` | `PROVIDER_KEY`/`CAPABILITY` select the document capability `a_share_minute_cross_section` to make document writes idempotent. | `excluded: other capability` | Capture storage |
| `quant-service/app/minute_cross_section.py:202` | `_DOCUMENT_SQL` is parameterized with the document `CAPABILITY`, never `LEGACY_CAPABILITY`. | `excluded: other capability` | Document-first read helper |
| `quant-service/app/minute_cross_section.py:246` | `day_documents` also passes the document capability and `symbol IS NULL`. | `excluded: other capability` | Document replay |
| `quant-service/app/market_radar_runtime.py:56` | `CAPABILITY` is `market_radar` and provider `local_derived`. | `excluded: other capability` | Radar/gate source |
| `quant-service/app/market_radar_runtime.py:124` | Same `market_radar` capability; `latest_point` feeds the radar. | `excluded: other capability` | Gate source |
| `quant-service/app/intraday_outcome_settlement.py:44` | Literals select `quant_scan/watch_scan_tape`; `PricePaths` merges that tape with `intraday_quote_observations`. | `excluded: other capability` | Signal outcome settlement |
| `quant-service/app/longhu_replay_read_repository.py:22` | Provider literal `longhuvip`; the query is Longhu replay-readiness evidence with no capability filter for Fuyao rows. | `excluded: other capability` | Replay readiness |
| `quant-service/app/public_market_repository.py:151` | Generic `provider, capability`, but all callers of `latest_observation_payloads` pass TDX capabilities (`tdx_security_list`, `tdx_gpcw_manifest`, `tdx_index_daily_bars`). | `excluded: other capability` | Post-close collector |
| `quant-service/app/public_market_repository.py:163` | Generic `provider, capability`, but all callers of `observation_payloads` pass `tdx_tipinfo`. | `excluded: other capability` | Post-close collector |
| `quant-service/app/market_snapshot_actions.py:184` | Literal `o.capability='realtime_quote'`; this is the bounded public-quote fallback, not the Fuyao snapshot rows. | `excluded: other capability` | Snapshot freshness gate |
| `quant-service/app/teacher_review_repository.py:58` | `PACK_CAPABILITY` (`teacher_review_pack`). | `excluded: other capability` | Teacher review |
| `quant-service/app/teacher_review_repository.py:82` | Same `PACK_CAPABILITY`, passed by `recent_packs`. | `excluded: other capability` | Teacher review |
| `quant-service/app/teacher_review_repository.py:305` | `SETTLEMENT_CAPABILITY` (`teacher_review_settlement`). | `excluded: other capability` | Teacher settlement |
| `quant-service/app/teacher_review_repository.py:326` | `OUTCOME_CAPABILITY` (`teacher_review_outcome`). | `excluded: other capability` | Teacher outcome review |
| `quant-service/app/strategy_change_log.py:105` | `CAPABILITY` is the strategy-change archive capability. | `excluded: other capability` | Change log |
| `quant-service/app/indicator_health.py:95` | Literal `market_radar`/`local_derived`. | `excluded: other capability` | Indicator health |
| `quant-service/app/indicator_health.py:138` | Parameter `MOOD_CAPABILITY` is Longhu `MoodNumCount`; caller `longhu_mood_checks` supplies that constant. | `excluded: other capability` | Indicator health |
| `quant-service/app/indicator_health.py:177` | Literal document capability `a_share_minute_cross_section` with provider `fuyao_ths`; it checks documents, not legacy rows. | `excluded: other capability` | Indicator health |
| `quant-service/app/strategy_review_service.py:85` | Provider literal `longhuvip`, with payload predicates for next-session Longhu evidence; no Fuyao snapshot capability. | `excluded: other capability` | Strategy review context |
| `quant-service/app/watch_daily_review.py:549` | Literals `quant_scan/watch_scan_tape`; `read_scan_tape` is the post-close tape reader. | `excluded: other capability` | Post-close review |
| `quant-service/app/watch_daily_review.py:584` | `REVIEW_PROVIDER`/`REVIEW_CAPABILITY` are `quant_scan/watch_daily_review`. | `excluded: other capability` | Post-close review |
| `quant-service/app/minute_cross_section_backfill.py:68` | `mcs.CAPABILITY` is the document capability, used to compare already converted minutes. | `excluded: other capability` | Legacy backfill verification |
| `quant-service/app/strategy_daily_candidate_ledger.py:229` | Literals `teacher_review/teacher_review_pack`; teacher plans become candidate evidence here. | `excluded: other capability` | Candidate materialization |

## Capability names and registries

These are metadata, health, or in-memory paths. They name the capability but do not query its rows.

| Site | How the name reaches the path | Class | Path |
| --- | --- | --- | --- |
| `quant-service/app/platform/evidence_contracts.py:45-48` | Contract key `fuyao_all_a_snapshot` declares provider `fuyao_ths` and capability `a_share_prices_snapshot`; `materialize_evidence_status` only attaches metadata. | `names the capability, reads nothing` | Scan source status |
| `quant-service/app/platform/evidence_contracts.py:69-72` | Contract key `fuyao_ths_derived_watch_flow` declares the same capability; status materialization is metadata only. | `names the capability, reads nothing` | Scan source status |
| `quant-service/app/platform/runtime_task_registry.py:26-29` | `intraday_monitor.provider_capabilities` includes `a_share_prices_snapshot`; registry serialization returns metadata. | `names the capability, reads nothing` | Scan task contract |
| `quant-service/app/platform/runtime_task_registry.py:87-90` | `all_a_level1_snapshot.provider_capabilities` is `(a_share_prices_snapshot,)`; the registry does not read rows. | `names the capability, reads nothing` | Snapshot capture contract |
| `quant-service/app/health_read_model.py:56-57,227` | Health receives an aggregate `provider_status` callback and publishes `market_providers`; this module has no capability literal and no raw-row query. | `names the capability, reads nothing` | Health/status only |
| `quant-service/app/storage_tiering_policy.py:47-50` | The `TierRule` literal marks the target stream as realtime and keeps it hot; `tiering_status` serializes the rule. | `names the capability, reads nothing` | Storage policy |
| `quant-service/app/raw_overflow_archive.py:27-34,62-77` | `DEFAULT_CAPABILITIES` and `RawOverflowConfig` allowlist the target; row reads are the four parameterized sites listed above, while configuration itself reads nothing. | `names the capability, reads nothing` | Archive configuration |
| `quant-service/app/indicator_registry.py:43-45` | `market.radar` dependency text names the Fuyao snapshot as an upstream of local-derived radar. | `names the capability, reads nothing` | Indicator metadata/gate description |
| `quant-service/app/indicator_registry.py:68-70` | `market.minute_documents` lists the snapshot as its upstream and the document capability as its stored output. | `names the capability, reads nothing` | Indicator metadata |
| `quant-service/app/level1_snapshot_runtime.py:156-159` | Provider-health success/failure recording receives the literal target capability; it writes health state and does not read raw rows. | `names the capability, reads nothing` | Capture health, adjacent to scan |
| `quant-service/app/market_snapshot_actions.py:151,158` | Fuyao public-quote success/failure mirrors health under the target capability; these are health writes, not raw-row reads. | `names the capability, reads nothing` | Snapshot health |
| `quant-service/app/market_snapshot_actions.py:287-289` | `provider_capabilities("fuyao_ths", ["a_share_prices_snapshot"])` is a circuit/freshness gate over provider-health state, not `raw_market_observations`. | `names the capability, reads nothing` | Provider gate |
| `quant-service/app/intraday_derived_flow_metrics.py:19-24,94-105` | The module documents and computes derived fields from the `quotes`/`reference` objects supplied by the scan; it performs no database read and receives the snapshot in memory. | `names the capability, reads nothing` | Scan input/derived flow |
| `frontend/` | No `a_share_prices_snapshot`, `fuyao_all_a_snapshot`, or `fuyao_ths_derived_watch_flow` occurrence; the console consumes API results without naming the capability. | `names the capability, reads nothing` | No frontend capability request |
| `feishu-relay/adapter/baidu-pan-market-archive.mjs:8`, `feishu-relay/adapter/index.mjs:225`, `feishu-relay/deploy/edge/relay.env.example:86`, `feishu-relay/deploy/edge/docker-compose.yml:168` | The adapter's raw-overflow allowlist/default names the target stream. It is an archive request configuration; it does not query the quant database itself. | `names the capability, reads nothing` | Edge archive hand-off |

The adjacent provider/source registries were also checked: `quant-service/app/datasources/catalog.py:29,312` maps `quote.all_a_snapshot` to the capability, and `quant-service/app/fuyao_catalog.py:27,113`/`fuyao_provider.py:214,218` define the upstream route and request. They are source metadata/provider I/O, not readers of stored raw rows.

## Path conclusion

- **Scan:** `intraday_watch_quote_capture` fetches the complete Fuyao cross-section and passes `all_a_rows` to `intraday_scan_preparation`; `intraday_derived_flow_metrics` derives flow fields from those in-memory objects. No scan query reads per-symbol `a_share_prices_snapshot` rows.
- **Signal:** the only target-capability SQL site on the strategy/signal surface is the document-first fallback in `strategy_cards_read_model.py:157`. Intraday signal construction receives the same provider result in memory. Settlement reads `watch_scan_tape`, not the target capability.
- **Gate:** the direction gate is built from `market_radar` and `market.main_net`; the target capability appears only in provider-health/circuit metadata. No gate reads target rows.

## Required summary

Counting target-capability SQL sites (18 total):

| Class | Count |
| --- | ---: |
| `document-first` | 7 |
| `rows-only, current day` | 0 |
| `rows-only, history` | 11 |
| `names the capability, reads nothing` | 0 SQL sites (16 metadata/configuration entries are listed above) |
| Excluded sites using another capability | 34 |

Every `rows-only, current day` reader: **none**. There is therefore no current-day reader on a scan, signal, or gate path to stop for planner review.

Files Step 2 would have to change: **none**. The target-capability readers that remain are already document-first or intentionally historical/retention-only.
