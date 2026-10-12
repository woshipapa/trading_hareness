# 0014 feishu-relay 的发布不受 owner 发布时间窗口限制

- 状态：生效
- 日期：2026-10-12

## 背景

禁止重启的时间窗（`config/release-windows.json`）是为 owner 的量化服务定的：盘前
检查、交易时段和盘后调度期间重启 owner 会中断采集和计算。edge 的 adapter 当时
也被列进了两个交易时段窗口，于是 feishu-relay 的每次发布都要等到 15:10 之后，
或者由操作者逐次批准 `RELEASE_WINDOW_OVERRIDE`。

feishu-relay（edge adapter 和它的 LarkAgentX bridge）与 owner 的量化服务是两个
独立的发布单元：不同主机、不同镜像、各自的源码覆盖层和回滚。它的故障和修复
常常就发生在交易时段——群消息正是那时最多，2026-10-12 一天之内就有四次修复
需要盘中上线。

## 决定

feishu-relay 的发布不受 owner 发布时间窗口限制，任何时间都可以发布。

- `edge-adapter` 从 `morning-session` 和 `afternoon-session` 两个窗口里移除，改登记
  在 `config/release-windows.json` 的 `unrestricted` 里。
- feishu-relay 的两个发布脚本（`hotfix-feishu-relay-edge.sh`、
  `deploy-feishu-relay-edge-release.sh`）仍然先调用
  `scripts/release_window.py check edge-adapter`。检查器认得这个对象，并且总是放行。
  调用保留下来，是为了以后要给 edge 单独设窗口时只改数据、不改脚本。
- owner 的三个对象（`owner-quant-research`、`owner-scheduler`、`owner-windows-api`）
  的窗口不变。同时检查 edge 和 owner 对象时，owner 的窗口照常生效。

## 后果

- 盘中发布 feishu-relay 会重启 adapter 容器和 bridge。实测 bridge 的 WebSocket 约
  15 秒后重连，期间的群消息由 bridge 的事件队列和缺口补读恢复，没有观察到丢失。
- adapter 同时为行情控制台提供静态资源并代理 owner 的只读研究接口，所以盘中
  发布会让控制台的请求失败几秒。这是这条决定接受的代价；owner 自身不受影响。
- `RELEASE_WINDOW_OVERRIDE` 不再用于 feishu-relay 的发布。
- 本记录收窄了 0003 里“所有会重启服务的发布脚本都先检查时间窗”一句的效果：
  脚本仍然检查，但 edge 没有窗口。0003 关于 owner 的内容不变。

## 怎样保证

- `scripts/test_release_window.py`：`edge-adapter` 在每个 owner 被拒绝的时刻都放行；
  它和 owner 对象一起检查时 owner 的窗口仍然拒绝；没有对象同时出现在窗口和
  `unrestricted` 里；两个 feishu-relay 发布脚本仍然调用检查器。
- `scripts/test_release_cli.py`：`release plan` 对 edge 的改动报告“allowed now”，对
  owner 的改动在交易时段报告“not now”。
