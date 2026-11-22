from __future__ import annotations


def discord_ai_runtime_status() -> tuple[bool, str]:
    """Discord has no safe, session-bound DragonZpyder channel delegation yet."""
    return False, (
        "Discord agent chat is unavailable until a signed DragonZpyder "
        "channel bridge is enabled. Account linking and workspace binding work."
    )
