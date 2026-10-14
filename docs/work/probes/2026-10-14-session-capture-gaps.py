"""B058 / W008: what did the owner host's outage on 2026-10-14 cost? Today's capture streams, counts and times only.
Read-only. Run inside the owner's quant-research container, stdin = this file."""
from app.database import Database

DAY = "2026-10-14"
database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    print("now (database):", connection.execute("SELECT now() AT TIME ZONE 'Asia/Shanghai' AS t").fetchone()["t"])
    rows = connection.execute(
        """SELECT provider_key, capability, (symbol IS NULL) AS market_wide, count(*) AS n,
                  min(effective_at AT TIME ZONE 'Asia/Shanghai') AS first, max(effective_at AT TIME ZONE 'Asia/Shanghai') AS last
             FROM quant.raw_market_observations
            WHERE effective_at >= %s::date::timestamp AT TIME ZONE 'Asia/Shanghai'
              AND effective_at < (%s::date + 1)::timestamp AT TIME ZONE 'Asia/Shanghai'
            GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 60""", (DAY, DAY)).fetchall()
    for row in rows:
        print(f"  {row['provider_key']:<22} {row['capability']:<40} market_wide={row['market_wide']!s:<5} n={row['n']:>6} "
              f"{row['first']:%H:%M:%S} .. {row['last']:%H:%M:%S}")
    # The two minute streams: which minutes of the session so far have a document, and the second it is stamped with.
    for provider, capability in (("fuyao_ths", "a_share_minute_cross_section"), ("tdx_public", "a_share_minute_cross_section_shadow")):
        minutes = connection.execute(
            """SELECT to_char(effective_at AT TIME ZONE 'Asia/Shanghai', 'HH24:MI') AS minute,
                      min(extract(second FROM effective_at))::int AS second, count(*) AS n
                 FROM quant.raw_market_observations
                WHERE provider_key = %s AND capability = %s AND symbol IS NULL
                  AND effective_at >= %s::date::timestamp AT TIME ZONE 'Asia/Shanghai'
                  AND effective_at < (%s::date + 1)::timestamp AT TIME ZONE 'Asia/Shanghai'
                GROUP BY 1 ORDER BY 1""", (provider, capability, DAY, DAY)).fetchall()
        have = [row["minute"] for row in minutes]
        seconds = sorted({row["second"] for row in minutes})
        runs, start, previous = [], None, None
        for minute in have:
            value = int(minute[:2]) * 60 + int(minute[3:])
            if previous is None or value != previous + 1:
                if start is not None:
                    runs.append((start, last_label))
                start = minute
            previous, last_label = value, minute
        if start is not None:
            runs.append((start, last_label))
        print(f"  {provider} {capability}: {len(have)} minutes; stamped seconds {seconds[:12]}; runs {runs[:20]}")
    for row in connection.execute(
            """SELECT capability, count(*) AS n, max(available_at AT TIME ZONE 'Asia/Shanghai') AS stored
                 FROM quant.raw_market_observations
                WHERE provider_key = 'local_derived' AND effective_at = (%s::date + time '15:00') AT TIME ZONE 'Asia/Shanghai'
                GROUP BY 1 ORDER BY 1""", (DAY,)).fetchall():
        print(f"  derived for {DAY}: {row['capability']} n={row['n']} stored {row['stored']}")
