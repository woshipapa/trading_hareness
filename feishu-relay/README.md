# Feishu Relay

`feishu-relay/` 是飞书接收、解析、去重、媒体处理和发送链路的独立维护边界。主 n8n 项目只保留量化服务、工作流和 dashboard shell；adapter 通过 HTTP webhook/API 接入这些能力。

```text
feishu-relay/
├── adapter/                 Node.js adapter、relay ledger、媒体和工作台 API
├── bridge/                  LarkAgentX WebSocket、protobuf 解码、事件 spool
├── config/                  Feishu 来源标签和 analyst 路由注册表
├── dashboard/               独立 Feishu Vue dashboard（监听、工作台、手动投递）
├── deploy/
│   ├── edge/                47edge compose、Dockerfile、systemd 和 hotfix 配置
│   ├── itougu-neican/       爱投顾轮询服务配置
│   └── itougu-perf-report/  爱投顾回测报告服务配置
├── docs/                    上线、媒体和 LarkAgentX 说明
└── scripts/
    ├── edge/                47edge 发布、回滚、交接和 workflow 对账
    ├── media/               百度网盘和媒体归档工具
    ├── sources/itougu/      爱投顾来源采集与投递
    ├── desktop/             本地人工投递入口
    └── tools/               Feishu 专项检查工具
```

运行边界保持清晰：47edge 上由 `bridge` 通过 LarkAgentX 私有 WebSocket 接收消息，`adapter` 负责消息规范化、幂等 ledger、webhook fan-out 和 n8n ingestion；官方 OAuth 只保留在显式补读或管理操作中。凭证、Cookie、webhook URL 和运行时状态仍放在受限环境，不进入这个目录。

每个 WebSocket 群都可以按群导出最近 1、7 或 30 天的 JSONL。导出内容只代表边缘监听器实际收到的消息，不声称补齐断线期间的群历史；消息 ID、来源标签和观测时间会保留，Cookie、token 和 webhook 不会进入分析输入。

## 常用命令

```bash
npm install --prefix feishu-relay/adapter --no-audit --no-fund
node --test feishu-relay/adapter/*.test.mjs
PYTHONPATH=feishu-relay/bridge python3 -m unittest discover -s feishu-relay/bridge -p 'test_*.py' -q
cd feishu-relay/dashboard && npm run typecheck && npm test -- --run && npm run build

# 只运行 adapter/API（构建上下文是本目录，不依赖量化项目）
export RELAY_PGPASSWORD='choose-a-local-password'
export FEISHU_APP_ID='local-placeholder'
export FEISHU_APP_SECRET='local-placeholder'
docker compose -f feishu-relay/compose.standalone.yaml up --build
curl http://127.0.0.1:18300/health

# 启动可选 WebSocket listener（需要已固定的 LarkAgentX checkout 和账号目录）
export LARKX_SOURCE_ROOT=/srv/larkagentx-audit
export LARKX_PROFILE_HOME=/var/lib/larkx-profile
export LARKX_LISTEN_CHAT_IDS='数字群ID'
export LARKX_SEND_CHAT_IDS='数字群ID'
docker compose -f feishu-relay/compose.standalone.yaml --profile listener up --build

# 47edge 源码覆盖层发布，不构建或拉取镜像
bash feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --apply

# 查看或回滚已保留的覆盖层
bash feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --list
bash feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --rollback <release-id> --apply
```

主项目的 `compose.yaml` 和 `deploy/compose.server.yaml` 使用这里的 adapter Dockerfile；`frontend/` 量化研究台和 `dashboard/` Feishu 工作台分别构建后一起注入 adapter。修改 Feishu 前端时以 `feishu-relay/dashboard/` 为代码归属，修改量化研究台时仍在主项目 `frontend/` 维护。

详细运行说明见 [`docs/SETUP.md`](docs/SETUP.md)、[`deploy/edge/README.md`](deploy/edge/README.md) 和 [`bridge/README.md`](bridge/README.md)。生产发布单元是 47edge 上的 `edge-relay`；source-overlay 和独立 `edge-workflows` 按 47 发布契约推进，已退役的 quant edge writer 必须保持禁用。

`adapter/Dockerfile.standalone` 是不含 dashboard 构建产物的 API 运行镜像；dashboard
仍在 `dashboard/` 独立安装、测试和构建。需要 WebSocket 监听时，再按
`bridge/README.md` 注入外部 LarkAgentX 安装和受限凭证，adapter 与 bridge 通过 HTTP
入口解耦。
