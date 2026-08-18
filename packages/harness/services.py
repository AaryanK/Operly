from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ServiceContext:
    events: Any
    company: Any
    context_builder: Any
    capabilities: Any
    actions: Any
    policy: Any
    llm: Any = None
    runner: Any = None
