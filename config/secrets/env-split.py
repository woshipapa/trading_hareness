#!/usr/bin/env python3
"""从 .env.local 生成 .env.owner 和 .env.edge。

设计原则：
- .env.local 是凭据的**唯一真相源**（single source of truth）
- .env.owner / .env.edge 是从它派生的子集
- 有些键在远端是不同前缀（owner compose 用 PEER_*），映射写在本脚本
- 有些键只在远端有意义（PG连接、运行时调参），不从 .env.local 抽取，
  而是在 EXTRA 块里直接声明默认值（敏感值仍从 .env.local 映射）

用法：
  python3 config/secrets/env-split.py [--dry-run]
  # 生成 config/secrets/.env.owner 和 config/secrets/.env.edge
"""
from __future__ import annotations
import os, sys, pathlib, re
from datetime import datetime

SECRETS_DIR = pathlib.Path(__file__).resolve().parent
LOCAL_ENV = SECRETS_DIR / ".env.local"

def parse_env(path: pathlib.Path) -> dict[str, str]:
    """读 .env 文件，返回 {KEY: 整行(含注释)}。"""
    kv: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)", stripped)
        if m:
            kv[m.group(1)] = m.group(2)
    return kv

def get(local: dict, key: str, fallback: str = "") -> str:
    return local.get(key, fallback)

# ═══════════════════════════════════════════════════════════════════════
# Owner 环境（shared-peer compose 用 PEER_* 前缀）
# ═══════════════════════════════════════════════════════════════════════
def build_owner(local: dict) -> str:
    lines = [
        f"# .env.owner — 47owner (shared-peer) 凭据",
        f"# 由 env-split.py 从 .env.local 生成于 {datetime.now():%Y-%m-%d %H:%M}",
        f"# 推远端: bash config/secrets/sync-secrets.sh owner",
        "",
        "# ── SSH 隧道 ──",
        f"PEER_SSH_HOST={get(local, 'LONGHU_SSH_HOST')}",
        f"PEER_SSH_PORT={get(local, 'LONGHU_SSH_PORT', '3535')}",
        f"PEER_SSH_USER={get(local, 'LONGHU_SSH_USER', 'stockpeer')}",
        f"PEER_SSH_KEY_PATH={get(local, 'LONGHU_SSH_KEY_PATH')}",
        f"PEER_KNOWN_HOSTS_PATH=~/.ssh/known_hosts",
        "",
        "# ── 数据库 ──",
        f"PEER_DB_PASSWORD={get(local, 'POSTGRES_PASSWORD')}",
        f"PEER_DB_USER=stock_peer",
        f"PEER_QUANT_DATABASE=trading_hareness",
        "",
        "# ── 量化服务鉴权 ──",
        f"QUANT_SHARED_READ_API_KEY={get(local, 'QUANT_SHARED_READ_API_KEY')}",
        f"PEER_QUANT_WRITE_API_KEY={get(local, 'QUANT_WRITE_API_KEY')}",
        "",
        "# ── 热更新 ──",
        f"QUANT_HOTFIX_ENABLED={get(local, 'QUANT_HOTFIX_ENABLED', 'true')}",
        f"QUANT_HOTFIX_HOST_DIR={get(local, 'QUANT_HOTFIX_HOST_DIR', '/opt/quant/hotfix')}",
        "",
        "# ── 运行时 ──",
        f"PEER_BACKGROUND_TASKS_ENABLED=false",
        f"PEER_REQUIRE_OWNER_SEMANTICS=true",
        f"PEER_OWNER_CONTRACT_MODE=strict",
        "",
        "# ── 远端端口（本机 ssh -L 映射用）──",
        f"REMOTE_DB_PORT=15432",
        f"REMOTE_BATCH_DB_PORT=15433",
        f"REMOTE_API_PORT=15681",
        f"PEER_QUANT_PORT=15682",
        "",
        "# ── 发布元数据（deploy-code-only.sh 运行时填充）──",
        f"PEER_APP_GIT_SHA=unknown",
        f"PEER_APP_RELEASE=unknown",
        f"PEER_APP_BUILD_CREATED_AT=unknown",
    ]
    return "\n".join(lines) + "\n"

