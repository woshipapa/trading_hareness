#!/usr/bin/env python3
"""Render the B300 / H100 collaboration progress page served at http://127.0.0.1:8888/b300.

The executor (B300 agent) and the reviewer (us) work only through the branch
crossarch/b300-collab of woshipapa/horizonal_fuse_kernel; their state lives in
markdown tables there (QUEUE, FIXES, the executor heartbeat, reviews, responses,
proposals) and in delivered run directories.  This script keeps a blobless,
sparse clone of that branch under state/, parses it, adds the H100 frequency-axis
ladder log (best effort, read over ssh), and writes one self-contained page in the
service index's visual style:

    state/b300-collab-dashboard.html   (served by serve_service_index.py at /b300)
    state/b300-collab-dashboard.json   (the same data)

The supervisor task b300-collab.watch runs it every 5 minutes after the Feishu
watcher.  Loopback only: nothing is sent anywhere, no secret is read.  Every text
taken from the repo is executor- or reviewer-written and is escaped as untrusted.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
REPO_URL = "https://github.com/woshipapa/horizonal_fuse_kernel.git"
BRANCH = "crossarch/b300-collab"
BRANCH_URL = "https://github.com/woshipapa/horizonal_fuse_kernel/commits/crossarch/b300-collab"
CLONE = Path(os.environ.get("B300_DASH_CLONE", str(STATE / "b300-collab-repo")))
OUT_HTML = Path(os.environ.get("B300_DASH_HTML", str(STATE / "b300-collab-dashboard.html")))
OUT_JSON = Path(os.environ.get("B300_DASH_JSON", str(STATE / "b300-collab-dashboard.json")))
H100_CACHE = STATE / "b300-collab-h100.json"
EXECUTOR_AUTHOR = os.environ.get("B300_COLLAB_EXECUTOR_AUTHOR", "b300-exec-agent")
COLLAB = "analysis/b300_collab"
SPARSE = [
    f"/{COLLAB}/*.md",
    f"/{COLLAB}/review/",
    f"/{COLLAB}/response/",
    f"/{COLLAB}/proposals/",
    f"/{COLLAB}/runs/*.json",
    f"/{COLLAB}/runs/*/cmd.sh",
    f"/{COLLAB}/runs/*/host.txt",
    f"/{COLLAB}/runs/*/results.md",
    f"/{COLLAB}/runs/*/campaign/audit.json",
]
# H100 frequency axis (analysis/H100_FREQUENCY_POWER_PLAN_20261009.md): read-only, best effort.
H100_HOST = os.environ.get("B300_DASH_H100_HOST", "teleai-gpu-test-vpn")
H100_CONTAINER = os.environ.get("B300_DASH_H100_CONTAINER", "e012f6b33d64")
H100_DIR = os.environ.get("B300_DASH_H100_DIR", "/workspace/clock_axis_h100_20261009")
COMMIT_DAYS = 4
LOCAL_TZ = timezone(timedelta(hours=8))   # 北京时间，与飞书群一致

STATUS_CLASSES = [  # (keyword at the start of the status cell, class, label)
    ("已结案", "closed", "已结案"), ("不执行", "closed", "不执行"), ("运行中", "running", "运行中"),
    ("待审", "review", "待审"), ("需返工", "blocked", "需返工"), ("等修复", "blocked", "等修复"),
    ("未开始", "todo", "未开始"),
]


# ---------------------------------------------------------------- git
def git(*args: str, timeout: int = 120) -> str:
    r = subprocess.run(["git", "-C", str(CLONE), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])} rc {r.returncode}: {r.stderr.strip()[-300:]}")
    return r.stdout


def sync_clone() -> str:
    """Clone once (blobless, sparse), then fetch and move to the branch tip.  The clone is ours."""
    if not (CLONE / ".git").exists():
        CLONE.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", "--single-branch",
                            "--branch", BRANCH, REPO_URL, str(CLONE)], capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise RuntimeError(f"git clone rc {r.returncode}: {r.stderr.strip()[-300:]}")
        git("sparse-checkout", "set", "--no-cone", *SPARSE)
        git("checkout", "--quiet", BRANCH, timeout=300)
    git("fetch", "--quiet", "origin", BRANCH, timeout=180)
    git("reset", "--hard", "--quiet", "FETCH_HEAD", timeout=300)
    return git("rev-parse", "HEAD").strip()


# ---------------------------------------------------------------- parsing helpers
def split_row(line: str) -> list[str]:
    """Cells of one markdown table row; `\\|` inside a cell is not a separator."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", body)]


