# Quant Research Service

`quant-service/` owns the research and evidence API that runs on 47owner. It
reads the remote provider and database contracts, materializes replay and
review evidence, and serves the quant research dashboard through the root
`frontend/` shell.

This component is research-only: analyst scores, regressions, pattern mining
and recommendations remain `live_effect=none` until an explicit promotion
record exists. It does not place orders and it must fail closed when bars,
provider freshness, sector mappings or point-in-time samples are incomplete.

The component owns `app/`, migrations and research tests. Root compose,
release manifests and cross-component workflows stay in the integration layer;
the Feishu adapter is consumed through its HTTP/event contract rather than by
importing Feishu implementation modules.

## Standalone API runtime

The API can be built from this directory without the repository root:

```bash
export POSTGRES_PASSWORD='choose-a-local-password'
docker compose -f compose.standalone.yaml up --build
curl http://127.0.0.1:5681/health
```

`compose.standalone.yaml` owns a local PostgreSQL volume and starts the
versioned schema bootstrap before the API. It uses an explicit, write-free
`public.ingestion_jobs` compatibility table because the integrated schema
keeps the analyst signal foreign key owned by the Feishu ingestion contract.
The standalone profile disables background collectors and remains research
only; provider credentials are optional until a research route needs them.
The standalone image installs `requirements.lock`; update that lock deliberately
when the API dependency set changes.

## Local verification

```bash
make -C quant-service test-release-path   # 工作区按发布路径跑；
# docker compose exec -T quant-research python -m unittest discover -s tests -q  # 只验镜像里的代码
cd ../frontend && npm run typecheck && npm run build
```

Production code and database changes follow the owner release rules in
[`../docs/UNIFIED_RELEASE_MODEL_47.md`](../docs/UNIFIED_RELEASE_MODEL_47.md):
migrations are applied and read back before switching the owner service, and
the running owner source SHA is checked independently from the edge relay.
