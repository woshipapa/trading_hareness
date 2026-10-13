# W010 飞书中继：目标群管理、撤回同步、加急告警、链接入群

- state: released
- holder: Claude Code 会话 eac0e1fc，2026-10-13
- goal: 在转发看板里完成建同步群、管理目标群成员、源头撤回后同步撤回、降级告警加急、粘贴邀请链接入群，以及把现有目标群纳入看板管理
- plan: docs/work/plans/relay-group-automation.md
- branch: integration/relay-auto
- head: 0413948f
- release: edge 中继 `hotfix-20261013T133421Z-b311b53e2193-21767`（七项功能全部上线）
- next: 用户在看板上试用后按反馈改；用户第一次真实使用时核对三件没在线上看到的事：(1) 会员到期提醒真的 @ 到别的租户的成员；(2) 一条文字转发被核对上、源头撤回后目标群同步撤回；(3) 一键建同步群走通全程。之后回收剩下的两个工作区和集成分支，做探测 P0-8，状态改 `verified`
- updated: 2026-10-13

## Requirements

- 2026-10-13 我给出盘点和建议（待办 B030）之后，用户：「可以这些全部推进，请你写好完整计划给codex-flow去执行推进。不同worktree最后merge」
- 2026-10-13 看过方案要点、写文件被权限分类器拦下的说明、以及探测会打扰到本人账号的两项之后，用户：「同意了，我是trust的」
- 2026-10-13 看过三轮审查后的改动、探测结果和六个工作区的处置之后，用户：「开始分发工作执行」
- 2026-10-13 base 线第一步在执行时，用户新增要求：「因为我的很多转发目标群里，不同会员购买的日期和时间不同，我希望可以自动化这个流程，每次新加一个会员，加的时候可以登记为开始日期，或者手动修改，然后以及持有的期限比如1个月，3个月，然后每天会自动审查，如果到期了，可以在对应目标群里，由webhook at这些用户通知他们到期了联系群主续费。」这在方案 r4 的“不做”里（成员到期）之外新增一项功能：会员期限登记与到期提醒（不是自动移除）。
- 2026-10-13 我说明到期提醒按“至少一次”做（极少数情况下同一人会被多提醒一次，但不漏）之后，用户：「可以多提醒」
- 2026-10-13 用户：「当前已经完成的功能你记得可以hotfix上线edge，让我前端自己实验一下」。（下一条见后）这改变了方案里“五条都合完才合进 main 并发布”的安排：
  每条线评审通过、合进集成分支并在集成分支上验收通过后，就把集成分支合进 main、热更新 edge，并告诉用户新上线了什么可以试。
- 2026-10-13 用户：「能并行的任务全部并行啊，给codex最高权限」。Codex 实现者本来就是 `danger-full-access`、不询问；“不接触线上”仍是提示里的规则，没有放开。
  这改变了“base 合并后才拉功能线”的安排：功能线的 bridge 和 adapter 两步不依赖 base 的看板，改为立即从 base 分支的提交点并行开工。

## Plan

`docs/work/plans/relay-group-automation.md`，现为 r7（r5 加功能 E，r6、r7 采纳 E 审查两轮各 5 条；r7 没有再送审，派 E 之前可再审一轮）。批准：用户 2026-10-13「同意了，我是trust的」（针对方案要点、六个工作区的处置和派工前的线上探测）。
方案审查第一轮（Codex，只读，线程 01a1342e，`gpt-5.6-sol` high）：7 条意见（3 条 high、4 条 medium），全部采纳，改成 r2，逐条处理写在方案末尾的“审查记录”。
第二轮 4 条（1 条 high、3 条 medium）、第三轮 1 条（medium），同样全部采纳（r3、r4）；没有不采纳的意见。第三轮那一条的改动没有再送审（最多三轮）。
按 codex-flow，审查后的改动要再给用户看一次才派工。

## Steps

