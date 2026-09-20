# LarkAgentX 后续执行优化

## 当前执行方案

47 上由 LarkAgentX 通过 WebSocket 监听已核验的 8 个数字源群，bridge 只对白名单 chat ID 放行，并把事件交给 `feishu-adapter`。源群的官方 `message.list` 轮询保持关闭，文本消息直接进入现有 relay ledger、过滤和目标群 fan-out。

非文本消息由实时事件触发旧链路补读：

```text
LarkAgentX WebSocket
        -> bridge
        -> feishu-adapter
        -> 必要时用户 OAuth message.list 按官方群、时间、类型及内容特征补读
        -> 原有图片/文件/卡片解析、资源处理和 webhook/API 发送
```

可直读的文本、完整卡片、独立图片及 post 富文本图片走直接路径；其他类型按文末的身份修复规则补读。读取失败返回错误，不能将 HTTP 接收成功等同于目标发送成功。消息级去重继续由现有 relay ledger 负责。

## post 富文本图片的 WebSocket 解密

对新野人哥会员群和书房的猫等真实历史 post 做了 LarkAgentX 私有网关回读。`PostContent.richText.imageIds` 只是编辑器元素 ID，真正的 CDN key 和解密材料位于图片元素的 `RichTextElement.property` 不透明 protobuf 中，结构中可稳定找到 `img_v3_...`、32 字节 AES key 和 12 字节 nonce。使用这三项从 CDN 下载后，AES-256-GCM 已在多条真实图片上解出 PNG 文件头并通过哈希校验。

47 上已加入 `integrations/larkagentx/larkagentx_image_property.py` 的受限 wire-format 提取器。bridge 在 `json_safe` 丢弃二进制属性之前完成解析，并将 `image_id`、`key_hex`、`iv_hex` 和富文本元素 ID 通过受保护的本地入口交给适配器。适配器按原有资源流程解密、上传生成 webhook 所需的 `image_key`，再发送 post；因此 `post` 图片不再需要 OAuth `message.list`。

解密器只在 key、IV、CDN 下载和 AES-GCM 校验全部成功时走直接路径。属性结构变化、密钥缺失、CDN 错误或校验失败会保留精确 OAuth 补读，不会把 `[图片]` 占位符当成成功消息。

## 本次重复发送修复

研习社公开文章同时投递到汇总群和尾盘掘金内参更新群时，尾盘群 webhook 因缺少机器人关键词返回 `19024 Key Words Not Found`。旧逻辑把部分成功的群发整体判为失败，下一轮重试便会再次投递已经成功的汇总群。已在 47 的受限环境补齐尾盘群关键词，重启 `itougu-neican` 后状态中的 `pending` 已清空，连续观察窗口内汇总群不再出现重复消息。

以后新增或更换 webhook 时，必须为每个开启关键词校验的机器人配置对应的 `chat_id=关键词`；部分 fan-out 的成功与失败也要分别记录，不能只用一个全局成功标记。

## Webhook ID 与 keyword 持久化契约

47 上 group relay 的生产映射已经写入持久化运行环境 `/etc/feishu-relay-edge/runtime.env`，adapter 每次由 compose 重启都会重新读取同一份配置。当前 4 个 webhook 目标与关键词必须保持如下对应：

| 目标 chat_id | keyword | 来源路由 |
| --- | --- | --- |
| `oc_392b9a177d3539c35b8fc090d890ff0c` | `anqiang` | `#anqiang` 安强训练营 |
| `oc_523b7e9e29854acba64272a948cb8eda` | `汇总` | `#quanneng`、尾盘掘金、`#cat` |
| `oc_606d21da56853a386b121561c6839b6f` | `liuzi` | `#liuzi` 六边形 |
| `oc_903e514c3e9d6d91323b081dd3439131` | `liwei` | `#liwei` 立伟 |

Itougu 轮询服务使用独立的持久化环境 `/etc/itougu-neican.env`，不能与 group relay 的变量混用。当前擒龙和尾盘产品的实际目标与关键词为：

| 产品目标 chat_id | keyword | Itougu 目标 |
| --- | --- | --- |
| `oc_523b7e9e29854acba64272a948cb8eda` | `汇总` | 共享汇总群 |
| `oc_844b3ba7c44c1d3cdb43184b2630fad2` | `尾盘掘金` | 尾盘掘金专属群 |
| `oc_cf156f51d085e2c51bd66fda198b88a0` | `擒龙内参` | 擒龙内参专属群 |

