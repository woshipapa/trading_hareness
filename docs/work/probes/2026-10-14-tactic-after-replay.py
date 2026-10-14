"""W006 after the history replay: how many sessions each capability of the ledger holds, and the newest. Read-only.
Run inside the owner's quant-research container, stdin = this file."""
from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    for row in connection.execute(
            """SELECT capability, count(DISTINCT effective_at) AS sessions, count(*) AS documents,
                      min(effective_at AT TIME ZONE 'Asia/Shanghai')::date AS first, max(effective_at AT TIME ZONE 'Asia/Shanghai')::date AS last
                 FROM quant.raw_market_observations
                WHERE provider_key = 'local_derived' AND capability LIKE 'tactic\\_trials\\_%' ESCAPE '\\'
                GROUP BY 1 ORDER BY 1""").fetchall():
        print(f"  {row['capability']:<36} sessions {row['sessions']:>4} documents {row['documents']:>5} {row['first']} .. {row['last']}")
