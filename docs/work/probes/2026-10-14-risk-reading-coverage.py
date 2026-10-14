"""B067: how big the gap is when the limit-up risk reading is stored, per session (read-only, counts only).

For the last 12 sessions with a reading: the reading's own coverage (missing_bar, rows, excluded), the
stage receipt, and the day's daily-bar count. Run inside the owner's quant-research container (README).
"""
import json

from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '30s'")
    readings = connection.execute(
        """SELECT DISTINCT ON (effective_at) (effective_at AT TIME ZONE 'Asia/Shanghai')::date AS day,
                  available_at AT TIME ZONE 'Asia/Shanghai' AS stored_at,
                  normalized->'coverage' AS coverage, normalized->'ranks_reliable' AS reliable,
                  jsonb_array_length(coalesce(normalized->'rows', '[]'::jsonb)) AS rows
             FROM quant.raw_market_observations
            WHERE provider_key='local_derived' AND capability='limit_up_risk_daily' AND symbol IS NULL
            ORDER BY effective_at DESC, available_at DESC LIMIT 12""").fetchall()
    for row in readings:
        bars = connection.execute(
            "SELECT count(*) AS n FROM quant.canonical_bars_daily WHERE trading_date=%s", (row["day"],)).fetchone()["n"]
        receipt = connection.execute(
            """SELECT status, updated_at AT TIME ZONE 'Asia/Shanghai' AS at
                 FROM quant.automation_runs
                WHERE task_key='post_close_refresh.stage' AND as_of_date=%s AND input_summary->>'stage'='peer_limit_up_risk'
                ORDER BY updated_at DESC LIMIT 1""", (row["day"],)).fetchone()
        coverage = row["coverage"] if isinstance(row["coverage"], dict) else json.loads(row["coverage"] or "{}")
        print(json.dumps({
            "day": str(row["day"]), "first_stored_of_newest_version": str(row["stored_at"])[:19], "rows": row["rows"],
            "ranks_reliable": row["reliable"], "missing_bar": coverage.get("missing_bar"),
            "limit_ratio": coverage.get("limit_ratio"), "not_at_limit": coverage.get("not_at_limit"),
            "insufficient_history": coverage.get("insufficient_history"), "bars": bars,
            "receipt": None if receipt is None else f"{receipt['status']} {str(receipt['at'])[:16]}",
        }, ensure_ascii=False))
