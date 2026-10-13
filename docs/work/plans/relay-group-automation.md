# 飞书中继：目标群管理、撤回同步、加急告警、链接入群（relay-group-automation）

- 版本：r5，2026-10-13，基于 main `9d75b5d9`；工作线 W010。r2、r3、r4 分别采纳了 Codex 方案审查三轮的 7 条、4 条、1 条意见（见文末“审查记录”）。r5 应用户 2026-10-13 的新要求加了功能 E（会员期限与到期提醒）。
- 组件：只动 `feishu-relay/`（发布单元 edge-relay）。不碰 quant-service、frontend、scripts、config/secrets、docs/work。
- 出处：待办 B030；用户 2026-10-13「可以这些全部推进，请你写好完整计划给codex-flow去执行推进。不同worktree最后merge」，
  看过方案要点后「同意了，我是trust的」。
- 先例：`feishu-relay/bridge/chat_bots.py`（规则与网络分离、可注入）、`feishu-relay/adapter/webhook-targets.mjs`、决定 0019/0020。

## 目标

看板里能做完这六件事，都用中继自己的飞书会话（网页端的网关命令），不改服务器配置、不发版：

| 编号 | 功能 | 用户看到的 |
|---|---|---|
| R | 现有目标群纳入看板 | 环境变量配置的 4 个目标群显示群名、机器人，和看板配置的目标群同表管理 |
| C | 一键建同步群 | 填群名 → 建群、只许群主发言、建转发机器人、登记为目标群、给出邀请链接和口令 |
| M | 目标群成员管理 | 成员列表、进退群记录、入群申请的通过与拒绝、移除成员 |
| D | 撤回同步与送达核对 | 每个目标群“已核对 / 未核对 / 已撤回”的计数；源头撤回后目标群里的那条也被撤回 |
| U | 加急告警 | 选一个接收人，中继降级类告警除发运维群外，再由账号发给接收人并应用内加急 |
| J | 粘贴邀请链接入群 | 新增源群时贴链接 → 预览群名和群主 → 确认加入 → 回到原有的建路由流程 |
| E | 会员期限与到期提醒 | 每个目标群的成员有开始日期和期限；每天自动审查，到期的由群里的转发机器人 @ 本人提醒联系群主续费 |

## 约束（每条线都适用）

1. 只在**本账号是群主**的群里做写操作（建群本身除外）；bridge 里统一核对，不是群主就拒绝。源群（老师的群）只读，J 的“加入”除外。
2. 实现者（Codex）**不接触线上**：不 SSH、不访问 feishu.cn、不跑 `scripts/edge/*`。网关命令的字段和实测行为以仓库里的
   `feishu-relay/bridge/WEB_GATEWAY.md` 为准（第 0 步由 Claude 写入）；文件里没写的行为不要猜，停下来报告。
3. 本机没有 larkx：bridge 的规则写成不依赖 larkx 的模块，网络访问由参数传入（同 `chat_bots.py`），本机用假对象测；
   完整 bridge 套件由 Claude 在 edge 上跑（`feishu-relay/scripts/edge/test-bridge-on-edge.sh`）。
4. webhook 地址、签名密钥、cookie 不打印、不进日志、不进接口响应、不进测试夹具以外的任何文件。
5. adapter 的新模块平铺在 `feishu-relay/adapter/`（测试命令是 `adapter/*.test.mjs`，`dockerfile-manifest.test.mjs` 要求
   两个 Dockerfile 的 COPY 清单包含 `index.mjs` 能到达的每个模块）。不新增 npm 依赖（adapter 的依赖变了热更新会拒绝）。
6. 面向用户的文字用中文；代码注释、提交信息用英文，风格照周围代码。

## 工作区与合并

集成分支 `integration/relay-auto`（Claude 从 main 建）。每条线一个工作区，处置都是 `merge`，目标都是集成分支：

| 顺序 | slug（分支 `codex/<slug>`） | 内容 | 依赖 |
|---|---|---|---|
| 1 | `relay-auto-base` | 接缝 + R | 无 |
| 2 并行 | `relay-auto-join` | J | base、P0-3 |
| 2 并行 | `relay-auto-create` | C | base、P0-1/2/3 |
| 2 并行 | `relay-auto-members` | M | base、P0-4/7 |
| 2 并行 | `relay-auto-urgent` | U | base、P0-6 |
| 2 并行 | `relay-auto-recall` | D | base、P0-5 |
| 3 | `relay-auto-membership` | E | base、M 已合并、P0-9 |

