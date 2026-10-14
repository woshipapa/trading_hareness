"""After the backfill: does the main minute series (the readers' view) hold the minutes 09:30-11:05 of 2026-10-14?
Read-only; counts and times only. Run inside the owner's quant-research container."""
from collections import Counter
from datetime import date
from app import minute_cross_section as mcs
from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '30s'")
    print("providers read:", mcs.DOCUMENT_PROVIDERS)
    rows = connection.execute(
        """SELECT provider_key, count(*) AS n, min(effective_at AT TIME ZONE 'Asia/Shanghai') AS first,
                  max(effective_at AT TIME ZONE 'Asia/Shanghai') AS last
             FROM quant.raw_market_observations
            WHERE capability='a_share_minute_cross_section' AND symbol IS NULL
              AND effective_at >= '2026-10-14 00:00:00+08' AND effective_at < '2026-10-15 00:00:00+08'
            GROUP BY 1 ORDER BY 1""").fetchall()
    for row in rows:
        print(f"  {row['provider_key']:<10} n={row['n']:>4} {row['first']:%H:%M:%S} .. {row['last']:%H:%M:%S}")
    documents = mcs.day_documents(connection, date(2026, 10, 14))
    minutes = sorted(at.astimezone().strftime("%H:%M") for at, _ in documents)
    print("day_documents:", len(documents))
    covered = set(minutes)
    wanted = [f"{h:02d}:{m:02d}" for h in (9, 10, 11) for m in range(60)]
    wanted = [x for x in wanted if "09:30" <= x <= "11:29"]
    # day_documents returns times in the database's zone; print the missing minutes of the morning session instead
    missing = [x for x in wanted if x not in covered]
    print("morning minutes 09:30-11:29 without a document:", len(missing), missing[:12])
    sample = mcs.first_between(connection, __import__("datetime").datetime(2026, 10, 14, 10, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai")),
                               __import__("datetime").datetime(2026, 10, 14, 10, 1, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai")))
    if sample:
        at, doc = sample
        row = mcs.rows_for(doc, ["000001.SZ"]).get("000001.SZ")
        print("10:00 minute document:", at.isoformat(), "metadata.provider", (doc.get("metadata") or {}).get("provider"),
              "reconstructed", (doc.get("metadata") or {}).get("reconstructed"), "000001.SZ price", row and row.get("price"))
