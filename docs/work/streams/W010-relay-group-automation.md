# W010 飞书中继：目标群管理、撤回同步、加急告警、链接入群

- state: building
- holder: Claude Code 会话 eac0e1fc，2026-10-13
- goal: 在转发看板里完成建同步群、管理目标群成员、源头撤回后同步撤回、降级告警加急、粘贴邀请链接入群，以及把现有目标群纳入看板管理
- plan: docs/work/plans/relay-group-automation.md
- branch: integration/relay-auto
- head: 6b31c254
- release: none
- next: base 线第一步（bridge 接缝和 R 的 bridge 部分）返回后：在工作区里自己跑验收命令和 `bash feishu-relay/scripts/edge/test-bridge-on-edge.sh`，读 `git diff integration/relay-auto...HEAD`，通过后用同一线程续派第二步（adapter 接缝和 R），再第三步（看板）
- updated: 2026-10-13

## Requirements

- 2026-10-13 我给出盘点和建议（待办 B030）之后，用户：「可以这些全部推进，请你写好完整计划给codex-flow去执行推进。不同worktree最后merge」
- 2026-10-13 看过方案要点、写文件被权限分类器拦下的说明、以及探测会打扰到本人账号的两项之后，用户：「同意了，我是trust的」
- 2026-10-13 看过三轮审查后的改动、探测结果和六个工作区的处置之后，用户：「开始分发工作执行」

## Plan

`docs/work/plans/relay-group-automation.md`，现为 r4。批准：用户 2026-10-13「同意了，我是trust的」（针对方案要点、六个工作区的处置和派工前的线上探测）。
方案审查第一轮（Codex，只读，线程 01a1342e，`gpt-5.6-sol` high）：7 条意见（3 条 high、4 条 medium），全部采纳，改成 r2，逐条处理写在方案末尾的“审查记录”。
第二轮 4 条（1 条 high、3 条 medium）、第三轮 1 条（medium），同样全部采纳（r3、r4）；没有不采纳的意见。第三轮那一条的改动没有再送审（最多三轮）。
按 codex-flow，审查后的改动要再给用户看一次才派工。

## Steps

- [x] 0 用户同意方案；方案落盘；Codex 方案审查三轮，12 条全部采纳，r4 — 399e0fc5
- [x] 1 线上探测 P0-1 至 P0-7 与 `WEB_GATEWAY.md` — 集成分支 6b31c254；输出在 `docs/work/probes/2026-10-13-relay-auto-p0*.out.txt`（P0-8 在 base 合并后做）
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
- 2026-10-13 16:45 方案审查第一轮返回 7 条，全部采纳（主要是：U、D 需要的调用点改由 base 预先接好，功能线不再碰共享文件；U 不能走 `Bridge.send()`；
  D 的指纹要用最终发出的文字并加一项探测 P0-8；本机跳过的 bridge 测试由 edge 套件把关；R 不筛掉非群主的目标群）。r2 交第二轮。
- 2026-10-13 17:20 方案审查三轮结束：第二轮把 bridge 给 D 的钩子改成不影响原处理的旁路、两处记录调用不等待不外传失败、观察名单定时下发；
  第三轮把“目标群变了”的通知收进 `targetRegistry` 自己的写方法。方案 r4 已提交。等用户做派工前的确认。
- 2026-10-13 17:32 P0-7、P0-6 试通，输出 `docs/work/probes/2026-10-13-relay-auto-p0-c.out.txt`：
  `PutChatChatter {chatId, chatterIds}` 成功（响应键 `chat`、`chatChatterVersion`），成员列表里出现被拉的人；`DeleteChatChatter` 同样成功，人已不在。
  发文字是命令 5（`PutMessage`），响应里读不到现成的消息编号，脚本用“读该会话最新的、发送者是本账号的消息”取到编号；
  `PutUrgent {messageId, chatterIds, urgentType: 'APP', chatId}` 成功，响应 `{urgent: {messageId, type: 1, sendTime, fromId}, chatterIds, urgentAckIds: {<对方>: <ackId>}}`；
  `PullUrgentsAckStatus {messageIds}` 返回 `urgentAcks[<messageId>].urgentAckList[]`，每项有 `ackId`、`toId`、`type`、`status`、`phoneStatus`。
  至此 P0-1 至 P0-7 全部通过，方案里没有因探测失败要拿掉的功能；P0-8 按方案在 base 合并后做。
