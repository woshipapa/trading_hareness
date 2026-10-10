"""Static completeness inventory for source adapters.

The check intentionally parses source modules instead of importing them: validation
must remain safe in a container without provider credentials or network access.
"""

from __future__ import annotations

import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).with_name("sources")

# Public data readers that are intentionally not capability bindings yet.
UNREGISTERED: dict[str, str] = {
    "tdx_protocol.call": "transport primitive used by the ticks adapter; no standalone capability",
    "tdx_protocol.call_sync": "transport primitive used by the ticks adapter; no standalone capability",
    "tdx_local_files.read_file": "offline import helper; callers bind the byte parsers",
    "tdx_local_files.discover": "offline file discovery helper; no independent row contract",
    "tdx_command_family:F10": "verified in the plan but scheduled for P5 company-profile capability",
    "tdx_command_family:finance": "verified in the plan but scheduled for P5 financial-statements capability",
    "tdx_command_family:files": "verified in the plan but scheduled for P4 local-file capability",
    "tdx_command_family:boards": "verified in the plan but scheduled for P3 board capabilities",
    "tdx_command_family:capital flow": "verified in the plan but scheduled for P3 flow capability",
    "tdx_command_family:MAC": "verified in the plan but scheduled for P3 MAC capabilities",
    "tdx_command_family:extended": "7727 extended-market handshake is unsolved (plan F7/P9)",
}

TDX_COMMAND_FAMILIES = (
    "quote", "bars", "minute", "ticks", "auction", "F10", "finance", "files", "boards",
    "capital flow", "MAC", "extended",
)

# The source functions below are referenced directly or through the package
# binding closures. Keeping this set explicit makes a newly added reader fail
# validation until it is bound or documented in UNREGISTERED.
BOUND_FETCH_FUNCTIONS = frozenset({
    "eastmoney_hot_rank.fetch_rank_list", "eastmoney_hot_rank.fetch_rank_history",
    "fuyao_evidence.fetch_all_pool_pages", "fuyao_evidence.fetch_code_batches",
    "tencent_limits.session_limit_cross_section",
    "ticks.fetch_tencent_ticks", "ticks.fetch_tdx_ticks", "ticks.fetch_tdx_capital_changes",
    "eastmoney_ztb.fetch_pool", "eastmoney_ztb.fetch_stock_changes", "eastmoney_ztb.fetch_board_changes",
    "eastmoney_datacenter.fetch_report", "eastmoney_datacenter.period_report", "eastmoney_datacenter.suspensions_on",
    "ttfund.fetch_nav_history", "news_flash.fetch_cls", "news_flash.fetch_jin10",
    "news_flash.fetch_eastmoney", "news_flash.fetch_ths", "xuangubao_pool.fetch_pool",
    "investor_qa.fetch_cninfo", "investor_qa.fetch_sse",
})


def public_fetch_functions() -> tuple[str, ...]:
    result: list[str] = []
    for path in sorted(SOURCE_ROOT.glob("*.py")):
        module = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
                continue
            if isinstance(node, ast.AsyncFunctionDef) or node.name.startswith("fetch_") or node.name in {
                "session_limit_cross_section", "period_report", "suspensions_on", "read_file", "discover",
                "call", "call_sync",
            }:
                result.append(f"{module}.{node.name}")
    return tuple(result)


def completeness_problems(capability_keys: set[str] | None = None) -> list[str]:
    problems = []
    for function in public_fetch_functions():
        if function not in BOUND_FETCH_FUNCTIONS and function not in UNREGISTERED:
            problems.append(f"unregistered source fetch function: {function}")
    keys = capability_keys or set()
    for family in TDX_COMMAND_FAMILIES:
        covered = (
            (family == "quote" and any(key.startswith("quote.") for key in keys))
            or (family in {"bars", "minute"} and any(key.startswith("bars.") for key in keys))
            or (family == "ticks" and any(key.startswith("ticks.") for key in keys))
            or (family == "auction" and any(key.startswith("auction.") for key in keys))
            or (family == "capital flow" and any(key.startswith("flow.") for key in keys))
            or f"tdx_command_family:{family}" in UNREGISTERED
        )
        if not covered:
            problems.append(f"unaccounted TDX command family: {family}")
    return problems


__all__ = ["BOUND_FETCH_FUNCTIONS", "TDX_COMMAND_FAMILIES", "UNREGISTERED", "completeness_problems", "public_fetch_functions"]
