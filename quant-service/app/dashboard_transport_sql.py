"""Project displayed fields in PostgreSQL before traversing the shared tunnel.

Full research evidence stays stored and remains available to detail readers.
These expressions preserve the existing compact counter and board curve views.
"""


def counter_summary_sql() -> str:
    return """(SELECT coalesce(jsonb_object_agg(k,v),'{}'::jsonb)
        FROM jsonb_each(summary) AS entry(k,v)
        WHERE k IN ('reason','returned','base_ready_30d','fresh_start_15d',
                    'base_forming_15d','eligible_candidates')) AS summary"""


def board_curve_payload_sql() -> str:
    # One bind parameter selects taxonomy; duplicates and value types survive.
    # The consumer reads exactly these fields, coverage is selected separately.
    return """jsonb_build_object('items',coalesce((SELECT jsonb_agg(
        jsonb_build_object('taxonomy_key',item->'taxonomy_key',
                          'sector_key',item->'sector_key','label',item->'label',
                          'net_inflow',item->'net_inflow','change_pct',item->'change_pct'))
        FROM jsonb_array_elements(coalesce(nullif(payload->'items','null'::jsonb),'[]'::jsonb)) AS item
        WHERE item->>'taxonomy_key'=%s),'[]'::jsonb)) AS payload"""
