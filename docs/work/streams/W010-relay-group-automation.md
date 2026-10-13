# W010 飞书中继：目标群管理、撤回同步、加急告警、链接入群

- state: approved
- holder: Claude Code 会话 eac0e1fc，2026-10-13
- goal: 在转发看板里完成建同步群、管理目标群成员、源头撤回后同步撤回、降级告警加急、粘贴邀请链接入群，以及把现有目标群纳入看板管理
- plan: docs/work/plans/relay-group-automation.md
- branch: none
- head: 9d75b5d9
- release: none
- next: 读 Codex 方案审查的结论并逐条裁决（改方案或写明不采纳的理由）；同时做线上探测 P0-1 至 P0-7，把结果写成 `feishu-relay/bridge/WEB_GATEWAY.md`；然后把审查后的改动给用户看，得到同意后建集成分支、派 base 线
- updated: 2026-10-13

## Requirements

- 2026-10-13 我给出盘点和建议（待办 B030）之后，用户：「可以这些全部推进，请你写好完整计划给codex-flow去执行推进。不同worktree最后merge」
- 2026-10-13 看过方案要点、写文件被权限分类器拦下的说明、以及探测会打扰到本人账号的两项之后，用户：「同意了，我是trust的」

## Plan

`docs/work/plans/relay-group-automation.md`，r1。批准：用户 2026-10-13「同意了，我是trust的」（针对方案要点、六个工作区的处置和派工前的线上探测）。
Codex 方案审查的结论和裁决记在 Log 里；按 codex-flow，审查后的改动要再给用户看一次才派工。

## Steps

- [ ] 0 用户同意方案（已同意）；方案落盘（已落盘）；Codex 方案审查
- [ ] 1 线上探测与 `WEB_GATEWAY.md`
- [ ] 2 base 线
- [ ] 3 五条功能线
- [ ] 4 合并、发布、线上验收

## Log

- 2026-10-13 15:40 预检：codex-teleai 两个工具可用；`lsworktrees.sh` 显示 `tactic-trials` 正在被别的会话使用，另有三天前的
  `tdx-*` 分支若干（待办 B029 已记）。本线不碰它们。
- 2026-10-13 15:50 只读查证：用到的网关命令在 `chats.*`、`urgent.*`、`messages.*` 下都有消息定义（建群 13、发言权限 254、
  邀请链接 5055/5057/5056、成员 47/45/5950、进退群记录 5029、入群申请 5002/5004、群主撤回 78、加急 30000/30005）；
  命令表里 `im.v1.*` 那一组同名命令没有消息定义，不用。字段清单在本机 scratchpad 的 `web_gateway_messages.txt`。
- 2026-10-13 16:00 方案文件写入被权限分类器拒绝，停下来等用户。
- 2026-10-13 16:10 用户同意。方案写成 `docs/work/plans/relay-group-automation.md` r1，交 Codex 审查；同时开始线上探测。
- 2026-10-13 16:20 方案审查已交 Codex（只读，线程 01a1342e，`gpt-5.6-sol` high），等结论。
- 2026-10-13 15:36–15:40（线上时间戳）探测 P0-1 至 P0-5 全部试通，输出 `docs/work/probes/2026-10-13-relay-auto-p0.out.txt`：
  建了“中继自测群”（7696053062053530578，跨租户，群主是本账号）并留作试验场；只许群主发言不挡机器人；邀请链接是
  `applink.feishu.cn/client/chat/chatter/add_by_link?link_token=…`；群主可以撤回机器人的消息（撤回后 status 2、内容为空）；
  机器人在账号所在群里发的消息会推到 bridge。自测群已登记为看板目标群 `wh_4c9095abdb79bb9ab648e8db`（自动创建的机器人，关键词“自测”）。
  没试到的：账号不在群里时凭链接入群的成功响应；有入群申请时每行的形态和处理申请。