# ═══════════════════════════════════════════════════════════════════════
# Edge 环境（feishu-relay + xhs-intel compose，用原名）
# ═══════════════════════════════════════════════════════════════════════
def build_edge(local: dict) -> str:
    # 直接从 local 透传的键（值相同）
    PASSTHROUGH = [
        # 飞书
        "FEISHU_APP_ID", "FEISHU_APP_SECRET",
        "FEISHU_ALERT_RECEIVE_ID", "FEISHU_ALERT_RECEIVE_ID_TYPE",
        "FEISHU_GROUP_RELAY_ENABLED", "FEISHU_SUMMARY_LISTENER_ENABLED",
        # 微信中继
        "RELAY_TOKEN",
        "WECHAT_BIZ_FEISHU_WEBHOOKS",
        # 百度网盘
        "BAIDU_PAN_APP_KEY", "BAIDU_PAN_SECRET_KEY", "BAIDU_PAN_REDIRECT_URI",
        "BAIDU_PAN_ROOT_PATH", "BAIDU_PAN_ENABLED", "BAIDU_PAN_ARCHIVE_PROVIDER",
        "BAIDU_PAN_MARKET_ARCHIVE_ENABLED", "BAIDU_PAN_MARKET_ARCHIVE_INTERVAL_SECONDS",
        "BAIDU_PAN_MARKET_ARCHIVE_ROOT_PATH",
        "BAIDU_PAN_RAW_OVERFLOW_ENABLED", "BAIDU_PAN_RAW_OVERFLOW_ROOT_PATH",
        "BAIDU_PAN_RAW_OVERFLOW_CAPABILITIES",
        # 量化服务鉴权
        "QUANT_WRITE_API_KEY", "QUANT_ALERT_WEBHOOK_TOKEN",
        # Paper KB
        "PAPER_KB_FEEDBACK_WEBHOOK", "PAPER_KB_FEISHU_CHAT_ID",
        "PAPER_KB_INGEST_WEBHOOK", "PAPER_KB_SEARCH_WEBHOOK",
        # XHS
        "XHS_COLLECTOR_TOKEN",
        # 远端归档
        "REMOTE_ANALYST_ARCHIVE_BASE_URL",
        # 数据源
        "TUSHARE_TOKEN", "TUSHARE_SUPER_TOKEN",
        "TUSHARE_SUPER_GET_API_KEY", "TUSHARE_SUPER_REALTIME_API_KEY",
        "TUSHARE_SUPER_REALTIME_FALLBACK_API_KEY", "TUSHARE_BACKUP_API_KEY",
        "TUSHARE_API_URL", "TUSHARE_BACKUP_API_URL",
        "TUSHARE_SUPER_API_URL", "TUSHARE_SUPER_GET_API_URL",
        "TUSHARE_SUPER_REALTIME_API_URL",
        "TUSHARE_SUPER_GET_PROXY_URL", "TUSHARE_SUPER_REALTIME_PROXY_URL",
        "TUSHARE_SUPER_PROXY_URL",
        "TUSHARE_SUPER_GET_MODE",
        "TUSHARE_SUPER_GET_REQUESTS_PER_MINUTE",
        "TUSHARE_SUPER_GET_MIN_INTERVAL_SECONDS",
        "TUSHARE_SUPER_REALTIME_REQUESTS_PER_MINUTE",
        "FUYAO_API_KEY",
        # 自动化开关
        "DAILY_SUMMARY_AUTOMATION_ENABLED", "DAILY_SUMMARY_FEISHU_ENABLED",
        "STRATEGY_REVIEW_AUTOMATION_ENABLED",
        "POST_CLOSE_STRATEGY_AUTOMATION_ENABLED",
        "POST_CLOSE_PUBLIC_ARCHIVE_ENABLED",
        "PUBLIC_EVIDENCE_CAPTURE_ENABLED", "MARKET_EVENT_CAPTURE_ENABLED",
        "TEN_DAY_LEADER_ROTATION_AUTOMATION_ENABLED",
        "ALL_BOARD_MEMBER_BACKFILL_ENABLED",
        "THS_CONCEPT_MEMBER_BACKFILL_ENABLED",
        "AUCTION_PULSE_ENABLED", "AUCTION_PULSE_INTERVAL_SECONDS",
        "AUCTION_PULSE_FEISHU_COOLDOWN_SECONDS",
        # 盘中
        "INTRADAY_SCAN_INTERVAL_SECONDS",
        "INTRADAY_SUPER_GET_FAST_INTERVAL_SECONDS",
        "INTRADAY_SUPER_GET_FAST_MAX_IN_FLIGHT",
        "INTRADAY_ORDER_BOOK_ENABLED",
        "INTRADAY_BOARD_CURVE_ENABLED",
        "INTRADAY_MINUTE_PROFILE_CAPTURE_ENABLED",
        "INTRADAY_BOARD_CURVE_RETENTION_DAYS",
        "INTRADAY_BOARD_ROTATION_RETENTION_DAYS",
        "INTRADAY_EPHEMERAL_SIGNAL_RETENTION_DAYS",
        "INTRADAY_FAST_QUOTE_RETENTION_DAYS",
        "INTRADAY_MINUTE_PROFILE_RETENTION_DAYS",
        "INTRADAY_RULE_INPUT_RETENTION_DAYS",
        "INGESTION_LEDGER_RETENTION_DAYS",
        "EDGE_CHANGE_JOURNAL_RETENTION_DAYS",
    ]

    lines = [
        f"# .env.edge — 47edge (feishu-relay + xhs-intel) 凭据",
        f"# 由 env-split.py 从 .env.local 生成于 {datetime.now():%Y-%m-%d %H:%M}",
        f"# 推远端: bash config/secrets/sync-secrets.sh edge",
        "",
    ]

    for key in PASSTHROUGH:
        val = local.get(key, "")
        lines.append(f"{key}={val}")

    lines += [
        "",
        "# ── Edge 专有（不在 .env.local 里的）──",
        "RELAY_PGUSER=relay",
        f"RELAY_PGPASSWORD={get(local, 'POSTGRES_PASSWORD')}",
        "RELAY_PGDATABASE=feishu_relay",
        "",
        "# ── Edge 数据库（quant-service on edge）──",
        f"PGHOST=quant-postgres",
        f"PGPORT=5432",
        f"PGUSER=quant",
        f"PGPASSWORD={get(local, 'POSTGRES_PASSWORD')}",
        f"PGDATABASE=quant_edge",
        "",
        "# ── 运行时调参（edge 专有值）──",
        "QUANT_RUNTIME_PROFILE=production",
        "QUANT_BACKGROUND_TASKS_ENABLED=true",
        "TZ=Asia/Shanghai",
    ]
    return "\n".join(lines) + "\n"


