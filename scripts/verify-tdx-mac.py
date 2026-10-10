#!/usr/bin/env python3
"""Read-only MAC protocol probe; no credentials and no writes."""
from __future__ import annotations
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "quant-service"))
from app.datasources.sources import tdx_mac, tdx_protocol

SYMBOLS = ("000001.SZ", "600519.SH")

def main() -> int:
    print("MAC hosts:", ", ".join(f"{h}:{p}" for h, p in tdx_mac.configured_hosts()))
    print("Go toolchain:", shutil.which("go") or "unavailable")
    try:
        def run(client):
            results = {}
            for board_type in range(7):
                boards = client.board_list(board_type)
                results[f"board_type_{board_type}"] = len(boards)
                results[f"board_type_{board_type}_sample"] = ",".join(row["code"] for row in boards[:3])
            results["board_members_881376"] = len(client.board_members("881376"))
            results["board_member_quotes_881376"] = len(client.board_member_quotes("881376"))
            for symbol in SYMBOLS:
                market, code = tdx_protocol.market_code(symbol)
                results[f"{symbol}.batch_quotes"] = len(client.batch_quotes([(market, code)]))
                results[f"{symbol}.bars"] = len(client.bars(market, code, count=5))
            for opcode, name in ((tdx_mac.OP_AUCTION, "auction"), (tdx_mac.OP_TICK_CHARTS, "tick_charts"), (tdx_mac.OP_MARKET_MONITOR, "market_monitor"), (tdx_mac.OP_BELONG_BOARD, "belong_board")):
                try:
                    results[name] = tdx_mac.parse_auxiliary_count(opcode, client.auxiliary(opcode, 0, "000001"))
                except Exception as exc:
                    results[name] = f"error:{type(exc).__name__}"
            return results
        results, host = tdx_mac.call_sync(run)
        print("Answered:", host)
        for key, value in results.items():
            print(f"{key}: {value} rows")
    except Exception as exc:  # probe is diagnostic; keep the failure visible
        print(f"MAC probe failed: {type(exc).__name__}: {exc}")
        return 2
    if shutil.which("go"):
        print("gotdx row-for-row comparison: not run (no vendored gotdx runner configured; set GOTDX_DIR to a checkout to compare).")
    else:
        print("gotdx row-for-row comparison: skipped (Go toolchain unavailable).")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
