"""Compatibility entrypoint for the canonical Discord channel adapter.

The historical Discord bot implementation used its own model/tool loop. Keep the
module path working, but route it to the one channel-independent execution path.
"""

from packages.connectors.discord.bot_shared import main


if __name__ == "__main__":
    main()
