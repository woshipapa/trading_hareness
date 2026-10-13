# 0019 飞书转发：看板登记的来源标签和目标群 webhook 存在中继自己的库里

- 状态：生效（2026-10-13）；实现见 `feishu-relay/adapter/source-tags.mjs`、`webhook-targets.mjs`
- 日期：2026-10-13

## 背景

新增一个源群有两处要人改服务器或仓库：

- 来源标签要写进 `feishu-relay/config/source-registry.json` 并发一次 edge 热更新，否则汇总群里带这个标签的消息被当成
  未登记标签忽略，进不了下游研究数据。
- 转发到一个从没用过的目标群，要先把该群自定义机器人的 webhook 地址和关键词写进 edge 的环境变量
  `FEISHU_GROUP_RELAY_WEBHOOKS`、`FEISHU_GROUP_RELAY_WEBHOOK_KEYWORDS`，再重启 adapter。

用户 2026-10-12 的要求是这类事只在看板里操作；2026-10-13 明确把这两处也算进去并授权（原话见
`docs/work/streams/W009-feishu-relay-new-tag-and-target.md`）。

## 决定

1. **来源标签有两个来源。** 仓库的登记表声明随版本发布的标签；看板建源群时用到的新标签写进
   `ingestion_source_profiles`，`config.origin = 'dashboard'`，立即生效，重启后从库里读回。两边都有的标签以登记表为准，
   看板不能开关登记表声明的标签。新标签的 `publisher_key` 和 `remote_analyst_id` 等于标签本身。
2. **新源群默认进下游。** 建路由的请求带 `ingest`，不带时按 `true`；编辑路由时只有请求里写了 `ingest` 才改。
   这是用户 2026-10-11 的规则（「默认是要加入的」）。`false` 用于只转发的群。
3. **看板配置的目标群 webhook 加密后存库。** 表 `feishu_group_relay_webhook_targets`；地址用 AES-256-GCM 加密，密钥由
   飞书应用密钥派生（和 OAuth 令牌同一做法，派生串不同）。接口、状态和日志只给群名、关键词和编号，不给地址。
   这是 `config/secrets/` 之外的第二个凭据存放处，只收这一类：由用户在看板里填、由中继自己用的 webhook 地址。
4. **保存前先验证。** 只接受飞书自定义机器人的地址（`open.feishu.cn` 或 `open.larksuite.com` 的 `/open-apis/bot/v2/hook/…`），
   并用它发一条带关键词的测试消息；发不出去就不保存。
5. **环境变量里的目标群不变。** 同一个编号两边都有时用环境变量的；看板不能改、不能删它们。
6. **不自动在飞书群里建机器人。** 开放平台没有这个接口，bridge 的私有协议里只有读取群机器人的消息；用个人账号调
   未公开的写接口有封号风险。这一步由用户在飞书里做。

## 后果

- 看板配置的目标群没有官方 `oc_` 编号，用 `wh_` 加 24 位十六进制作编号。它只能走 webhook：文字、富文本和卡片能发，
  文件类消息发不了，会按失败记录并写明原因。
- 换了飞书应用密钥后，库里的地址解不开：启动时记一条错误，看板把这些目标群标成“需重新填写”，不影响其他目标群。
- 仓库里的登记表不再是标签的完整清单；完整清单看 `/api/group-relay/status` 的 `source_tags`。
- 新标签登记后，消息会建成中继库里的下游任务；owner 那边是否要先有这个分析师编号没有核对（见 W009 的 Open questions）。

## 怎样保证

- `feishu-relay/adapter/source-tags.test.mjs`：登记表声明的标签不被看板覆盖或开关；库里没有 `dashboard` 来源的旧行不算登记。
- `feishu-relay/adapter/webhook-targets.test.mjs`：非飞书地址不发请求；测试消息被拒不入库；入库的是密文、对外的形状里
  没有地址；环境变量的目标群不被库里的同号行替换，也不能从看板改删；还有源群在用的目标群不能删。
