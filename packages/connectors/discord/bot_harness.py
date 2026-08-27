"""Compatibility entrypoint for the canonical Discord channel adapter.

Legacy deployments may still launch this module. Load the secured runtime first
so every Discord event uses the canonical identity and ChannelService path while
preserving the historical launch command.
"""

from packages.connectors.discord import secure_runtime as _secure_runtime  # noqa: F401
from packages.connectors.discord.bot_shared import main


if __name__ == "__main__":
    main()
