from __future__ import annotations

import asyncio

from packages.plugins import hosting_e2e


async def _noop_init_db() -> None:
    return None


if __name__ == "__main__":
    print("PLUGIN_HOSTING_E2E_RUNNER_START", flush=True)
    # The production Operly API and Platform Worker have already initialized the
    # shared schema. Running init_db concurrently from this one-shot harness can
    # contend on schema initialization, so the harness reuses the live schema.
    hosting_e2e.init_db = _noop_init_db
    asyncio.run(hosting_e2e.main())
