from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext


def test_expanded_gmail_reads_require_private_connector_authority():
    context = PluginInvocationContext(
        tenant_id="workspace-1",
        user_id="user-1",
        role="member",
        objective="inspect invoice evidence",
        channel="web",
        metadata={"shared_surface": True},
    )
    for capability_id in (
        "gmail.read_thread",
        "gmail.list_attachments",
        "gmail.read_attachment",
    ):
        assert PluginAgentHarness.capability_authorized(
            capability_id,
            {"messaging:read"},
            context,
        ) is False
        assert PluginAgentHarness.capability_authorized(
            capability_id,
            {"messaging:read", "gmail:read"},
            context,
        ) is True


def test_workspace_harness_never_invents_personal_connector_authority():
    # Personal-to-workspace connector delegation is deliberately fail-closed until
    # an explicit delegation resolver supplies authority. Merely being on a private
    # surface does not manufacture Gmail authority.
    context = PluginInvocationContext(
        tenant_id="workspace-1",
        user_id="user-1",
        role="owner",
        objective="read personal mail",
        channel="web",
        metadata={"shared_surface": False},
    )
    assert PluginAgentHarness.capability_authorized(
        "gmail.read_thread",
        {"messaging:read"},
        context,
    ) is False
