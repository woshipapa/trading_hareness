# 爱投顾内参 → 飞书 轮询（远端 47 部署）

盘中(A股 9:30-11:30 / 13:00-15:00, 周一~周五)每 15s 直连 itougu API 拉两个内参的追加内容，
有新增就发飞书「公众号同步群」。与本机专表监听(itougu-table-watch)重叠无妨——飞书按
appendContentId 的 uuid 幂等去重，不会重复。

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