def main():
    dry_run = "--dry-run" in sys.argv
    local = parse_env(LOCAL_ENV)
    print(f"从 {LOCAL_ENV} 读到 {len(local)} 个键")

    owner_text = build_owner(local)
    edge_text = build_edge(local)

    owner_path = SECRETS_DIR / ".env.owner"
    edge_path = SECRETS_DIR / ".env.edge"

    if dry_run:
        print(f"\n[dry-run] .env.owner ({owner_text.count(chr(10))} 行):")
        # 只打印键名
        for line in owner_text.splitlines():
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
            if m:
                print(f"  {m.group(1)}=***")
            else:
                print(f"  {line}")
        print(f"\n[dry-run] .env.edge ({edge_text.count(chr(10))} 行):")
        for line in edge_text.splitlines():
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
            if m:
                print(f"  {m.group(1)}=***")
            else:
                print(f"  {line}")
    else:
        owner_path.write_text(owner_text, encoding="utf-8")
        os.chmod(owner_path, 0o600)
        edge_path.write_text(edge_text, encoding="utf-8")
        os.chmod(edge_path, 0o600)
        print(f"✅ {owner_path}  ({owner_text.count(chr(10))} 行)")
        print(f"✅ {edge_path}  ({edge_text.count(chr(10))} 行)")
        print("\n下一步: bash config/secrets/sync-secrets.sh all")


if __name__ == "__main__":
    main()
