"""Bounded terminal recovery for Operly Studio website source runs.

The website model is allowed to stop because of a model-turn/time/doom-loop guard, but
Studio must not discard a source workspace that is already changed and passes the
website safety + grounding contract merely because the model failed to make one last
`finish` tool call.

This module intentionally patches only the Studio website agent after the existing
Studio runtime policy is installed. The general/custom-software coding harness keeps
its stricter terminal semantics.
"""
from __future__ import annotations

from typing import Any

from packages.coding_harness import opencode_agent as coding
from packages.studio import runtime_policy as policy


_RECOVERABLE_TERMINAL_MARKERS = (
    "did not converge within",
    "did not respond within the bounded website-edit window",
    "exhausted its bounded model-turn budget",
    "stopped before completing the requested source change",
    "repeated the same",
)

_EXTRA_RUNTIME_RULE = (
    "- Never add third-party remote script URLs (for example <script src=\"https://...\">). "
    "Studio rejects them. Use local HTML/CSS/JavaScript and authorized Operly capabilities instead."
)

_APPLIED = False
_ORIGINAL_SESSION = None


def _changed_paths(before: dict[str, str], workspace: coding.VirtualWorkspace) -> list[str]:
    after = workspace.snapshot()
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def recover_verified_terminal_session(
    *,
    mode: str,
    specification: str,
    workspace: coding.VirtualWorkspace,
    before: dict[str, str],
    require_change: bool,
    editor_context: dict[str, Any] | None,
    error: Exception,
) -> coding.CodingSession | None:
    """Return a finished session only for a verified workspace at a known terminal guard.

    This does not repair source and does not weaken validation. It only prevents a
    valid, changed website from being discarded because the model stopped before a
    final finish call.
    """
    if mode == "plan":
        return None
    message = str(error or "")
    if not any(marker in message for marker in _RECOVERABLE_TERMINAL_MARKERS):
        return None

    files = workspace.source_files()
    changed = _changed_paths(before, workspace)
    if not files or (require_change and not changed):
        return None
    try:
        policy.validate_studio_website(files, specification)
    except policy.StudioWebsiteContractError:
        return None

    session = coding.CodingSession(
        mode=mode,
        workspace=workspace,
        before=before,
        editor_context=editor_context or {},
    )
    session.finished = True
    session.summary = "Verified website update preserved after the bounded source agent stopped."
    session.verification = [
        "Studio website safety and factual-grounding contract passed.",
        "The changed workspace was preserved instead of being discarded at the agent boundary.",
    ]
    session.notes.append(
        "Operly verified and preserved the changed website workspace after the bounded model loop stopped."
    )
    return session


def apply_studio_terminal_recovery() -> None:
    """Install Studio-only terminal recovery after apply_studio_runtime_policy()."""
    global _APPLIED, _ORIGINAL_SESSION
    if _APPLIED:
        return

    if _EXTRA_RUNTIME_RULE not in policy.STUDIO_WEBSITE_SYSTEM:
        policy.STUDIO_WEBSITE_SYSTEM = policy.STUDIO_WEBSITE_SYSTEM.rstrip() + "\n" + _EXTRA_RUNTIME_RULE

    _ORIGINAL_SESSION = policy.StudioWebsiteCodingAgent._session

    async def resilient_session(
        self,
        mode,
        specification,
        workspace,
        task,
        *,
        require_change,
        editor_context,
    ):
        before = workspace.snapshot()
        try:
            return await _ORIGINAL_SESSION(
                self,
                mode,
                specification,
                workspace,
                task,
                require_change=require_change,
                editor_context=editor_context,
            )
        except coding.CodingHarnessError as error:
            recovered = recover_verified_terminal_session(
                mode=mode,
                specification=str(specification or ""),
                workspace=workspace,
                before=before,
                require_change=bool(require_change),
                editor_context=editor_context or {},
                error=error,
            )
            if recovered is None:
                raise
            await self._progress(
                {
                    "phase": "recovery",
                    "summary": (
                        "The model stopped at its bounded guard after producing a valid changed website. "
                        "Operly verified and preserved the source instead of discarding it."
                    ),
                    "ok": True,
                    "detail": str(error)[:1200],
                }
            )
            return recovered

    policy.StudioWebsiteCodingAgent._session = resilient_session
    _APPLIED = True