- [x] 0 用户同意方案；方案落盘；Codex 方案审查三轮，12 条全部采纳，r4 — 399e0fc5
- [x] 1 线上探测 P0-1 至 P0-7 与 `WEB_GATEWAY.md` — 集成分支 6b31c254；输出在 `docs/work/probes/2026-10-13-relay-auto-p0*.out.txt`（P0-8 在 base 合并后做）
- [x] 2 base 线 — 后端 `codex/relay-auto-base` ae1bb9d6、看板 `codex/relay-auto-base-ui` ed33aac1，合进集成分支 0e20e6f0，合进 main 10d5f55b；
  集成分支上 adapter 289 项、看板 10 项、构建通过，edge 上 bridge 套件 179 项通过；发布 `hotfix-20261013T100002Z-10d5f55bf233-24792`，状态脚本 ALL CHECKS PASSED
- [x] 3 六条功能线全部合并 — join、create、members、recall、urgent、membership，集成分支 063c82c2；集成分支上 adapter 333 项、看板 29 项、edge 上 bridge 套件 216 项通过
- [x] 4 合并、发布、线上验收 — main b311b53e（另有 0413948f 只改接口说明），`hotfix-20261013T133421Z-b311b53e2193-21767`，`release-sync-status.sh --skip-owner --edge-source-sha b311b53e` → ALL CHECKS PASSED；
  与方案不同：是完成一条上线一条（用户要求），共六次热更新；线上没验到的三件事见 `next`

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
- 2026-10-13 19:30 base 第 2b 步返回（bb228919）。本会话复跑：adapter 289 项通过；edge 上 bridge 套件 179 项通过。`index.mjs` 的改动与报告一致：
  功能模块的创建、建表、`start()`、路由分发（在跨站检查之后、其他分支之前）、状态里的 `webhook_targets`、目标群增删后的 `resolve` / `forget`、`notifyOps` 末尾的加急调用；
  group relay 等三个对象的创建整体挪到了功能模块之后（因为要传送达记录的两个函数）。待评审确认的一点：`notifyOps` 用到的 `urgentAlerts` 定义在它后面，
  启动期间如果有告警先触发会不会碰到未初始化。base 后端的评审者（Codex，只读）已派。
- 2026-10-13 19:25 按用户“全部并行”的要求，从 bb228919 另建七个工作区并同时派出（见 Running）。join、create 两条的 bridge+adapter 已返回，
  各自报告 adapter 292 / 297 项通过、bridge 本机 183 项（70 跳过）；本会话还没复跑。
- 2026-10-13 19:45 base 后端的评审（Codex，只读，线程 01a13496）结论 WARNING，三条都核实成立并已修（ae1bb9d6）：[codex] 没配令牌时内部路由放行空令牌头；
  [codex] 路径参数里畸形的百分号编码会在请求回调里抛未捕获异常；[codex] 飞书未知的拒绝文字原样透出，可能带出地址。[claude] 的疑问（`notifyOps` 是否会早于 `urgentAlerts` 初始化）
  经核对不成立：三个调用点都在第 608 行注册的健康检查定时器里，晚于第 413 行。
- 2026-10-13 20:00 base 合并并发布：集成分支 0e20e6f0 → main 10d5f55b → `hotfix-20261013T100002Z-10d5f55bf233-24792`（本会话执行）。线上核对：`/targets` 页面 200、构建里有“目标群”“识别现有目标群”；
  `POST /api/group-relay/target-groups/resolve` 用 22.5 秒把 4 个环境变量目标群和自测群都对上了群名、数字群 ID、机器人、群主（响应和日志里没有 webhook 地址）；10 条启用路由 healthy。
  一个缺口：启动时的自动识别因 bridge 还没起来失败了（日志“目标群识别失败：fetch failed”），之后不会重试；这次已手动触发并入表，表里有行后不再受影响。要不要加重试，留作后续。
