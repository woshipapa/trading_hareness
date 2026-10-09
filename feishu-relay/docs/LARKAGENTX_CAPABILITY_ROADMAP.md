# LarkAgentX 能力拓展路线图（2026-10-09）

给执行代理的前瞻计划。历史执行记录见 `LARKAGENTX_FOLLOWUP_OPTIMIZATION.md`，
本文只写"接下来做什么、怎么验收、怎么回滚"。目标系统是用户自有的市场研究
平台；LarkAgentX 用用户本人的飞书登录态驱动个人会话（内部 WS/网关，非官方
API），官方 OAuth 应用只做精确补读与兜底。

## 0. 执行约束（硬规则，每个阶段都适用）

1. **组件边界**：只改 `feishu-relay/**`；不 import `app`（quant）或 `xhs-intel`；
   跨组件只走 HTTP（如共享 `model-service :8791`）。改完跑
   `python3 scripts/verify_component_boundaries.py <paths>`。
2. **上游 pinned 不可编辑**：`/opt/larkagentx/source`（larkx）是外部 pinned checkout。
   需要改行为一律在 `bridge/bridge.py` 的 `RecoveringLarkClient` 子类里覆盖，或用
   env 开关（先例：`LARKX_WS_PARAM_OVERRIDES`、`LARKX_GATEWAY_HEADER_OVERRIDES`、
   `LARKX_WS_NOTIFY_CMDS`）。
3. **发布只走 hotfix**：`feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --apply`
   （发 overlay、重建 adapter 容器）；bridge 改动还需
   `systemctl restart larkagentx-group-relay`，然后用 PID1 argv 确认跑的是
   `hotfix/current/bridge/bridge.py`，并读 `http://127.0.0.1:8090/health` 的
   `release`。不 rebuild 镜像。
4. **bridge 测试必须在 edge 跑**：本机缺 larkx，`feishu-relay/bridge/test_*.py`
   会整体 skip。把 `bridge/` rsync 到 edge 临时目录，用
   `/opt/supervisor/.venv/bin/python -m unittest discover -s . -p 'test_*.py'`
   真跑（当前基线 92 全绿），跑完删临时目录。adapter 用 `node --test
   feishu-relay/adapter/*.test.mjs`（基线 190），dashboard 用
   `npm run typecheck && npm test -- --run && npm run build`。
5. **新行为默认关、env 打开、回滚=删一行 env**。先用 throwaway 实例（独立
   `LARKX_HOME`、独立端口、`timeout` 自毁）验证，再碰生产服务。
6. **密钥纪律**：cookie/token/key_hex/iv_hex 永不进日志、payload、健康端点、
   提交；读远端 env 只 `grep '^KEY=' | cut -d= -f2-` 不回显。
7. **每阶段一个提交**（只提交自己的文件，仓库多代理并行），附测试与验收证据；
   手动审批模式下逐步放行。
8. **保号优先**：不做规避风控的伪装（随机延迟、设备指纹欺骗等）——那是最快
   封号的路。靠保守限速、幂等、官方通道兜底换取长期可用。

## 1. 登录与会话闭环（扫码登录做成浏览器可用）

**现状**：bridge 已有 `POST /qr/init|/qr/poll`（token 守卫）、认证状态机
（`authed/needs_login`，凭证死不崩溃、扫码后原地重连）、health 的 `auth` 块。
缺浏览器面向的页面与 token 注入。

**改动**
- adapter `index.mjs`：代理 `GET/POST /api/group-relay/larkagentx/qr/init`、
  `/qr/poll` → bridge `/qr/*`，服务端注入 `LARKX_BRIDGE_TOKEN`（先例：
  `handleLarkAgentXHistoryExport`）。`GET /api/group-relay/larkagentx/auth` 返回
  bridge health 的 `auth` 块（确认 `larkAgentXDashboardStatus` 是白名单映射还是
  透传；若白名单，把 `auth`、`ws_notify_*`、`private_tail_repair_chat_ids` 补进去）。
- dashboard：群监听页新增「账号与会话」卡片——状态标签（已登录/需要扫码）、
  user_id、凭证年龄、上次 QR 登录时间；「扫码登录」按钮弹出二维码（`qr_png`
  data URL，缺失则显示 `qr_content` 文本），每 2s 轮询 `/qr/poll`，成功后刷新
  状态。文件：`dashboard/src/api/group-relay.ts`、
  `composables/useFeishuRelayWorkspace.ts`、`views/GroupRelayMonitorView.vue`。
- bridge 主动探活：每 `LARKX_AUTH_PROBE_SECONDS`（默认 600）调
  `auth.validate()`（ticket 探针，不写存储），结果进 health
  `auth.probe_ok/probe_at/probe_error`；探针失败但 WS 仍在时标 `degrading`，
  面板提前变黄。
- 告警：`needs_login` 持续 > 2 分钟时，经现有飞书 webhook 机制（adapter 已有
  `QUANT_ALERT_WEBHOOK_TOKEN` 家族；或 `scripts/b300_collab_watch` 的 ops webhook
  先例）发一条"请扫码"通知，含面板链接，去抖 30 分钟。

