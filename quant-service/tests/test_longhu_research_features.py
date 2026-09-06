from datetime import date, datetime, timezone

from app.longhu_research_features import normalize_payload, payload_rows, strict_symbol


def test_strict_symbol_requires_exchange_or_known_code_prefix():
    assert strict_symbol("600000.SH") == "600000.SH"
    assert strict_symbol("SZ000001") == "000001.SZ"
    assert strict_symbol("bad") is None


def test_payload_rows_preserves_vendor_array_shape_and_normalizes_provenance():
    payload = {"errcode": 0, "list": [["600000", 1], ["000001.SZ", 2]]}
    assert payload_rows(payload) == payload["list"]
    rows = normalize_payload(
        target="longhu_market_wide", action="GetPlateInfo_w38", controller="DailyLimitResumption",
        payload=payload, trade_date=date(2026, 9, 4),
        observed_at=datetime(2026, 9, 4, 8, tzinfo=timezone.utc),
    )
    assert len(rows) == 2
    assert rows[0]["target"] == "longhu_market_wide"
    assert rows[0]["exchange_date"] == "2026-09-04"
    assert rows[0]["research_only"] is True
    assert rows[0]["live_effect"] == "none"
    assert rows[0]["payload"] == ["600000", 1]
