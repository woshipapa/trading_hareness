# Intraday edge deployment

> **Current boundary (2026-09-21):** this host is not the live market-data
> writer. The remote owner/peer host owns provider polling, real-time strategy
> scans, Feishu alerts and production writes. The workstation/edge side only
> keeps relay or analysis functions and may read the owner API through the
> `15682` SSH tunnel. The historical recovery units below must not be enabled
> as a second live collector.

This role is the single writer for live polling and Feishu research alerts.
It runs the `intraday_edge` background profile on a loopback-only FastAPI
process and keeps the local workstation in the complementary `research`
profile. It never submits orders.

The historical edge collector and its `quant_intraday_edge` database were
archived and retired after the owner cutover. The edge host now keeps only the
Feishu relay; the active quant API is the owner endpoint reached through the
15682 SSH tunnel. The old systemd units and evidence puller remain documented
for recovery only and must not be enabled as a live writer.

The historical evidence pull path is retained only for recovery and is no
longer scheduled. `scripts/pull-intraday-edge-evidence.sh` must not be loaded by
LaunchAgent because the retired edge database is intentionally absent. Current
research reads come from the owner API path.

The puller records its latest local attempt separately from the evidence
cursor. A failed pull therefore appears as a visible warning with its last
error and last success time; it does not change the edge collector's ownership
or make a stale snapshot look healthy.
`edge_export_grants.sql` grants that account SELECT on the same explicit table
set only; it does not receive default access to future schema additions.

## Market-session acceptance

Run `scripts/verify-intraday-edge-live-session.sh` during an SSE continuous
auction session (09:30–11:30 or 13:00–15:00, Asia/Shanghai). It is read-only
and checks the remote edge's release identity, `intraday_edge` runtime profile,
and every currently expected market-data loop's fresh observation, age budget,
and error state. It exits with code `3` outside that session rather than
mistaking an intentional standby state for a passing live acceptance. Use
`--allow-standby` for a control-plane-only off-session check.

The edge additionally writes a secret-free acceptance receipt at 09:35 and
13:05 Asia/Shanghai on weekdays. It records `passed`, `failed`, or `standby`
in its retained data directory and includes it in the next evidence handoff,
so an off workstation does not lose the following session's verification.

Required secret environment values are installed directly into
`/etc/quant-intraday-edge.env` with mode `0640`; they are not stored here.
The checked edge configuration uses a 10 GiB disk warning watermark and an
8 GiB capture-protection floor. A warning is visible in the local dashboard;
below the floor the runtime reports degraded rather than hiding the condition.

## Release identity

The service runs one committed source release through the `current` symlink.
`scripts/deploy-intraday-edge-release.sh` retains each revision under
`/opt/quant-intraday-edge/releases/<git-sha>` and writes only non-secret build
provenance to `/etc/quant-intraday-edge.release.env`. `/health.build` then
reports the deployed Git SHA, release label, and build timestamp. See
[`RELEASE.md`](RELEASE.md) for the fenced deployment command.
