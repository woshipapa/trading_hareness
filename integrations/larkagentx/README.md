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

它默认不会接管官方机器人发送、卡片、图片或文件流程。LarkAgentX 当前只验证了个人账号文本发送，因此桥接服务要求监听会话和发送会话都显式配置白名单。适配器接受消息后才写入现有 ledger；桥接到适配器之间的短暂网络故障最多自动重试三次，仍失败时只记录错误，不会伪造已接收状态。

## 本机试运行

先在隔离目录安装已经拉取的上游项目并完成个人账号登录：

```bash
cd /private/tmp/larkagentx-audit
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
export LARKX_HOME=/Users/papa/.larkx
lark auth qr
lark auth check
```

然后在 `/Users/papa/codebase/n8n` 目录运行桥接服务。令牌通过交互式环境注入，不能提交到仓库：

```bash
export PYTHONPATH=/private/tmp/larkagentx-audit
export LARKX_HOME=/Users/papa/.larkx
export LARKX_BRIDGE_TOKEN='从安全运行环境注入'
export LARKX_INGRESS_URL='http://127.0.0.1:5680/internal/larkagentx/inbound'
export LARKX_LISTEN_CHAT_IDS='oc_...'
export LARKX_SEND_CHAT_IDS='oc_...'
python3 integrations/larkagentx/bridge.py
```

发送文本需要携带相同的桥接令牌：

```bash
curl -X POST http://127.0.0.1:8090/send \
  -H 'content-type: application/json' \
  -H 'x-larkagentx-token: 从安全运行环境注入' \
  -d '{"chat_id":"oc_...","text":"测试文本"}'
```

只有 `LARKX_LISTEN_CHAT_IDS` 和 `LARKX_SEND_CHAT_IDS` 中的会话会被处理。个人账号发出的回显消息会被桥接层丢弃，避免自动回复回环。

监听消息正文需要以当前 `source-registry.json` 中已注册的路由标签开头，例如 `#liwei`；这是现有 n8n 导入链的必要路由条件。
