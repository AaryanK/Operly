from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ChannelAttachment:
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None
    url: str | None = None
    content_bytes: bytes | None = None


@dataclass(slots=True)
class ChannelEnvelope:
    provider: str
    external_user_id: str
    external_conversation_id: str
    actor_name: str
    text: str
    external_space_id: str | None = None
    space_name: str | None = None
    is_direct: bool = False
    images: list[str] = field(default_factory=list)
    attachments: list[ChannelAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChannelResponse:
    message: str
    conversation_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    role: str | None = None
    status: str = "ok"
    tenant_options: list[dict[str, str]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Keep generated files visible even in text-only channel adapters.

        Rich adapters may upload/render ``artifacts`` directly. Older adapters that
        only send ``message`` still receive authenticated Operly download links, so a
        durable artifact can never disappear merely because the surface has not added
        a native file-card implementation yet.
        """
        if not self.artifacts:
            return
        links: list[str] = []
        for artifact in self.artifacts[:10]:
            filename = str(artifact.get("filename") or "generated file")
            url = str(artifact.get("download_url") or "").strip()
            if url:
                links.append(f"• {filename}: {url}")
        if links:
            self.message = (str(self.message or "").rstrip() + "\n\nFiles:\n" + "\n".join(links)).strip()
