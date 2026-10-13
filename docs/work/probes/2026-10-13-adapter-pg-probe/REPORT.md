# PostgreSQL probe of the sibling modules (observed by running, PG 16, TZ=Asia/Shanghai)

Adapter: /Users/papa/codebase/.codex-wt/n8n-relay-auto-int/feishu-relay/adapter (read-only; nothing changed there).
Method: per module one script in this directory; the real `ledger.mjs` (createLedger + init, i.e. the service's own schema,
source-profile and webhook-target methods) on a fresh database each, real node-pg; the modules' own `schema` statements run;
bridge, clock, fetch and LarkAgentX `bots` are stand-ins. Chains use the real neighbours (webhook-targets, target-registry).
Run: `TZ=Asia/Shanghai node <name>_probe.mjs`. Databases probe_src, probe_wh, probe_reg, probe_dc, probe_ua, probe_tg.

## Result: no statement failed. 0 failures in 61 steps.

| module (script) | steps | failed | call sites not reached |
|---|---|---|---|
| source-tags (source_tags_probe) | 9 | 0 | 0 (no SQL of its own; ledger methods below reached) |
| webhook-targets (webhook_targets_probe) | 12 | 0 | 0 (no SQL of its own; ledger methods below reached) |
| target-registry (target_registry_probe) | 11 | 0 | 0 |
| delivery-check (delivery_check_probe) | 12 | 0 | 0 |
| urgent-alerts (urgent_alerts_probe) | 9 | 0 | 0 |
| target-groups + target-members + join-link (target_groups_probe) | 8 | 0 | 0 (these three send no SQL: no `ledger`/`pool` use, confirmed by grep; target-groups reaches SQL only through registry/webhook-targets, which ran) |

## SQL call sites

### delivery-check.mjs
| line | what | reached by step | result |
|---|---|---|---|
| 14-26 (schema) | CREATE TABLE deliveries | runSchema | ok |
| 32 | INSERT delivery ($4 Date -> timestamptz) | record delivery m1/m2 | ok |
| 40 | SELECT replay by target_message_id | observed (match, replay, none) | ok |
| 46 | SELECT candidates, sent_at >= $2 AND <= $3 (Date params) | observed | ok |
| 50 | UPDATE target_message_id, observed_at | observed: match m1, m2 | ok |
| 57 | SELECT rows to recall | recallDeliveries m1, m2, repeat (0 rows) | ok |
| 65 | UPDATE recalled_at ($2 Date) | recallDeliveries m1 | ok |
| 67 | UPDATE recall_error | recallDeliveries m2 with bridge failing | ok |
| 105 | counts with count(*) FILTER ... ::int, sent_at >= $2 | GET deliveries | ok |
| 111 | SELECT recent rows | GET deliveries | ok |

### target-registry.mjs
| line | what | reached by | result |
|---|---|---|---|
| 6 (schema) | CREATE TABLE | runSchema | ok |
| 9 | SELECT all rows | list / get / GET route | ok |
| 33 | INSERT ... ON CONFLICT DO UPDATE (7 params; resolved_at Date or ISO string) | start->resolve, record (string resolved_at, absent resolved_at), POST resolve | ok |
| 46 | DELETE | forget | ok |
| 97 | SELECT count(*)::int | start (empty, non-empty, and with the table renamed away: error is caught and logged, as designed) | ok |

### urgent-alerts.mjs
| line | what | reached by | result |
|---|---|---|---|
| 22 (schema) | CREATE TABLE (value jsonb) | runSchema | ok |
| 28 | SELECT value by key | GET settings (no row, then stored), notify, test | ok |
| 39 | INSERT ... ON CONFLICT DO UPDATE, $2 is a JS object -> jsonb | PUT settings x4 | ok (stored as jsonb object) |

### Ledger methods used by the other modules (ledger.mjs)
| line | what | reached by | result |
|---|---|---|---|
| 80, 81, 82 (saveSourceProfile) | INSERT topics / publishers / source_profiles ON CONFLICT; $7 is the route object -> jsonb | ledger.init with a file route; source-tags register, setEnabled | ok |
| 188 sourceProfiles | SELECT profiles | source-tags load (twice, incl. after "restart") | ok |
| 194 webhookTargets | SELECT webhook rows | webhook-targets load (empty, 2 rows) | ok |
| 199 saveWebhookTarget | INSERT ... ON CONFLICT ... RETURNING; verifiedAt Date | add, add again (upsert), addManaged | ok |
| 205 deleteWebhookTarget | DELETE | remove typed, remove managed | ok |

Reached only without SQL (not a gap): webhook-targets "unreadable row" branch (a row whose ciphertext cannot be decrypted) was not
executed; it runs no SQL.

## date / timestamptz handling (observed values)
No place assumes text. All timestamptz columns come back as `Date`:
- webhook-targets: `verified_at`, `created_at` came back as `Date` (instanceof true, e.g. 2026-10-13T02:30:00.000Z); `asIso()` (line ~72)
  handles `Date`, so list() shows ISO strings. Not a bug.
- target-registry: `resolved_at` comes back as `Date`; module never compares or returns it (list() does not include it).
- delivery-check `deliveries()` (line 111) returns `sent_at`, `observed_at`, `recalled_at` as `Date` objects; JSON serialisation turns them
  into ISO strings, so only a consumer inside the process that expects text would notice.
- Observation, not a failure: `delivery_id` is `bigserial`, node-pg returns it as the string "1" (observed typeof string), and the
  route passes it through to JSON as a string. `observed()` passes it back as a parameter to `WHERE delivery_id=$1`, which works.
  If the frontend expects a number in `deliveries[].delivery_id` it gets a string (frontend not checked).
- No `date` (calendar date) columns exist in these modules.

## Failures
None. (No suggested changes.)

## Not checked
- Concurrency, locking and performance of the statements; only one connection pair, serial steps.
- Frontend consumers of the returned shapes (bigint string, Date -> ISO).
- Other parts of ledger.mjs, memberships.mjs (excluded by the task).
- Real bridge, webhook and bot behaviour (stand-ins).
