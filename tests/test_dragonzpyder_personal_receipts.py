"""Never forward tool arguments or unbounded internal receipt metadata to browsers."""
from packages.agent_runtime.dragonzpyder_personal import (
    PersonalAgentResult, sanitize_execution_receipts,
)


def test_personal_harness_receipts_accept_only_bounded_names():
    response = {
        "status": "completed",
        "execution_receipts": {
            "tool_names": ["task", "read_file", "web_search", "operly_tasks_list",
                           "<script>alert(1)</script>", "x" * 81, {"name": "bad"}],
            "failed_tool_count": 200,
            "artifact_readback_count": 99,
            "private_data": "never forward this",
        },
    }
    got = sanitize_execution_receipts(response)
    assert got["tool_names"] == ["task", "read_file", "web_search", "operly_tasks_list"]
    assert got["failed_tool_count"] == 100
    assert got["artifact_readback_count"] == 30
    assert got["objective_verified"] is False
    assert "private_data" not in str(got)


def test_personal_harness_receipts_fail_closed_on_missing_or_invalid_counts():
    got = sanitize_execution_receipts({"status": "incomplete", "execution_receipts": {
        "tool_names": "eval", "failed_tool_count": True, "artifact_readback_count": -4,
        "objective_verified": True,
    }})
    assert got == {
        "tool_names": [], "failed_tool_count": 0, "artifact_readback_count": 0,
        "status": "incomplete", "objective_verified": False,
    }
    serialized = PersonalAgentResult(
        message="incomplete", capability_calls=(), run_id="test-id",
        execution_receipts=got,
    ).as_dict()
    assert serialized["execution_receipts"] == got
    assert serialized["objective_verified"] is False
