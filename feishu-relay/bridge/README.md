# LarkAgentX 实验桥

这个桥接层基于已拉取的 LarkAgentX（当前审计 commit `2cb4f90`）把个人会话接入当前 `feishu-adapter`：

```text
LarkAgentX 私有 WS
        | 文本消息
        v
feishu-adapter /internal/larkagentx/inbound
        | ledger 幂等、路由、n8n 投递
        v
现有研究导入链

HTTP /send
        |
        v
LarkAgentX 私有网关发送文本
```

它不替换目标群的 webhook 发送，而是把个人会话的实时接收交给适配器。当前已验证文本、完整卡片、独立图片，以及 post 富文本图片的私有 protobuf 解密链路。适配器接受消息后才写入现有 ledger；桥接到适配器之间的短暂网络故障最多自动重试三次，仍失败时只记录错误，不会伪造已接收状态。

## 本机试运行

先在隔离目录安装已经拉取的上游项目并完成个人账号登录：

```bash
cd /private/tmp/larkagentx-audit
export LARKX_HOME=/Users/papa/.larkx
/Users/papa/.venvs/svc/bin/python -m pip install -e .
/Users/papa/.venvs/svc/bin/lark auth qr
/Users/papa/.venvs/svc/bin/lark auth check
```

然后在 `/Users/papa/codebase/n8n` 目录运行桥接服务。令牌通过交互式环境注入，不能提交到仓库：

```bash
export PYTHONPATH=/private/tmp/larkagentx-audit
export LARKX_HOME=/Users/papa/.larkx
export LARKX_BRIDGE_TOKEN='从安全运行环境注入'
export LARKX_INGRESS_URL='http://127.0.0.1:5680/internal/larkagentx/inbound'
export LARKX_LISTEN_CHAT_IDS='oc_...'
export LARKX_SEND_CHAT_IDS='oc_...'
/Users/papa/.venvs/svc/bin/python feishu-relay/bridge/bridge.py
```

群实时转发部署在 47 时，`bridge.py` 只负责 LarkAgentX WebSocket 到本地适配器的实时入口；它会对 `LARKX_LISTEN_CHAT_IDS` 做白名单过滤。适配器继续复用现有 relay ledger、过滤和目标群 fan-out。路由格式是 `personal_web_chat_id=source_key`，例如：

```text
LARKX_GROUP_RELAY_ENABLED=true
LARKX_GROUP_RELAY_ROUTES=7661209668907207659=anqiang;7667390477875858612=liwei
```

前端新增源群时可以启用动态发现：适配器会提供受桥接 token 保护的路由目录，bridge
在收到尚未配置数字群 ID 的 WebSocket 事件时，按群名做唯一匹配并把该消息带上 source
key 交给适配器。这样新增源群无需手工改 `LARKX_GROUP_RELAY_ROUTES`；首次消息完成绑定，
同名群会 fail closed。启用参数为 `LARKX_DYNAMIC_ROUTE_DISCOVERY=true` 和
`LARKX_ROUTE_CATALOG_URL=http://127.0.0.1:18300/internal/larkagentx/routes`。

动态绑定成功后，数字 `chat_id` 会原子写入当前 Profile 的
`route-bindings.json`（可用 `LARKX_ROUTE_BINDINGS_FILE` 覆盖路径），下次重启会先恢复
该绑定再建立 WebSocket。这样首条消息不会再承担“发现并绑定”的职责。health 同时提供
`persisted_route_bindings`、`ignored_by_chat` 和 `position_stats`；未知群事件按 chat_id
持久化计数，WebSocket position 跳号会记录缺口范围和累计缺口数。

适配器入口为 `/internal/larkagentx/group-relay`，由 `x-larkagentx-token` 保护。文本、完整卡片、独立图片和带有完整解密参数的 post 图片直接进入 relay；bridge 在 WebSocket 事件进入 JSON 前解析 `RichTextElement.property` 中的 `img_v3` key、32 字节 AES key 和 12 字节 nonce。适配器通过 LarkAgentX cookie 下载密文，在本地完成 AES-256-GCM 解密；webhook 目标可直接复用源 `image_key`，从而绕过飞书图片上传额度。汇总群也可以通过 `LARKX_SUMMARY_CHAT_IDS` 和 `LARKX_SUMMARY_INGRESS_URL` 进入同一 WebSocket 事件链。edge 正常运行时关闭官方历史轮询和启动缺口补读：`FEISHU_GROUP_RELAY_ENABLED=false`、`FEISHU_SUMMARY_LISTENER_ENABLED=false`、`LARKX_GAP_REPAIR_ENABLED=false`。缺少媒体解密参数或尚未实现的文件类型会记录为不支持，不能假定官方 OAuth 补读仍然可用。systemd 环境中应使用统一 supervisor venv 的 `/opt/supervisor/.venv/bin/python`，凭证放在受限权限的 `LARKX_HOME`，不要提交到仓库。

