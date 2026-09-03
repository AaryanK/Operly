def test_discord_auth_router_imports_current_stack_only():
    from apps.api import discord_auth_router

    assert discord_auth_router.router.prefix == "/api/identities"
    assert discord_auth_router.DISCORD_AUTHORIZE_URL.startswith("https://discord.com/")


def test_discord_auth_router_does_not_depend_on_retired_channels_package():
    from pathlib import Path

    source = Path("apps/api/discord_auth_router.py").read_text(encoding="utf-8")
    assert "packages.channels" not in source
