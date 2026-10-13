# W010 飞书中继：目标群管理、撤回同步、加急告警、链接入群

- state: building
- holder: Claude Code 会话 eac0e1fc，2026-10-13
- goal: 在转发看板里完成建同步群、管理目标群成员、源头撤回后同步撤回、降级告警加急、粘贴邀请链接入群，以及把现有目标群纳入看板管理
- plan: docs/work/plans/relay-group-automation.md
- branch: integration/relay-auto
- head: 6b31c254
- release: none
- next: base 线第 2b 步返回后：在 base 工作区里复跑验收命令、读 `git diff integration/relay-auto...HEAD -- feishu-relay/adapter/index.mjs feishu-relay/adapter/group-relay.mjs`，通过后用线程 01a13455 派第三步（看板）；三步都完成后做双重评审（Codex 评审者用新线程、只读），在 edge 上跑 bridge 套件，合进集成分支，再合进 main 并热更新 edge（用户已要求做完一项上线一项）
- updated: 2026-10-13

## Requirements

- 2026-10-13 我给出盘点和建议（待办 B030）之后，用户：「可以这些全部推进，请你写好完整计划给codex-flow去执行推进。不同worktree最后merge」
- 2026-10-13 看过方案要点、写文件被权限分类器拦下的说明、以及探测会打扰到本人账号的两项之后，用户：「同意了，我是trust的」
- 2026-10-13 看过三轮审查后的改动、探测结果和六个工作区的处置之后，用户：「开始分发工作执行」
- 2026-10-13 base 线第一步在执行时，用户新增要求：「因为我的很多转发目标群里，不同会员购买的日期和时间不同，我希望可以自动化这个流程，每次新加一个会员，加的时候可以登记为开始日期，或者手动修改，然后以及持有的期限比如1个月，3个月，然后每天会自动审查，如果到期了，可以在对应目标群里，由webhook at这些用户通知他们到期了联系群主续费。」这在方案 r4 的“不做”里（成员到期）之外新增一项功能：会员期限登记与到期提醒（不是自动移除）。
- 2026-10-13 我说明到期提醒按“至少一次”做（极少数情况下同一人会被多提醒一次，但不漏）之后，用户：「可以多提醒」
- 2026-10-13 用户：「当前已经完成的功能你记得可以hotfix上线edge，让我前端自己实验一下」。这改变了方案里“五条都合完才合进 main 并发布”的安排：
  每条线评审通过、合进集成分支并在集成分支上验收通过后，就把集成分支合进 main、热更新 edge，并告诉用户新上线了什么可以试。

## Plan

`docs/work/plans/relay-group-automation.md`，现为 r7（r5 加功能 E，r6、r7 采纳 E 审查两轮各 5 条；r7 没有再送审，派 E 之前可再审一轮）。批准：用户 2026-10-13「同意了，我是trust的」（针对方案要点、六个工作区的处置和派工前的线上探测）。
方案审查第一轮（Codex，只读，线程 01a1342e，`gpt-5.6-sol` high）：7 条意见（3 条 high、4 条 medium），全部采纳，改成 r2，逐条处理写在方案末尾的“审查记录”。
第二轮 4 条（1 条 high、3 条 medium）、第三轮 1 条（medium），同样全部采纳（r3、r4）；没有不采纳的意见。第三轮那一条的改动没有再送审（最多三轮）。
按 codex-flow，审查后的改动要再给用户看一次才派工。

## Steps

- [x] 0 用户同意方案；方案落盘；Codex 方案审查三轮，12 条全部采纳，r4 — 399e0fc5
- [x] 1 线上探测 P0-1 至 P0-7 与 `WEB_GATEWAY.md` — 集成分支 6b31c254；输出在 `docs/work/probes/2026-10-13-relay-auto-p0*.out.txt`（P0-8 在 base 合并后做）
- [ ] 2 base 线：第一步 bridge 完成（`codex/relay-auto-base` e81059b0；本机 `python3 -m unittest discover -s feishu-relay/bridge -p 'test_*.py' -q` → Ran 178 tests, OK (skipped=70)；
  edge 上 `bash feishu-relay/scripts/edge/test-bridge-on-edge.sh` → Ran 178 tests, OK）；第二步 adapter、第三步看板未做
- [ ] 3 六条功能线（join、create、members、urgent、recall，以及 members 之后的 membership）
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
- 2026-10-13 18:00 探测 P0-9：自测群里用 webhook 以成员列表的数字 ID @ 中继账号自己，文字和富文本都成功（`docs/work/probes/2026-10-13-relay-auto-p0-9.out.txt`）。
  据此把“会员期限与到期提醒”作为功能 E 加进方案 r5（新线 `relay-auto-membership`，排在 members 之后），并在 `WEB_GATEWAY.md` 里补了 @ 的写法（集成分支 10097a5a）。
  E 这一节交 Codex 审查（同一审查线程）。
