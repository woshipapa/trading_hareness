# 飞书中继：目标群管理、撤回同步、加急告警、链接入群（relay-group-automation）

- 版本：r1，2026-10-13，基于 main `9d75b5d9`；工作线 W010
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

base 合进集成分支后，其余五条从集成分支拉出并行做，各自只改自己的文件（见“接缝”），按 join → create → members →
urgent → recall 的顺序 `--no-ff` 合回集成分支，每合一条在集成分支上跑一遍全部验收命令。五条都合完、双重评审无未决的
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

## 接缝（base 线先做，之后各线互不碰对方的文件）

base 线建好下面的空壳和接线；空壳是给并行工作区留的位置，不是多余代码，评审时不按“死代码”处理。

**bridge**
- `bridge/group_admin.py`：`class GroupAdminError(Exception)`；`require_owner(chat_info, user_id)`（群主不是本账号就抛）。
- `bridge/feature_routes.py`：`FEATURE_ROUTES: dict[str, Callable[[Bridge, dict], dict]]`，由六个功能模块各自的 `ROUTES` 合并而成。
  功能模块：`target_registry.py`（R，base 实现）、`join_link.py`、`target_groups.py`、`chat_members.py`、`urgent_alerts.py`、
  `target_observer.py`（后五个 base 只建 `ROUTES = {}`）。
- `bridge/bridge.py` 的 `do_POST`：在返回 404 之前查 `FEATURE_ROUTES`；`except (GroupLookupError, HistoryRangeError, ChatBotError)`
  里加上 `GroupAdminError`（同样回 422）。

**adapter**
- `adapter/ledger.mjs`：返回对象里加 `query: (text, params) => pool.query(text, params)`，功能模块自己建表、自己写 SQL。
- `adapter/feature-routes.mjs`：功能模块的约定是 `createXxx(deps)` 返回 `{ schema: string[], routes: Route[], ... }`，
  `Route = { method, path: string | RegExp, handle({ params, query, body }) }`；本文件提供匹配和分发（读 JSON 体、写 JSON、
  异常回 400）。`readJsonBody` 现在在 `index.mjs` 里，作为依赖传入。
- `adapter/index.mjs`：创建六个功能对象（`target-registry.mjs` 由 base 实现；`join-link.mjs`、`target-groups.mjs`、
  `target-members.mjs`、`urgent-alerts.mjs`、`delivery-check.mjs` 先是返回空 `schema`/`routes` 的空壳），启动时依次执行它们的
  `schema`，在 `createServer` 回调里、现有的 `/api/group-relay/targets` 分支之前调用分发函数。
- 两个 Dockerfile（`adapter/Dockerfile`、`deploy/edge/Dockerfile.adapter`）的 COPY 清单一次加齐七个新文件。

**看板**
- 新增一级栏目 `targets`（“目标群”）：`App.vue` 的菜单、标题、视图切换；`useFeishuDashboardWorkspace.ts` 的栏目白名单
  （第 86 行）和 `DashboardSection` 类型；`adapter/frontend-assets.mjs` 的 `FEISHU_ROUTES` 加 `/targets`。
- `views/TargetGroupsView.vue` 依次放五个面板组件，每个面板有自己的 SFC、组合函数和 API 模块，base 只实现第一个，其余是空壳：
  `TargetRegistryPanel`（R）、`TargetGroupCreatePanel`（C）、`TargetMembersPanel`（M）、`DeliveryPanel`（D）、`UrgentAlertsPanel`（U）。
  `JoinByLinkPanel`（J）放进 `App.vue` 的新增源群对话框，位置在“源群”表单项上方，base 接好。
- 面板之间共用“当前选中的目标群”：`TargetGroupsView` 用 `provide` 给出 `selectedTarget`（一个 ref），R 面板的表格行点击设置它。

## R：现有目标群纳入看板（base 线）

- bridge `target_registry.py`，`POST /targets/resolve {webhook_hashes: [sha256 十六进制]}`：取账号所在的群
  （`pull_joined_groups(self.client.feed_page)`），用 `self.client.chat_infos` 留下群主是本账号的；对每个群 `self.client.chat_bots`，
  对其中的自定义机器人调 `chatbot_api("GetWebhookBotInfo")`，算 webhook 的 sha256。返回
  `{groups: [{chat_id, name, bots: [{bot_id, name, key_words, webhook_hash}]}]}`。地址本身不出 bridge。
- adapter `target-registry.mjs`：表 `feishu_group_relay_target_groups(chat_id text primary key, websocket_chat_id text, chat_name text,
  bot_id text, bot_name text, owned boolean not null, resolved_at timestamptz not null)`，`chat_id` 是目标群编号（`oc_…` 或 `wh_…`）。
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

## U：加急告警

