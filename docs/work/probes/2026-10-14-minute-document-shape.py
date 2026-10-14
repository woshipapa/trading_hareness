"""B058: what one all-A minute document holds (column names, constants, one row), main source and TDX shadow.
Read-only; one document each; no row bodies beyond one symbol. Run inside the owner's quant-research container."""
import json
from app import minute_cross_section as mcs
from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    for provider, capability in (("fuyao_ths", "a_share_minute_cross_section"),
                                 ("tdx_public", "a_share_minute_cross_section_shadow")):
        row = connection.execute(
            """SELECT effective_at, normalized FROM quant.raw_market_observations
                WHERE provider_key=%s AND capability=%s AND symbol IS NULL
                  AND effective_at >= '2026-10-14 11:10:00+08' ORDER BY effective_at LIMIT 1""", (provider, capability)).fetchone()
        document = row["normalized"] if isinstance(row["normalized"], dict) else json.loads(row["normalized"])
        print(provider, capability, row["effective_at"], "schema", document.get("schema"), "rows", document.get("rows"))
        print("  constants:", json.dumps(document.get("constants"), ensure_ascii=False, default=str)[:600])
        print("  columns:", sorted((document.get("columns") or {}).keys()))
        print("  aliases:", document.get("aliases"))
        print("  metadata:", json.dumps(document.get("metadata"), ensure_ascii=False, default=str)[:500])
        symbols = mcs.rows_for(document, ["000001.SZ"])
        print("  one row:", json.dumps(symbols.get("000001.SZ"), ensure_ascii=False, default=str)[:1200])
