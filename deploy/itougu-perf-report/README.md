# 爱投顾内参 操作与收益 盘后日报/周报（远端 47 部署）

`scripts/itougu_perf_report.py` 在收盘后把内参公布的模拟操作算成账：买卖 FIFO 配对成闭环交易、
按收盘价给未平仓个股打浮动，然后发一条日报（每交易日 15:30）或周报（每周五 16:00）到飞书。

与 `itougu-neican`（实时轮询转发）的分工：轮询只看 `appendContent/list` 第一页、有新内容就转发一条；
本报告用 H5 的游标协议（`appendContentId` + `orderId`）翻完整个可见追加流，只负责统计。
两者共用 `/opt/itougu-neican/itougu_auth.json` 与 relay 里的飞书发送逻辑，**本报告不改动、也不重装
正在运行的 relay**。

## 口径与失败姿态

- 时间与交易日一律 `Asia/Shanghai`；报告按"截至报告日"切片，回补历史日期不会把之后的操作算进去。
- 行情缺失、行情日期与报告日期不符 → 输出「行情缺失」，**不用昨收或成本价顶替**。
- 当日上证指数无当日行情（非交易日/行情未更新）→ 整篇跳过，不发空报告。
- 卖出若找不到可见的建仓记录（建仓早于订阅可见窗口），单独列出并**排除在收益统计之外**。
- 上游只保留最近一段追加内容，因此每次跑完把流水并进 `ITOUGU_PERF_STATE_FILE` 的 ledger，
  窗口滚掉也不会丢失已配对的建仓价。
- 同一天/同一周重复触发由 ledger 里的 `sent` 记录去重；`--force` 可显式重发。

## 自动化部署

```bash
# 干跑（只打印计划，不改远端）
scripts/deploy-itougu-perf-report.sh <git-sha-or-tag>
# 实际部署
scripts/deploy-itougu-perf-report.sh <git-sha-or-tag> --apply
```

脚本会：跑本地单测 → 拒绝未提交/未推送的发布路径 → 校验 `origin/main` 就是该 sha →
在 47 上从 codeload 流式取该 commit、只落地报告脚本与 unit → 校验已部署 relay 的辅助函数齐全 →
首次部署时用 `/etc/itougu-neican.env` 里的飞书凭据生成 `/etc/itougu-perf-report.env`(0600) →
**用真实凭据跑一次 `--dry-run` 冒烟（不发消息）** → 通过后才 `daemon-reload` 并 `enable --now` 两个 timer。

覆盖默认值的环境变量：`ITOUGU_EDGE_HOST`、`ITOUGU_EDGE_SSH_KEY`、`ITOUGU_EDGE_ROOT`、
`ITOUGU_EDGE_GITHUB_REPOSITORY`、`ITOUGU_EDGE_GITHUB_BRANCH`。

## 配置（`/etc/itougu-perf-report.env`, 0600）

见 `itougu-perf-report.env.example`。当前约定是尾盘掘金收益日报/周报发送到分析师发送汇总群，不发送到尾盘掘金专属群；目标群用
`ITOUGU_PERF_TARGETS=<businessProductId>=<chat_id>[,<chat_id>][;<productId>=<chat_id>]` 配置；
没有配任何目标群时脚本只打印不发送（fail closed）。

## 运维

```bash
systemctl list-timers --all 'itougu-perf-*'          # 下次触发时间
systemctl start itougu-perf-daily.service            # 手动补一次今天的日报
journalctl -u itougu-perf-daily.service -n 50        # 看上一次结果
# 手动预览（不发消息），例如指定日期与产品
python3 /opt/itougu-neican/itougu_perf_report.py --weekly --date 2026-09-04 \
  --product 1661993558510538753 --dry-run
```

回滚：`systemctl disable --now itougu-perf-daily.timer itougu-perf-weekly.timer`；
脚本与 unit 都是幂等安装，重跑部署脚本指向旧 sha 即可还原。