base 合进集成分支后，其余五条从集成分支拉出并行做，各自只改自己的文件（见“接缝”），按 join → create → members →
urgent → recall 的顺序 `--no-ff` 合回集成分支（E 在 members 合并后才从集成分支拉出，最后合），每合一条在集成分支上跑一遍全部验收命令。
base 之后，任何功能线都**不再修改**这些共享文件：`bridge/bridge.py`、`bridge/feature_routes.py`、`bridge/group_admin.py`、
`adapter/index.mjs`、`adapter/group-relay.mjs`、`adapter/ledger.mjs`、`adapter/feature-routes.mjs`、`adapter/frontend-assets.mjs`、
两个 Dockerfile、`dashboard/src/App.vue`、`dashboard/src/views/TargetGroupsView.vue`、`dashboard/src/composables/useFeishuDashboardWorkspace.ts`。
功能线发现非改不可时停下来报告，由 Claude 在集成分支上改。五条都合完、双重评审无未决的
critical/high 后，集成分支 `--no-ff` 合进 main（要用户同意），随后 edge 热更新和线上验收（Claude 做）。
每次派给 Codex 的任务控制在 15 分钟内：每条线拆成 bridge、adapter、看板三步，用同一线程续派。

## 第 0 步（Claude，派工之前）：线上探测 P0 与 `WEB_GATEWAY.md`

除群机器人接口和两条读取命令外，下面的命令只确认了存在，没调用过。Claude 用中继会话逐条试，结果写进
`feishu-relay/bridge/WEB_GATEWAY.md`（命令号、用到的请求字段、实测响应、错误码）并提交到集成分支；输出另存 `docs/work/probes/`。
**哪条没试通，对应的功能就从本方案里拿掉或改方案，不让 Codex 猜。**

| 编号 | 探测 | 命令 | 对用户可见的影响 |
|---|---|---|---|
| P0-1 | 建一个“中继自测群”，留作以后的试验场 | `chats.PutChatRequest`（13） | 账号多一个群 |
| P0-2 | 设成只许群主发言后，机器人 webhook 还能不能发 | `chats.PatchChatPostUserRequest`（254） | 自测群里几条消息 |
| P0-3 | 生成邀请链接；用它读群信息；对已在群里的账号再“加入”一次看返回 | `PullChatLinkTokenRequest`（5055）、`PullChatLinkInfoRequest`（5057）、`PutChatterViaLinkRequest`（5056） | 无 |
| P0-4 | 读入群申请列表 | `chats.PullChatApplicationsRequest`（5002） | 无 |
| P0-5 | 机器人发一条 → 群主撤回它；读回确认；看这条消息在历史里的 `from_id` 和内容长什么样 | `messages.DeleteGroupMessageRequest`（78） | 自测群里一条被撤回的消息 |
| P0-6 | 账号给用户本人的账号发一条文字并应用内加急，查回执 | `urgent.PutUrgentRequest`（30000）、`PullUrgentsAckStatusRequest`（30005） | 用户的飞书会收到一次加急提醒 |
| P0-7 | 把用户本人的账号拉进自测群再移除 | `chats.PutChatChatterRequest`（47）、`DeleteChatChatterRequest`（45） | 用户会收到一次入群和被移出的通知 |
| P0-8 | 让中继往自测群各转发一条文字、带图富文本、卡片，记下“发出去的最终文字”和“从群里读回的文字” | 现有转发通道 + `PullMessagesByPositions` | 自测群里三条消息 |

P0-1 至 P0-7 已于 2026-10-13 试通（`docs/work/probes/2026-10-13-relay-auto-p0.out.txt`、`…-p0-c.out.txt`）；P0-8 在 base 合并后做。
P0-9（webhook 用成员列表里的数字 ID @ 群成员，文字和富文本两种形态）也已试通（`docs/work/probes/2026-10-13-relay-auto-p0-9.out.txt`），
被 @ 的是本租户的账号；别的租户的成员没试。

## 接缝（base 线先做，之后各线互不碰对方的文件）

base 线建好下面的空壳和接线；空壳是给并行工作区留的位置，不是多余代码，评审时不按“死代码”处理。

