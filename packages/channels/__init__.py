from packages.channels.envelope import ChannelAttachment, ChannelEnvelope, ChannelResponse
from packages.channels.identity import IdentityLinkConflict, IdentityService

__all__ = [
    "ChannelAttachment",
    "ChannelEnvelope",
    "ChannelResponse",
    "IdentityLinkConflict",
    "IdentityService",
    "ChannelService",
]


def __getattr__(name: str):
    """Keep the channel package import-light.

    Presentation/envelope helpers are used by low-level capability discovery. Importing
    ChannelService eagerly pulls in the business agent and capability registry, which
    creates a registry -> presentation -> channels -> agent cycle. Service construction
    is intentionally lazy while preserving ``from packages.channels import ChannelService``.
    """
    if name == "ChannelService":
        from packages.channels.service import ChannelService

        return ChannelService
    raise AttributeError(name)
