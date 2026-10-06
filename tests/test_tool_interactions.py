"""Tests for report-time tool call/result normalization."""

from acb.report_metrics import content_type_breakdown, normalize_tool_interactions, shell_command_breakdown


def _record(request_id, content_type, tokens, call_id, name="bash"):
    return {
        "request_id": request_id,
        "content_type": content_type,
        "input_tokens": tokens,
        "output_tokens": 0,
        "tools": [{"name": name, "call_id": call_id}],
    }


def test_matching_call_and_result_are_one_interaction():
    interactions = normalize_tool_interactions([
        _record("request-call", "tool_call", 100, "call-1"),
        _record("request-result", "tool_result", 200, "call-1"),
    ])

    assert len(interactions) == 1
    assert interactions[0]["tokens"] == 300
    assert interactions[0]["complete_pair"] is True
    assert interactions[0]["request_ids"] == ["request-call", "request-result"]


def test_unmatched_records_are_retained_with_status():
    interactions = normalize_tool_interactions([
        _record("request-call", "tool_call", 100, "call-only"),
        _record("request-result", "tool_result", 200, "result-only"),
    ])

    assert len(interactions) == 2
    assert sum(i["call_only"] for i in interactions) == 1
    assert sum(i["result_only"] for i in interactions) == 1


def test_breakdown_reports_unified_tool_call_type():
    breakdown = content_type_breakdown([
        _record("request-call", "tool_call", 100, "call-1"),
        _record("request-result", "tool_result", 200, "call-1"),
    ])

    assert set(breakdown["by_type"]) == {"tool_call"}
    assert breakdown["by_type"]["tool_call"]["count"] == 1
    assert breakdown["by_type"]["tool_call"]["total_tokens"] == 300
    assert breakdown["tool_usage"]["bash"]["interactions"] == 1


def test_response_tool_call_is_completed_by_result_metadata():
    call = _record("request-call", "tool_call", 100, "call-1")
    call["tool_results"] = [{"name": "bash", "call_id": "call-1"}]
    interactions = normalize_tool_interactions([call])

    assert interactions[0]["complete_pair"] is True
    assert interactions[0]["result_only"] is False


def test_duplicate_call_ids_are_one_logical_interaction():
    first = _record("request-call-1", "tool_call", 100, "call-1", name="shell")
    second = _record("request-call-2", "tool_call", 200, "call-1", name="shell")
    interactions = normalize_tool_interactions([first, second])

    assert len(interactions) == 1
    assert interactions[0]["tokens"] == 300
    assert interactions[0]["request_ids"] == ["request-call-1", "request-call-2"]


def test_result_and_next_call_in_one_response_keep_both_interactions():
    first = _record("request-1", "tool_call", 100, "call-1", name="Bash")
    second = _record("request-2", "tool_call", 200, "call-2", name="Task")
    second["tool_results"] = [{"name": "Bash", "call_id": "call-1"}]

    interactions = normalize_tool_interactions([first, second])

    assert [(item["call_id"], item["complete_pair"], item["call_only"])
            for item in interactions] == [("call-1", True, False), ("call-2", False, True)]
    assert sum(item["tokens"] for item in interactions) == 300
    assert interactions[0]["request_ids"] == ["request-1", "request-2"]


def test_result_without_matching_call_remains_visible_with_next_call():
    record = _record("request-2", "tool_call", 80, "call-2", name="Task")
    record["tool_results"] = [{"name": "Bash", "call_id": "call-1"}]

    interactions = normalize_tool_interactions([record])

    assert {(item["call_id"], item["result_only"]) for item in interactions} == {
        ("call-2", False), ("call-1", True),
    }
    assert sum(item["tokens"] for item in interactions) == 80


def test_shell_breakdown_reads_new_tool_identities_and_legacy_shape():
    modern_call = _record("request-1", "tool_call", 10, "call-1", name="Bash")
    modern_call["tools"][0]["detail"] = "rg"
    modern_result = _record("request-2", "tool_result", 20, "call-1", name="Bash")
    modern_result["tools"][0]["detail"] = "rg"
    legacy = {"request_id": "request-3", "content_type": "tool_call",
              "input_tokens": 5, "output_tokens": 0, "tool_name": "git", "tool_detail": "bash"}

    commands = shell_command_breakdown([modern_call, modern_result, legacy])

    assert commands["rg"]["calls"] == commands["rg"]["results"] == 1
    assert commands["rg"]["total_tokens"] == 30
    assert commands["git"]["calls"] == 1
    assert commands["git"]["total_tokens"] == 5


def test_shell_command_breakdown_groups_bash_calls_by_command():
    from acb.report_metrics import shell_command_breakdown

    rows = [
        {"content_type": "tool_call", "tool_name": "Bash", "tool_detail": "git",
         "input_tokens": 10, "output_tokens": 0, "turn_index": 0},
        {"content_type": "tool_result", "tool_name": "bash", "tool_detail": "git",
         "input_tokens": 20, "output_tokens": 0, "turn_index": 1},
        {"content_type": "tool_call", "tool_name": "Read", "tool_detail": None,
         "input_tokens": 5, "output_tokens": 0, "turn_index": 2},
    ]

    breakdown = shell_command_breakdown(rows)

    assert list(breakdown) == ["git"]
    assert breakdown["git"]["calls"] == 1
    assert breakdown["git"]["results"] == 1
    assert breakdown["git"]["total_tokens"] == 30
