# W010 飞书中继：目标群管理、撤回同步、加急告警、链接入群

- state: proposed
- holder: Claude Code 会话 eac0e1fc，2026-10-13
- goal: 在转发看板里完成建同步群、管理目标群成员、源头撤回后同步撤回、降级告警加急、粘贴邀请链接入群，以及把现有目标群纳入看板管理
- plan: none
- branch: none
- head: 9d75b5d9
- release: none
- next: 等用户看过方案并同意后，把方案写成 `docs/work/plans/relay-group-automation.md`，交 Codex 做方案审查，再按 codex-flow 进入派工前的确认
- updated: 2026-10-13

## Requirements

- 2026-10-13 我给出盘点和建议（待办 B030）之后，用户：「可以这些全部推进，请你写好完整计划给codex-flow去执行推进。不同worktree最后merge」

## Plan

方案草稿已写好，但写入 `.claude/plan/relay-group-automation.md` 时被自动模式的权限分类器拒绝（理由标注为 Real-World Transactions），
没有落盘，也没有绕过去写。方案全文在本会话的对话里交给了用户，等用户决定。要点：

- 只动 `feishu-relay/`。集成分支 `integration/relay-auto`，六个工作区：先做 `relay-auto-base`（各功能互不碰文件的接缝，加上
  “现有目标群纳入看板”），再并行做 `relay-auto-join`、`-create`、`-members`、`-urgent`、`-recall`，依次合回集成分支，最后合进 main。
- 派工之前由本会话用中继的飞书会话逐条试用到的网关命令（建自测群、发言权限、邀请链接、入群申请、群主撤回机器人消息、加急、
  拉人和移除），结果写成仓库里的 `feishu-relay/bridge/WEB_GATEWAY.md`，Codex 只按它实现、不接触线上。没试通的功能不做。
- 写操作只在本账号是群主的群里做；源群只读。

## Steps

- [ ] 0 用户同意方案；方案落盘；Codex 方案审查
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

## Waiting on the user

- [ ] 2026-10-13 是否同意方案并允许把它写成文件、交给 Codex 审查和执行；其中派工前的线上探测有两项会打扰到用户本人的账号
  （一次应用内加急；一次被拉进自测群再移出）。为什么等：权限分类器把写方案文件判为需要用户确认的操作；探测会影响用户本人的账号。
  之后：方案落盘 → Codex 审查 → 再给用户看审查后的版本和每个工作区的处置，得到同意后才派工。

## Running

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

方案还没落盘，什么都没派出去，仓库里没有本线的代码改动。第一步是看 Waiting on the user 那一项有没有答复。
方案全文只在会话 eac0e1fc 的对话里；如果那段对话已经不在，按 Plan 一节的要点和 B030 重写。
不要绕过权限分类器去写方案文件；不要在用户同意之前做任何线上写操作。