def plain(text: str) -> str:
    """Markdown inline marks off (bold, code ticks, links), for display."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.replace("**", "").replace("`", "")


CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_number(text: str) -> Optional[int]:
    """十五 -> 15, 二十三 -> 23, 八 -> 8 (up to 99)."""
    if not text:
        return None
    if "十" in text:
        tens, _, ones = text.partition("十")
        t = CN_DIGITS.get(tens, 1) if tens else 1
        o = CN_DIGITS.get(ones, 0) if ones else 0
        return t * 10 + o
    return CN_DIGITS.get(text)


def status_class(cell: str) -> tuple[str, str]:
    for word, cls, label in STATUS_CLASSES:
        if cell.startswith(word):
            return cls, label
    return "other", cell[:6] or "—"


def mark_ready(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A not-started task is ready when every Q-number in its dependency cell is closed; any other
    text there (a resource, a release by the reviewer) is shown but not judged."""
    closed = {t["id"] for t in tasks if t["status_class"] == "closed"}
    for t in tasks:
        needs = re.findall(r"Q\d+", t["deps"])
        t["waiting_on"] = [q for q in needs if q not in closed]
        if t["status_class"] == "todo":
            t["status_class"] = "waiting" if t["waiting_on"] else "ready"
    return tasks


def parse_queue(text: str) -> list[dict[str, Any]]:
    tasks, section = [], ""
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not re.match(r"^\|\s*Q\d+\s*\|", line):
            continue
        cells = split_row(line)
        if len(cells) < 7:
            continue
        cls, label = status_class(cells[6])
        tasks.append({"id": cells[0], "task": plain(cells[1]), "owner": cells[2], "deps": plain(cells[3]),
                      "basis": plain(cells[4]), "accept": plain(cells[5]), "status": plain(cells[6]),
                      "status_class": cls, "status_label": label, "section": section})
    return mark_ready(tasks)


