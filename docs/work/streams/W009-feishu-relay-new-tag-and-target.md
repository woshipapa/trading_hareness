# W009 飞书转发：全新标签和全新目标群也只在前端操作

- state: released
- holder: Claude Code 会话 eac0e1fc，2026-10-13
- goal: 在转发看板里新增源群时，用一个从没用过的标签、或转发到一个从没配置过的目标群，都不需要改仓库文件、改服务器配置或发版
- plan: none
- branch: main
- head: ea3d5865
- release: edge 中继 `hotfix-20261013T060210Z-ea3d5865b40e-16279`
- next: 用户第一次在看板里用新标签建源群、或新增一个目标群之后，读 `curl -s http://127.0.0.1:18300/api/group-relay/status` 的 `source_tags` 和 `webhook_targets`，确认多出一行 `dashboard` 来源并且该路由 healthy；通过后把状态改成 `verified`
- updated: 2026-10-13

## Requirements

- 2026-10-12 「希望你把这个全部变成自动化的。我们前端只需要操作就可以了」（群目录、退群识别、历史回读已在 10-12 上线，见 Log 第一条）
- 2026-10-11 关于新源群的标签是否进入下游研究数据：「默认是要加入的」
- 2026-10-13 我说明“全新的标签要登记进 source-registry 并发一次 edge 热更新、全新的目标群要先在服务器上配 webhook 密钥”之后，用户：「全新的这些你也要搞成可以自动化的，这些我都允许了。」

## Plan

方案写在本记录里，没有单独的方案文件。用户 2026-10-13 的原话（上一节第三条）是对目标的授权，方案细节由本会话定。

1. **标签登记改成运行时可写。** 库里已有 `ingestion_source_profiles`（启动时由 `feishu-relay/config/source-registry.json` 写入）。新增
   `adapter/source-tags.mjs`：启动时在文件登记之外再读出库里 `config.origin = 'dashboard'` 的行；建路由时标签没登记就写一行
   （`publisher_key`、`remote_analyst_id` 都等于标签，`label` 取群名）并立即生效。文件里声明的标签仍以文件为准。
   建路由的请求带 `ingest`，默认 `true`；`false` 表示只转发、不进下游（`cat`、`diaoyan` 那种）。
2. **目标群的 webhook 改成前端可配。** 新增 `adapter/webhook-targets.mjs` 和表 `feishu_group_relay_webhook_targets`：
   地址用 AES-256-GCM 加密后入库（密钥由应用密钥派生，和 OAuth 令牌同一做法），接口和日志都不返回地址。保存前先用该
   webhook 发一条带关键词的测试消息，失败就不保存。保存后写进运行中的两张映射表，不重启就能用。环境变量里已有的
   目标群不变，前端不能改它们。
3. **看板。** 源群表单加“进入下游研究数据”勾选项和“新增目标群”入口；目标群表显示群名、来源，前端建的可以删除。
4. edge 热更新发布，用线上接口核对：建一个新标签的路由后 `/api/config` 里出现该标签；新增一个目标群后它出现在下拉里。

不做：自动在飞书群里创建自定义机器人。飞书开放平台没有这个接口；bridge 用的私有协议里只有读取群机器人的消息
（`PullChatBots` 等，2026-10-13 在 edge 的 `/opt/larkagentx/source` 里查过），没有创建。用个人账号调未公开的写接口有
封号风险。所以“在飞书群里加一个自定义机器人、复制它的地址”这一步仍由用户在飞书里做，其余都在看板里完成。

## Steps

- [x] 1 标签登记：`source-tags.mjs`、ledger 方法、路由创建接入、测试 — ea3d5865 — `node --test feishu-relay/adapter/*.test.mjs` → tests 275, pass 275, fail 0
- [x] 2 目标群：`webhook-targets.mjs`、表、三个接口、测试 — ea3d5865 — 同上（其中 `webhook-targets.test.mjs` 7 项、`source-tags.test.mjs` 6 项）
- [x] 3 看板：表单、目标群对话框、目标群表 — ea3d5865 — `cd feishu-relay/dashboard && npm run typecheck && npm test -- --run && npm run build` → Tests 6 passed，构建成功
- [x] 4 提交、edge 热更新、线上核对 — `bash feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --apply`（本会话执行，14:02）→ `overlay hotfix activated`；
  `scripts/release-sync-status.sh --skip-owner --edge-source-sha ea3d5865` → ALL CHECKS PASSED。发布表已加一行。
  与方案不同：线上没有真的登记一个新标签、也没有真的加一个目标群，只核对了加载、读取和五种拒绝情形（原因见 Log）。

## Log