- 2026-10-13 16:25 P0-6（加急）和 P0-7（拉人、移除）的执行被 Claude Code 自动模式的权限分类器拒绝（没有给理由）。没有绕过；
  把这两项写成用户自己运行的脚本 `docs/work/probes/2026-10-13-relay-auto-p0-c.sh`。

## Waiting on the user

- [x] 2026-10-13 是否同意方案并允许把它写成文件、交给 Codex 审查和执行 → 「同意了，我是trust的」（2026-10-13）。原问题：其中派工前的线上探测有两项会打扰到用户本人的账号
  （一次应用内加急；一次被拉进自测群再移出）。为什么等：权限分类器把写方案文件判为需要用户确认的操作；探测会影响用户本人的账号。
  之后：方案落盘 → Codex 审查 → 再给用户看审查后的版本和每个工作区的处置，得到同意后才派工。
- [ ] 2026-10-13 运行 `! bash docs/work/probes/2026-10-13-relay-auto-p0-c.sh`（P0-6 加急、P0-7 拉人和移除）。为什么等：这两项会通知用户本人的飞书账号，
  agent 执行被权限分类器拒绝。之后：读同目录生成的 `…-p0-c.out.txt`，把结果并进 `WEB_GATEWAY.md`；不运行的话，U（加急）和 M 里的“移除成员”
  按没试过处理——要么从方案里拿掉，要么上线后由用户第一次使用时验收。
- [ ] 2026-10-13 看过方案审查后的改动，同意派工（codex-flow 的派工前确认）。之后：建集成分支 `integration/relay-auto`，写 `WEB_GATEWAY.md`，派 base 线。

## Running

- [ ] 2026-10-13 16:20 Codex 方案审查（MCP 调用，线程 01a1342e，cwd 是主检出，只读）。结束标志：调用返回 `SOUND` 或一份编号的问题清单。
  之后：逐条裁决，改方案或写明不采纳的理由，再审一轮（最多 3 轮）。该调用挂在会话 eac0e1fc 上，会话结束它就停；接手者重新发起审查即可。

## Due

## Evidence

- 本机 `/private/tmp/claude-501/-Users-papa-codebase-n8n/eac0e1fc-0e56-4ad6-b497-353bd69ffb47/scratchpad/web_gateway_messages.txt`
  （不在版本库里）：20 条网关命令的请求和响应字段，取自 edge 上 LarkAgentX 的生成代码。
- `docs/work/probes/2026-10-13-larkagentx-capability-survey.out.txt`：能力盘点和两项只读探测。
- 先例：`docs/decisions/0019-feishu-relay-dashboard-registrations.md`、`docs/decisions/0020-feishu-relay-creates-target-bots.md`、
  `docs/work/streams/W009-feishu-relay-new-tag-and-target.md`。

## Open questions

- 群主能不能撤回机器人发的消息（`messages.DeleteGroupMessageRequest`）：没试。答案决定“撤回同步”做不做，探测 P0-5 回答。
- 设成只许群主发言后，机器人的 webhook 还能不能发：没试。探测 P0-2 回答。
- 加急对单聊里的对方账号是否可用、是否占额度：没试。探测 P0-6 回答。

## Resume here

方案已批准并落盘（r1），线上探测 P0-1 至 P0-5 已做完并存档；还没有建集成分支，也没有派工，仓库里没有本线的代码改动。
接手先看 Waiting on the user 的两项和 Running 的一项。P0-6、P0-7 只能由用户运行脚本，不要自己再试，也不要换别的办法绕过权限分类器。
“中继自测群”（7696053062053530578）是留着的试验场，已是看板目标群，可以用它做以后的验收；探测 P0-1 不要再跑（会再建一个群）。
`feishu-relay/bridge/WEB_GATEWAY.md` 还没写：内容来自 `docs/work/probes/2026-10-13-relay-auto-p0.out.txt` 和
`docs/work/probes/2026-10-13-web-gateway-messages.out.txt`，要提交在集成分支上。
按 codex-flow：审查后的改动给用户看过并得到同意，才能建工作区派工。
