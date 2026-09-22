# 爱投顾内参 → 飞书 轮询（远端 47 部署）

盘中(A股 9:30-11:30 / 13:00-15:00, 周一~周五)每 15s 直连 itougu API 拉两个内参的追加内容，
有新增就发飞书「公众号同步群」；尾盘掘金内参同时发「尾盘掘金内参更新1」。与本机专表监听(itougu-table-watch)重叠无妨——飞书按
appendContentId 的 uuid 幂等去重，不会重复。

轮询服务会把不含凭据的运行心跳写入 `/var/lib/itougu-neican/status.json`，由 47 上的
feishu-adapter 只读挂载并显示在群监听监控页中。状态包括服务心跳、轮询频率、最近一轮、发送/失败计数、两个产品的目标群，以及每个 webhook 的关键词加载情况；不会写入 webhook URL 或 Itougu Authorization。

监控页的「强制刷新一次」按钮通过 adapter 的 `/api/group-relay/itougu/refresh` 调用轮询进程的本机回环控制端口 `18084`。它复用定时轮询的完整流程（增量内参、公开圈子补读、去重和 webhook fan-out），不会启动第二个进程；定时轮询正在执行时返回 busy，避免重复发送。控制端口只监听 `127.0.0.1`，不直接暴露到 Tailnet。

两个产品的专属文字出口会在正文首尾各加一行「认真一手咸鱼店铺：餐厅焦糖味的momo，其他都是二手转发。」；图片出口只在图片内显示一次「咸鱼店铺：餐厅焦糖味的momo」；公众号同步群仍接收未修改的原文。
专属出口还会显示形如 `【动态水印 W1-xxxxxxxxxxxxxxxx】` 的消息级编号；同一条消息在不同专属群编号不同，长消息的每个分片都会带编号。编号由本机/edge 的 0600 HMAC 密钥生成，不记录密钥本身。
尾盘掘金内参和擒龙内参的无外链专属出口使用飞书 Card JSON 2.0（`interactive`）；外链消息使用原生 `post` 以保证可点击；共享公众号同步群仍使用原 `post` 格式。
专属卡片将不含外链的完整正文（包括推荐股票信息）渲染为带平铺指纹的 PNG 图片（单次店铺水印 + `momo` + 每消息动态水印）；Card JSON 只保留图片 key，不暴露原始正文。含 `http(s)`/`www.` 外链的午盘/晚盘消息改用飞书原生 `post`，URL 以显式链接元素发送，可直接点击。图片渲染或上传失败时图片消息会失败并等待重试，不降级为可被 OAuth 直接读取的纯文本。

测试边界：只验证公众号同步群时，显式设置 `ITOUGU_CHAT_IDS` 为该群，并清空
`ITOUGU_JUEJIN_CHAT_IDS`、`ITOUGU_ARTICLE_CHAT_IDS`；不要把测试流量导向尾盘掘金内参更新1。
也可以为测试进程设置 `ITOUGU_TEST_CHAT_IDS`；该变量会覆盖所有产品专用出口，测试结束后必须清空。
生产环境仍可按产品配置专用群。网络拉取和群 fan-out 使用有界并发，单个来源失败不会阻塞其它来源。

## 部署步骤（在 47 上，root）
1. mkdir -p /opt/itougu-neican
2. 安装图片正文渲染依赖：`apt-get update && apt-get install -y --no-install-recommends python3-pil fonts-noto-cjk fonts-noto-color-emoji`
3. 拷贝：feishu-relay/scripts/sources/itougu/itougu_neican_relay.py → /opt/itougu-neican/
        本地 itougu_auth.json → /opt/itougu-neican/itougu_auth.json  (chmod 600)
4. cp itougu-neican.env.example /etc/itougu-neican.env  (填 FEISHU_APP_ID/SECRET, chmod 600)
5. cp itougu-neican.service /etc/systemd/system/
6. 首次去重基线（不发历史）：
   ITOUGU_AUTH_FILE=/opt/itougu-neican/itougu_auth.json ITOUGU_STATE_FILE=/var/lib/itougu-neican/state.json ITOUGU_STATUS_FILE=/var/lib/itougu-neican/status.json \
   FEISHU_APP_ID=... FEISHU_APP_SECRET=... python3 /opt/itougu-neican/itougu_neican_relay.py --bootstrap
7. systemctl daemon-reload && systemctl enable --now itougu-neican
8. 验证：systemctl status itougu-neican;  journalctl -u itougu-neican -f
