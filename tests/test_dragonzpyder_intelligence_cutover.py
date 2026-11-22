"""Prevent reintroduction of Operly's independent chat interpreter."""
from pathlib import Path

def test_chat_routes_have_no_local_model_path():
    src = Path("apps/api/agent_runtime_router.py").read_text()
    assert "run_personal_harness(" in src
    assert "run_workspace_harness(" in src
    for old in ("Runtime1Agent", "OpenAICompatibleAgentModel", "get_agent_service",
                "get_personal_agent_service", "collect_inference_usage",
                "build_personal_runtime", "build_workspace_runtime",
                "OPERLY_AI_EXECUTION_OWNER", "OPERLY_RETIRE_LOCAL_AGENT_EXECUTION"):
        assert old not in src
    main = Path("apps/api/main.py").read_text()
    assert "run_startup_objective_eval_if_enabled" not in main

def test_workspace_permissions_and_mcp_remain_intact():
    source = Path("packages/agent_runtime/dragonzpyder_workspace.py").read_text()
    assert "issue_workspace_delegation(" in source
    source = Path("packages/agent_runtime/dragonzpyder_personal.py").read_text()
    assert "issue_personal_delegation(" in source
    assert Path("packages/mcp/first_party_workspace.py").exists()
    assert Path("packages/security/permissions.py").exists()
    assert "context.is_guest_workspace" in Path("apps/api/agent_runtime_router.py").read_text()