def fix_key(num: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", num)) or (0,)


def parse_fixes(text: str) -> list[dict[str, Any]]:
    fixes = []
    for line in text.splitlines():
        if not re.match(r"^\|\s*20\d\d-\d\d-\d\d\s*\|", line):
            continue
        cells = split_row(line)
        if len(cells) < 6:
            continue
        m = re.match(r"\s*(\d+\.\d+)", cells[2])
        if not m:
            continue
        title = re.search(r"\*\*(.+?)\*\*", cells[3])
        fixes.append({"num": m.group(1), "date": cells[0], "commit": plain(cells[1]),
                      "source": plain(cells[2][len(m.group(1)):]).strip("（）() "),
                      "title": plain(title.group(1) if title else cells[3])[:160],
                      "rerun": plain(cells[4])[:160], "status": plain(cells[5])})
    fixes.reverse()          # the table is appended in time order; its numbers are not (2.x are older)
    return fixes


def parse_reviews(review_dir: Path, response_dir: Path) -> list[dict[str, Any]]:
    responses = {p.name.split(".response")[0] for p in response_dir.glob("*.md")} if response_dir.is_dir() else set()
    out = []
    for p in sorted(review_dir.glob("*.md")) if review_dir.is_dir() else []:
        first = next((l for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.startswith("# ")), "")
        m = re.search(r"第([一二三四五六七八九十两零]+)份", first)
        title = first[2:].split("：", 1)[-1] if "：" in first else first[2:]
        out.append({"file": p.name, "ordinal": cn_number(m.group(1)) if m else None,
                    "date": p.name[:8], "title": plain(title)[:150], "responded": p.stem in responses})
    out.sort(key=lambda r: (r["ordinal"] or 0, r["file"]), reverse=True)
    return out


def parse_proposals(prop_dir: Path, answers_text: str, added_by: Optional[dict] = None) -> list[dict[str, Any]]:
    """A proposal is answered when FIXES or a review cites its file name or the short sha of the
    commit that added it ("采纳提案 b9a35ca")."""
    out = []
    for p in sorted(prop_dir.glob("*.md")) if prop_dir.is_dir() else []:
        if p.name.lower() == "readme.md":
            continue
        first = next((l for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.startswith("# ")), p.stem)
        sha = (added_by or {}).get(p.name, "")
        out.append({"file": p.name, "title": plain(first.lstrip("# ").replace("提案：", "").replace("建议：", ""))[:140],
                    "answered": p.stem in answers_text or bool(sha and sha[:7] in answers_text)})
    return sorted(out, key=lambda x: x["file"], reverse=True)


def parse_heartbeat(text: str) -> dict[str, Any]:
    blocks = re.split(r"^## ", text, flags=re.M)
    for b in blocks[1:]:
        head, _, body = b.partition("\n")
        items = [plain(l.strip()[2:]) for l in body.splitlines() if l.strip().startswith("- ")]
        return {"time": head.strip(), "items": items[:8]}
    return {"time": "", "items": []}


def parse_lock_choice(path: Path) -> dict[str, Any]:
    d = json.loads(path.read_text(encoding="utf-8"))
    locks = []
    for l in d.get("locks", []):
        why = [w.split(": ", 1)[-1] for w in l.get("why_not", [])]
        locks.append({"lock": l.get("lock_mhz"), "passes": bool(l.get("passes")), "why": [plain(w)[:150] for w in why][:3]})
    ctrl = d.get("positive_control") or {}
    return {"file": path.name, "chosen": d.get("chosen_lock_mhz"), "locks": locks,
            "control": bool(ctrl.get("capping_detected")), "why_none": d.get("why_none", [])[:2]}


def kv_file(path: Path) -> dict[str, str]:
    out = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^(?:export\s+)?([A-Za-z0-9_]+)[=:]\s*(.*)$", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"')
    return out


def parse_runs(runs_dir: Path, limit: int = 30) -> list[dict[str, Any]]:
    out = []
    for d in runs_dir.glob("b300_fair_*") if runs_dir.is_dir() else []:
        m = re.search(r"_(\d{8}T\d{6}Z)_", d.name)
        if not m:
            continue
        cmd, host = kv_file(d / "cmd.sh"), kv_file(d / "host.txt")
        arms = Path(cmd.get("B300_ARMS", "")).name
        kind = {"lock_select_arms.txt": "选档短 campaign", "lock_stress_arms.txt": "选档压力测试",
                "data_control_arms.txt": "全零对照"}.get(arms, cmd.get("B300_PANEL", "?"))
        audit = ""
        ap = d / "campaign" / "audit.json"
        if ap.is_file():
            try:
                audit = json.loads(ap.read_text(encoding="utf-8")).get("status", "")
            except (ValueError, OSError):
                audit = "?"
        verdict = ""
        rp = d / "results.md"
        if rp.is_file():
            lines = [l.strip() for l in rp.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
            verdict = plain(next((l for l in lines if not l.startswith("#")), ""))[:120]
        out.append({"name": d.name, "stamp": m.group(1), "lock": cmd.get("B300_LOCK_MHZ", ""),
                    "gpu": cmd.get("B300_GPU_IDS", host.get("gpu_index", "")), "kind": kind,
                    "audit": audit, "verdict": verdict})
    return sorted(out, key=lambda r: r["stamp"], reverse=True)[:limit]


def commit_kind(subject: str) -> str:
    m = re.match(r"^([a-z]+)(?:\([^)]*\))?:", subject)
    return m.group(1) if m else "other"


def parse_commits(raw: str) -> list[dict[str, Any]]:
    out = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, author, when, subject = parts
        out.append({"sha": sha[:8], "side": "executor" if author == EXECUTOR_AUTHOR else "reviewer",
                    "when": when, "subject": subject[:200], "kind": commit_kind(subject)})
    return out


# ---------------------------------------------------------------- H100 ladder (best effort)
H100_SNIPPET = r'''
import glob, json, os
D = os.environ["D"]
logs = sorted(glob.glob(D + "/runs/ladder_*.log"), key=os.path.getmtime)
out = {"log": open(logs[-1]).read().splitlines()[-40:] if logs else [], "timings": [], "screens": []}
tag = os.path.basename(logs[-1])[7:-4] if logs else ""
for j in sorted(glob.glob(D + "/runs/h100_clock_timing_*_" + tag + "/judge.json")):
    try:
        x = json.load(open(j))[0]
        out["timings"].append({"run": os.path.basename(os.path.dirname(j)), "lock": x.get("lock_mhz"),
                               "p90": x.get("round_deviation_p90"), "passes": x.get("passes"),
                               "capped": x.get("capped_windows"), "audit": x.get("audit")})
    except Exception:
        pass
for s in sorted(glob.glob(D + "/runs/clock_screen_*_" + tag + ".json")):
    try:
        x = json.load(open(s))
        out["screens"].append({"lock": x.get("lock_mhz"), "passing": len(x.get("passing_cells", [])),
                               "cells": len(x.get("cells", {}))})
    except Exception:
        pass
print(json.dumps(out))
'''


def read_h100() -> dict[str, Any]:
    if os.environ.get("B300_DASH_H100", "1") == "0":
        return {"state": "off"}
    cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none",
           "-o", "ClearAllForwardings=yes", H100_HOST,
           f"docker exec -i -e D={H100_DIR} {H100_CONTAINER} python3 -"]
    try:
        r = subprocess.run(cmd, input=H100_SNIPPET, capture_output=True, text=True, timeout=45)
        data = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else None
    except (subprocess.TimeoutExpired, ValueError, IndexError, OSError):
        data = None
    if data is not None:
        snap = {"state": "live", "at": now_iso(), **data}
        try:
            H100_CACHE.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return snap
    try:
        cached = json.loads(H100_CACHE.read_text(encoding="utf-8"))
        return {**cached, "state": "stale"}
    except (OSError, ValueError):
        return {"state": "unreachable"}


# ---------------------------------------------------------------- collect
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect(tip: str) -> dict[str, Any]:
    base = CLONE / COLLAB
    read = lambda name: (base / name).read_text(encoding="utf-8", errors="replace") if (base / name).is_file() else ""
    fixes_text = read("FIXES.md")
    review_text = " ".join(p.read_text(encoding="utf-8", errors="replace") for p in (base / "review").glob("*.md"))
    raw = git("log", f"--since={COMMIT_DAYS} days ago", "--format=%H%x1f%an%x1f%aI%x1f%s")
    # lock decisions, newest commit first (a fresh clone gives every file the same mtime)
    choices = []
    for f in (base / "runs").glob("lock_choice_*.json"):
        try:
            when = int(git("log", "-1", "--format=%ct", "--", str(f.relative_to(CLONE))).strip() or 0)
            choices.append((when, parse_lock_choice(f)))
        except (ValueError, OSError, RuntimeError):
            continue
    choices.sort(key=lambda x: x[0], reverse=True)
    added_by = {}
    for line in git("log", "--diff-filter=A", "--format=%x1e%h", "--name-only", "--", f"{COLLAB}/proposals").split("\x1e"):
        parts = line.strip().splitlines()
        for name in parts[1:]:
            added_by.setdefault(Path(name).name, parts[0])
    updated = re.search(r"^最近更新：(.+)$", read("QUEUE.md"), re.M)
    return {"generated_at": now_iso(), "tip": tip, "branch_url": BRANCH_URL,
            "queue": parse_queue(read("QUEUE.md")), "queue_updated": plain(updated.group(1)) if updated else "",
            "fixes": parse_fixes(fixes_text), "reviews": parse_reviews(base / "review", base / "response"),
            "proposals": parse_proposals(base / "proposals", fixes_text + review_text, added_by),
            "heartbeat": parse_heartbeat(read("EXEC_HEARTBEAT.md")), "commits": parse_commits(raw),
            "lock_choice": choices[0][1] if choices else None,
            "lock_history": [{"file": c["file"], "chosen": c["chosen"], "passing": [l["lock"] for l in c["locks"] if l["passes"]],
                              "when": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")} for t, c in choices[1:8]],
            "runs": parse_runs(base / "runs"), "h100": read_h100()}


# ---------------------------------------------------------------- render
def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def pill(cls: str, text: str) -> str:
    return f'<span class="pill {cls}">{esc(text)}</span>'


def local_time(iso: str, fmt: str = "%m-%d %H:%M") -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(LOCAL_TZ).strftime(fmt)
    except ValueError:
        return iso


def render(d: dict[str, Any]) -> str:
    q = d["queue"]
    open_tasks = [t for t in q if t["status_class"] != "closed"]
    by_owner = lambda who: [t for t in open_tasks if t["owner"].startswith(who)]
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = [c for c in d["commits"] if datetime.fromisoformat(c["when"]) >= day_ago]
    ex24 = sum(1 for c in recent if c["side"] == "executor")
    latest_review = d["reviews"][0] if d["reviews"] else {}
    latest_fix = d["fixes"][0] if d["fixes"] else {}
    open_props = [p for p in d["proposals"] if not p["answered"]]

    tiles = [
        ("执行方未结任务", f"{len(by_owner('执行方'))}", f"运行中 {sum(1 for t in by_owner('执行方') if t['status_class'] == 'running')}"),
        ("审查方未结任务", f"{len(by_owner('审查方'))}", f"运行中 {sum(1 for t in by_owner('审查方') if t['status_class'] == 'running')}"),
        ("已结案", f"{len(q) - len(open_tasks)}", f"共 {len(q)} 项"),
        ("最新审查", f"第 {latest_review.get('ordinal') or '?'} 份", "已回复" if latest_review.get("responded") else "待执行方回复"),
        ("最新修复", f"{latest_fix.get('num', '—')}", latest_fix.get("status", "")),
        ("待答复提案", f"{len(open_props)}", f"共 {len(d['proposals'])} 份"),
        ("近 24 h 提交", f"{len(recent)}", f"执行方 {ex24} · 审查方 {len(recent) - ex24}"),
    ]
    tiles_html = "".join(f'<div class="tile"><span>{esc(a)}</span><strong>{esc(b)}</strong><em>{esc(c)}</em></div>'
                         for a, b, c in tiles)

    hb = d["heartbeat"]
    hb_html = (f'<p class="when">{esc(hb["time"])}</p><ul>' + "".join(f"<li>{esc(i)}</li>" for i in hb["items"]) + "</ul>"
               if hb["time"] else '<p class="muted">没有心跳记录</p>')

    h = d["h100"]
    if h.get("state") in ("live", "stale"):
        screens = {s["lock"]: s for s in h.get("screens", [])}
        rows = []
        for t in h.get("timings", []):
            s = screens.get(t["lock"], {})
            p90 = f'{100 * t["p90"]:.2f}%' if isinstance(t.get("p90"), (int, float)) else "—"
            pos = re.search(r"_p(\d+)_", t["run"])
            rows.append(f'<tr><td>{esc(t["lock"])} MHz</td><td>{esc(pos.group(1) if pos else "")}</td>'
                        f'<td>{esc(s.get("passing", "—"))}/{esc(s.get("cells", "—"))}</td><td>{esc(p90)}</td>'
                        f'<td>{esc(t.get("audit") or "")}</td></tr>')
        last = [l for l in h.get("log", []) if l.startswith("[")][-3:]
        badge = pill("green", "实时") if h["state"] == "live" else pill("amber", "缓存")
        h_html = (f'<p class="when">{badge} 读取于 {esc(local_time(h.get("at", "")))}</p>'
                  + (f'<div class="scroll"><table><thead><tr><th>锁频</th><th>次序</th><th>筛查通过</th><th>轮间偏差 p90</th><th>审计</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></div>' if rows else '<p class="muted">还没有计时结果</p>')
                  + '<ul class="log">' + "".join(f"<li>{esc(l[22:] if len(l) > 22 else l)}</li>" for l in last) + "</ul>"
                  + '<p class="note">H100 的轮间偏差在 1.5–2.6% 属正常（同机计划 49/50），只作报告，不作门槛。</p>')
    elif h.get("state") == "off":
        h_html = '<p class="muted">已关闭（B300_DASH_H100=0）</p>'
    else:
        h_html = '<p class="muted">暂时读不到 H100 机（ssh 失败），也没有缓存</p>'

    lc = d["lock_choice"]
    if lc:
        lrows = "".join(
            f'<tr><td>{esc(l["lock"])} MHz</td><td>{pill("green", "通过") if l["passes"] else pill("red", "不过")}</td>'
            f'<td class="why">{"<br>".join(esc(w) for w in l["why"]) or "—"}</td></tr>' for l in lc["locks"])
        hist = "".join(f'<li>{esc(local_time(h_["when"]))} · {esc(h_["file"])} · 选定 {esc(h_["chosen"] or "无")}'
                       f'{(" · 通过 " + esc(", ".join(str(x) for x in h_["passing"]))) if h_["passing"] else ""}</li>'
                       for h_ in d.get("lock_history", []))
        lock_html = (f'<p class="when">最新一次：{esc(lc["file"])} · 选定：<strong>{esc(lc["chosen"]) if lc["chosen"] else "无（chosen=null）"}</strong>'
                     f' · 阳性对照{"有效" if lc["control"] else "无效"}</p>'
                     f'<table><thead><tr><th>锁频</th><th>结果</th><th>原因</th></tr></thead><tbody>{lrows}</tbody></table>'
                     + (f'<p class="note">更早的 decide：</p><ul class="log">{hist}</ul>' if hist else ""))
    else:
        lock_html = '<p class="muted">还没有 decide 结果</p>'

    columns = [("running", "运行中"), ("ready", "可开始"), ("waiting", "等依赖"), ("review", "待审 · 返工"), ("closed", "已结案")]
    board = []
    for cls, label in columns:
        items = [t for t in q if (t["status_class"] in ("review", "blocked", "other") if cls == "review" else t["status_class"] == cls)]
        cards = "".join(
            f'<article class="task {esc(t["status_class"])}" data-owner="{"executor" if t["owner"].startswith("执行方") else "reviewer"}">'
            f'<header><b>{esc(t["id"])}</b><span class="pill {"blue" if t["owner"].startswith("执行方") else "violet"}">{esc(t["owner"])}</span></header>'
            f'<p title="{esc(t["task"])}">{esc(t["task"][:90])}{"…" if len(t["task"]) > 90 else ""}</p>'
            f'<footer><span>{("等 " + esc("、".join(t.get("waiting_on", [])))) if t.get("waiting_on") and t["status_class"] == "waiting" else ("依赖 " + esc(t["deps"] or "—"))}</span>'
            f'<span title="{esc(t["status"])}">{esc(t["status"][:40])}</span></footer></article>'
            for t in items)
        board.append(f'<div class="col"><h3>{esc(label)} <em>{len(items)}</em></h3>{cards or "<p class=muted>无</p>"}</div>')

    tl = "".join(
        f'<li class="{esc(c["side"])}"><time>{esc(local_time(c["when"]))}</time>'
        f'<span class="pill {"blue" if c["side"] == "executor" else "violet"}">{"执行方" if c["side"] == "executor" else "审查方"}</span>'
        f'<span class="kind">{esc(c["kind"])}</span><span class="subj">{esc(c["subject"])}</span>'
        f'<a href="https://github.com/woshipapa/horizonal_fuse_kernel/commit/{esc(c["sha"])}">{esc(c["sha"][:7])}</a></li>'
        for c in d["commits"][:60])

    reviews = "".join(f'<li><b>第 {esc(r["ordinal"] or "?")} 份</b> <span class="muted">{esc(r["date"])}</span> '
                      f'{pill("green", "已回复") if r["responded"] else pill("amber", "待回复")}'
                      f'<p>{esc(r["title"])}</p></li>' for r in d["reviews"][:12])
    props = "".join(f'<li>{pill("green", "已答复") if p["answered"] else pill("amber", "待答复")}'
                    f'<p>{esc(p["title"])}</p></li>' for p in d["proposals"][:10])
    fixes = "".join(f'<li><b>{esc(f["num"])}</b> <span class="muted">{esc(f["commit"][:20])}</span> '
                    f'{pill("green" if f["status"].startswith("完成") else "amber", f["status"][:8])}'
                    f'<p>{esc(f["title"])}</p></li>' for f in d["fixes"][:14])

    def run_time(stamp: str) -> str:
        return local_time(datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat())

    runs = "".join(f'<tr><td>{esc(run_time(r["stamp"]))}</td>'
                   f'<td>{esc(r["kind"])}</td><td>{esc(r["lock"])}</td><td>{esc(r["gpu"])}</td>'
                   f'<td>{pill("green", "pass") if r["audit"] == "pass" else esc(r["audit"] or "—")}</td>'
                   f'<td class="why">{esc(r["verdict"])}</td></tr>' for r in d["runs"][:25])

    return PAGE.format(
        generated=esc(local_time(d["generated_at"], "%Y-%m-%d %H:%M")), tip=esc(d["tip"][:8]), branch_url=esc(d["branch_url"]),
        queue_updated=esc(d["queue_updated"]), tiles=tiles_html, heartbeat=hb_html, h100=h_html, lock=lock_html,
        board="".join(board), timeline=tl or "<li class=muted>近几天没有提交</li>", reviews=reviews, proposals=props or "<li class=muted>无</li>",
        fixes=fixes, runs=runs)


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>B300 协作进展</title>
<style>
:root {{
  --font-ui: Inter, "SF Pro Text", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  --font-mono: "SFMono-Regular", Consolas, monospace;
  --ink: #172033; --muted: #738198; --bg: #f4f6f9; --surface: #ffffff; --surface-soft: #f7f9fc;
  --line: #e3e8f0; --green: #18865c; --green-soft: #e9f7f1; --amber: #9a6414; --amber-soft: #fff6df;
  --blue: #2f6f9f; --blue-soft: #edf5fb; --violet: #6a4fa3; --violet-soft: #f3effb;
  --red: #bf3040; --red-soft: #fff0f1; --shadow: 0 8px 24px rgba(22, 30, 38, 0.06);
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 32px 20px 60px; background: var(--bg); color: var(--ink); font: 14px/1.55 var(--font-ui); }}
main {{ max-width: 1240px; margin: 0 auto; }}
a {{ color: var(--blue); text-decoration: none; }}
.masthead {{ display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 20px; }}
.masthead strong {{ font-size: 24px; font-weight: 720; }}
.masthead p {{ margin: 4px 0 0; color: var(--muted); font-size: 13px; }}
.meta {{ color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }}
h2 {{ margin: 28px 0 12px; font-size: 15px; font-weight: 700; color: var(--muted); }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; margin-top: 22px; }}
.tile {{ padding: 12px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; box-shadow: var(--shadow); }}
.tile span {{ display: block; color: var(--muted); font-size: 12px; }}
.tile strong {{ display: block; margin-top: 2px; font-size: 22px; font-weight: 720; font-variant-numeric: tabular-nums; }}
.tile em {{ font-style: normal; color: var(--muted); font-size: 12px; }}
.grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(360px, 100%), 1fr)); gap: 14px; }}
.grid2 > section {{ min-width: 0; }}
.card {{ min-width: 0; padding: 16px 18px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; box-shadow: var(--shadow); overflow-wrap: anywhere; }}
.card h3 {{ margin: 0 0 8px; font-size: 15px; }}
.when {{ margin: 0 0 8px; color: var(--muted); font-size: 12px; }}
.muted {{ color: var(--muted); }}
.note {{ margin: 8px 0 0; color: var(--muted); font-size: 12px; }}
.card ul {{ margin: 0; padding-left: 18px; }}
.card li {{ margin: 3px 0; }}
.log {{ margin-top: 8px !important; font-family: var(--font-mono); font-size: 12px; color: var(--muted); }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }}
th, td {{ padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
th {{ color: var(--muted); font-weight: 600; font-size: 12px; }}
td.why {{ color: var(--muted); font-size: 12px; white-space: normal; min-width: 220px; }}
.card table {{ overflow-wrap: normal; }}
th, td {{ white-space: nowrap; }}
.scroll {{ overflow-x: auto; }}
.pill {{ display: inline-block; padding: 0 8px; border-radius: 999px; font-size: 11px; line-height: 18px; white-space: nowrap; }}
.pill.green {{ background: var(--green-soft); color: var(--green); }}
.pill.amber {{ background: var(--amber-soft); color: var(--amber); }}
.pill.red {{ background: var(--red-soft); color: var(--red); }}
.pill.blue {{ background: var(--blue-soft); color: var(--blue); }}
.pill.violet {{ background: var(--violet-soft); color: var(--violet); }}
.filters {{ display: flex; gap: 8px; margin: -4px 0 12px; }}
.filters button {{ padding: 4px 12px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface); color: var(--ink); font: inherit; font-size: 13px; cursor: pointer; }}
.filters button[aria-pressed="true"] {{ border-color: var(--blue); color: var(--blue); background: var(--blue-soft); }}
.board {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; align-items: start; }}
.col {{ min-width: 0; max-height: 640px; overflow-y: auto; padding: 10px; background: var(--surface-soft); border: 1px solid var(--line); border-radius: 10px; }}
.col h3 {{ margin: 2px 4px 10px; font-size: 13px; }}
.col h3 em {{ font-style: normal; color: var(--muted); font-weight: 400; }}
.task {{ margin-bottom: 8px; padding: 9px 10px; background: var(--surface); border: 1px solid var(--line); border-left: 3px solid var(--line); border-radius: 8px; }}
.task.running {{ border-left-color: var(--green); }} .task.blocked {{ border-left-color: var(--red); }} .task.review {{ border-left-color: var(--amber); }}
.task header {{ display: flex; justify-content: space-between; align-items: center; gap: 6px; }}
.task p {{ margin: 4px 0; font-size: 13px; }}
.task {{ overflow-wrap: anywhere; }}
.task.ready {{ border-left-color: var(--blue); }}
.task footer {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 2px 8px; color: var(--muted); font-size: 11px; }}
.task footer span:last-child {{ text-align: right; }}
.timeline {{ list-style: none; margin: 0; padding: 0; max-height: 520px; overflow-y: auto; }}
.timeline li {{ display: grid; grid-template-columns: 82px 54px 58px minmax(0, 1fr) 64px; gap: 8px; align-items: baseline; padding: 6px 4px; border-bottom: 1px solid var(--line); font-size: 13px; }}
.timeline time, .timeline .kind {{ color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }}
.timeline .subj {{ overflow-wrap: anywhere; }}
.docs {{ list-style: none; padding: 0 !important; }}
.docs li {{ padding: 6px 0; border-bottom: 1px solid var(--line); }}
.docs p {{ margin: 2px 0 0; color: var(--muted); font-size: 12px; }}
@media (max-width: 1100px) {{ .board {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }} }}
@media (max-width: 900px) {{ .board {{ grid-template-columns: 1fr 1fr; }} .timeline li {{ grid-template-columns: 70px 50px minmax(0, 1fr); }} .timeline .kind, .timeline a {{ display: none; }} }}
@media (max-width: 560px) {{ .board {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
<div class="masthead">
  <div><strong>B300 协作进展</strong>
  <p>执行方（B300 agent）与审查方的任务、文档与运行；数据来自分支 crossarch/b300-collab 与 H100 频率轴，每 5 分钟刷新。</p></div>
  <div class="meta">生成于 {generated}（北京时间）· 分支 <a href="{branch_url}">{tip}</a> · <a href="/">服务索引</a></div>
</div>
<div class="tiles">{tiles}</div>

<div class="grid2">
  <section><h2>执行方现在在做</h2><div class="card">{heartbeat}</div></section>
  <section><h2>H100 频率轴（审查方在跑）</h2><div class="card">{h100}</div></section>
</div>

<section><h2>锁频选档（Q03）</h2><div class="card scroll">{lock}</div></section>

<section><h2>任务队列（QUEUE.md）</h2>
  <p class="meta">{queue_updated}</p>
  <div class="filters" role="group" aria-label="负责方">
    <button type="button" data-f="all" aria-pressed="true">全部</button>
    <button type="button" data-f="executor" aria-pressed="false">执行方</button>
    <button type="button" data-f="reviewer" aria-pressed="false">审查方</button>
  </div>
  <div class="board">{board}</div>
</section>

<section><h2>提交时间线（近 4 天）</h2><div class="card"><ul class="timeline">{timeline}</ul></div></section>

<div class="grid2">
  <section><h2>审查与回复</h2><div class="card"><ul class="docs">{reviews}</ul></div></section>
  <section><h2>提案</h2><div class="card"><ul class="docs">{proposals}</ul></div></section>
</div>
<section><h2>审查方修复（FIXES.md）</h2><div class="card"><ul class="docs">{fixes}</ul></div></section>

<section><h2>最近交付的运行目录</h2><div class="card scroll"><table>
  <thead><tr><th>时间</th><th>类型</th><th>锁频</th><th>卡</th><th>审计</th><th>结论</th></tr></thead>
  <tbody>{runs}</tbody></table></div></section>
</main>
<script>
document.querySelectorAll('.filters button').forEach((b) => b.addEventListener('click', () => {{
  document.querySelectorAll('.filters button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
  document.querySelectorAll('.task').forEach((t) => {{ t.hidden = b.dataset.f !== 'all' && t.dataset.owner !== b.dataset.f; }});
}}));
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-sync", action="store_true", help="use the clone as it is (no fetch)")
    args = parser.parse_args()
    try:
        tip = git("rev-parse", "HEAD").strip() if args.no_sync else sync_clone()
        data = collect(tip)
    except Exception as exc:  # noqa: BLE001 - the previous page stays; next run retries
        print(f"[b300-dashboard] {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 1
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp = OUT_HTML.with_suffix(".tmp")
    tmp.write_text(render(data), encoding="utf-8")
    tmp.replace(OUT_HTML)
    print(f"[b300-dashboard] {data['tip'][:8]}: {len(data['queue'])} tasks, {len(data['commits'])} commits, "
          f"h100 {data['h100'].get('state')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
