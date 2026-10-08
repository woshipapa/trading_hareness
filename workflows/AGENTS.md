# Workflow ownership

This directory is shared by all three components, so ownership here is **by
filename**, exactly as declared in `../config/components.json`:

| File pattern | Component | Release unit |
|---|---|---|
| `quant-*.json` | `quant-research` | `owner-quant` |
| `edge-relay/**` | `feishu-relay` | `edge-workflows` |
| `xhs-intel-edge.json` | `xhs-intel` | `edge-xhs` |

- Run `python3 ../scripts/verify_component_boundaries.py workflows/<file>` if you
  are unsure which component a file belongs to. Do not add a workflow whose name
  matches none of the patterns above — `--check` will report it as unowned.
- A workflow is **not** container source code, so it has no source-overlay hot
  update. It is imported into n8n; `edge-workflows` is its own release unit and
  is deployed separately from `edge-relay`'s adapter code.
- A workflow must not reach across a component boundary. An edge workflow must
  not name the owner host, and a quant workflow must not call the relay's
  internal endpoints; cross-component data access goes through the declared
  `foreign_data_contracts` / HTTP APIs only.
- Never commit credentials, webhook secrets or cookies inside a workflow JSON.
  n8n credential references are fine; literal values are not.
