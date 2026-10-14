# 0025 The relay stack shares the owner host, as an independent release unit

- Status: accepted, 2026-10-15
- Records: docs/work/streams/W015-edge47-relay-to-owner-host.md, docs/work/plans/edge-relay-move.md
- Supersedes (in part): the host split in 0024 and in `docs/RELEASE_SYNC_47.md` section 1, where the relay stack ran on a
  separate host `47.114.113.152`

## Decision

feishu-relay (adapter, LarkAgentX bridge, n8n and its runners, the `n8n_relay` database), xhs-intel, itougu and the paper
services run on the owner host `116.62.30.225`, the same machine as the quant service. The old edge host
`47.114.113.152` keeps only what this repository does not own (stock-reports, sub2api, the proxy) and the retired quant
writer, which stays disabled.

The two are still **separate release units**. A shared machine is not a shared release:

| | quant service (`47owner`) | relay stack (`47edge`) |
|---|---|---|
| Docker | rootless daemon of user `stockpeer` | system-level daemon (`docker.io`), `network_mode: host` |
| Database | the owner PostgreSQL on the workstation, through the db-tunnel containers | PostgreSQL 16 on the host, `127.0.0.1` only |
| Directories | `~/trading_hareness`, `~/.local/share/trading-hareness` | `/opt/feishu-relay-edge`, `/opt/larkagentx`, `/opt/itougu-neican`, `/opt/paper-arxiv-edge` |
| Credentials | `~/.secrets/owner/.env.owner` | `/root/.secrets/edge/.env.edge`, `/etc/*.env` |
| Release tools | `scripts/shared-peer/deploy-code-only.sh`, `OWNER_PEER_HOST` (`stockpeer`) | `feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh` and the xhs and workflow scripts, `RELAY_EDGE_HOST` (`root`, key `relay_release_ed25519`) |
| Window rules | no restart 08:30-11:30, 13:00-15:10, 18:45-22:05 (Beijing) | the host operator's rule: no Docker restart, system upgrade or reboot 09:15-15:10 |

The relay stack cannot run in the quant service's rootless Docker: there `network_mode: host` is rootlesskit's namespace,
and a container cannot reach the host's `127.0.0.1` (measured with a throwaway container on 2026-10-14). That is why the
host has two Docker daemons.

## What changes in the repository

- `config/components.json` gives both host ids the same `address`; `47edge` records `shares_physical_host_with`.
- The relay-side scripts default to `root@116.62.30.225` and `~/.ssh/relay_release_ed25519`
  (`scripts/test_release_unit_isolation.py: RELAY_SIDE_SCRIPTS` lists them and keeps the copies in step).
- Isolation is checked by name, not by address: edge scripts must not name `OWNER_PEER_*`, `stockpeer` or the earlier
  owner hosts; owner scripts must not name `EDGE_HOST` or the old edge address.
- The workstation's tunnels and Paper-KB's pulls read the host from `LONGHU_SSH_*` in `config/secrets/.env.local`, the
  same settings as the owner tunnel, so the workstation also writes the address once.
- The four retired intraday-edge scripts keep the old edge address on purpose and say so in their header.
- `scripts/harden-remote-http-workflow.mjs` and the managed n8n workflows still name `47.114.113.152:18081`: that is
  stock-reports, which did not move.

## Consequences

- One machine failure takes the quant service and the relay stack down together. The 2026-10-14 outage of the earlier
  owner host (B058) would now have stopped the Feishu relay as well. Accepted by the user on 2026-10-14.
- The relay stack's containers carry memory and CPU limits (adapter 512m/1 core, xhs 1g/1 core, n8n 1g/1 core,
  runners 512m/0.5 core) so that they cannot starve the quant service at the open.
- `config/secrets/sync-secrets.sh edge` now writes to the unified host. Before using it, reconcile the derived
  `.env.edge` with the file on the host: they differed on 2026-10-14 and the cause is not known (B086).
- Rollback to the old edge is possible only while its stopped containers and disabled units are kept (B086).
