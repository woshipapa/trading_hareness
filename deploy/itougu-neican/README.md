# 爱投顾内参 → 飞书 轮询（远端 47 部署）

盘中(A股 9:30-11:30 / 13:00-15:00, 周一~周五)每 15s 直连 itougu API 拉两个内参的追加内容，
有新增就发飞书「公众号同步群」；尾盘掘金内参同时发「尾盘掘金内参更新1」。与本机专表监听(itougu-table-watch)重叠无妨——飞书按
appendContentId 的 uuid 幂等去重，不会重复。

两个产品的专属出口会在正文首尾各加一行「认真一手咸鱼店铺：餐厅焦糖味的momo，其他都是二手转发。」；公众号同步群仍接收未修改的原文。
专属出口还会显示形如 `【动态水印 W1-xxxxxxxxxxxxxxxx】` 的消息级编号；同一条消息在不同专属群编号不同，长消息的每个分片都会带编号。编号由本机/edge 的 0600 HMAC 密钥生成，不记录密钥本身。
尾盘掘金内参和擒龙内参的专属出口使用飞书 Card JSON 2.0（`interactive`）；共享公众号同步群仍使用原 `post` 格式。

测试边界：只验证公众号同步群时，显式设置 `ITOUGU_CHAT_IDS` 为该群，并清空
`ITOUGU_JUEJIN_CHAT_IDS`、`ITOUGU_ARTICLE_CHAT_IDS`；不要把测试流量导向尾盘掘金内参更新1。
也可以为测试进程设置 `ITOUGU_TEST_CHAT_IDS`；该变量会覆盖所有产品专用出口，测试结束后必须清空。
生产环境仍可按产品配置专用群。网络拉取和群 fan-out 使用有界并发，单个来源失败不会阻塞其它来源。

## 部署步骤（在 47 上，root）
1. mkdir -p /opt/itougu-neican
2. 拷贝：scripts/itougu_neican_relay.py → /opt/itougu-neican/
        本地 itougu_auth.json → /opt/itougu-neican/itougu_auth.json  (chmod 600)
3. cp itougu-neican.env.example /etc/itougu-neican.env  (填 FEISHU_APP_ID/SECRET, chmod 600)
4. cp itougu-neican.service /etc/systemd/system/
5. 首次去重基线（不发历史）：
   ITOUGU_AUTH_FILE=/opt/itougu-neican/itougu_auth.json ITOUGU_STATE_FILE=/var/lib/itougu-neican/state.json \
   FEISHU_APP_ID=... FEISHU_APP_SECRET=... python3 /opt/itougu-neican/itougu_neican_relay.py --bootstrap
6. systemctl daemon-reload && systemctl enable --now itougu-neican
7. 验证：systemctl status itougu-neican;  journalctl -u itougu-neican -f
