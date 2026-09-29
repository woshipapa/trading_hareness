# 组件拆分与实时优化执行状态

当前仓库仍保持 monorepo，但已经把 `quant-research`、`feishu-relay`、`xhs-intel` 和 `integration` 的路径、运行位置、发布单元、测试入口写入 `config/components.json`。`scripts/verify_component_boundaries.py` 检查跨组件导入，`scripts/export_component.py` 可以生成不含 secrets、构建目录和依赖缓存的独立归档；归档不是生产发布。

当前分支为 `feat/component-split-20260929`，拆分提交为 `3629529`，并已合并包含 owner
迁移 lineage 的 `origin/main@bdbebee`（合并提交 `6aa26d7`）。工作树已清洁；这只代表
代码具备可审阅的版本身份，不代表已经切换 47edge 或 47owner 的运行服务。

三个运行项目现在各自有独立构建上下文、compose 入口和 runtime contract：Quant 使用
`quant-service/compose.standalone.yaml`，Feishu 使用
`feishu-relay/compose.standalone.yaml`（WebSocket bridge 为 `listener` profile），XHS
使用 `xhs-intel/compose.standalone.yaml`。三套镜像均已在本地真实构建并完成健康端点烟雾验证；
XHS 的 `Spider_XHS`、Feishu 的 LarkAgentX 和账号凭证仍是显式外部运行契约。

每个项目现在还带有自己的 `component.json` 和 `Makefile`，导出归档的元数据不再写入
本机绝对路径；Feishu adapter 已加入 `package-lock.json` 并使用 `npm ci` 构建。跨项目
入口集中记录在 [`COMPONENT_CONTRACTS.md`](COMPONENT_CONTRACTS.md)，因此后续物理拆仓时
可以把本地 manifest、compose、测试和契约一起迁移，而不依赖根目录 Python/Node 代码。

已落地的运行改进包括：

- 47edge Feishu WebSocket 群级历史导出契约保留 1/7/30 天范围；Quant、Feishu、XHS 前端和边缘服务各自有测试与构建入口。
- Fuyao、东财、盘口、扫描、Longhu、Level-1 和市场事件的健康证据按实际来源/能力记录；缺少上游时间戳会是 `unknown`，不会伪装成 `fresh`。
- 全 A 快照消费者复用进程内单飞缓存；Longhu 实时请求与公共源执行器分离；公共源加入非阻塞速率槽，超限走可观测 fallback。
- 策略注册表增加 `alert_effect`，补齐因子、板块流向和龙头研究契约；概率画像使用 `next_close` T+1 结果，三重屏障成本生效，纸面未成交按原因汇总。
- 技术分析、缺失日线、未来扰动、T+1 标签和数据源能力契约有回归测试；变更评估支持 `inconclusive`，衰减状态机只生成人工复核提案。
- `provider_canary.py` 已提供区分空响应、拒绝、超时、不可达以及报价结构/时间戳校验的纯函数和测试；按交易日租约执行及 owner 端点证据仍待接入。

本轮没有把未合并工作树发布到生产。只读状态检查显示 edge 仍是 hotfix overlay、owner 与 `origin/main` SHA 漂移；按 47 发布规范，必须先将变更合并到 `origin/main`，再在交易时段外按 `RELEASE_SYNC_47.md` 迁移/切换/回读。远端服务因此不能被描述为已运行本轮改动。

仍需单独批准或远端证据的高风险工作：D5 时点版本化迁移、完整 `CapabilityResolver` 热路径切换、按交易日执行的 provider canary、strategy lifecycle append-only 表及 API、27 个交易时段字面量迁移、规则全命中/I2 插件化、因子滚动 IC，以及外部视频 harness 的 K1-K3。这些路径不能通过本地单元测试直接宣称生产生效。
