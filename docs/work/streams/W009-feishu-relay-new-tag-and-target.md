# W009 飞书转发：全新标签和全新目标群也只在前端操作

- state: building
- holder: Claude Code 会话 eac0e1fc，2026-10-13
- goal: 在转发看板里新增源群时，用一个从没用过的标签、或转发到一个从没配置过的目标群，都不需要改仓库文件、改服务器配置或发版
- plan: none
- branch: main
- head: 85c81967
- release: none
- next: 写 `feishu-relay/adapter/source-tags.mjs` 和 `webhook-targets.mjs` 及其测试，接进 `index.mjs` 的路由创建和三个目标群接口
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

- [ ] 1 标签登记：`source-tags.mjs`、ledger 方法、路由创建接入、测试
- [ ] 2 目标群：`webhook-targets.mjs`、表、三个接口、测试
- [ ] 3 看板：表单、目标群对话框、目标群表
- [ ] 4 提交、edge 热更新、线上核对

## Log

- 2026-10-13 13:40 起因：用户刚导入「RCCS小杰夜报【禁言】」，核对映射和历史回读都成功（路由 13:33:54 建立，WebSocket 同秒绑定，
  762 个位置 13:35:04 读完，库里 692 条，没存的 70 个位置重读后是 57 条系统消息和 13 条已撤回）。这次不用发版是因为
  `xiaojie` 标签早已登记、目标群是现成的汇总群。
- 2026-10-13 13:55 读代码后的结论：标签登记只差“运行时写库并更新内存表”；目标群投递读的是两张 `Map`，运行时加一项就生效。

## Waiting on the user

## Running

## Due

## Evidence

- 本机 `/private/tmp/claude-501/-Users-papa-codebase-n8n/eac0e1fc-0e56-4ad6-b497-353bd69ffb47/scratchpad/`（不在版本库里）：
  `gr_routes.json`、`gr_chats.json`、`gr_status.json`（2026-10-13 13:38 经 `127.0.0.1:18300` 读 edge 的三个接口）；
  `hist_check.mjs`、`missing_check.mjs`（在 edge 的 adapter 容器里只读核对历史副本的脚本）。

## Open questions

- 新标签的 `remote_analyst_id` 在 owner 那边是否要先存在：没查。`jinli`（10-11 登记）有 4 个任务停在 `text_or_submit`，
  其余标签的下游任务自 9 月下旬起没有新增；下游提交这条链路用户说过先不管（「先不用管后续的这个n8n流程」）。
  要回答：读 n8n 的提交工作流和 owner 的分析师归档接口。

## Resume here

刚开工，代码还没写。先看 `git status --short feishu-relay`：有改动就从 Steps 里第一个没勾的接着做。主检出里有别的会话
没提交的文件（`config/secrets/*`、`scripts/svc_supervisor.py` 等），不要碰，提交时按路径加自己的文件。
