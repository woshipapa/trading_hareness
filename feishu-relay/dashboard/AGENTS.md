# Feishu dashboard instructions

This directory is the independently deployable Vue dashboard for the Feishu
relay. Keep Feishu API clients, relay status views and manual delivery state in
this project. The quant research console belongs to `/frontend` and must not be
imported here.

The dashboard uses same-origin adapter endpoints. Do not put app secrets,
OAuth tokens, cookies or webhook URLs in source, browser storage or build
artifacts. Run the following checks after a change:

```bash
npm run typecheck
npm test -- --run
npm run build
```

The adapter packages this build as `/app/frontend-dist`; the separately built
quant console is packaged as `/app/quant-frontend-dist`.
