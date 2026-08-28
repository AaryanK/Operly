import pytest

from packages.agents.control_plane import FactoryBlueprintCompiler


@pytest.mark.asyncio
async def test_trivial_chat_skips_blueprint_model_call(monkeypatch):
    compiler = FactoryBlueprintCompiler()

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("trivial chat must not spend a blueprint model call")

    monkeypatch.setattr(compiler, "_infer", fail_if_called)
    blueprint = await compiler.compile("hello")

    assert len(blueprint.graph.stages) == 1
    stage = blueprint.graph.stages[0]
    assert stage.objective == "hello"
    assert stage.capability_intents == ()


@pytest.mark.asyncio
async def test_planner_failure_fallback_still_resolves_task_capabilities(monkeypatch):
    compiler = FactoryBlueprintCompiler()

    async def empty_plan(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(compiler, "_infer", empty_plan)
    objective = "Send the approved launch update to the customer"
    blueprint = await compiler.compile(objective)

    assert len(blueprint.graph.stages) == 1
    stage = blueprint.graph.stages[0]
    assert stage.objective == objective
    assert stage.capability_intents == (objective,)


def test_direct_fallback_uses_plain_language_intent_not_invented_tool_id():
    objective = "Create a PDF report from the uploaded files"
    blueprint = FactoryBlueprintCompiler._fallback(objective)

    stage = blueprint.graph.stages[0]
    assert stage.capability_intents == (objective,)
    assert "." not in stage.capability_intents[0].split()[0]
