from packages.coding_harness.model_client import (
    _has_mutation_after,
    _latest_failed_finish,
    _repair_packet,
)


def _failed_finish():
    return {
        "role": "tool",
        "tool_name": "finish",
        "content": (
            '{"ok":false,"error":"runtime contract failed",'
            '"objectiveAudit":{"message":"QR decode missing",'
            '"behaviorGaps":[{"behavior":"qr_decode"}],'
            '"unmetRequirements":["R-002"],'
            '"runtimeContractGaps":["interaction contract"]}}'
        ),
    }


def test_latest_failed_finish_is_detected_and_compacted_for_repair():
    messages = [{"role": "assistant", "tool_calls": []}, _failed_finish()]

    found = _latest_failed_finish(messages)

    assert found is not None
    index, payload = found
    assert index == 1
    packet = _repair_packet(payload)
    assert "QR decode missing" in packet
    assert "qr_decode" in packet
    assert "R-002" in packet
    assert "interaction contract" in packet


def test_workspace_mutation_after_rejected_finish_releases_finish_gate():
    messages = [
        _failed_finish(),
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "edit",
                        "arguments": '{"path":"app.js","old":"a","new":"b"}',
                    }
                }
            ],
        },
    ]

    assert _has_mutation_after(messages, 0) is True


def test_reads_after_rejected_finish_do_not_count_as_repair():
    messages = [
        _failed_finish(),
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "read", "arguments": '{"path":"app.js"}'}}
            ],
        },
    ]

    assert _has_mutation_after(messages, 0) is False
