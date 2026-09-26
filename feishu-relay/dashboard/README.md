# Feishu relay dashboard

This is the standalone frontend for Feishu relay operations. It contains the
LarkAgentX WebSocket monitor, webhook and keyword health, Itougu polling
status, Feishu workbench actions and manual media delivery page.

The quant research console remains in the repository root at `frontend/` and
has its own package, tests and build. Both applications are built separately
and the adapter serves them from the same origin: `/`, `/research` and
`/personal` select the quant console, while `/monitor`, `/workbench` and
`/relay` select this dashboard. API and SSE requests continue to use the
adapter's same-origin boundary.

```bash
npm ci
npm run typecheck
npm test -- --run
npm run build
```

The adapter image copies this build to `/app/frontend-dist`. The edge hotfix
script stages it as `frontend-dist/` and stages the quant build separately as
`quant-frontend-dist/`, so a frontend change does not require rebuilding the
Node/Python runtime when the hotfix path is used.
