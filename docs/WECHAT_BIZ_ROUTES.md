# 公众号路由注册

`config/wechat-biz-routes.json` 是公众号监听的唯一显式路由表。未出现在
`accounts` 中的账号使用 `default`；当前默认是 `hold`，只记录
`skipped_no_route` 并推进游标，不会自动生成 `#quanneng`。

## 分析师标签

```json
{
  "gh_example": {
    "sinks": [{ "kind": "analyst", "tag": "anqiang" }]
  }
}
```

## Webhook / n8n / 飞书机器人

Webhook 会收到完整 JSON，并附带 `x-source-message-id` 幂等头；可将它接到
n8n 的邮件节点、飞书机器人或其他内部入口。敏感请求头只从环境变量读取：

```json
{
  "gh_example": {
    "sinks": [{
      "kind": "webhook",
      "url": "https://example.invalid/webhook/wechat-biz",
      "headers_env": { "authorization": "WECHAT_BIZ_WEBHOOK_AUTH" }
    }]
  }
}
```

若希望监听器直接使用飞书机器人发送到群聊，可注册 `feishu` sink。监听器
从 n8n `.env` 读取 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 获取 tenant token，
不会把凭据写入路由文件或日志；`chat_id` 应使用 OAuth 查到的 `oc_...` 群 ID：

```json
{
  "gh_example": {
    "sinks": [{
      "kind": "feishu",
      "chat_id": "oc_0123456789abcdef",
      "name": "公众号同步群"
    }]
  }
}
```

同一账号可以配置多个 `feishu` sink；每条消息会以飞书 `post` 发送标题和
全文，各 sink 使用独立幂等 UUID。发送失败会阻止该消息推进游标，以便下次
重试。

## SMTP 邮件

```json
{
  "gh_example": {
    "sinks": [{
      "kind": "email",
      "to": ["research@example.com"],
      "subject": "公众号转发"
    }]
  }
}
```

邮件需要在 supervisor 环境中设置 `WECHAT_BIZ_SMTP_HOST`，以及可选的
`WECHAT_BIZ_SMTP_PORT`、`WECHAT_BIZ_SMTP_USER`、`WECHAT_BIZ_SMTP_PASSWORD`、
`WECHAT_BIZ_SMTP_FROM` 和 `WECHAT_BIZ_SMTP_STARTTLS`。密码不会写入路由文件或日志。

配置修改后重启 `wechat-biz-relay`，或重启统一 supervisor；没有明确配置的账号不会投递到任何分析师。