**bridge**
- `bridge/group_admin.py`：`class GroupAdminError(Exception)`；`require_owner(chat_info, user_id)`（群主不是本账号就抛）。
- `bridge/feature_routes.py`：`FEATURE_ROUTES: dict[str, Callable[[Bridge, dict], dict]]`，由六个功能模块各自的 `ROUTES` 合并而成。
  功能模块：`target_registry.py`（R，base 实现）、`join_link.py`、`target_groups.py`、`chat_members.py`、`urgent_alerts.py`、
  `target_observer.py`（后五个 base 只建 `ROUTES = {}`）。
- `bridge/bridge.py` 的 `do_POST`：在返回 404 之前查 `FEATURE_ROUTES`；`except (GroupLookupError, HistoryRangeError, ChatBotError)`
  里加上 `GroupAdminError`（同样回 422）。
- `bridge/bridge.py` 给 D 留的钩子是一个**旁路**（base 接好，`target_observer.py` 的空壳什么都不做）：在处理一条收到的群消息的
  最前面——早于 XHS 指令群的分支（约第 2291–2295 行）和监听名单的判断——调用 `target_observer.tap(self, chat_id, message)`。
  `tap` 自己判断这个群是不是在观察、发送者是不是该群登记的机器人，是就把一份副本发到观察入口；它不返回值、不改变后面的处理，
  它里面的异常只记日志（旁路不能影响正常转发，这是有意的吞异常）。观察中的目标群**不加入** `websocket_chat_ids`：
  没监听的目标群，消息在 `tap` 之后照旧因不在名单被忽略；本来就在监听的群（例如汇总群）照旧走原通道。
  没有 `from_id` 的消息在更早的地方就被丢弃（约第 565 行），本来也对不上机器人。功能线 D 只改 `target_observer.py`。
- 功能模块的处理函数拿到的是 `bridge` 对象；需要 larkx 的地方在函数内部导入（本机没有 larkx，模块顶层不能导入它），
  规则写成不依赖 `bridge` 的纯函数，由处理函数把网络访问传进去。

**adapter**
- `adapter/ledger.mjs`：返回对象里加 `query: (text, params) => pool.query(text, params)`，功能模块自己建表、自己写 SQL。
- `adapter/feature-routes.mjs`：功能模块的约定是 `createXxx(deps)` 返回 `{ schema: string[], routes: Route[], ... }`，
  `Route = { method, path: string | RegExp, handle({ params, query, body }) }`；本文件提供匹配和分发（读 JSON 体、写 JSON、
  异常回 400）。`readJsonBody` 现在在 `index.mjs` 里，作为依赖传入。
- `adapter/index.mjs`：创建六个功能对象（`target-registry.mjs` 由 base 实现；`join-link.mjs`、`target-groups.mjs`、
  `target-members.mjs`、`urgent-alerts.mjs`、`delivery-check.mjs` 先是空壳），启动时依次执行它们的 `schema`，在 `createServer`
  回调里、现有的 `/internal/larkagentx/*` 和 `/api/group-relay/targets` 分支之前调用分发函数。
  `Route` 可带 `internal: true`：分发函数先按现有内部入口的做法校验 `x-larkagentx-token`。
  执行完 `schema` 后，对有 `start()` 的功能对象调用一次 `start()`（D 用它定时把观察名单发给 bridge）。
  `targetRegistry` 提供 `onChange(listener)`，由它**自己的写方法**通知监听者：`resolve()` 写表后、`record(target)`（登记或更新一行）、
  `forget(chatId)`（删一行）。`/api/group-relay/targets` 新增和删除目标群的两个处理分支（`index.mjs`，base 改）成功后分别调用
  `record` 和 `forget`；C 建好同步群后也调用 `record`。这样任何途径加减目标群都会通知到，功能线不需要碰 `index.mjs`。
- 给 U 和 D 预先接好的三处调用（空壳里是什么都不做的函数，功能线只改自己的模块）：
  `notifyOps`（第 109 行）在原有的发运维群之后 `await urgentAlerts.notify(key, text)`；
  创建 group relay 时把 `deliveryCheck.recordWebhookDelivery` 和 `deliveryCheck.recallDeliveries` 传进去；
  `adapter/group-relay.mjs` 的 `sendMessage` 在 webhook 发送成功后调用前者（传 `messageId`、`targetChatId`、最终发出的 `webhookMsgType`
  和加过关键词的最终内容），源头撤回的分支（第 1007–1010 行，`updateRelaySourceMessage` 之后）调用后者。
  这两处调用都**不等待、不让失败外传**（`Promise` 链末尾 `catch` 记日志）：webhook 没有幂等，记录失败如果让 `sendMessage` 报错，
  重试会把同一条消息再发一遍；撤回失败也不能打断轮询。这是本方案里另一处有意的吞异常，注释里写明原因。