**测试/验收**
- adapter：代理路由的 401 透传、token 注入（mock fetch）。
- dashboard：组件渲染 needs_login 态显示按钮、authed 态隐藏。
- 真实验收：throwaway bridge（空 `LARKX_HOME`、端口 8097）接到 adapter 测试路由，
  面板显示"需要扫码"+二维码；用真实手机扫，日志出现
  `QR 登录成功 user_id=… 触发客户端重连`，health 变 `authed`，全程不重启进程。
**回滚**：面板隐藏入口；bridge 探活 env 设 0。

## 2. 捕获时效与覆盖

**现状**：cmd=6 push + cmd=7001 通知（→ 即时尾部补读）已生效；尾部巡检只覆盖
2 个群（监听数字群共 8 个）——猫群曾因此盲 4 小时；全帧捕获已累积大量未解释
帧：cmd **200（324 帧）**、7032（9）、74（12）、0（16）、49/48。发送者只有
`from_id`。

**改动**
- 尾部巡检默认全覆盖：`LARKX_PRIVATE_TAIL_REPAIR_CHAT_IDS` 为空 ⇒ 覆盖
  `websocket_chat_ids ∩ private_gap_repair_chat_ids`（当前语义是"空=不限"，核对
  `_private_tail_repair_allowed` 后把 edge env 的显式两群改为空）。
- 解剖未解释帧：新增 `feishu-relay/scripts/tools/inspect-raw-captures.py`
  （只读，在 edge 跑）：按 cmd 聚合、对每类 cmd 用通用 protobuf walker 打印字段
  树（先例：本次对 7001 的解剖脚本），输出含哪些 chat_id/message_id 字样。目标
  是把 cmd 200/7032/74 归类为"控制帧/已读回执/在线态/另一种消息信封"。只对
  **证据表明承载消息**的 cmd 加解析，走 `proto_wire.parse_notify_frame` 的模式
  （新函数 + `LARKX_WS_NOTIFY_CMDS` 扩展，或新 `LARKX_WS_PUSH_CMDS`），绝不猜。
- 发送者名解析：bridge 新增 `sender_names.py`——SQLite 缓存（`LARKX_HOME/
  sender-names.db`，TTL 7 天），未命中时经 `client.get_user_name(user_id,
  chat_id)`（上游网关，已有）解析，单飞+限速（每分钟 ≤ 20 次）。
  `on_message` 在归档前填 `sender_name`；`history_render` 优先用它；
  `/history/transcript` 默认带名。
- 通知帧延迟 1.5s 可调（`LARKX_NOTIFY_PULL_DELAY_SECONDS`），统计
  `ws_notify_pull_hit/miss`（拉到/没拉到）进 health，用于调参。

**验收**
- 猫群新卡从创建到 relay 账本 ≤ 60s（巡检）或数秒（通知命中）；
  `ws_notify_pull_hit` 占比可观测。
- 转录出现真实昵称；缓存命中率 > 95%。
- 未解释帧报告落在 `docs/`（数据，不含聊天内容）。
**回滚**：env 复原；名解析开关 `LARKX_SENDER_NAMES=0`。

## 3. OAuth 补读通道稳健化

**现状**：服务端按会话降级的 bot 卡（猫群等）在个人会话里永远是降级版，唯一
真内容通道是 OAuth 官方 API 补读；refresh token 本次只给 7 天、用到即轮换；
过期靠 `scripts/tools/feishu-user-oauth-reauthorize.py`（本机，需人点同意）。

**改动**
- 到期预警：adapter 定时（每小时）读 `feishuUserOauth.status()`，
  `refresh_expires_at - now < 48h` 时发告警（同第 1 阶段通道），面板显示倒计时徽标。
- 面板内重授权：adapter 新增 `GET /api/group-relay/larkagentx/oauth/authorize-url`
  （用 `FEISHU_APP_ID` + 固定 redirect `http://localhost:8080/callback` + 必需
  scope 拼 URL；app_id 不是密钥但不要写日志）；dashboard 提供"复制授权链接 →
  粘贴回调 code → 提交"三步表单，提交走现有 `POST /internal/feishu-user-oauth`
  （需 `QUANT_WRITE_API_KEY`，由 adapter 服务端持有，前端不接触）。
- 保活：每 5 天若无补读流量，adapter 主动 `forceRefresh` 一次（已有方法），
  防止安静群把 token 饿死。
**验收**：人为把 `refresh_expires_at` 视为临近（测试桩）触发告警；面板完成一次
真实重授权；保活日志每 5 天一条。
**回滚**：`FEISHU_OAUTH_KEEPALIVE=0`；面板入口隐藏。

## 4. 导出数据源升级（降级群取补读账本）

**现状**：导出链路（bridge 转录渲染 + adapter 代理 + 服务端书签 + 面板按钮 +
本机 CLI）已上线；但对被降级的群，bridge 归档存的是降级卡（转录成 `[图片]`），
真内容在 adapter 账本 `feishu_group_relay_messages.message`（补读后的官方卡 JSON）。