- 2026-10-13 20:00 六条功能线的后端全部返回，各工作区合入集成分支后本会话复跑：join 292、create 297、members 291、urgent 294、recall 294、membership 299 项 adapter 测试通过，
  bridge 本机都通过（70 项跳过）。提交：join 46619efb、create 8410d72d、members 3d956d98、urgent 6ec46a63、recall 685a957e、membership 69269ceb。
  membership 的实现比方案多了一项：设置里有 `exempt_user_ids`（不提醒名单，例如群主本人），因为成员列表里分不出谁是本账号。
- 2026-10-13 20:40 J、C 合并发布：集成分支 4749fc2c → main 5a25076a → `hotfix-20261013T123830Z-5a25076a4894-10762`。评审：join WARNING 三条（失败原因里可能带出邀请令牌；未入群时
  预览会因缺省字段报错；预览缺群主）已修（a1f55d5e）；create APPROVED，三条 P2 没改（`chat_infos` 的失败不经 `checked`；两处测试覆盖不足）。
  线上：重新生成邀请链接、凭链接预览都正常。
- 2026-10-13 20:45 线上发现共性问题：飞书拒绝一条网关命令时回的是 HTTP 400 加失败外层，客户端先按状态码抛了 `HTTPError`，`checked` 读不到原因，
  用户看到的是“400 Client Error…”。correction：`WEB_GATEWAY.md` 里“调用返回失败外层”的说法只对探测脚本成立（它没有 `raise_for_status`）。
  修法在接缝里：`group_admin.require_client` 返回的客户端把这种异常换成带飞书原因的 `GroupAdminError`（base 分支 02824743）。`WEB_GATEWAY.md` 还没改，待改。
- 2026-10-13 21:00 M 合并发布：集成分支 64a55af6 → main 85a5af5e → `hotfix-20261013T125819Z-85a5af5ee34d-12722`。评审 WARNING 已修（3f75a626）：缺省的 `action` 报 KeyError；
  URL 里的编号没校验；门禁测试只测了第一条路由。线上：成员列表、进退群记录、入群申请都能读；已在群里再加入时现在显示飞书的原因且不带令牌。
  线上又发现两点：(1) 成员列表里有机器人（自测群里是本账号和“转发机器人”），接口没区分——已派补丁加 `is_bot`、`is_self`，E 要据此跳过；
  (2) 看板的 DELETE 不带请求体，分发函数把它交给 JSON 解析而报“请求不是有效 JSON”，移除成员因此不可用——本会话在集成分支上直接修了（f1de3093，带测试）。
- 2026-10-13 21:06 D 合并发布（连同上面的分发修正）：集成分支 645531e6 → main 141fb2ea → `hotfix-20261013T130447Z-141fb2ea8305-26916`，状态脚本 ALL CHECKS PASSED。
  评审 WARNING 四条已修（e8b9b2a7）：旁路每条消息起一个线程没有上限（改成有界队列加一个工作线程）；观察到的非文字消息也会参与匹配；`start()` 重复装定时器；看板切换目标群时旧响应覆盖新数据。
  线上：不带请求体的 DELETE 正常；`/deliveries` 可读（自测群为空）；bridge 每 60 秒收到一次观察名单（启动时第一次因 bridge 未就绪失败，下一轮成功）。
  还没看到的：真实转发消息被核对上、以及源头撤回后的同步撤回——要等有文字转发流量和一次真实撤回；探测 P0-8（富文本和卡片读回的样子）没做，目前只核对文字。
- 2026-10-13 21:10 membership（WARNING：同步把形状不对的回答当成空名单会把所有人标成已离群；失败的审查每分钟重试；编辑成员是两次独立写入；另两条简化）和
  urgent（WARNING 四条 high：发送被拒没检查；取消息编号可能取到别的消息；每小时上限没加锁；设置没校验）都已交回各自线程修。
- 2026-10-13 21:11 U 合并发布：集成分支 e054d1b5 → main 764ca2b2 → `hotfix-20261013T131021Z-764ca2b22292-2811`，状态脚本通过。评审的四条 high 已修（f4b03028）。
  线上：设置默认关闭；接收人列表里是“用户731118, 范一真”这个两人会话。测试加急没有替用户点。
