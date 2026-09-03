def test_discord_auth_router_imports_current_stack_only():
    from apps.api import discord_auth_router

    assert discord_auth_router.router.prefix == "/api/identities"
    assert discord_auth_router.DISCORD_AUTHORIZE_URL.startswith("https://discord.com/")
