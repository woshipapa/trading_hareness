"""Write resolved sector outcomes without per-row network transactions."""
from typing import Any
from psycopg.types.json import Json


def upsert_outcomes(connection: Any, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    rows = sorted(items, key=lambda r: (r["taxonomy_key"], r["sector_key"], r["trading_date"], r["horizon_days"]))
    fields = ("taxonomy_key", "sector_key", "trading_date", "horizon_days", "transition", "status",
              "entry_date", "exit_date", "entry_close", "exit_close", "raw_return", "excess_return",
              "directional_return", "outcome_available_at")
    arrays = tuple([r.get(key) for r in rows] for key in fields)
    connection.execute(
        """INSERT INTO quant.sector_flow_daily_outcomes(
             taxonomy_key,sector_key,signal_date,horizon_days,transition,status,
             entry_date,exit_date,entry_close,exit_close,raw_return,cross_section_excess_return,
             directional_return,outcome_available_at,quality_flags)
           SELECT t.taxonomy_key,t.sector_key,t.signal_date,t.horizon_days,t.transition,t.status,
             t.entry_date,t.exit_date,t.entry_close,t.exit_close,t.raw_return,t.excess_return,
             t.directional_return,t.outcome_available_at,f.flags::jsonb FROM unnest(
             %s::text[],%s::text[],%s::date[],%s::int[],%s::text[],%s::text[],
             %s::date[],%s::date[],%s::numeric[],%s::numeric[],%s::numeric[],%s::numeric[],
             %s::numeric[],%s::timestamptz[]) WITH ORDINALITY AS t(
             taxonomy_key,sector_key,signal_date,horizon_days,transition,status,
             entry_date,exit_date,entry_close,exit_close,raw_return,excess_return,
             directional_return,outcome_available_at,ordinal)
           JOIN json_array_elements(%s::json) WITH ORDINALITY AS f(flags,ordinal)
             USING (ordinal)
           ORDER BY t.taxonomy_key,t.sector_key,t.signal_date,t.horizon_days
           ON CONFLICT(taxonomy_key,sector_key,signal_date,horizon_days) DO UPDATE SET
             transition=EXCLUDED.transition,status=EXCLUDED.status,entry_date=EXCLUDED.entry_date,
             exit_date=EXCLUDED.exit_date,entry_close=EXCLUDED.entry_close,exit_close=EXCLUDED.exit_close,
             raw_return=EXCLUDED.raw_return,cross_section_excess_return=EXCLUDED.cross_section_excess_return,
             directional_return=EXCLUDED.directional_return,
             outcome_available_at=EXCLUDED.outcome_available_at,quality_flags=EXCLUDED.quality_flags,
             updated_at=now()
           -- The whole history is re-settled every evening and most of it is
           -- unchanged; such a row keeps its version (no dead tuple, no index churn).
           WHERE (quant.sector_flow_daily_outcomes.transition,quant.sector_flow_daily_outcomes.status,
                  quant.sector_flow_daily_outcomes.entry_date,quant.sector_flow_daily_outcomes.exit_date,
                  quant.sector_flow_daily_outcomes.entry_close,quant.sector_flow_daily_outcomes.exit_close,
                  quant.sector_flow_daily_outcomes.raw_return,quant.sector_flow_daily_outcomes.cross_section_excess_return,
                  quant.sector_flow_daily_outcomes.directional_return,
                  quant.sector_flow_daily_outcomes.outcome_available_at,quant.sector_flow_daily_outcomes.quality_flags)
             IS DISTINCT FROM
                 (EXCLUDED.transition,EXCLUDED.status,EXCLUDED.entry_date,EXCLUDED.exit_date,EXCLUDED.entry_close,
                  EXCLUDED.exit_close,EXCLUDED.raw_return,EXCLUDED.cross_section_excess_return,
                  EXCLUDED.directional_return,EXCLUDED.outcome_available_at,EXCLUDED.quality_flags)""",
        (*arrays, Json([r.get("quality_flags", []) for r in rows])),
    )
