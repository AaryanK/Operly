"""Attachment ingestion plugin for the Operly harness.

This plugin is a perception/preprocessing boundary only. It may parse and analyze
untrusted uploaded content into a bounded application-generated artifact, but it
never receives business/action capabilities and can never execute side effects.
The normal Personal/Workspace agent consumes the derived artifact afterwards.
"""
from __future__ import annotations

from typing import Any

from packages.harness.plugins import RuntimePluginContext

from .models import AttachmentBundle
from .multimodal_processor import MultimodalProcessor


class AttachmentIngestionPlugin:
    id = "attachments.multimodal-ingestion"
    kind = "attachment_ingestion"
    priority = 10

    def __init__(self, processor: MultimodalProcessor | None = None) -> None:
        self.processor = processor or MultimodalProcessor()

    @property
    def limits(self):
        return self.processor.limits

    def supports(self, payload: dict[str, Any], context: RuntimePluginContext) -> bool:
        del context
        return isinstance(payload.get("bundle"), AttachmentBundle)

    async def invoke(
        self,
        payload: dict[str, Any],
        context: RuntimePluginContext,
    ):
        del context
        bundle = payload.get("bundle")
        if not isinstance(bundle, AttachmentBundle):
            raise ValueError("attachment_ingestion requires an AttachmentBundle")
        return await self.processor.process(bundle, payload.get("temp_dir"))
