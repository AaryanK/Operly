"""Compatibility alias for canonical Discord response transport.

Artifact delivery used to have a second implementation here. Keep the import path
without retaining parallel upload/scope logic.
"""

from packages.connectors.discord.transport import send_discord_response

__all__ = ["send_discord_response"]
