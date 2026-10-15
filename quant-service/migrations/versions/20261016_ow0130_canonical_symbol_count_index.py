"""Index canonical symbols for feature-readiness cardinality reads.

Revision ID: 20261016_ow0130
Revises: 20261016_ow0129
"""

from alembic import op


revision = "20261016_ow0130"
down_revision = "20261016_ow0129"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Feature readiness counts distinct valid symbols across the whole
    # canonical history.  Keep the date in the index payload so PostgreSQL
    # can answer that cardinality and latest-date projection index-only.
    op.execute("DROP INDEX IF EXISTS quant.canonical_bars_daily_symbol_count_idx")
    op.execute("""
        CREATE INDEX canonical_bars_daily_symbol_count_idx
            ON quant.canonical_bars_daily(symbol) INCLUDE (trading_date)
            WHERE symbol <> '000300.SH' AND adj_factor > 0
    """)
    # Rebuild the two indexes introduced by 0129 with the date payload as
    # well.  The readiness projection returns both distinct symbols and the
    # latest date, so this keeps those scans index-only where visibility
    # permits it without adding another duplicate symbol index.
    for table, index in (
        ("daily_fundamentals", "daily_fundamentals_symbol_count_idx"),
        ("daily_trade_limits", "daily_trade_limits_symbol_count_idx"),
    ):
        op.execute(f"DROP INDEX IF EXISTS quant.{index}")
        op.execute(f"""
            CREATE INDEX {index}
                ON quant.{table}(symbol) INCLUDE (trading_date)
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS quant.canonical_bars_daily_symbol_count_idx")
    op.execute("DROP INDEX IF EXISTS quant.daily_trade_limits_symbol_count_idx")
    op.execute("DROP INDEX IF EXISTS quant.daily_fundamentals_symbol_count_idx")
    # 0129 owns these two narrow symbol indexes.  Restore their pre-0130
    # definitions so a downgrade leaves the schema at the 0129 contract.
    op.execute("""
        CREATE INDEX IF NOT EXISTS daily_fundamentals_symbol_count_idx
            ON quant.daily_fundamentals(symbol)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS daily_trade_limits_symbol_count_idx
            ON quant.daily_trade_limits(symbol)
    """)
