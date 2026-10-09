"""Which stored industry membership names a stock's industry, most trusted first.

Longhu's 104 licensed boards cover the whole market at one level and are
refreshed every session; the Fuyao-served 同花顺 industry list follows (it
spans several levels, so it only fills symbols Longhu lacks); the Tushare-era
``ths_industry``/``ths_index_i`` rows stopped refreshing on 2026-10-08 and
serve the dates before the live lists existed.  A reader that groups stocks by
industry compares each stock with whichever board this order picks.
"""

from __future__ import annotations

INDUSTRY_TAXONOMIES = ("longhu_ths_industry", "fuyao_ths_industry", "ths_industry", "ths_index_i")


def industry_taxonomies_sql() -> str:
    return ",".join(f"'{taxonomy}'" for taxonomy in INDUSTRY_TAXONOMIES)


def industry_preference_sql(alias: str = "member") -> str:
    return f"CASE {alias}.taxonomy_key " + " ".join(
        f"WHEN '{taxonomy}' THEN {rank}" for rank, taxonomy in enumerate(INDUSTRY_TAXONOMIES)) + " END"


__all__ = ["INDUSTRY_TAXONOMIES", "industry_preference_sql", "industry_taxonomies_sql"]