- 分发函数把处理函数抛出的异常一律回 400 并带上原因；bridge 回的 422、429 经 `bridgePostJson` 变成带原因的异常，同样回 400。
- 两个 Dockerfile（`adapter/Dockerfile`、`deploy/edge/Dockerfile.adapter`）的 COPY 清单一次加齐八个新文件（含 E 的 `memberships.mjs` 空壳）。
- `adapter/webhook-targets.mjs` 加一个方法 `sendText(chatId, text)`：用该目标群的 webhook 发一条文字（关键词由调用方写进文字里），
  地址仍不出这个模块。E 用它发到期提醒；它是 base 线的改动，功能线不碰这个文件。

**看板**
- 新增一级栏目 `targets`（“目标群”）：`App.vue` 的菜单、标题、视图切换；`useFeishuDashboardWorkspace.ts` 里四处：
  `DashboardSection` 类型、`selectActiveSection` 的白名单（第 86 行）、`loadActiveSection` 的分支（第 93–99 行）、
  `initialSection` 认的路径和本地存储值（第 173–177 行，否则 `/targets` 刷新后回到监听页）；
  `adapter/frontend-assets.mjs` 的 `FEISHU_ROUTES` 加 `/targets`。
- `views/TargetGroupsView.vue` 依次放六个面板组件，每个面板有自己的 SFC、组合函数和 API 模块，base 只实现第一个，其余是空壳：
  `TargetRegistryPanel`（R）、`TargetGroupCreatePanel`（C）、`TargetMembersPanel`（M）、`MembershipPanel`（E）、`DeliveryPanel`（D）、`UrgentAlertsPanel`（U）。
  `JoinByLinkPanel`（J）放进 `App.vue` 的新增源群对话框，位置在“源群”表单项上方，base 接好。
- 面板之间共用“当前选中的目标群”：`TargetGroupsView` 用 `provide` 给出 `selectedTarget`（一个 ref），R 面板的表格行点击设置它。

## R：现有目标群纳入看板（base 线）

- bridge `target_registry.py`，`POST /targets/resolve {webhook_hashes: [sha256 十六进制]}`：取账号所在的全部群
  （`pull_joined_groups(self.client.feed_page)`），用 `self.client.chat_infos` 标出群主是不是本账号（`owned`），不按它筛掉；
  对每个群 `self.client.chat_bots`，对其中的自定义机器人调 `chatbot_api("GetWebhookBotInfo")`，读得到地址的算 sha256，
  读不到的（`no_permission`）没有哈希。返回 `{groups: [{chat_id, name, owned, bots: [{bot_id, name, key_words, webhook_hash}]}]}`。
  地址本身不出 bridge。只有哈希在请求里出现过的机器人才返回 `webhook_hash`。
- adapter `target-registry.mjs`：表 `feishu_group_relay_target_groups(chat_id text primary key, websocket_chat_id text, chat_name text,
  bot_id text, bot_name text, owned boolean not null, resolved_at timestamptz not null)`，`chat_id` 是目标群编号（`oc_…` 或 `wh_…`）。
  群主不是本账号的目标群照样入表（`owned` 为假）：看板显示它的群名，M、D 的写操作对它不可用。
  `resolve()`：对运行中的 webhook 映射表里每个目标群算地址的 sha256，交给 bridge，按哈希对上后写表。
  `list()`：把 `webhookTargets.list()` 和本表合并，每行多出 `websocket_chat_id`、`bot_id`、`bot_name`、`owned`，环境变量的目标群有了 `name`。
  路由：`GET /api/group-relay/target-groups`、`POST /api/group-relay/target-groups/resolve`。
  `/api/group-relay/status` 的 `webhook_targets` 改为取 `targetRegistry.list()`，这样源群表单的目标群下拉也显示群名。
  启动后表为空时自动 `resolve()` 一次（bridge 未登录就跳过并记日志，这是真实边界）。
- 看板 `TargetRegistryPanel`：全部目标群一张表（群名、关键词、来源、机器人、是否群主），按钮“识别现有目标群”。
  “Webhook 目标与关键词”那张旧表留在监听页不动。
- 其他功能通过 `targetRegistry.get(chatId)` 取 `{ websocket_chat_id, bot_id, owned }`。