- 2026-10-13 21:35 E 合并发布：members 的补丁（7cf36524，成员带 `is_bot`、`is_self`，机器人不能移除）和 membership（6ea3d8ab，只给真人建会员记录）一起，
  集成分支 063c82c2 → main b311b53e → `hotfix-20261013T133421Z-b311b53e2193-21767`，状态脚本通过。membership 评审的五条已修（b0e62f26）。
  线上（自测群）：成员列表两人分别标了本账号和机器人；会员设置默认未启用、期限 1 个月；同步后没有会员记录（正确，群里没有别人）；预览为空。
  到期提醒真的发出、并发两次只提醒一次，在线上没有验到（自测群里没有真人成员）。
- 2026-10-13 21:40 `WEB_GATEWAY.md` 改正两处（5167d38a，main 0413948f，只是文档，没有重新发布）。回收工作区：base、base-ui、join、create、urgent、recall 已回收；
  members、membership 因十分钟内有 Codex 活动被脚本拒绝，留待下次；集成分支和它的工作区保留，后续修补还要用。
- 2026-10-13 21:40 开销（`codex-trace.py --usage`，各线程累计，含各自的评审者）：八个工作区合计输入 9828 万 token（其中缓存 9291 万、新输入 538 万），输出 44.4 万，推理 18.4 万；
  方案审查线程 01a1342e 另计输入 712 万（缓存 650 万、新输入 62 万）、输出 4.2 万、推理 2.9 万。

## Waiting on the user

- [ ] 2026-10-13 在看板上试用七项功能并反馈；特别是会员期限：对真实目标群第一次“同步成员”后，老成员的开始日期是各自的入群日期、期限是默认的 1 个月，
  多数会直接显示已到期——先改成真实的购买日期和期限，用“预览今天要提醒谁”确认名单，再打开“每天自动审查”。为什么等：只有用户知道每个会员的购买日期和期限。
  之后：按 `next` 核对线上没验到的三件事。

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
- [x] 2026-10-13 19:05 base 第 2b 步 → 19:30 返回（bb228919），见 Log。原说明：Codex 实现者在做 base 线第 2b 步（线程 01a13455，cwd 是 base 工作区）：2a 的三条修正，加 `adapter/index.mjs` 和
  `adapter/group-relay.mjs` 的接线及测试。结束标志：调用返回、工作区干净。之后：复跑验收、读 diff（重点看 `index.mjs`、`group-relay.mjs` 只动了报告里列的地方），
  再派第三步（看板：`targets` 栏目、`TargetGroupsView`、`TargetRegistryPanel`、其余面板空壳、`JoinByLinkPanel` 的位置）。
- [x] 2026-10-13 19:25 起并行的七个调用 → 20:00 前全部返回（各线后端的提交见 Log）。原说明：并行的 Codex 调用（都挂在会话 eac0e1fc 上；工作区都在 `/Users/papa/codebase/.codex-wt/n8n-<slug>`，都从 base 分支的 bb228919 起步，
  处置 merge、目标集成分支；每个只做 bridge 和 adapter 两步，看板那一步等 base 的看板合并后再续派）：
  `relay-auto-base-ui`（base 的看板一步）；`relay-auto-join`（线程 01a1348b，已返回 46619efb）；`relay-auto-create`（线程 01a1348d，已返回 8410d72d）；
  `relay-auto-members`；`relay-auto-urgent`；`relay-auto-recall`；`relay-auto-membership`（只有 adapter）。另有 base 后端的评审者（只读，cwd 是 base 工作区）。
  没返回的线程编号用 `python3 ~/codebase/agent-skills/own/codex-flow/scripts/codex-trace.py --list` 按 cwd 找。
  结束标志：各调用返回、各工作区 `git status --short` 为空。之后：见 `next`。
