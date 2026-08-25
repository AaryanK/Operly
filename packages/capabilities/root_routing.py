"""Small product-level routing contracts for canonical root operations.

This module is intentionally not a general planner. It only answers whether a root
objective is plainly asking Operly to construct a software product rather than save an
inert source/text artifact. The high-level software capability then owns planning,
coding, build, test, health and acceptance internally.
"""
from __future__ import annotations

import re


_BUILD_ACTION_RE = re.compile(
    r"\b(?:build|create|make|develop|implement|generate|write|code|design|produce|set\s+up|spin\s+up)\b",
    re.IGNORECASE,
)
_SOFTWARE_PRODUCT_RE = re.compile(
    r"\b(?:app|application|web\s+app|website|software|codebase|dashboard|portal|platform|api|backend|frontend|full[-\s]?stack)\b",
    re.IGNORECASE,
)
_SYSTEM_RE = re.compile(r"\bsystem\b", re.IGNORECASE)
_STRONG_COMPLETION_RE = re.compile(
    r"\b(?:working|runnable|deployable|complete|entire|full[-\s]?stack|production[-\s]?ready)\b",
    re.IGNORECASE,
)
_NON_PRODUCT_SYSTEM_RE = re.compile(
    r"\b(?:system\s+prompt|design\s+system|file\s+system|nervous\s+system|solar\s+system)\b",
    re.IGNORECASE,
)


def requires_software_build(objective: str) -> bool:
    """Return True only for a clear root request to construct runnable software.

    Deliberately excluded examples include a code snippet, one source file, a schema
    file, or a system prompt. Those remain valid artifact/file-authoring requests.
    """

    text = " ".join(str(objective or "").split()).strip()
    if not text or not _BUILD_ACTION_RE.search(text):
        return False

    if _SOFTWARE_PRODUCT_RE.search(text):
        return True

    if _SYSTEM_RE.search(text) and not _NON_PRODUCT_SYSTEM_RE.search(text):
        # "Build/create X system" is a common application request. Requiring either
        # explicit completion language or a sufficiently descriptive objective keeps
        # short non-software phrases from becoming application builds accidentally.
        return bool(_STRONG_COMPLETION_RE.search(text) or len(text.split()) >= 6)

    return False