## J：粘贴邀请链接入群

- bridge `join_link.py`：从链接里取 `link_token`（`WEB_GATEWAY.md` 给出链接的实际形态）；`POST /chats/link-info {link}` →
  `PullChatLinkInfo` → `{chat_id, name, owner_name, member_count, already_in_chat, expire_time}`；`POST /chats/join-by-link {link}` →
  `PutChatterViaLink` → `{chat_id, name}`，随后让 `_joined_groups_cache` 失效。
- adapter `join-link.mjs`：`POST /api/group-relay/chats/link-info`、`POST /api/group-relay/chats/join`；加入成功后
  `groupSync.requestRefresh()`。
- 看板 `JoinByLinkPanel`：输入框 + “查看” → 显示群名、群主、人数、是否已在群内 → “加入这个群”；成功后重新读群列表并把
  该群填进源群表单（调用已有的 `pickRouteGroup`）。

## C：一键建同步群

- bridge `target_groups.py`：`POST /target-groups/create {name, description}` → `PutChat`（`type: GROUP`、`groupName`、`groupDesc`，
  其余字段按 `WEB_GATEWAY.md`）→ `{chat_id, name}`；`POST /target-groups/post-permission {chat_id, only_owner}` → `PatchChatPostUser`
  （`ONLY_ADMIN` / `ANYONE`，先 `require_owner`）；`POST /target-groups/invite {chat_id}` → `PullChatLinkToken` →
  `{shared_url, kou_ling, expire_time}`（先 `require_owner`）。
- adapter `target-groups.mjs`：`POST /api/group-relay/target-groups {name, keyword?, only_owner_posts?}` 依次：bridge 建群 →
  `webhookTargets.addManaged({ name, websocketChatId, keyword })`（已有，会建转发机器人并发测试消息）→ 需要时设只许群主发言 →
  取邀请链接 → `targetRegistry` 记一行（`owned: true`）→ `groupSync.requestRefresh()`。返回 `{target, invite}`。
  中途失败：已建的群留着，错误信息里写明走到了哪一步；再次提交同一个群名不重复建群这件事不做（见“不做”）。
  `POST /api/group-relay/target-groups/:chatId/invite` 重新取邀请链接。
- 看板 `TargetGroupCreatePanel`：群名（必填）、关键词（可不填）、“只许群主发言”（默认勾）；成功后显示邀请链接和口令（可复制），
  并提示可在源群表单里把它选为目标群。

## M：目标群成员管理

- bridge `chat_members.py`（全部先 `require_owner`）：`POST /chat-members/list {chat_id}` → `PullChatChattersV2` 翻页取全，
  名字用已有的 `self.name_cache.resolve(chat_id, user_ids, self.client.get_user_name)`，返回 `[{user_id, name, joined_at}]`；
  `POST /chat-members/history {chat_id, cursor}` → `PullChatJoinLeaveHistory`，返回 `[{user_id, name, action, event, at}]` 和下一页游标；
  `POST /chat-members/applications {chat_id}` → `PullChatApplications`（`type: JOIN_CHAT`）；
  `POST /chat-members/applications/decide {id, agree}` → `ProcessChatApplication`；
  `POST /chat-members/remove {chat_id, user_id}` → `DeleteChatChatter`（不能移除本账号自己）。
- adapter `target-members.mjs`：`GET /api/group-relay/target-groups/:chatId/members | /history | /applications`，
  `POST …/applications/:id {agree}`，`DELETE …/members/:userId`。`:chatId` 是目标群编号，经 `targetRegistry.get` 换成数字群 ID；
  没有数字群 ID 或 `owned` 为假的目标群回 400 并说明原因。
- 看板 `TargetMembersPanel`：对选中的目标群显示三个页签（成员、进退群记录、入群申请）；移除和拒绝前用 `ElMessageBox.confirm`。

## E：会员期限与到期提醒（`relay-auto-membership`，M 合并后做）

用户的要求：目标群里各会员的购买日期不同；新会员进群时登记开始日期（可手动改）和期限（1 个月、3 个月等）；每天自动审查，
到期的由群里的 webhook @ 本人，提醒联系群主续费。不自动移除。

