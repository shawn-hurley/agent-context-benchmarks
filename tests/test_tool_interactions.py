"""Tests for report-time tool call/result normalization."""

from acb.html_report import _content_type_breakdown, _normalize_tool_interactions


def _record(request_id, content_type, tokens, call_id, name="bash"):
    return {
        "request_id": request_id,
        "content_type": content_type,
        "input_tokens": tokens,
        "output_tokens": 0,
        "tools": [{"name": name, "call_id": call_id}],
    }


def test_matching_call_and_result_are_one_interaction():
    interactions = _normalize_tool_interactions([
        _record("request-call", "tool_call", 100, "call-1"),
        _record("request-result", "tool_result", 200, "call-1"),
    ])

    assert len(interactions) == 1
    assert interactions[0]["tokens"] == 300
    assert interactions[0]["complete_pair"] is True
    assert interactions[0]["request_ids"] == ["request-call", "request-result"]


def test_unmatched_records_are_retained_with_status():
    interactions = _normalize_tool_interactions([
        _record("request-call", "tool_call", 100, "call-only"),
        _record("request-result", "tool_result", 200, "result-only"),
    ])

    assert len(interactions) == 2
    assert sum(i["call_only"] for i in interactions) == 1
    assert sum(i["result_only"] for i in interactions) == 1


def test_breakdown_reports_unified_tool_call_type():
    breakdown = _content_type_breakdown([
        _record("request-call", "tool_call", 100, "call-1"),
        _record("request-result", "tool_result", 200, "call-1"),
    ])

    assert set(breakdown["by_type"]) == {"tool_call"}
    assert breakdown["by_type"]["tool_call"]["count"] == 1
    assert breakdown["by_type"]["tool_call"]["total_tokens"] == 300
    assert breakdown["tool_usage"]["bash"]["interactions"] == 1