验收不能只看环境文件是否存在：`GET /api/group-relay/status` 的 `webhook_config` 必须同时满足 `all_webhook_keywords_loaded=true`、`missing_keyword_chat_ids=[]` 和 `orphan_keyword_chat_ids=[]`；Itougu 则同时检查配置文件与运行进程的 webhook/keyword ID 集合。关键词值本身不写入日志中的 URL，也不把凭证暴露到仓库。

## 图片解密 PR 评估

[LarkAgentX PR #18](https://github.com/cv-cat/LarkAgentX/pull/18) 增加了 AES-256-GCM 图片解密、CDN 下载、文件类型识别、本地缓存和 MCP `download_image` 工具。这部分能力有后续移植价值，但不能直接合并到当前运行版本。当前已按现有协议完成兼容移植，PR 本身没有直接 cherry-pick。

原因如下：

1. PR 修改的是旧版 `builder/proto.py`、`app/api/lark_client.py` 和旧服务层；当前版本使用 `larkx/proto/decoders.py`、`larkx/client.py` 和 `larkx/media.py`，直接合并会覆盖当前 WebSocket、认证和协议实现。
2. PR 按旧版 `Content.imageKey` 和手写嵌套 protobuf 解析图片；当前版本的图片数据已经由 `ImageContent.imageV2.crypto.cipher.key/iv` 暴露，解析入口不同。
3. PR 的结果主要保存到 `~/.lark/msg/images/`，尚未接入当前 bridge 的消息传输、relay ledger、资源归档和目标群 fan-out，因此不能直接启用。
4. PR 的解密读取了登录 cookie，并通过 CDN 下载图片；兼容移植仍必须保持凭证不出日志、不进消息 payload，并验证缓存、大小限制、原子写入和失败重试。

当前已部署的兼容实现：bridge 从当前 protobuf 的 `imageV2.crypto.cipher` 提取图片 ID、AES key 和 IV，通过受令牌保护的 `/resource` 接口使用登录会话下载加密载荷；适配器在现有 relay 资源处理前完成 AES-256-GCM 解密。JPEG、PNG、WebP、GIF 等已解密图片继续走原有媒体上传和目标群 fan-out，无法直接识别或解密时仍回退到官方 OAuth 补读。当前也已接入 LarkAgentX `CARD/INTERACTIVE` 的 `jsonCard` 与 `openCardContent`，完整 Card 2.0 会直接进入现有卡片解析和 Webhook fan-out，不再因缺少 `message.list` 补读结果而丢失；卡片内图片若只有跨租户资源 Key，仍按媒体权限走资源补读。凭证只存在 47 上的受限环境和会话文件中。

## 后续优化顺序

### P1：先完成当前旧链路验收

- 等待并记录真实外部成员的 `TEXT`、`IMAGE`、`FILE`、`CARD` 事件各一条。
- 分别验证 LarkAgentX WebSocket 接收、官方补读、relay ledger、目标 webhook/API 回执和重复事件去重。
- 继续保持源群官方轮询关闭；只在补读失败或断线恢复时使用有限历史补偿。

### P2：补强当前 protobuf 的图片解密模块

- 在当前 `larkx` 版本中从 `content_data.imageV2` 提取 `imageKey`、AES key 和 IV，不采用 PR 的旧 `Content` 解析器。
- 已实现 AES-256-GCM 解密、CDN 下载、类型识别和最大字节数限制；继续补充缓存校验与原子写入。
- 使用临时文件、校验成功后原子改名，避免进程中断留下伪缓存。
- 为 JPEG、PNG、WebP、无效 key/IV、过大文件、CDN 401/403 和重复下载增加离线测试。
- 已补充 post 富文本 `RichTextElement.property` 的嵌套 key/IV 提取和真实 PNG 解密验证；后续继续覆盖多图、图文混排、`richTextWithMd` 和协议字段变更。

### P3：完善现有转发链路验收

- bridge 已提供受限的图片结果传输接口，禁止把 cookie、key、IV 和原始凭证写入消息 payload 或日志。
- 适配器已把解密后的图片交给现有 `group-relay` 资源处理和 webhook/API fan-out。
- 保留 `message_id` 幂等键、源群标签、目标群回执和失败重试；图片解密成功不能代替发送回执。
- 只有在 P1/P2/P3 的真实群消息验收通过后，才评估减少图片类 OAuth 补读。

## 暂不采用的方案

不直接 cherry-pick 或部署 PR #18，不替换 47 上当前已验证的 `larkx` WebSocket 客户端。PR 的独立图片解密思路已兼容移植；post 富文本则额外从 `RichTextElement.property` 提取密钥并走同一资源桥。对于缺少完整参数的消息，仍保留 OAuth 精确补读作为安全兜底。

## 2026-09-16 P0/P1 借鉴增强

本轮将 `feishu-user-plugin` 的事件日志/消费游标和
`feishu-message-2API` 的 Profile 隔离思路落到 bridge，而不是继续依赖进程内
重试：

- `integrations/larkagentx/event_spool.py` 使用本地 SQLite 保存规范化事件，
  入站 HTTP 之前先落盘；事件有递增 sequence、连续 drain cursor、
  `queued/processing/delivered/failed` 状态、lease、有限重试和启动后回放。
  单条 payload 上限 512 KiB，目录/文件权限为 0700/0600，不保存 Cookie 或
  原始 protobuf frame。
- `integrations/larkagentx/owner_lock.py` 为每个 Profile 提供 Unix exclusive
  owner lock，同一凭证目录只能有一个 bridge 持有个人 WebSocket；第二实例
  fail-closed，不会形成两个消费者。
- `LARKX_PROFILE` 将 credentials、event spool 和 owner lock 分到独立目录；
  health 公开 profile、owner、pending/failed、回放次数和最近错误，仍不输出
  token、Cookie 或事件原文。
- WebSocket 仍由当前 LarkAgentX 主客户端负责，官方 OAuth 缺口补读仍是 P1
  恢复路径；spool 回放只重投已经成功解码的事件，最终重复判断仍由 adapter
  relay ledger 完成。

新增测试覆盖 SQLite 事件幂等、lease/replay、payload 上限、Profile 路径隔离
和 owner 排他锁；hotfix overlay 的 AST/import/file gate 也会检查新模块，避免
只上传 `bridge.py` 而漏掉依赖。

## 2026-09-16 执行记录

本轮按“协议兼容 → 消息隔离 → 内容 fail-closed → 恢复 → 发布验证”的顺序执行；每一步均独立提交：

| Commit | 变更 | 验证 |
| --- | --- | --- |
| `dbe57aa` | 抽出 `proto_wire.py`，增加未知字段、group、wire mismatch 的 telemetry | protobuf 回放通过 |
| `1d9dffc` | 同一 push frame 内隔离坏 entity，保留后续有效消息 | partial-entry 测试通过 |
| `486d2cf` | 未知正文类型 fail-closed，转入官方补读而非转发占位文本 | ingress 10 项通过 |
| `29a8208` | 删除 bridge 内重复的旧 parser，保持单一协议入口 | Python 6 项通过 |
| `d5c004a` | hotfix overlay 打包、远端 import 和 active 文件校验加入 `proto_wire.py` | dry-run、adapter 145 项通过 |
| `fc9eac4` | WebSocket 重连后触发 600 秒幂等缺口补读 | recovery 测试通过 |
| `a662b33` | 进程首次启动也执行有限历史补读 | edge 实测 `completed`, `sent=0`, `failed=0` |
| `21fa57f` | 增加随机扩展字段、嵌套 group 和边界 fuzz 回放 | 6 项协议测试通过 |
| `eb5bad7` | 容错 parser 只恢复部分 entity 时自动触发官方补读 | recovery 10 项通过 |

当前 edge 最后一次 overlay 发布为 `hotfix-20260916T090503Z-eb5bad76c1df-dirty-2690`；bridge WebSocket 已连接，`decode_error_count=0`，`decode_fallback_count=0`，启动补读已完成，补读窗口为 600 秒。`dirty` 只表示工作树仍有其他未纳入本轮的历史改动，不代表本轮 LarkAgentX 提交未记录。
# 2026-09-14 OAuth 补读身份修复

补读路由必须读取 `relayRoutes()` 返回的 `chatId`，不能读数据库字段名 `chat_id` 后回落到 WebSocket 数字群 ID。WebSocket 数字消息 ID 与官方 `om_` ID 不做相等比较。

补读使用官方群、源消息时间前后 2 秒、类型、正文或资源 key 的一致性确定唯一候选；候选冲突、分页不完整、时间缺失或只有占位符时拒绝猜测。未匹配时额外等待 1.5 秒和 3.5 秒重读，权限/API 错误直接报告。

官方消息 ID 保留在 `message_id`，供官方资源下载及已有 OAuth 账本去重；WebSocket ID 单独保存在消息 JSON 的 `larkagentx_origin` 中。该字段用于追踪，不要求两套 ID 相等。这里的延迟重读不是持久化补偿队列。

远端实测：猫群 `7685299326167305463`、`7685299525346561247` 两条历史 WebSocket 载荷均按原始正文、秒级时间成功找到不同 `om_` ID 的 OAuth 原消息。测试仅验证补读，没有重发历史消息。相关匹配/转发测试 52 项通过，包括官方资源 ID 和后续 OAuth 轮询去重。