- bridge：不新增模块，成员和入群时间取自 M 的 `POST /chat-members/list`。
- adapter `memberships.mjs`：
  - 表 `feishu_group_relay_memberships(target_chat_id text, user_id text, display_name text not null, started_on date not null,
    term_months integer not null, expires_on date not null, note text, reminded_on date, left_at timestamptz, created_at, updated_at,
    primary key (target_chat_id, user_id))` 和 `feishu_group_relay_membership_settings(target_chat_id text primary key,
    enabled boolean not null, default_term_months integer not null, remind_text text not null, last_review_on date)`。
  - `sync(chatId)`：向 bridge 取成员；新成员建一行，开始日期是入群那天（上海日期），期限取该群的默认期限；已不在群里的写 `left_at`；
    重新进群的清掉 `left_at`、保留原有日期。本账号自己不建行（成员列表里没有机器人）。
  - 到期日 = 开始日期加 `term_months` 个日历月（月底对齐：1 月 31 日加 1 个月是 2 月最后一天）。改开始日期、期限或备注时重算到期日并清空
    `reminded_on`（续费就是改这两项）。
  - `review(chatId, { dryRun })`：先 `sync`，再取 `expires_on <= 今天`、`left_at` 为空、`reminded_on` 为空的成员；每条消息最多 @ 20 人，
    文字是 `<关键词> <at user_id="成员ID">名字</at>… <提醒文字>`，用 `webhookTargets.sendText` 发；发成功才把这些人的 `reminded_on` 写成今天，
    发失败不写，下次审查再试。`dryRun` 只返回名单不发。每人只提醒一次，除非续费后再次到期，或在看板上点“再提醒一次”。
  - `start()`：每分钟看一次时间，上海时间 09:00 之后且某个启用的目标群 `last_review_on` 不是今天，就对它 `review` 并写 `last_review_on`
    （adapter 在 09:00 时没在运行的话，启动后补做当天这一次）。
  - 路由：`GET /api/group-relay/target-groups/:chatId/memberships`（每行带状态：正常、7 天内到期、已到期、已离群）；
    `PUT …/memberships/:userId {started_on, term_months, note}`；`GET/PUT …/membership-settings {enabled, default_term_months, remind_text}`；
    `POST …/memberships/review {dry_run}`；`POST …/memberships/:userId/remind`。目标群没有数字群 ID 或群主不是本账号时回 400。
  - 默认设置：未启用；默认期限 1 个月；提醒文字“会员已到期，请联系群主续费”。
- 看板 `MembershipPanel`：选中目标群的开关、默认期限、提醒文字；成员表（成员、开始日期、期限、到期日、状态、上次提醒、备注）可编辑
  （期限给 1、3、6、12 个月的快捷项，也可填别的月数）；按钮“同步成员”“预览今天要提醒谁”“立即审查”“再提醒一次”。
- 测试至少覆盖：月底和闰年的到期日；新成员按入群日和默认期限登记；改期限后清空已提醒；审查只取到期、在群、未提醒的；
  发送失败不写 `reminded_on`；超过 20 人分成多条；未启用的群不审查；09:00 之前不审查、当天审查过不再审查。
- 没验证的：被 @ 的成员属于别的租户时 @ 是否生效（对外的同步群成员都是这种）。第一次真实提醒就是验收；@ 不生效时消息里仍有名字。

## U：加急告警

- bridge `urgent_alerts.py`：`POST /alerts/recipients` → 可选的接收会话（形态以 `WEB_GATEWAY.md` 的 P0-6 结果为准）；
  `POST /alerts/urgent {chat_id, user_id, text}`：核对 `user_id` 是该会话里除本账号外唯一的成员 → 发文字 → 对这条消息
  `PutUrgent`（`urgentType: APP`）→ `{message_id, urgent: true}`。
  发文字**不走** `Bridge.send()`（它只认 `LARKX_SEND_CHAT_IDS` 里的会话，也不返回消息编号）：本模块自己用
  `larkx.proto.builders.build_send_message_packet` 和 `bridge.client._gateway_post` 发，前后调 `bridge.send_gate.begin / finish`，
  频率限制和审计因此照用；消息编号的取法按 `WEB_GATEWAY.md` 的 P0-6 结果。另设每小时最多 6 次加急的上限，超过时抛
  `SendRateLimited`（`do_POST` 已把它映射成 429）。