仅在显式设置 `LARKX_OFFICIAL_FALLBACK_ENABLED=true` 时才允许恢复完整旧的官方补读路径；正常 edge 运行保持关闭。若某些 CARD/INTERACTIVE 的 WebSocket protobuf 只有 `[卡片]` 占位而没有正文，可单独设置 `LARKX_CARD_BACKFILL_ENABLED=true`，只对这类不完整卡片做一次官方补读，不会恢复群轮询。

发送文本需要携带相同的桥接令牌：

```bash
curl -X POST http://127.0.0.1:8090/send \
  -H 'content-type: application/json' \
  -H 'x-larkagentx-token: 从安全运行环境注入' \
  -d '{"chat_id":"oc_...","text":"测试文本"}'
```

只有 `LARKX_LISTEN_CHAT_IDS` 和 `LARKX_SEND_CHAT_IDS` 中的会话会被处理。个人账号发出的回显消息会被桥接层丢弃，避免自动回复回环。

bridge 在投递到 adapter 之前会把规范化事件写入 `LARKX_EVENT_SPOOL_DB`（SQLite，目录 0700、文件 0600），并使用递增 sequence、连续 drain cursor、lease、失败重试和启动后回放处理进程崩溃或 adapter 暂时不可用。事件 payload 有 512 KiB 上限，不保存 Cookie 或原始 protobuf frame；adapter 仍以 `message_id`/relay ledger 负责最终幂等。`LARKX_PROFILE` 会隔离凭证、spool 和 owner lock，适合在同一主机上运行不同会话，但每个 profile 仍只允许一个 WebSocket bridge。

bridge 同时维护一个脱敏的 `LARKX_HISTORY_DB`（默认与 spool 同目录的
`history.db`），用于导出已经收到的 WebSocket 消息和后续增量。它只保存规范化
JSON，不保存 Cookie、原始 protobuf frame 或图片 AES key/IV。接口同样由桥接令牌
保护：

```bash
# 导出指定 WebSocket 群今天的 JSONL
curl -o cat-history.jsonl \
  'http://127.0.0.1:8090/history/export?chat_id=7684122107030031634&from_time=<epoch-seconds>' \
  -H 'x-larkagentx-token: 从安全运行环境注入'

# 以返回文件中的 x-larkagentx-next-sequence 作为下一次增量游标
curl -o cat-history-increment.jsonl \
  'http://127.0.0.1:8090/history/export?chat_id=7684122107030031634&after_sequence=<sequence>' \
  -H 'x-larkagentx-token: 从安全运行环境注入'
```

网页端的“群消息转发状态”面板会为每个已监听的 WebSocket 群提供导出按钮，
支持最近 1 天、7 天和 30 天。浏览器通过 adapter 同源接口下载 JSONL，bridge
令牌不会下发到浏览器；导出内容只包含该群已经写入 `history.db` 的规范化事件。

WebSocket 是实时、至少一次接收通道，不能回放登录前或断线期间没有收到的群历史；
要补齐这部分历史，仍需要一次可用的官方历史接口或外部导入。历史接口额度耗尽时，
导出范围就是 bridge 已经持久化的事件，之后的新消息会继续按 sequence 追加。

bridge 启动时取得 `LARKX_OWNER_LOCK_PATH` 的 Unix exclusive lock；第二个实例会 fail closed，不会形成两个个人 WebSocket 消费者。health 会公开 profile、owner lock、spool pending/failed、回放次数和最近错误，但不会输出 Cookie、token 或事件原文。

监听消息正文需要以当前 `source-registry.json` 中已注册的路由标签开头，例如 `#liwei`；这是现有 n8n 导入链的必要路由条件。

47 上的小范围修复统一通过仓库根目录的
`feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --apply` 发布。它会把 adapter、两套前端、
路由表和本目录中的 bridge Python 文件放进同一个版本化覆盖层；adapter
容器复用原有 image，bridge 复用 `/opt/supervisor/.venv`，两者都不触发
image 构建或依赖安装。systemd 只固定执行
`/opt/larkagentx/bridge-entrypoint.sh`，入口根据覆盖层的 `current` 原子
指针选择代码，登录 cookie、token 和 `LARKX_HOME` 不随源码上传。需要回退
时使用 `feishu-relay/scripts/edge/hotfix-feishu-relay-edge.sh --rollback <release-id> --apply`；
只有 Node、Python 依赖或基础运行时变化才走正式运行时发布。

bridge health 在刚重启且尚未收到下一条消息时可能显示
`websocket.state=connecting`；这是上游客户端没有 on-open 回调的状态语义，
服务进程、健康接口和后续消息接收仍分别由发布检查与运行时统计核验。
