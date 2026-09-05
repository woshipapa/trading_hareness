from app.longhu_capability_probe import sanitized_request, summarize_result


def test_sanitized_request_removes_credentials_and_bounds_lists():
    request = {
        "target": "longhu_quote",
        "params": {"a": "GetStockBid", "Token": "secret", "nested": {"UserID": "u"}},
        "batch": {"param": "StockIDs", "values": list(range(30))},
    }
    projection = sanitized_request(request)
    assert "Token" not in projection["params"]
    assert "UserID" not in projection["params"]["nested"]
    assert len(projection["batch"]["values"]) == 20


def test_summarize_result_is_research_only_and_contains_shapes_not_rows():
    summary = summarize_result({
        "target": "longhu_history",
        "calls": 1,
        "pages": [{"payload": {"errcode": 0, "data": [{"x": 1}, {"x": 2}]}}],
    })
    assert summary["status"] == "completed"
    assert summary["page_shapes"][0]["type"] == "object"
    assert summary["research_only"] is True
    assert summary["live_effect"] == "none"
    assert "data" not in summary


def test_summarize_result_marks_empty_or_error_as_failed_or_partial():
    assert summarize_result({"target": "x", "pages": []})["status"] == "failed"
    assert summarize_result({"target": "x", "pages": [{"payload": {"errcode": 7}}]})["status"] == "partial"
