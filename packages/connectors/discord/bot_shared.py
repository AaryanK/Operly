"""Compatibility exports for the canonical Discord connector.

The historical ``bot_shared`` module used to own a second attachment pipeline,
scheduler and message runtime. Those execution paths are retired. Keep only the
old import surface needed by compatibility entrypoints while delegating everything
to the canonical connector runtime.
"""

from packages.connectors.discord.client import addressed_to_operly, bot, clean_prompt, envelope_for
from packages.connectors.discord.secure_runtime import (
    PUBLIC_BASE_URL,
    create_channel_link,
    main,
    send_chunks,
    server_tenant,
    store_message,
)

__all__ = [
    "PUBLIC_BASE_URL",
    "addressed_to_operly",
    "bot",
    "clean_prompt",
    "create_channel_link",
    "envelope_for",
    "main",
    "send_chunks",
    "server_tenant",
    "store_message",
]
