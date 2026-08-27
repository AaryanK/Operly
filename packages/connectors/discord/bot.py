"""Compatibility entrypoint for the canonical Discord channel adapter.

The historical Discord bot implementation used its own model/tool loop. Keep the
module path working, but load the secured runtime first so its canonical event
handler replaces legacy identity-pairing behavior before the shared bot starts.
"""

from packages.connectors.discord import secure_runtime as _secure_runtime  # noqa: F401
from packages.connectors.discord.bot_shared import main


if __name__ == "__main__":
    main()
