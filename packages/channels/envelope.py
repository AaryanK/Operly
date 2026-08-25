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
    base_message: str = field(init=False, default="")

    def __post_init__(self) -> None:
        """Keep a transport-neutral message while preserving text-only fallback.

        Rich adapters such as Discord need the original prose so they can attach the
        actual artifact bytes without also printing redundant download links. Older
        text-only adapters may continue reading ``message`` and receive authenticated
        Operly download links as a compatibility fallback.
        """
        self.base_message = str(self.message or "").strip()
        if not self.artifacts:
            self.message = self.base_message
            return
        links: list[str] = []
        for artifact in self.artifacts[:10]:
            filename = str(artifact.get("filename") or "generated file")
            url = str(artifact.get("download_url") or "").strip()
            if url:
                links.append(f"• {filename}: {url}")
        if links:
            self.message = (self.base_message.rstrip() + "\n\nFiles:\n" + "\n".join(links)).strip()
        else:
            self.message = self.base_message
