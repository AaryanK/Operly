import json

from packages.agents.control_plane import ContextCapsule, StageSpec
from packages.agents.control_plane.stage_prompt_pipeline import FactoryStagePromptPipeline


def _user_payload(messages):
    return json.loads(messages[1]["content"])


def test_worker_prompt_projects_execution_state_instead_of_replaying_factory_state():
    stage = StageSpec(
        id="follow-up",
        objective="Review recent mail and upcoming meetings, then create justified tasks.",
        dependencies=("mail-stage",),
        context_intents=("recent workspace conversation",),
        capability_intents=("email_analysis", "calendar_read", "task_create"),
        input_refs=("mail-stage",),
        validation_ids=("summary-exists",),
        assigned_role="business_agent",
    )
    capsule = ContextCapsule(
        stage_id=stage.id,
        objective=stage.objective,
        context_refs=("ctx-1",),
        artifact_refs=("artifact-1",),
        facts=(
            ("workspace_mode", "full"),
            ("temporal_context", {"actor_timezone": "America/Chicago"}),
            ("resolved_capability_ids", ["gmail.search", "calendar.list_events", "task.create"]),
        ),
        capability_ids=("gmail.search", "calendar.list_events", "task.create"),
    )

    payload = _user_payload(
        FactoryStagePromptPipeline(stage=stage, capsule=capsule).initial_messages()
    )

    assert payload["stage"] == {"id": stage.id, "objective": stage.objective}
    context = payload["context_capsule"]
    assert context["context_refs"] == ["ctx-1"]
    assert context["artifact_refs"] == ["artifact-1"]
    assert "capability_ids" not in context
    assert "resolved_capability_ids" not in context["facts"]
    assert context["facts"]["workspace_mode"] == "full"
    assert context["facts"]["temporal_context"]["actor_timezone"] == "America/Chicago"


def test_continuation_projection_keeps_latest_tool_result_without_factory_bookkeeping():
    stage = StageSpec("mail", "Inspect recent mail")
    capsule = ContextCapsule(
        stage_id="mail",
        objective="Inspect recent mail",
        facts=(("resolved_capability_ids", ["gmail.search"]),),
        capability_ids=("gmail.search",),
    )
    pipeline = FactoryStagePromptPipeline(stage=stage, capsule=capsule)
    messages = [
        *pipeline.initial_messages(),
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "gmail.search", "arguments": {"q": "newer_than:7d"}},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "tool_name": "gmail.search",
            "content": '{"ok":true,"count":3}',
        },
    ]

    reduced = pipeline.continuation_messages(messages)
    payload = _user_payload(reduced)

    assert "capability_ids" not in payload["context_capsule"]
    assert "resolved_capability_ids" not in payload["context_capsule"]["facts"]
    assert reduced[-1]["role"] == "tool"
    assert "count" in reduced[-1]["content"]
