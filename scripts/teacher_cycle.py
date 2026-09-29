#!/usr/bin/env python3
"""老师复盘的每日闭环编排：从收盘结算一路跑到策略包入池，再把老师的方法蒸馏出来。

这条链原来是七个手动步骤，每天靠人记着顺序跑。各段本身已经自动化：

* T 16:15 之后 peer 上的 ``peer_close_research`` 自己跑结算、次日复盘、自选股
  复盘和小杰结算（``teacher_review_roll`` 等四个阶段）；
* 老师发出复盘视频后 harness 自己下载、转写、连实体、读 owner 证据、写策略稿
  （``VIDEO_HARNESS_AUTO_AGENT=1``）。

中间断开的就是这一段：**把策略稿变成策略包并入池**，以及**把老师的量价推演
沉淀下来**。本模块把它们接上，并保持每一步可重入、可审计：

    gate → context → pack → check → import → distill → report

安全边界（不可绕过）：
* ``import`` 自 2026-09-25 起默认自动执行（``TEACHER_CYCLE_AUTO_IMPORT=0`` 可关）。
  导入会写 owner 的观察池，是这条链里唯一的对外写操作，所以过三道闸：
  ``gate`` 通过、``check`` 的 ``problems`` 为空、且在 ``import_window`` 允许的时间窗内
  （目标就是今天而且已开盘或已收盘，一律不自动入池，写 hold 等人确认）。
* 同一个包绝不导入两次（``check`` 会报 ``already imported``，这里也自己记账）。
* owner 读路径不通就停下，不用本地 5681 冒充。
* 只做研究：不下单、不改实盘阈值、不执行 owner DDL。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

CN = dt.timezone(dt.timedelta(hours=8))
HARNESS = pathlib.Path(os.environ.get("VIDEO_HARNESS_DIR", "/Users/papa/codebase/video_understanding_harness"))
DRIVER = pathlib.Path(__file__).with_name("teacher_review_daily.py")
JOBS = pathlib.Path(os.environ.get("VIDEO_HARNESS_ARTIFACTS",
                                   "/Users/papa/codebase/video_understanding_artifacts")) / "jobs"
OWNER_BASE = os.environ.get("VIDEO_RESEARCH_API_BASE_URL", "http://127.0.0.1:15682")
PY = sys.executable
STEPS = ("gate", "chips", "context", "pack", "check", "import", "sweep", "overlap", "distill", "report")


def _read_json(path: pathlib.Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: pathlib.Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _driver(command: list[str], job: pathlib.Path, timeout: int = 2400) -> dict[str, Any]:
    """跑一条 teacher_review_daily 子命令，回传它的 JSON 与退出码。"""
    completed = subprocess.run([PY, str(DRIVER), *command, str(job)], capture_output=True, text=True, timeout=timeout)
    payload: dict[str, Any] = {"returncode": completed.returncode}
    lines = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    if lines:
        try:
            payload["json"] = json.loads("\n".join(completed.stdout[completed.stdout.index(lines[0]):].splitlines()))
        except json.JSONDecodeError:
            payload["json"] = None
    payload["stdout_tail"] = completed.stdout[-1500:]
    payload["stderr_tail"] = completed.stderr[-1500:]
    return payload


def owner_ok(base: str = OWNER_BASE) -> dict[str, Any]:
    """owner 读路径是否可用；不可用就不该继续（不得改用 5681）。"""
    sys.path.insert(0, str(HARNESS))
    from owner_universe import owner_health  # noqa: PLC0415 - harness module, imported on demand
    health = owner_health(base)
    return {"ok": bool(health.get("ok")), "base_url": base, **{k: health.get(k) for k in ("http_status", "release", "error_type")}}


def handoff_of(job: pathlib.Path) -> dict[str, Any]:
    return _read_json(job / "teacher_review_handoff.json", {}) or {}


def draft_quality(job: pathlib.Path) -> tuple[int, int, int, str]:
    """同一场复盘有两份稿子时，用什么挑：``(代码问题, 代码警告, 未核对上, 生成时间)``。

    2026-09-29 同一个视频被跑了两遍（自动入库一份、手工又投了一份），两个任务
    的 ``review_date`` 都是那天，而原来只按复盘日排序，最后取哪个**由文件系统
    的遍历顺序决定** —— 那天碰巧挑中了干净的那份（159 个数字、0 代码问题），
    另一份有 14 条 unbound 代码问题。这种事不能靠运气。

    排序键越小越好：先看代码问题（写错代码最致命），再看未核对上的数字，
    最后用生成时间做确定性的平手判据（取较晚的那份）。
    """
    handoff = handoff_of(job)
    numbers = handoff.get("number_check") or {}
    # ``code_warnings`` 才是"候选代码没绑上"的信号所在：2026-09-29 被淘汰的那份
    # code_problems 是 0，但 code_warnings 有 14 条（雪龙集团、襄阳轴承、机器人…
    # 全写成了"候选X（代码）"）。只看 problems 会把这份和干净的那份看成一样。
    return (int(numbers.get("code_problems") or 0),
            int(numbers.get("code_warnings") or 0),
            int(numbers.get("unverified_count") or 0),
            str(handoff.get("generated_at") or ""))


def eligible_jobs(root: pathlib.Path = JOBS) -> list[tuple[pathlib.Path, str]]:
    """有交接清单、且策略稿和证据都在的任务，按复盘日排序；同一天取更干净的那份。"""
    found: list[tuple[pathlib.Path, str]] = []
    for job in root.iterdir():
        if not job.is_dir():
            continue
        handoff = handoff_of(job)
        review = str(handoff.get("review_date") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", review):
            continue
        if handoff.get("status") != "completed":
            continue
        strategy = job / str(handoff.get("canonical_strategy_file") or "")
        if not strategy.is_file():
            continue
        found.append((job, review))
    # 调用方取 ``[-1]``：复盘日升序，同一天把**更好的那份排在后面**
    # （质量键越小越好，所以同日内按质量降序排）。
    found.sort(key=lambda item: (item[1], tuple(-value for value in draft_quality(item[0])[:3]),
                                 draft_quality(item[0])[3]))
    return found


def gate(job: pathlib.Path, review: str) -> dict[str, Any]:
    """能不能开跑：owner 通不通、交接清单在不在、稿子里的代码有没有阻断问题。"""
    handoff = handoff_of(job)
    health = owner_ok()
    numbers = handoff.get("number_check") or {}
    blocking = [reason for reason in (handoff.get("blocking_reasons") or []) if "代码问题" in str(reason)]
    reasons: list[str] = []
    if not health["ok"]:
        reasons.append(f"owner 读路径不可用（{health.get('error_type') or health.get('http_status')}）")
    if handoff.get("status") != "completed":
        reasons.append(f"交接清单状态 {handoff.get('status')}")
    if int(numbers.get("code_problems") or 0) > 0 or blocking:
        reasons.append(f"正文代码问题 {numbers.get('code_problems')} 处，必须人工改正")
    thin = _read_json(job / "writer_short_passes.json", {}) or {}
    if thin.get("passes"):
        reasons.append(f"写稿有 {len(thin['passes'])} 节重试后仍不完整：" + "；".join(str(x) for x in thin["passes"][:3]))
    if int(numbers.get("checked") or 0) < 50:
        # 空证据跑出来的稿子只核对十几个数字；这种稿子不能拿去建包。
        reasons.append(f"稿子只核对了 {numbers.get('checked')} 个数字，owner 证据疑似缺失，先重跑 Agent")
    return {"ok": not reasons, "reasons": reasons, "owner": health,
            "review_date": review, "target_session": handoff.get("target_session"),
            "number_check": numbers}


def run_pack(job: pathlib.Path, review: str) -> dict[str, Any]:
    """第 4 阶段：生成策略包（已存在就不重做）。"""
    existing = job / f"teacher_pack_{review.replace('-', '')}.json"
    if existing.is_file():
        pack = _read_json(existing, {}) or {}
        return {"status": "exists", "pack_file": existing.name, "pack_id": pack.get("pack_id"),
                "stocks": len(pack.get("stocks") or [])}
    sys.path.insert(0, str(HARNESS))
    import teacher_pack_agent as agent  # noqa: PLC0415
    from agent_dispatch import _load_provider  # noqa: PLC0415 - reuse the harness provider plumbing
    provider = _load_provider()
    base, _model, _effort = provider.load_endpoint()
    keys = provider.ordered_keys()
    if not keys:
        return {"status": "failed", "error": "没有可用的 provider key"}
    audit = agent.build_pack(job, review_date=review, provider=provider, base=base, keys=keys,
                             chain=provider.default_chain(),
                             timeout=int(os.environ.get("VIDEO_AGENT_TIMEOUT", "900")))
    return {"status": "built" if not audit["validate_problems"] else "invalid", **audit}


def chips(job: pathlib.Path, review: str) -> dict[str, Any]:
    """筹码分布代理：正常由 harness 第二阶段生成；这里为旧任务或缺文件的情况补算。"""
    path = job / f"chip_evidence_{review.replace('-', '')}.json"
    existing = _read_json(path, {}) or {}
    if (existing.get("profiles") or {}):
        ok = sum(1 for row in existing["profiles"].values() if row.get("status") == "ok")
        return {"status": "exists", "profiles": len(existing["profiles"]), "ok": ok}
    evidence = _read_json(job / f"owner_market_evidence_{review.replace('-', '')}.json", {}) or {}
    closes = {symbol[:6]: row.get("close") for symbol, row in (evidence.get("stocks") or {}).items()
              if isinstance(row, dict) and row.get("close") is not None}
    if not closes:
        return {"status": "skipped", "reason": "证据里没有个股收盘价"}
    sys.path.insert(0, str(HARNESS))
    from chip_distribution import build_chip_evidence  # noqa: PLC0415
    built = build_chip_evidence(list(closes), base_url=OWNER_BASE, trade_date=review, closes=closes)
    _write_json(path, built)
    ok = sum(1 for row in built["profiles"].values() if row.get("status") == "ok")
    return {"status": "built", "profiles": len(built["profiles"]), "ok": ok,
            "errors": (built.get("errors") or [])[:5]}


def distill(job: pathlib.Path) -> dict[str, Any]:
    """重建方法库，并把次日结果贴到每条推演上。"""
    sys.path.insert(0, str(HARNESS))
    import teacher_method_library as lib  # noqa: PLC0415
    library = lib.build(JOBS, job)
    out = JOBS.parent / "teacher_method_library"
    _write_json(out.with_suffix(".json"), library)
    out.with_suffix(".md").write_text(lib.markdown(library["notes"]), encoding="utf-8")
    return {"counts": library["counts"], "structured": library["structured"], "file": str(out.with_suffix(".md"))}


def report(job: pathlib.Path, review: str, state: dict[str, Any]) -> pathlib.Path:
    # 驱动打印的 JSON 是子集（没有 overrides / carried_unmentioned），完整的在
    # pack_check_<T>.json 里；报告要读文件，否则"覆盖旧计划"永远是 0。
    check = _read_json(job / f"pack_check_{review.replace('-', '')}.json", {}) or {}
    check = check or ((state.get("check") or {}).get("json") or {})
    imported = ((state.get("import") or {}).get("json") or {}).get("import") or {}
    if not imported.get("status"):
        imported = ((state.get("import") or {}).get("json") or {})
    if not imported.get("status"):
        # 导入可能是人工在这套编排之外跑的（9/28 就是：check 卡住后人补了代码再导）。
        # 池子里的事实写在文件里，报告要读它，否则会一直写"未导入，等人确认"。
        imported = (_read_json(job / f"import_report_{review.replace('-', '')}.json", {}) or {}).get("import") or {}
    # ``pack`` 那一步第二轮就被 done() 跳过了，state 里存的是第一轮的 pack_id 和只数。
    # 包在那之后被改过（补代码、去掉板块条目），报告必须按文件现在的样子写。
    on_disk = _read_json(job / f"teacher_pack_{review.replace('-', '')}.json", {}) or {}
    pack = dict(state.get("pack") or {})
    if on_disk:
        pack.update({"pack_file": f"teacher_pack_{review.replace('-', '')}.json",
                     "pack_id": on_disk.get("pack_id"), "stocks": len(on_disk.get("stocks") or []),
                     "forecasts": len(on_disk.get("forecasts") or []),
                     "method_notes": len(on_disk.get("method_notes") or [])})
    # ``already imported`` 是"这个包已经在池子里"，是成功；只有其余的才是真阻断。
    already = [item for item in check.get("problems") or [] if "already imported" in str(item)]
    blocking = [item for item in check.get("problems") or [] if item not in already]
    lines = [f"# 老师复盘每日闭环 · {review}", "",
             f"任务 `{job.name}`｜下一交易日 {state.get('target_session') or '?'}｜"
             f"生成于 {dt.datetime.now(CN).isoformat(timespec='seconds')}", ""]
    for step in STEPS:
        entry = state.get(step)
        if step == "import" and imported.get("status") and not entry:
            # 这一轮没跑 import，但池子里已经有了（人工导的，或上一轮导的）。
            lines.append(f"- import：{imported['status']}（按 import_report 文件）")
            continue
        if entry is None:
            lines.append(f"- {step}：未执行")
            continue
        # 每种步骤报成功的方式不同：gate 给 ok、驱动给 returncode、pack 给 status、
        # distill 只给统计字段。把"没有失败迹象"当成功，否则 distill 明明跑出了
        # 48 条也会被写成失败。
        if entry.get("status"):
            status = str(entry["status"])
        elif entry.get("returncode") is not None:
            status = "ok" if entry["returncode"] == 0 else f"失败（exit {entry['returncode']}）"
            reason = str(entry.get("stderr_tail") or "").strip().splitlines()
            if status.startswith("失败") and reason:
                status += f"：{reason[-1][:120]}"
        elif entry.get("ok") is not None:
            status = "ok" if entry["ok"] else "不通过"
        else:
            status = "ok"
        if step == "check" and status.startswith("失败") and already and not blocking:
            status = f"ok（{already[0]}：本包已在池中，不重复导入）"
        lines.append(f"- {step}：{status}")
    lines.append("")
    if pack.get("pack_id"):
        lines += [f"**策略包** `{pack.get('pack_file')}`｜pack_id `{pack['pack_id']}`｜"
                  f"个股 {pack.get('stocks')} 只｜预判 {pack.get('forecasts')} 条｜"
                  f"量价推演 {pack.get('method_notes')} 条", ""]
    if check:
        counts = check.get("counts") or {}
        lines += [f"**检查**：阻断 {len(blocking)}、warnings {len(check.get('warnings') or [])}、"
                  f"推送 {counts.get('watched')}、只记录 {counts.get('record_only')}、"
                  f"覆盖旧计划 {len(check.get('overrides') or [])}、延续 {len(check.get('carried_unmentioned') or [])}", ""]
        for problem in (check.get("problems") or [])[:10]:
            label = "已导入" if "already imported" in str(problem) else "阻断"
            lines.append(f"- {label}：{problem}")
        lines.append("")
    if imported.get("status"):
        lines += [f"**导入**：{imported.get('status')}｜session {imported.get('session_date')}｜"
                  f"planned {len(imported.get('planned') or [])}｜plan_failures {len(imported.get('plan_failures') or [])}", ""]
    elif state.get("hold"):
        lines += [f"**未导入（等人确认）**：{state['hold']}", ""]
    # 写稿哪几节重试后还是写不全 —— 这条必须露在报告里，否则一份缺了半节的稿子
    # 只有交接清单里看得见，人不会去翻。
    thin = _read_json(job / "writer_short_passes.json", {}) or {}
    if thin.get("passes"):
        lines += [f"**写稿有 {len(thin['passes'])} 节重试 {thin.get('retries')} 次后仍不完整**：", ""]
        lines += [f"- {item}" for item in thin["passes"][:10]] + [""]
    sweep = ((state.get("sweep") or {}).get("json") or {})
    if sweep:
        lines += [f"**反事实扫描**：{json.dumps({k: v for k, v in sweep.items() if not isinstance(v, (list, dict))}, ensure_ascii=False)}", ""]
    gaps = state.get("pack_gaps") or []
    if gaps:
        lines += [f"**建包可能漏了 {len(gaps)} 只**（抽取里已绑定、有原话、有立场，但不在包里；"
                  "细则要求他给了看法的必须入包，回避就记 rejected）：", ""]
        for row in gaps[:10]:
            lines.append(f"- {row.get('name')}（{row.get('code')}）｜立场 {row.get('stance')}"
                         f"｜{row.get('time_range')}｜{row.get('teacher_judgment')}"
                         f"｜原话「{row.get('quote')}」")
        lines.append("")
    overlap = state.get("overlap") or {}
    if overlap.get("status") == "ok":
        counts = overlap.get("counts") or {}
        lines += [f"**与 owner 策略对照**：老师 {counts.get('teacher')} 只、owner {counts.get('owner')} 只；"
                  f"两边都选 {counts.get('both')}、只有老师 {counts.get('teacher_only')}、"
                  f"只有 owner {counts.get('owner_only')}", ""]
        for row in overlap.get("disagreements") or []:
            lines.append(f"- 分歧：{row.get('name')}（{row.get('code')}）owner 提名而老师明确回避"
                         f"，原话「{row.get('teacher_quote')}」")
        if overlap.get("disagreements"):
            lines.append("")
    elif overlap:
        lines += [f"**与 owner 策略对照**：{overlap.get('reason') or overlap.get('status')}", ""]
    chip = state.get("chips") or {}
    if chip.get("profiles"):
        lines += [f"**筹码分布代理**：{chip['profiles']} 只，其中可用 {chip.get('ok')} 只"
                  "（日 K 代理，按估算陈述）", ""]
    if state.get("distill"):
        lines += [f"**方法蒸馏**：{json.dumps(state['distill']['counts'], ensure_ascii=False)}，"
                  f"其中结构化 {state['distill']['structured']} 条 → `{state['distill']['file']}`", ""]
    lines += ["---", "", "研究记录，不构成交易指令。"]
    path = job / f"cycle_report_{review.replace('-', '')}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def in_session_now(now: dt.datetime) -> bool:
    """现在是不是真的盘中；和 teacher_review_daily 用同一份交易日历判断。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("teacher_review_daily", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return bool(module.in_session_now(now))


def import_state(entry: Any) -> tuple[str, str]:
    """这个任务的导入记录到底算什么：``(done|retry|ambiguous, 说明)``。

    以前跟其它步骤共用 ``done()``，只要 ``returncode == 0`` 就算导过了。手工跑时人会
    去看输出，自动化不会：驱动退出码为 0 但实际没导入，就会被永远跳过。所以这里要
    看真实状态，看不出来时明确报"记录不清楚"，既不重复导也不悄悄放弃。
    """
    if not isinstance(entry, dict):
        return "retry", ""
    status = str(((entry.get("json") or {}).get("import") or {}).get("status")
                 or (entry.get("json") or {}).get("status") or entry.get("status") or "")
    if status == "imported":
        return "done", "本任务已记录导入过（status=imported），不重复导入"
    if status:
        return "retry", ""
    if entry.get("returncode") == 0:
        return "ambiguous", ("上次导入退出码为 0 但记录里没有 status=imported，"
                            "无法确认是否真的入池；请人工核对 import_report 后再决定")
    return "retry", ""


def import_window(target_session: str | None, now: dt.datetime | None = None) -> tuple[bool, str]:
    """自动导入的时间窗：``(可以导, 理由)``。

    手工导入时人自己看得见钟点；自动化每 20 分钟一轮，稿子出晚了就会在**盘中**
    把"今天"的计划导进池子 —— 一个已经过了半场的交易日，计划不该再自动进池。
    盘中只拦"目标就是今天"的包；目标是未来某个交易日的照常导。
    """
    if not target_session:
        return False, "交接清单没有 target_session，不自动导入"
    now = now or dt.datetime.now(CN)
    today = now.date().isoformat()
    if target_session < today:
        # 目标交易日已经过去：这一场结算都做完了，再入池只会污染观察池。
        return False, f"目标交易日 {target_session} 已经过去（今天 {today}），不入池"
    if target_session != today:
        return True, ""
    if in_session_now(now):
        return False, (f"目标就是今天（{target_session}）且已开盘 "
                       f"{now:%H:%M}，盘中不自动入池；要用请人工确认后手动导入")
    if now.hour >= 15:
        return False, (f"目标就是今天（{target_session}）但已收盘 {now:%H:%M}，"
                       "这一场已经过去，不自动入池")
    return True, ""


def run_cycle(job: pathlib.Path, review: str, *, auto_import: bool, only: str | None = None) -> dict[str, Any]:
    state_path = job / f"cycle_{review.replace('-', '')}.json"
    state: dict[str, Any] = _read_json(state_path, {}) or {}
    # ``hold`` 描述的是**上一轮**为什么没往下走。这一轮一开始就丢掉它，否则一个早已
    # 解决的原因（比如隧道那会儿断了）会一直挂在报告和看板上，看起来像现在还坏着。
    state.pop("hold", None)
    state.update({"schema_version": "teacher-cycle/v1", "job_id": job.name, "review_date": review,
                  "updated_at": dt.datetime.now(CN).isoformat(timespec="seconds"), "research_only": True})

    def done(step: str) -> bool:
        entry = state.get(step)
        if not isinstance(entry, dict):
            return False
        return entry.get("ok") is True or entry.get("status") in {"built", "exists", "imported"} or entry.get("returncode") == 0

    if only in (None, "gate"):
        state["gate"] = gate(job, review)
        state["target_session"] = state["gate"].get("target_session")
        _write_json(state_path, state)
        if not state["gate"]["ok"]:
            state["hold"] = "；".join(state["gate"]["reasons"])
            _write_json(state_path, state)
            report(job, review, state)
            return state
    if only in (None, "chips") and not done("chips"):
        try:
            state["chips"] = chips(job, review)
        except Exception as error:  # noqa: BLE001 - 补充证据，不阻断
            state["chips"] = {"status": "failed", "error": str(error)[:300]}
        _write_json(state_path, state)
    if only in (None, "context") and not done("context"):
        state["context"] = _driver(["context", f"--date={review}"], job)
        _write_json(state_path, state)
    if only in (None, "pack") and not done("pack"):
        try:
            state["pack"] = run_pack(job, review)
        except Exception as error:  # noqa: BLE001 - 记录后由人接手
            state["pack"] = {"status": "failed", "error": str(error)[:500]}
        _write_json(state_path, state)
    if only in (None, "check"):
        state["check"] = _driver(["check"], job)
        # 确定性补充检查：抽取里已绑定、有原话、有立场，却没进包的票。建包是模型判断
        # 会漏（9/24 漏了奥佳华），这一条不依赖模型。只报 warning，不阻断。
        try:
            sys.path.insert(0, str(HARNESS))
            from teacher_board import dropped_bound_stocks
            state["pack_gaps"] = dropped_bound_stocks(job, review)
        except Exception as error:  # noqa: BLE001 - 补充检查失败不该拖垮 check
            state["pack_gaps"] = [{"error": str(error)[:200]}]
        _write_json(state_path, state)
    problems = ((state.get("check") or {}).get("json") or {}).get("problems")
    # ``pack <id> is already imported`` 说的是这个包已经在池子里了 —— 那是成功。
    # 以前它和"代码不是六位"一样被算成 problems 非空，于是当晚每一轮都报
    # "check 未通过 → 未导入（等人确认）"，看板上像还没入池。
    already = [item for item in problems or [] if "already imported" in str(item)]
    blocking = [item for item in problems or [] if item not in already]
    green = isinstance(problems, list) and not blocking
    if only in (None, "import"):
        if already:
            state["import"] = {"status": "imported", "json": _read_json(
                job / f"import_report_{review.replace('-', '')}.json", {}) or {"import": {"status": "imported"}}}
            state["import_note"] = f"{already[0]}（本包已在池中，不重复导入）"
        elif not green:
            state["hold"] = "check 未通过（problems 非空或没跑出 JSON），不导入"
        elif not auto_import:
            state["hold"] = "check 全绿，但 TEACHER_CYCLE_AUTO_IMPORT 未开启，等人确认后导入"
        elif (ledger := import_state(state.get("import")))[0] != "retry":
            state["hold"] = ledger[1]
        elif not (window := import_window(state.get("target_session")))[0]:
            state["hold"] = window[1]
        else:
            state["import"] = _driver(["import"], job, timeout=3600)
            state.pop("hold", None)
        _write_json(state_path, state)
    if only in (None, "sweep"):
        # 反事实扫描：换一个阈值会多抓到什么、又会多放进来什么。只量，不改。
        # 「没触发却大涨」要先在这里量过，才有资格谈改阈值。
        state["sweep"] = _driver(["sweep", f"--date={review}"], job, timeout=1800)
        _write_json(state_path, state)
    if only in (None, "overlap"):
        # 与 owner 自己策略的同日对照。只读；对不齐交易日就明说，不硬凑。
        try:
            sys.path.insert(0, str(HARNESS))
            from teacher_owner_overlap import compare as compare_owner
            state["overlap"] = compare_owner(review)
            _write_json(job / f"overlap_{review.replace('-', '')}.json", state["overlap"])
        except Exception as error:  # noqa: BLE001 - 对照失败不阻断别的步骤
            state["overlap"] = {"status": "failed", "error": str(error)[:300]}
        _write_json(state_path, state)
    if only in (None, "distill"):
        try:
            state["distill"] = distill(job)
        except Exception as error:  # noqa: BLE001
            state["distill"] = {"status": "failed", "error": str(error)[:300]}
        _write_json(state_path, state)
    state["report_file"] = str(report(job, review, state))
    _write_json(state_path, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job_dir", nargs="?", type=pathlib.Path, help="harness 任务目录；省略则自动挑最新一个")
    parser.add_argument("--date", help="复盘交易日 T，默认取交接清单里的")
    parser.add_argument("--only", choices=STEPS, help="只跑其中一步")
    parser.add_argument("--auto-import", action="store_true",
                        help="check 全绿时直接导入；也可用 TEACHER_CYCLE_AUTO_IMPORT=1")
    args = parser.parse_args()
    auto = args.auto_import or os.environ.get("TEACHER_CYCLE_AUTO_IMPORT") == "1"
    if args.job_dir:
        job = args.job_dir.resolve()
        review = args.date or str(handoff_of(job).get("review_date") or "")
    else:
        candidates = eligible_jobs()
        if not candidates:
            print(json.dumps({"status": "skipped", "reason": "没有可处理的任务"}, ensure_ascii=False))
            return 0
        job, review = candidates[-1]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", review or ""):
        print(json.dumps({"status": "failed", "reason": "无法确定复盘交易日，请用 --date"}, ensure_ascii=False))
        return 2
    state = run_cycle(job, review, auto_import=auto, only=args.only)
    problems = (((state.get("check") or {}).get("json") or {}).get("problems") or [])
    summary = {"job_id": job.name, "review_date": review, "target_session": state.get("target_session"),
               "pack": (state.get("pack") or {}).get("status"),
               # 包在这一轮之外被改过也要报对：pack_id 按文件读，不按 state。
               "pack_id": (_read_json(job / f"teacher_pack_{review.replace('-', '')}.json", {}) or {}
                           ).get("pack_id") or (state.get("pack") or {}).get("pack_id"),
               "check_problems": len([item for item in problems if "already imported" not in str(item)]),
               "import": (((state.get("import") or {}).get("json") or {}).get("status")
                          or ((_read_json(job / f"import_report_{review.replace('-', '')}.json", {}) or {}
                               ).get("import") or {}).get("status")),
               "hold": state.get("hold"), "report": state.get("report_file")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