- 2026-10-13 17:45 用户确认派工。建集成分支和它的工作区，提交 `WEB_GATEWAY.md`（6b31c254）；建 base 工作区并快进到集成分支；
  派 base 线第一步给 Codex（`danger-full-access`，`gpt-5.6-sol` high）。

## Waiting on the user

- [x] 2026-10-13 是否同意方案并允许把它写成文件、交给 Codex 审查和执行 → 「同意了，我是trust的」（2026-10-13）。原问题：其中派工前的线上探测有两项会打扰到用户本人的账号
  （一次应用内加急；一次被拉进自测群再移出）。为什么等：权限分类器把写方案文件判为需要用户确认的操作；探测会影响用户本人的账号。
  之后：方案落盘 → Codex 审查 → 再给用户看审查后的版本和每个工作区的处置，得到同意后才派工。
- [x] 2026-10-13 运行 P0-6、P0-7 的脚本 → 用户在对话里发了这条命令（前面带空格，没有被当作 shell 命令执行），本会话据此代为运行，17:32 完成，两项都通过（见 Log）。原说明：运行 `! bash docs/work/probes/2026-10-13-relay-auto-p0-c.sh`（P0-6 加急、P0-7 拉人和移除）。为什么等：这两项会通知用户本人的飞书账号，
  agent 执行被权限分类器拒绝。之后：读同目录生成的 `…-p0-c.out.txt`，把结果并进 `WEB_GATEWAY.md`；不运行的话，U（加急）和 M 里的“移除成员”
  按没试过处理——要么从方案里拿掉，要么上线后由用户第一次使用时验收。
- [x] 2026-10-13 看过方案审查后的改动，同意派工 → 「开始分发工作执行」（2026-10-13）。原说明：之后：建集成分支 `integration/relay-auto`，写 `WEB_GATEWAY.md`，派 base 线。

## Running

- [x] 2026-10-13 16:20 Codex 方案审查（线程 01a1342e）→ 17:20 三轮结束，共 12 条意见全部采纳，方案 r4；结果记在 Plan 一节和方案末尾。原说明：  之后：逐条裁决，改方案或写明不采纳的理由，再审一轮（最多 3 轮）。该调用挂在会话 eac0e1fc 上，会话结束它就停；接手者重新发起审查即可。
- [ ] 2026-10-13 17:45 Codex 实现者在做 base 线第一步（cwd `/Users/papa/codebase/.codex-wt/n8n-relay-auto-base`）。结束标志：MCP 调用返回，
  分支 `codex/relay-auto-base` 在 6b31c254 之后有新提交且工作区 `git status --short` 为空。之后：见 `next`。该调用挂在会话 eac0e1fc 上，
  会话结束它就停：接手者先看分支和工作区，再用新的 Codex 调用从没做完的地方接着派（方案和 `WEB_GATEWAY.md` 都在工作区里）。

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

集成分支 `integration/relay-auto` 的工作区在 `/Users/papa/codebase/.codex-wt/n8n-relay-auto-int`；base 线是分支 `codex/relay-auto-base`，
工作区 `/Users/papa/codebase/.codex-wt/n8n-relay-auto-base`（处置 merge，目标集成分支）。
已开始派工。集成分支 `integration/relay-auto`（6b31c254）有自己的工作区，用来提交、合并和跑验收；base 线的工作区已建，
第一步（bridge）在 Codex 手里（见 Running）。base 线还有第二步（adapter）和第三步（看板），三步都做完、双重评审通过后合进集成分支，
再做探测 P0-8，然后从集成分支拉五条功能线并行派。
先跑 `bash ~/codebase/agent-skills/own/codex-flow/scripts/lsworktrees.sh . --target integration/relay-auto` 看各工作区的状态。
mkworktree 用主检出的 HEAD 作基点：建好功能线的工作区后要在里面 `git merge --ff-only integration/relay-auto`，评审的 diff 用
`integration/relay-auto...HEAD`。本机跑不到导入 `bridge.py` 的测试，每条线合并前要在 edge 上跑完整 bridge 套件。
不要再跑 P0-1 和 P0-c 脚本。合并进 main、发布都要用户同意；结束时回收工作区并报告 token 开销。
