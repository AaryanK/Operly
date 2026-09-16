from __future__ import annotations


class CapabilityDependencyUnavailable(LookupError):
    """A governed capability exists, but an external dependency is not usable.

    Providers may use a stable, non-secret code so Runtime1 can report an actionable
    blocker without exposing credentials, provider payloads, or cross-account state.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "capability_dependency_unavailable",
    ) -> None:
        super().__init__(message)
        clean = str(code or "").strip().lower()
        self.code = clean or "capability_dependency_unavailable"