- adapter `urgent-alerts.mjs`：表 `feishu_group_relay_alert_settings(setting_key text primary key, value jsonb not null)` 存接收人
  和开关；`notify(key, text)`：开着且有接收人就调 bridge。`index.mjs` 的 `notifyOps`（第 109 行）在原有的发运维群之后调用它；
  bridge 未登录时发不了加急，属于预期，只记日志。路由：`GET/PUT /api/group-relay/alerts/settings`、
  `GET /api/group-relay/alerts/recipients`、`POST /api/group-relay/alerts/test`。
- 看板 `UrgentAlertsPanel`：选接收人、总开关、“发一条测试加急”。

## D：撤回同步与送达核对

- bridge `target_observer.py`：`POST /targets/observe {chats: [{chat_id, bot_ids: [...]}]}` 设定要观察的目标群（只存内存，整表替换）；
  `tap(bridge, chat_id, message)`（调用点见“接缝”）：群在观察名单里且 `from_id` 属于该群的 `bot_ids` 时，把消息的
  `{chat_id, message_id, position, create_time, from_id, msg_type, text}` 发到 `…/internal/larkagentx/target-observed`
  （由 `LARKX_INGRESS_URL` 换路径得到，不新增环境变量；带 `x-larkagentx-token`）。别人发的消息不发。
  `POST /messages/recall-in-group {chat_id, message_id}`：`require_owner` → `DeleteGroupMessage`。
- adapter 一侧由 `delivery-check.mjs` 的 `start()` 每 60 秒把观察名单（`targetRegistry.list()` 里有数字群 ID 和 `bot_id` 的目标群）
  发给 bridge，并订阅 `targetRegistry.onChange` 在变化时立即发一次；bridge 重启后名单最多 60 秒内恢复。
- adapter `delivery-check.mjs`：表 `feishu_group_relay_target_deliveries(delivery_id bigserial primary key, source_message_id text not null,
  target_chat_id text not null, fingerprint text not null, sent_at timestamptz not null, target_message_id text, observed_at timestamptz,
  recalled_at timestamptz, recall_error text)`。
  1. `recordWebhookDelivery({ messageId, targetChatId, msgType, content })`（调用点由 base 接好）收到的是**最终发出**的类型和内容
     （卡片在带关键词时已被改成文字或富文本，关键词已加上）。本模块的 `outboundText(msgType, content)` 把它变成纯文字；
     指纹 = 该文字去掉关键词和全部空白后的前 80 个字的 sha256。从群里观察到的消息用同一个规范化算指纹。
     哪些形态两边的文字对得上，以 P0-8 的样本为准，写在 `WEB_GATEWAY.md` 里；对不上的形态不建发送记录（显示为“不核对”），不猜。
  2. 内部入口 `/internal/larkagentx/target-observed`（校验 `x-larkagentx-token`）：按目标群取 120 秒内未核对的发送记录，指纹相同且
     **恰好一条**时写入 `target_message_id`、`observed_at`；零条或多条都不写（宁可“未核对”，不猜）。
  3. `group-relay.mjs` 第 1007–1010 行源头撤回的分支，在 `updateRelaySourceMessage` 之后调用注入的 `recallDeliveries(sourceMessageId)`：
     对已核对、未撤回、目标群 `owned` 的记录调 bridge 撤回，成功写 `recalled_at`，失败写 `recall_error`，不重试。
  4. 路由 `GET /api/group-relay/target-groups/:chatId/deliveries`（近 24 小时计数和最近 50 条）。
- 看板 `DeliveryPanel`：选中目标群的计数（已核对、未核对、已撤回、撤回失败）和明细表。
- P0-5 如果显示群主撤不了机器人的消息：D 只做送达核对，撤回部分拿掉。

## 验收命令（每条线在自己的工作区里跑，合并后在集成分支上再跑）

```
npm install --prefix feishu-relay/adapter --no-audit --no-fund
node --test feishu-relay/adapter/*.test.mjs
python3 -m unittest discover -s feishu-relay/bridge -p 'test_*.py' -q
(cd feishu-relay/dashboard && npm ci && npm run typecheck && npm test -- --run && npm run build)
git diff --check integration/relay-auto...HEAD
```

本机没有 larkx：导入 `bridge.py` 的测试在本机会被跳过（现有测试就是这样）。所以规则的测试写在功能模块自己的测试文件里，
不导入 `bridge.py`；`bridge.py` 里的接线（`do_POST` 查表、给 D 的钩子）只有 edge 上的套件能测到，由 Claude 在每条线合并前跑
`bash feishu-relay/scripts/edge/test-bridge-on-edge.sh`，这是 bridge 改动的验收门。实现者在报告里写明本机跳过了哪些测试。

