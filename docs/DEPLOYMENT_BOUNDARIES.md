# 部署边界与数据面归属

状态：当前生产约束（2026-09-21）。本文件是运行拓扑的优先说明；与旧的
“本地 edge 采集器”或“本地实时 writer”描述冲突时，以本文件和远端 `/health`
返回的运行身份为准。

## 一句话

远端 owner/peer 是唯一的实时数据面、provider 调用面、策略扫描面、租约调度面和
生产写入面；本地工作站只做分析、研究、回放、可视化，以及通过受控 API/SSH 隧道
拉取远端已经采集的证据。

本项目仍然是研究平台，不连接券商、不提交订单。实时策略输出和飞书提醒都是带
来源与时间戳的研究证据，除非存在明确的 promotion record，否则不得当作交易指令。

## 运行角色

| 角色 | 允许做的事 | 不应做的事 |
|---|---|---|
| 远端 owner/peer `quant-research` | 09:15 起竞价/盘中 provider 轮询、Longhu/Fuyao/Tushare 等远端数据采集、观察池扫描、实时证据写入 owner PostgreSQL、研究提醒 | 订单执行；绕过租约直接启动第二个 writer |
| 远端 `quant-research-scheduler` | 午盘/收盘、盘后策略、日终摘要、十日轮动等 research leases；读取 owner PostgreSQL | 获取实时采集 leases；与主容器重复轮询 |
| 本地工作站 | 通过 `15682` owner API 做只读分析、复盘、回放、报告和开发验证；按需读取远端证据 | 启动生产 provider 轮询、持有生产上游凭据、创建实时 writer lease、把本地数据库当生产事实源 |

远端主容器使用 `intraday_edge`，远端 scheduler 使用 `research`。这里的
`research` 是远端盘后调度 profile，不代表本地工作站重新成为实时采集器。

## 端口和通路

- 远端 `127.0.0.1:15682`：owner 主 quant API，实时服务和共享 API 的远端入口。
- 远端 `127.0.0.1:15683`：owner scheduler 的健康/研究 API 入口。
- peer 容器内 `db-tunnel:5432`：盘中会话；`db-tunnel:5433`：批量/研究会话。
- 本地 `127.0.0.1:15682`：可选的 SSH `-L` 转发，只是工作站访问远端 owner
  的入口，不是本地生产服务。关闭它不会影响远端采集；开启它后本地分析工具可
  访问远端 API。

本地 `.env` 可以配置 `QUANT_SHARED_READ_API_BASE_URL` 指向
`http://host.docker.internal:15682`，但本地后台任务仍必须保持关闭。上游 Longhu
等供应商凭据只放在远端 owner，工作站不直连供应商。

## 数据和发布原则

1. 实时数据的唯一事实源是远端 owner PostgreSQL 及其远端采集容器；本地数据库是
   分析/缓存/回放用途，不能反向覆盖 owner 的实时事实。
2. 代码提交、镜像构建和本地测试不等于生产发布。生产验收必须看远端 release、
   `/health`、租约、provider 状态、数据库 lineage 和错误回流。
3. 远端发布期间，按 `quant.owner_deploy_events` 处理 HTTP/隧道短暂重启；本地
   客户端应退避重试，不把一次 API 断开误判为数据源故障。
4. 本地做研究时保留 `available_at`、`observed_at` 和 `strategy_available_at`，
   不因拉取时间改变策略可用时点，不把事后补抓的数据伪装成实时数据。

## 旧文档的适用范围

涉及 `quant_intraday_edge`、本地 edge writer、L0 edge-hot 或本地实时采集器的段落，
仅用于历史切换、回滚或证据归档。除非文档明确写明“恢复演练”，不得按这些段落
重新启用本地实时服务。当前切换背景和远端 writer 说明见
[`EDGE_FUTURE_WRITER_CUTOVER.md`](EDGE_FUTURE_WRITER_CUTOVER.md)。
