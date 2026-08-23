"""Small deterministic policies that protect the model/tool boundary.

These checks are intentionally narrow. They do not replace semantic routing; they
only identify conversation turns that clearly do not need capability exposure.
"""
from __future__ import annotations

import re


_TRIVIAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(?:hi|hy|hey|hello|yo)(?:\s+(?:there|operly|assistant))?[!.?]*$",
        r"^(?:thanks|thank\s+you|thx)[!.?]*$",
        r"^(?:how\s+are\s+you|how(?:'|’)s\s+it\s+going|what(?:'|’)s\s+up|whats\s+up)[!.?]*$",
        r"^good\s+(?:morning|afternoon|evening)[!.?]*$",
    )
)


def is_trivial_conversation(objective: str) -> bool:
    """Return True only for unmistakable greeting/thanks/chitchat turns.

    Mixed requests such as "hello, email John" intentionally return False so
    capability access remains available when the user actually asks for work.
    """
    normalized = " ".join(str(objective or "").strip().split())
    if not normalized or len(normalized) > 80:
        return False
    return any(pattern.fullmatch(normalized) for pattern in _TRIVIAL_PATTERNS)
