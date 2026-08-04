import asyncio
import re
import time
from collections import defaultdict, deque
from typing import Any


SECRET_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|cookie)",
    re.IGNORECASE,
)

MAX_USER_TEXT = 12_000
MAX_TOOL_RESULT = 8_000
MAX_ASSISTANT_TEXT = 12_000


class AgentSecurityError(RuntimeError):
    pass


def bounded_text(value: Any, maximum: int) -> str:
    text = str(value or "")
    if len(text) > maximum:
        return text[:maximum] + "\n[truncated]"
    return text


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if SECRET_KEY_PATTERN.search(str(key)):
                output[str(key)] = "[REDACTED]"
            else:
                output[str(key)] = redact_secrets(item)
        return output

    if isinstance(value, list):
        return [redact_secrets(item) for item in value]

    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]

    return value


class SlidingWindowRateLimiter:
    def __init__(self, limit: int = 20, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            events = self._events[key]
            while events and events[0] < cutoff:
                events.popleft()

            if len(events) >= self.limit:
                raise AgentSecurityError(
                    "Too many AI requests. Wait a moment and try again."
                )

            events.append(now)