- [x] 2026-10-13 20:05 六条线的看板一步 → 全部返回（中途三个调用断线，续派后完成），见 Log。原说明：六条功能线的看板一步并行在跑（各用自己的实现线程续派；工作区都已合入集成分支 0e20e6f0）：
  join 01a1348b（已返回 ae4f174f，评审者已派，cwd 是 join 工作区）、create 01a1348d、members 01a1348e、urgent 01a13490、recall 01a13492、membership 01a13494。
  结束标志：各调用返回、工作区干净。之后：见 `next`。都挂在会话 eac0e1fc 上；接手者按各工作区的提交和 `git status` 判断做到哪。
- [x] 2026-10-13 21:10 三个调用 → 21:35 前全部完成并合并（见 Log）。原说明：三个 Codex 调用在跑（都挂在会话 eac0e1fc 上）：members 补丁（线程 01a1348e，工作区 `n8n-relay-auto-members`）；urgent 的四条修正（线程 01a13490）；
  membership 的五条修正（线程 01a13494）。Codex 的调用有时会提前返回空内容或上一轮的结果，而会话其实还在干活：以工作区的提交和 `git status` 为准，
  不要因为返回为空就重发。结束标志：各工作区有新提交且干净。之后：见 `next`。

## Due

## Evidence

- 本机 `/private/tmp/claude-501/-Users-papa-codebase-n8n/eac0e1fc-0e56-4ad6-b497-353bd69ffb47/scratchpad/web_gateway_messages.txt`
  （不在版本库里）：20 条网关命令的请求和响应字段，取自 edge 上 LarkAgentX 的生成代码。
- `docs/work/probes/2026-10-13-larkagentx-capability-survey.out.txt`：能力盘点和两项只读探测。
- 先例：`docs/decisions/0019-feishu-relay-dashboard-registrations.md`、`docs/decisions/0020-feishu-relay-creates-target-bots.md`、
  `docs/work/streams/W009-feishu-relay-new-tag-and-target.md`。

## Open questions

- 到期提醒 @ 别的租户的成员是否生效：没验（P0-9 只 @ 了本租户的账号）。第一次对外部同步群的真实提醒回答。
- 富文本和卡片形态的转发读回的文字与发出的是否一致：没验（P0-8 没做），所以 D 目前只核对文字。
- 有入群申请时每行的形态、处理申请（通过和拒绝）：没验，等第一条真实申请。
- 账号不在群里时凭链接入群的成功响应：没验，等第一个真实邀请链接。

## Resume here

七项功能全部上线：edge 中继是 `hotfix-20261013T133421Z-b311b53e2193-21767`（main b311b53e；main 之后的 0413948f 只改了 `WEB_GATEWAY.md`，未推送）。
先跑 `bash scripts/release-sync-status.sh --skip-owner --edge-source-sha b311b53e` 确认线上仍是这一版。
还开着的：用户试用后的反馈（Waiting on the user）；线上没验到的四件事（Open questions）；待办 B046–B049（后续修补）；
工作区 `n8n-relay-auto-members`、`n8n-relay-auto-membership` 和集成分支 `integration/relay-auto`（工作区 `n8n-relay-auto-int`）还没回收——
它们的内容都已在 main 里，用 `bash ~/codebase/agent-skills/own/codex-flow/scripts/finishworktree.sh . <slug> --into integration/relay-auto --apply` 回收前两个，
集成分支在没有后续修补要做时 `git worktree remove` 再删分支。
要修补时的做法：在集成分支工作区改（或新建 `codex/<slug>` 工作区派给 Codex）→ 集成分支上跑三套测试和 edge 上的 bridge 套件 → 合进 main → 热更新 → 线上核对。
注意：撤回同步对所有目标群自动生效；会员审查默认不启用；加急默认关闭。“中继自测群”（7696053062053530578，目标群 `wh_4c9095abdb79bb9ab648e8db`）可随意用来验收。