- 2026-10-13 18:10 base 线第一步返回（线程 01a13455，三个提交到 e81059b0）。本会话复跑验收通过，edge 上完整套件 178 项通过。读 diff 的两条意见，随第二步一起交回：
  [claude] `target_registry.resolve` 在 bridge 未登录时会因 `bridge.client` 为空报属性错误，应像现有处理函数那样明确报“尚未登录”；
  [claude] `resolve_groups` 对 `GetWebhookBotInfo` 的回答做了两层防御式判断，接口总是回 `{code, msg, data}`，应简化。
  旁路 `tap` 接在 `on_message` 的最前面，补读和更正重放的消息也会经过它（D 要按消息编号去重）。
- 2026-10-13 18:40 E 的审查第二轮返回 5 条（2 条 high、3 条 medium），全部采纳为 r7：依赖里加 `refreshGroups`、`webhookTargets.addressHashes()`、
  认领带时间且 10 分钟失效（至少一次）、“再提醒一次”走同一个认领、校验默认期限。r7 已放到集成分支（50ae99d1），base 工作区要等 2a 返回后再合进去。
  2b 的派工要带上 `refreshGroups` 这一项依赖。
- 2026-10-13 18:50 用户要求完成一项就上线一项。从集成分支直接发布会和别的会话从 main 发的中继热更新互相覆盖，所以做法是：
  每条线过了评审就把集成分支 `--no-ff` 合进 main 再从主检出发热更新。没做完的功能在看板上的空面板不显示内容。
  当时线上是 `hotfix-20261013T070041Z-49b44b1da5e4-5527`，W010 还没有可见的功能上线。
- 2026-10-13 19:00 base 线第 2a 步返回（4b5ddb97）：本会话复跑 adapter 286 项通过、bridge 本机 179 项（70 项跳过）。读 diff 的三条意见随 2b 交回：
  [claude] `resolve()` 先清空表再插入，漏扫一次就会丢行；[claude] `list()` 给已不存在的目标群造了一行；[claude] 监听者失败会让写入失败。
  另：`readJsonBody` 默认上限 18 MiB，接线时给功能路由包一层 64 KiB。2a 的开销并在线程 01a13455 的累计里。

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
- [x] 2026-10-13 17:45 base 线第一步 → 18:10 返回，结果见 Log。原说明：Codex 实现者在做 base 线第一步（cwd `/Users/papa/codebase/.codex-wt/n8n-relay-auto-base`）。结束标志：MCP 调用返回，
  分支 `codex/relay-auto-base` 在 6b31c254 之后有新提交且工作区 `git status --short` 为空。之后：见 `next`。该调用挂在会话 eac0e1fc 上，
  会话结束它就停：接手者先看分支和工作区，再用新的 Codex 调用从没做完的地方接着派（方案和 `WEB_GATEWAY.md` 都在工作区里）。
- [x] 2026-10-13 18:05 E 的审查第一轮 → 18:25 返回 5 条（1 条 high、4 条 medium），全部采纳，方案 r6（7fd… 见 git log 的 `notes: W010 plan r6`）。原说明：Codex 审查方案 r5 里功能 E 那一节（审查线程 01a1342e，只读）。结束标志：返回 `SOUND` 或问题清单。之后：裁决、改方案，
  把 r5 的方案文件带进集成分支和 base 工作区，再用线程 01a13455 续派 base 第二步（adapter 接缝、R，以及 E 的三处接缝和上面两条意见）。
- [x] 2026-10-13 18:30 2a 和 E 第二轮审查 → 都已返回（见 Log）。原说明：两个 Codex 调用在跑：(1) base 线第 2a 步（实现线程 01a13455，cwd 是 base 工作区）：第一步的两条修正加 adapter 的模块
  （`ledger.query`、`feature-routes.mjs`、`target-registry.mjs`、六个空壳、`webhookTargets.sendText`、两个 Dockerfile、测试），不碰 `index.mjs` 和 `group-relay.mjs`；
  (2) E 的审查第二轮（审查线程 01a1342e，只读）。结束标志：各自的调用返回。之后：对 (1) 复跑验收、读 diff，再续派 2b（`index.mjs`、`group-relay.mjs` 接线）和第三步（看板）；
  对 (2) 裁决并改方案。两个调用都挂在会话 eac0e1fc 上。
- [ ] 2026-10-13 19:05 Codex 实现者在做 base 线第 2b 步（线程 01a13455，cwd 是 base 工作区）：2a 的三条修正，加 `adapter/index.mjs` 和
  `adapter/group-relay.mjs` 的接线及测试。结束标志：调用返回、工作区干净。之后：复跑验收、读 diff（重点看 `index.mjs`、`group-relay.mjs` 只动了报告里列的地方），
  再派第三步（看板：`targets` 栏目、`TargetGroupsView`、`TargetRegistryPanel`、其余面板空壳、`JoinByLinkPanel` 的位置）。

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
