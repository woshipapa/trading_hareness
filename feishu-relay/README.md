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

## 常用命令

```bash
node --test feishu-relay/adapter/*.test.mjs
PYTHONPATH=feishu-relay/bridge python3 -m unittest discover -s feishu-relay/bridge -p 'test_*.py' -q

# 47edge 源码覆盖层发布，不构建或拉取镜像
bash feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --apply

# 查看或回滚已保留的覆盖层
bash feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --list
bash feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --rollback <release-id> --apply
```

主项目的 `compose.yaml` 和 `deploy/compose.server.yaml` 使用这里的 adapter Dockerfile；`frontend/` 量化研究台和 `dashboard/` Feishu 工作台分别构建后一起注入 adapter。修改 Feishu 前端时以 `feishu-relay/dashboard/` 为代码归属，修改量化研究台时仍在主项目 `frontend/` 维护。

详细运行说明见 [`docs/SETUP.md`](docs/SETUP.md)、[`deploy/edge/README.md`](deploy/edge/README.md) 和 [`bridge/README.md`](bridge/README.md)。