- 2026-10-13 13:40 起因：用户刚导入「RCCS小杰夜报【禁言】」，核对映射和历史回读都成功（路由 13:33:54 建立，WebSocket 同秒绑定，
  762 个位置 13:35:04 读完，库里 692 条，没存的 70 个位置重读后是 57 条系统消息和 13 条已撤回）。这次不用发版是因为
  `xiaojie` 标签早已登记、目标群是现成的汇总群。
- 2026-10-13 13:55 读代码后的结论：标签登记只差“运行时写库并更新内存表”；目标群投递读的是两张 `Map`，运行时加一项就生效。

- 2026-10-13 14:00 提交前的检查：bridge `python3 -m unittest discover -s feishu-relay/bridge` 165 项通过（70 项本机跳过）；
  `verify_component_boundaries.py --check` 通过，改动只落在 feishu-relay；`pytest scripts` 357 通过，另有 1 项失败与本线无关：
  `test_runtime_hosts` 报 supervisor 任务 `s2-gateway-tunnel` 没有归类，来自别的会话未提交的 `scripts/svc_supervisor.py`。
- 2026-10-13 14:02 发布。两个 Dockerfile 的 COPY 清单加了两个新模块（守卫测试要求），热更新脚本只在依赖变化时才拒绝，所以仍走源码覆盖。
- 2026-10-13 14:08 线上核对（输出见 Evidence）：adapter 和 bridge 的 release 都是新编号，WebSocket 14:03:59 重连，10 条启用路由 healthy，
  刚导入的 RCCS 群仍绑定、历史 763/763。新接口：非飞书地址 400、飞书不认识的机器人地址 400（这一条真的向飞书发了请求并被拒）、
  删环境变量目标群 400、删不存在的目标群 404、从看板关登记表声明的标签 400 且路由没被改动。
  没做的两件：建一条新标签的路由会在生产库留下路由、绑定和历史副本，不为验证去造；加目标群需要一个真的机器人地址，要用户在飞书里建。
  这两条路径的写库语句各自在线上跑过（标签登记和启动时写登记表是同一个函数；目标群表启动时已建并读过），没跑过的是接口到模块的那一段接线。
- 2026-10-13 14:08 adapter 日志里看到两条“百度网盘研究快照轮询失败：量化服务归档读取返回无效 JSON（HTTP 200）”，与本线改动的模块无关，没有处理。

## Waiting on the user

- [ ] 2026-10-13 要转发到一个全新的目标群时：先在飞书里打开该群 → 设置 → 群机器人 → 添加机器人 → 自定义机器人，安全设置选“自定义关键词”，
  再到看板「新增目标群」里填 webhook 地址和关键词。为什么等：飞书没有创建自定义机器人的接口（见 Plan 的“不做”）。之后：本会话或接手者按 `next` 核对。

## Running

## Due

## Evidence

- 本机 `/private/tmp/claude-501/-Users-papa-codebase-n8n/eac0e1fc-0e56-4ad6-b497-353bd69ffb47/scratchpad/`（不在版本库里）：
  `gr_routes.json`、`gr_chats.json`、`gr_status.json`（2026-10-13 13:38 经 `127.0.0.1:18300` 读 edge 的三个接口）；
  `hist_check.mjs`、`missing_check.mjs`（在 edge 的 adapter 容器里只读核对历史副本的脚本）；`hotfix_ea3d5865.log`（热更新脚本的完整输出）。
- `docs/work/probes/2026-10-13-feishu-relay-w009-live-check.out.txt`：发布后的状态脚本输出、新接口的读取和五种拒绝情形、看板构建里的新文字。
- 决定：`docs/decisions/0019-feishu-relay-dashboard-registrations.md`。

## Open questions

- 新标签的 `remote_analyst_id` 在 owner 那边是否要先存在：没查。`jinli`（10-11 登记）有 4 个任务停在 `text_or_submit`，
  其余标签的下游任务自 9 月下旬起没有新增；下游提交这条链路用户说过先不管（「先不用管后续的这个n8n流程」）。
  要回答：读 n8n 的提交工作流和 owner 的分析师归档接口。

## Resume here

代码已提交（ea3d5865，未推送）并发布到 edge，线上核对通过；还差一次真实使用的确认（见 `next`）。
先跑 `bash scripts/release-sync-status.sh --skip-owner --edge-source-sha ea3d5865` 确认 edge 中继仍是这一版。
注意：(1) 线上没有真的登记过新标签、加过新目标群，第一次真实使用就是验收；(2) 新标签登记后消息会建成中继库里的下游任务，
但下游提交那条 n8n 链路本来就停着，用户说过先不管，不要顺手去修；(3) 看板配置的目标群编号是 `wh_` 开头，只能发文字、富文本和卡片；
(4) 主检出里有别的会话没提交的文件（`config/secrets/*`、`scripts/svc_supervisor.py` 等），不要碰。
