import asyncio

from packages.email.providers.base import EmailEnvelope


class MemoryEmailProvider:
    """Deterministic provider for tests; never selected from production config."""

    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[EmailEnvelope] = []
        self.fail = fail
        self._lock = asyncio.Lock()

    async def send(self, envelope: EmailEnvelope) -> None:
        if self.fail:
            raise RuntimeError("Injected email delivery failure")
        async with self._lock:
            self.messages.append(envelope)