- bridge `urgent_alerts.py`：`POST /alerts/recipients` → 可选的接收会话（形态以 `WEB_GATEWAY.md` 的 P0-6 结果为准）；
  `POST /alerts/urgent {chat_id, user_id, text}`：核对 `user_id` 是该会话里除本账号外唯一的成员 → 用已有的发送通道发文字
  （`self.send_gate` 的频率限制和审计照用；这类会话不要求在 `LARKX_SEND_CHAT_IDS` 里）→ 对返回的消息 `PutUrgent`
  （`urgentType: APP`）→ `{message_id, urgent: true}`。另设每小时最多 6 次加急的上限，超过回 429。
- adapter `urgent-alerts.mjs`：表 `feishu_group_relay_alert_settings(setting_key text primary key, value jsonb not null)` 存接收人
  和开关；`notify(key, text)`：开着且有接收人就调 bridge。`index.mjs` 的 `notifyOps`（第 109 行）在原有的发运维群之后调用它；
  bridge 未登录时发不了加急，属于预期，只记日志。路由：`GET/PUT /api/group-relay/alerts/settings`、
  `GET /api/group-relay/alerts/recipients`、`POST /api/group-relay/alerts/test`。
- 看板 `UrgentAlertsPanel`：选接收人、总开关、“发一条测试加急”。

## D：撤回同步与送达核对

- bridge `target_observer.py`：`POST /targets/observe {chats: [{chat_id, bot_ids: [...]}]}` 设定要观察的目标群（存内存；adapter 启动时
  和目标群变化时下发）。`bridge.py`：观察中的群加入监听集合；`ingress_url_for` 增加第三条去向——观察中的群的消息只发到
  `…/internal/larkagentx/target-observed`（由 `LARKX_INGRESS_URL` 换路径得到，不新增环境变量），且只转发 `from_id` 属于该群
  `bot_ids` 的消息；这些群的消息不进普通转发通道。`POST /messages/recall-in-group {chat_id, message_id}`：`require_owner` →
  `DeleteGroupMessage`。
- adapter `delivery-check.mjs`：表 `feishu_group_relay_target_deliveries(delivery_id bigserial primary key, source_message_id text not null,
  target_chat_id text not null, fingerprint text not null, sent_at timestamptz not null, target_message_id text, observed_at timestamptz,
  recalled_at timestamptz, recall_error text)`。
  1. `group-relay.mjs` 的 `sendMessage` 在 webhook 发送成功后调用注入的 `recordWebhookDelivery({ sourceMessageId, targetChatId, text })`；
     指纹 = 发出文字去掉关键词和空白后的前 80 个字的 sha256。
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

每个功能模块要有会失败的测试，至少覆盖：
- bridge：不是群主时拒绝且不发任何写命令；J 的非法链接不调用网关；U 的接收人不是会话里唯一的对方时被拒、超过每小时上限被拒；
  D 只转发机器人发的消息、别人的消息不转发。
- adapter：R 的哈希对不上时不写表、响应里没有地址；C 中途失败时返回走到的步骤；M 对没有数字群 ID 或非群主的目标群回 400；
  D 指纹零条或多条匹配时不写、源头撤回只撤已核对的、撤回失败不重试；U 关着或没有接收人时不调用 bridge。
- 看板：每个面板的组合函数各有一个测试，断言发出的请求（方法、路径、请求体）和失败时不改变状态。

Claude 在评审时另跑：`bash feishu-relay/scripts/edge/test-bridge-on-edge.sh`、`python3 scripts/verify_component_boundaries.py --check`、
`/Users/papa/.venvs/svc/bin/python -m pytest scripts -q`。

## 风险

- 这些是网页端内部接口，会随飞书改版变；每个 bridge 模块的报错要带上服务器给的原因，方便看板上看出是哪条命令失效。
- 写操作用的是个人账号：只在自己是群主的群里做；加急每小时 6 次封顶；移除成员、处理申请、入群都只由人在看板上点，不做自动规则。
- D 让 bridge 多监听目标群：这些群的消息量就是转发量本身；只转发机器人发的消息，不进普通转发通道，避免把目标群当源群再转一遍。
- 发布时 adapter 和 bridge 重启，WebSocket 断约 15 秒（与平时发布相同）。
- 并行工作区的冲突靠接缝避免；D 和 U 各改一处共享文件（`bridge.py` 的去向函数和 `group-relay.mjs`、`index.mjs` 的 `notifyOps`），
  所以排在合并顺序的最后两位。

## 不做

入群申请的自动通过规则、成员到期自动移除、二维码图片的生成与识别、短信和电话加急、webhook 目标群的编辑同步、
往源群写任何东西、解散群、同名群防重复创建、把旧的“Webhook 目标与关键词”表迁走、环境变量配置的退役。
