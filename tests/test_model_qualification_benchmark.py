from packages.model_runtime.qualification_benchmark import (
    QualificationCase,
    QualificationReport,
    _safe_python,
)


def _report(*cases: QualificationCase) -> QualificationReport:
    return QualificationReport(
        resource_id="groq:qwen/qwen3.6-27b",
        provider="groq",
        model_id="qwen/qwen3.6-27b",
        canonical_id="qwen:qwen3.6-27b",
        advertised_capabilities=["coding", "reasoning", "text"],
        free=True,
        context_length=None,
        cases=list(cases),
    )


def test_deep_evidence_verifies_tools_coding_and_repair():
    report = _report(
        QualificationCase("availability", True, 10),
        QualificationCase("structured_json", True, 10),
        QualificationCase("reasoning", True, 10),
        QualificationCase("tool_single", True, 10),
        QualificationCase("tool_multi", True, 10),
        QualificationCase("coding", True, 10),
        QualificationCase("repair", True, 10),
        QualificationCase("planning", False, 10, classification="rate_limited"),
    )

    assert report.score == 95
    assert report.verified_capabilities == [
        "coding",
        "reasoning",
        "repair",
        "structured_output",
        "text",
        "tools",
    ]


def test_single_tool_call_alone_does_not_overclaim_full_tool_loop_capability():
    report = _report(
        QualificationCase("availability", True, 10),
        QualificationCase("tool_single", True, 10),
        QualificationCase("tool_multi", False, 10),
    )

    assert "tools" not in report.verified_capabilities


def test_safe_python_accepts_correct_bounded_function():
    passed, detail = _safe_python(
        "def clamp(n, low, high):\n    return min(high, max(low, n))\n",
        "clamp",
        [((5, 0, 10), 5), ((-1, 0, 10), 0), ((11, 0, 10), 10)],
    )
    assert passed, detail


def test_safe_python_rejects_imports_and_unexpected_calls():
    passed, detail = _safe_python(
        "import os\ndef clamp(n, low, high):\n    return os.system('true')\n",
        "clamp",
        [((5, 0, 10), 5)],
    )
    assert not passed
    assert "forbidden" in detail