**改动**
- adapter 导出代理新增 `source=archive|ledger|merged`（默认 `merged`）：
  - `ledger`：直接从 Postgres 取该 chat（按 `source_chat_id`/`oc_` 映射）的
    `status='sent'` 行，用 `card-content.mjs` 的 `cardText`（已含降级横幅与诱饵
    过滤）渲染为同格式转录（时间/发送者/文本），发送者用 `sender_name`；
  - `merged`：以 bridge 归档为主干，凡归档行是降级卡/占位（`isPlaceholderCardRelay`
    或 `larkAgentXCardIsDegraded`），按 chat + `create_time ±3s`（复用
    `relayMessagesEquivalent` 的窗口）用账本官方内容替换。
- 书签按 `(chat_id, source)` 分键（`larkagentx_export_bookmarks` 加 `source`
  列，PRIMARY KEY 改复合，`CREATE TABLE IF NOT EXISTS` + `ALTER … ADD COLUMN IF
  NOT EXISTS` 的既有迁移模式）。
- CLI `export-group-history.py` 加 `--source`；面板格式选择旁加数据源选择。
**验收**：猫群 `source=merged` 转录出现 `Eric: 临盘拉了欧盟反制` 这类真文本而非
`[图片]`；普通文本群三种 source 结果一致；书签互不串扰。
**回滚**：默认 `source=archive`。

## 5. 发送层产品化（面向自有群）

**现状**：bridge `POST /send`（`LARKX_SEND_CHAT_IDS` 白名单 13 群，文本 + 可选
`root_id` 回复），无限速、无幂等、无审计。上游只有文本发送包
（`build_send_message_packet`），`media.py` 只下载——**图片/文件上传上游不支持**，
需单独逆向，列为研究项不纳入本阶段验收。

**改动**
- bridge：令牌桶（每群 `LARKX_SEND_PER_CHAT_PER_MIN` 默认 6、全局
  `LARKX_SEND_GLOBAL_PER_HOUR` 默认 120，超限 429 + `retry_after`）；幂等键
  `idempotency_key`（event_spool 新表 `send_audit`：key/chat/text_sha256/
  root_id/result/error/ts，24h 内重复键直接返回首次结果）；每次发送落审计行；
  health 暴露桶余量与最近失败。
- adapter：`POST /api/group-relay/larkagentx/send` 代理（注入 bridge token），
  仅允许白名单群；`GET …/send/audit?limit=`。
- dashboard：「发送」页——选群（白名单）、正文、可选回复 root_id、发送；下方审计
  列表。保号提示：显示当前桶余量。
- 研究项（不阻塞）：抓一次官方客户端发图的网关请求，评估是否可复用
  `_gateway_post` 发 `PutMessage` 带图片 key；结论写进本文档。
**验收**：向自有测试群发一条，账本审计有行；连发超限返回 429；同
`idempotency_key` 重放不重复发。
**回滚**：`LARKX_SEND_ENABLED=0`（新增总开关，默认沿用现状=开）。

## 6. 可观测与前端整合

- 面板补齐 bridge 新字段：`auth` 块、`ws_notify_cmds/count/last`、尾部巡检覆盖
  群列表、原始帧捕获计数与最近 cmd 分布、OAuth 到期倒计时、发送桶余量。
- 服务索引（`scripts/generate_service_index.py` 的 REGISTRY）加 `/monitor#account`
  等深链。
- 告警统一：`needs_login`、OAuth 剩余 < 48h、解码错误 1h 内 > 20、通知拉取
  miss 率 > 50%，走同一 webhook helper（adapter 内新增 `notifyOps()`，去抖）。
**验收**：人为触发每类告警各一次，群里收到且 30 分钟内不重复。

## 7. 上游钉扎与测试基建

- larkx 钉扎校验：在 `config/` 或 `feishu-relay/` 记录 `/opt/larkagentx/source`
  的内容摘要（先例：XHS 要求记录 Spider_XHS digest）；hotfix 脚本比对，漂移即
  fail closed 并提示重新评审覆盖点（`build_ws_url`、`_gateway_post`、
  `decode_push_messages` 的 cmd 过滤）。
- `feishu-relay/scripts/edge/test-bridge-on-edge.sh`：封装"rsync 到 edge 临时目录
  → supervisor venv 跑全套 → 清理"，输出通过数；hotfix 脚本可选调用。
- 捕获文件只读分析脚本（第 2 阶段）纳入 `scripts/tools/`。
**验收**：故意改一行上游副本，hotfix 拒绝发布；edge 测试脚本一条命令出结果。

## 8. 明确不做

- 不替换或 fork pinned 的 larkx 客户端（见既有决策"暂不采用的方案"）。
- 不做规避风控的流量伪装；不向非自有群自动群发。
- 不在仓库内建 AI 分析器——导出产物由用户交给自己的 AI。

## 建议顺序与依赖

1 → 2 → 3 → 4 → 5 → 6 → 7。第 1 阶段独立；第 2 阶段的名解析被第 4 阶段复用；
第 3 阶段是第 4 阶段 `ledger` 源的前提（token 必须活）；第 6 阶段汇总前面所有
健康字段；第 7 阶段随时可插队做。每阶段预计一次 hotfix + 一次 bridge 重启。