每个功能模块要有会失败的测试，至少覆盖：
- bridge：不是群主时拒绝且不发任何写命令；J 的非法链接不调用网关；U 的接收人不是会话里唯一的对方时被拒、超过每小时上限被拒；
  D 的 `tap` 只对观察名单里的群、且发送者是登记的机器人时发副本，其余情况不发，发送失败不抛出。
- adapter：R 的哈希对不上时不写表、响应里没有地址；C 中途失败时返回走到的步骤；M 对没有数字群 ID 或非群主的目标群回 400；
  D 指纹零条或多条匹配时不写、源头撤回只撤已核对的、撤回失败不重试；U 关着或没有接收人时不调用 bridge。
- 看板：每个面板的组合函数各有一个测试，断言发出的请求（方法、路径、请求体）和失败时不改变状态。

Claude 在评审时另跑：`bash feishu-relay/scripts/edge/test-bridge-on-edge.sh`、`python3 scripts/verify_component_boundaries.py --check`、
`/Users/papa/.venvs/svc/bin/python -m pytest scripts -q`。

## 风险

- 这些是网页端内部接口，会随飞书改版变；每个 bridge 模块的报错要带上服务器给的原因，方便看板上看出是哪条命令失效。
- 写操作用的是个人账号：只在自己是群主的群里做；加急每小时 6 次封顶；移除成员、处理申请、入群都只由人在看板上点，不做自动规则。
- D 只是旁路：观察中的目标群不进监听名单，bridge 只多发一份机器人消息的副本，原有的转发、汇总群处理都不变，
  不会把目标群当源群再转一遍。
- 发布时 adapter 和 bridge 重启，WebSocket 断约 15 秒（与平时发布相同）。
- 并行工作区的冲突靠接缝避免：共享文件只由 base 线改，U 和 D 需要的调用点 base 预先接好。

## 不做

入群申请的自动通过规则、成员到期自动移除（E 只提醒）、到期前的预提醒、二维码图片的生成与识别、短信和电话加急、webhook 目标群的编辑同步、
往源群写任何东西、解散群、同名群防重复创建、把旧的“Webhook 目标与关键词”表迁走、环境变量配置的退役。

## 审查记录

第一轮（Codex，只读，2026-10-13，线程 01a1342e）7 条，全部采纳：
1. high：U、D 仍要改 `adapter/index.mjs` → 调用点和内部入口改由 base 预先接好，功能线不再碰共享文件。
2. high：`Bridge.send()` 拒绝名单外会话、不返回消息编号 → U 自己发文字，直接用 `send_gate` 记账。
3. high：分发函数一律回 400，与“回 429”矛盾 → 429 只在 bridge 一侧，adapter 统一回 400 并带原因。
4. medium：`initialSection`、`loadActiveSection` 没改 → 加入 base 的清单。
5. medium：D 的指纹不是最终发出的文字 → 调用点传最终类型和内容，规范化两边一致，并用 P0-8 的样本确认。
6. medium：导入 `bridge.py` 的测试本机被跳过 → 规则测试不导入 `bridge.py`，接线由 edge 套件把关。
7. medium：R 筛掉了群主不是本账号的目标群 → 全部入表，用 `owned` 区分。

第二轮（同一线程）4 条，全部采纳：
1. high：布尔钩子拦不住目标群里别人的消息进普通通道 → 改成旁路 `tap`，观察中的群不进监听名单，原处理一律不变。
2. medium：钩子的位置晚于 XHS 分支 → 调用点提到处理的最前面；没有 `from_id` 的消息本来就对不上机器人。
3. medium：记录发送失败会让已成功的 webhook 被当成失败而重发 → 两处调用不等待、不外传失败。
4. medium：没有把观察名单发给 bridge 的时机 → `start()` 定时发、`targetRegistry.onChange` 变化时发，都由 base 接好。

第三轮（同一线程，最后一轮）1 条，采纳：
1. medium：C 新建的目标群不经过那两个分支，观察名单不会立即更新 → 通知改由 `targetRegistry` 自己的写方法发出（`record` / `forget` / `resolve`）。
   这一条改动没有再送审（审查最多三轮）。

r5（2026-10-13）加了功能 E 和它在 base 线里的三处接缝（`memberships.mjs` 空壳、`MembershipPanel` 空壳、`webhookTargets.sendText`）。
E 这一节另做审查，结论记在这里。
