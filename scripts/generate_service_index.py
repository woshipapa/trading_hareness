#!/usr/bin/env python3
"""Generate docs/services.html: one clickable index of every local service port.

The workstation reaches everything through 127.0.0.1 — local supervisor
services, Docker compose containers, and SSH tunnels onto the 47 hosts — but
the ports lived only in AGENTS.md prose and memory.  This registry renders a
dependency-free static page (open it via file://, every link is loopback) so
one bookmark reaches every console.  ``--check`` fails when the committed page
is stale; adding or moving a service means editing REGISTRY here and
regenerating.
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "services.html"

# (分组, 名称, base URL, 形态, 说明, [(路径, 链接名), ...])
REGISTRY: list[tuple[str, str, str, str, str, list[tuple[str, str]]]] = [
    ("控制台", "服务索引（本页）", "http://127.0.0.1:8888",
     "supervisor 服务（service-index）",
     "常驻入口，加书签用这个地址；以 http 打开时左侧圆点是各服务的存活探测。",
     [("/", "本页"), ("/health", "health")]),
    ("控制台", "飞书 Relay / Quant 控制台", "http://127.0.0.1:18300",
     "SSH 隧道 → 47edge（feishu-tunnel）",
     "同源双应用：adapter 按路径路由 quant 研究控制台与 relay 运维面板；owner（47 主机）量化数据的前端呈现就在这里。",
     [("/", "Quant 总览"), ("/research", "研究"), ("/personal", "个人决策"),
      ("/monitor", "群监听"), ("/workbench", "飞书工作台"), ("/relay", "转发台账")]),
    ("控制台", "XHS 情报控制台", "http://127.0.0.1:18790",
     "SSH 隧道 → 47edge（xhs-edge-tunnel）",
     "小红书采集/筛选/每日简报/能力控制台；会话仅 loopback。",
     [("/xhs/", "控制台"), ("/health", "health")]),
    ("控制台", "本机 n8n", "http://127.0.0.1:5678",
     "Docker compose（com.papa.n8n-compose）",
     "工作流编辑器；运行时权威是它的 Postgres（workflows/CATALOG.md 有全量清单）。",
     [("/", "工作流编辑器")]),
    ("控制台", "Paper KB", "http://127.0.0.1:8787",
     "supervisor 服务（paperkb.server）",
     "论文知识库前端：检索与问答、论文页（PDF、Deepread、对话、对比/导出）、学习、流水线与研究面板；"
     "静态卡片索引是旧入口，点论文打开的是 md 卡片。",
     [("/", "前端（搜索）"), ("/#browse", "浏览"), ("/#learning", "学习"), ("/#pipeline", "流水线"),
      ("/#research", "研究"), ("/#features", "功能总览"), ("/index.html", "静态卡片索引（md）")]),
    ("控制台", "Agent Skills", "http://127.0.0.1:8790",
     "supervisor 服务（agent-skills.web）",
     "技能库浏览与编辑（~/codebase/agent-skills）。",
     [("/", "技能库")]),
    ("控制台", "B300 / H100 协作进展", "http://127.0.0.1:8888",
     "supervisor 任务（b300-collab.watch，每 5 分钟）",
     "执行方（B300 agent）与审查方的任务队列、审查与修复、提交时间线、锁频选档和 H100 频率轴进度。",
     [("/b300", "进展看板"), ("/b300.json", "数据")]),
    ("控制台", "视频理解 Harness", "http://127.0.0.1:8765",
     "supervisor 服务（video-harness）",
     "NiceGUI 上传与任务页 + 处理 API。",
     [("/", "任务页")]),
    ("API / 健康", "model-service", "http://127.0.0.1:8791",
     "supervisor 服务（model-service）",
     "共享模型注册与 API（无页面）。",
     [("/", "API base")]),
    ("API / 健康", "XHS AI worker", "http://127.0.0.1:8793",
     "supervisor 服务（xhs-ai-worker）",
     "本机 AI worker 的健康与计数。",
     [("/health", "health")]),
    ("API / 健康", "本地 quant 容器", "http://127.0.0.1:5681",
     "Docker compose（n8n-quant-research）",
     "仅本地检查用；不是 owner 证据路径，勿混用。",
     [("/health", "health")]),
    ("API / 健康", "Owner 读路径", "http://127.0.0.1:15682",
     "SSH 隧道 → 47owner（owner-tunnel）",
     "47 owner 只读行情证据路径（LONGHU_* 配置存在时才运行）；owner 无 Web 前端，其数据的页面入口是 :18300 控制台。",
     [("/health", "health")]),
]

# 不经任何服务、直接以 file:// 打开的本地页面。
FILE_PAGES: list[tuple[str, str, str]] = [
    ("文献地图总览", "/Users/papa/codebase/literature_maps/combined_index_2026/dashboard.html",
     "Arch/HPC 与 MLSys 论文集合的汇总看板（本地文件）。"),
]

NOTES = [
    "paper-kb-webhook-tunnel 是反向隧道（edge 飞书适配器 → 本机 n8n webhook），没有本地入口；边缘 n8n 本机未映射端口。",
    "隧道类入口依赖 supervisor 的 SSH forward 存活；打不开先看 `codebase/n8n/logs/svc-supervisor.log`。",
    "本页由 `python3 scripts/generate_service_index.py` 生成；增删端口改该文件的 REGISTRY 后重跑。",
]

GROUP_ORDER = ["控制台", "API / 健康"]


def render() -> str:
    def esc(value: str) -> str:
        return html.escape(str(value), quote=True)

    cards: dict[str, list[str]] = {}
    for group, name, base, kind, desc, links in REGISTRY:
        anchors = "".join(
            f'<a href="{esc(base + path)}">{esc(label)}</a>' for path, label in links)
        port = base.rsplit(":", 1)[-1]
        tunnel = "隧道" in kind
        probe_path = next((path for path, _label in links if path == "/health"), links[0][0])
        cards.setdefault(group, []).append(
            f'<article class="card" data-probe="{esc(base + probe_path)}">'
            f'<header><h2><span class="dot" title="存活探测"></span>{esc(name)}</h2>'
            f'<span class="port">:{esc(port)}</span></header>'
            f'<span class="kind{" tunnel" if tunnel else ""}">{esc(kind)}</span>'
            f'<p>{esc(desc)}</p><nav>{anchors}</nav></article>')

    sections = "".join(
        f'<section><h1>{esc(group)}</h1><div class="grid">{"".join(cards[group])}</div></section>'
        for group in GROUP_ORDER if group in cards)
    file_cards = "".join(
        f'<article class="card"><header><h2>{esc(name)}</h2><span class="port">file</span></header>'
        f'<span class="kind">本地文件</span><p>{esc(desc)}</p>'
        f'<nav><a href="file://{esc(path)}">打开</a></nav></article>'
        for name, path, desc in FILE_PAGES)
    if file_cards:
        sections += f'<section><h1>本地文件页</h1><div class="grid">{file_cards}</div></section>'
    notes = "".join(f"<li>{esc(note)}</li>" for note in NOTES)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>服务索引</title>
<style>
:root {{
  --font-ui: Inter, "SF Pro Text", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  --ink: #172033; --muted: #738198; --bg: #f4f6f9; --surface: #ffffff;
  --surface-soft: #f7f9fc; --line: #e3e8f0; --blue: #2f6f9f; --blue-soft: #edf5fb;
  --green: #18865c; --green-soft: #e9f7f1;
  --shadow: 0 8px 24px rgba(22, 30, 38, 0.06);
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 36px 20px 60px; background: var(--bg); color: var(--ink);
  font: 15px/1.6 var(--font-ui); }}
main {{ max-width: 1080px; margin: 0 auto; }}
.masthead {{ margin-bottom: 26px; }}
.masthead strong {{ font-size: 24px; font-weight: 720; }}
.masthead p {{ margin: 6px 0 0; color: var(--muted); font-size: 13px; }}
section {{ margin-top: 26px; }}
section > h1 {{ margin: 0 0 12px; font-size: 15px; font-weight: 700; color: var(--muted);
  text-transform: none; letter-spacing: .02em; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }}
.card {{ padding: 16px 18px; background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; box-shadow: var(--shadow); }}
.card header {{ display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }}
.card h2 {{ margin: 0; font-size: 16px; font-weight: 700; }}
.port {{ color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }}
.kind {{ display: inline-block; margin-top: 6px; padding: 1px 9px; border-radius: 999px;
  background: var(--green-soft); color: var(--green); font-size: 11px; }}
.kind.tunnel {{ background: var(--blue-soft); color: var(--blue); }}
.card p {{ margin: 10px 0 12px; color: var(--muted); font-size: 13px; }}
.card nav {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.card nav a {{ padding: 4px 12px; border: 1px solid var(--line); border-radius: 7px;
  background: var(--surface-soft); color: var(--ink); font-size: 13px; text-decoration: none; }}
.card nav a:hover {{ border-color: var(--blue); color: var(--blue); }}
.notes {{ margin-top: 34px; padding: 14px 18px 14px 34px; background: var(--surface);
  border: 1px solid var(--line); border-radius: 10px; color: var(--muted); font-size: 13px; }}
.notes li {{ margin: 4px 0; }}
.dot {{ display: inline-block; width: 9px; height: 9px; margin-right: 8px; border-radius: 50%;
  background: var(--line); vertical-align: 1px; }}
.dot.up {{ background: var(--green); box-shadow: 0 0 0 3px var(--green-soft); }}
.dot.down {{ background: #bf3040; box-shadow: 0 0 0 3px #fff0f1; }}
</style>
<script>
// 存活点灯：仅在本页经 http 托管（:8800）时探测；file:// 打开保持中性灰点。
// no-cors 的不透明响应足以区分"端口有服务"与"连接失败"。
addEventListener('DOMContentLoaded', () => {{
  if (location.protocol !== 'http:') return;
  document.querySelectorAll('.card[data-probe]').forEach((card) => {{
    const dot = card.querySelector('.dot');
    fetch(card.dataset.probe, {{ mode: 'no-cors', cache: 'no-store' }})
      .then(() => dot.classList.add('up'))
      .catch(() => dot.classList.add('down'));
  }});
}});
</script>
</head>
<body>
<main>
<div class="masthead">
<strong>服务索引</strong>
<p>本机视角的全部入口：supervisor 服务、Docker 容器与 SSH 隧道（均为 127.0.0.1）。</p>
</div>
{sections}
<ul class="notes">{notes}</ul>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != text:
            print("docs/services.html is stale; run python3 scripts/generate_service_index.py", file=sys.stderr)
            return 1
        print("service index is current")
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
