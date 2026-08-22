from __future__ import annotations

import re
from typing import Iterable

from packages.capabilities.contracts import CapabilityDescriptor, CapabilityProvider


_TOKEN_RE = re.compile(r"[a-z0-9_.:-]+")


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_RE.findall(str(value or "").lower()))


class CapabilityRegistry:
    def __init__(self, *, enabled_resolver=None, config_resolver=None):
        self._providers: list[CapabilityProvider] = []
        self._enabled_resolver = enabled_resolver or (lambda tenant_id, definition: True)
        self._config_resolver = config_resolver or (lambda tenant_id, definition: {})

    def register(self, provider: CapabilityProvider) -> None:
        if any(item.name == provider.name for item in self._providers):
            raise ValueError(f"Provider already registered: {provider.name}")
        duplicates = {
            definition.id
            for definition in provider.capabilities
            if any(existing.id == definition.id for existing in self.definitions())
        }
        if duplicates:
            raise ValueError(
                "Capability already registered: " + ", ".join(sorted(duplicates))
            )
        self._providers.append(provider)

    def resolve(
        self,
        tenant_id: str,
        capability: str,
        *,
        authority: set[str] | None = None,
    ) -> CapabilityProvider:
        matches = [provider for provider in self._providers if provider.supports(capability)]
        if not matches:
            raise LookupError(f"No provider for capability: {capability}")
        definition = next(
            item
            for item in matches[0].capabilities
            if item.id == capability or item.name == capability
        )
        if not self._enabled_resolver(tenant_id, definition):
            raise LookupError(f"Plugin is disabled for tenant: {capability}")
        if authority is not None and not set(definition.permissions).issubset(authority):
            raise PermissionError(f"Missing authority for plugin: {capability}")
        return matches[0]

    def definition(self, capability: str):
        for definition in self.definitions():
            if definition.id == capability or definition.name == capability:
                return definition
        raise LookupError(f"Unknown capability: {capability}")

    def definitions(self):
        return [
            definition
            for provider in self._providers
            for definition in provider.capabilities
        ]

    def metadata(self, tenant_id: str, *, authority: set[str] | None = None):
        return [
            item
            for item in self.definitions()
            if self._enabled_resolver(tenant_id, item)
            and (
                authority is None
                or set(item.permissions).issubset(authority)
            )
        ]

    def provider_config(self, tenant_id: str, capability: str) -> dict:
        provider = self.resolve(tenant_id, capability)
        definition = next(
            x
            for x in provider.capabilities
            if x.id == capability or x.name == capability
        )
        return dict(self._config_resolver(tenant_id, definition) or {})

    def descriptor(
        self,
        tenant_id: str,
        capability: str,
        *,
        authority: set[str] | None = None,
    ) -> CapabilityDescriptor:
        definition = self.definition(capability)
        installed = bool(self._enabled_resolver(tenant_id, definition))
        config = dict(self._config_resolver(tenant_id, definition) or {})
        configured = bool(config.get("configured", True))
        healthy = config.get("healthy")
        authorized = (
            None
            if authority is None
            else set(definition.permissions).issubset(authority)
        )
        return CapabilityDescriptor(
            id=definition.id,
            version=definition.version,
            plugin_id=definition.plugin_id,
            display_name=definition.display_name or definition.name,
            description=definition.description,
            risk=definition.risk_level,
            execution_mode=str(definition.execution_mode),
            permissions=tuple(definition.permissions),
            category=definition.category,
            tags=tuple(sorted(definition.tags)),
            semantic_operations=tuple(sorted(definition.semantic_operations)),
            installed=installed,
            configured=configured,
            healthy=healthy if isinstance(healthy, bool) else None,
            authorized=authorized,
        )

    def describe(
        self,
        tenant_id: str,
        capabilities: Iterable[str],
        *,
        authority: set[str] | None = None,
        include_schema: bool = True,
    ) -> list[dict]:
        output = []
        for capability in capabilities:
            try:
                definition = self.definition(str(capability))
                descriptor = self.descriptor(
                    tenant_id,
                    definition.id,
                    authority=authority,
                )
            except LookupError:
                continue
            row = {
                "id": descriptor.id,
                "version": descriptor.version,
                "plugin_id": descriptor.plugin_id,
                "display_name": descriptor.display_name,
                "description": descriptor.description,
                "risk": descriptor.risk,
                "execution_mode": descriptor.execution_mode,
                "permissions": list(descriptor.permissions),
                "category": descriptor.category,
                "tags": list(descriptor.tags),
                "semantic_operations": list(descriptor.semantic_operations),
                "installed": descriptor.installed,
                "configured": descriptor.configured,
                "healthy": descriptor.healthy,
                "authorized": descriptor.authorized,
            }
            if include_schema:
                row["input_schema"] = definition.input_schema
                row["output_schema"] = definition.output_schema
            output.append(row)
        return output

    def search(
        self,
        tenant_id: str,
        query: str,
        *,
        authority: set[str] | None = None,
        limit: int = 12,
        categories: Iterable[str] = (),
        tags: Iterable[str] = (),
    ) -> list[dict]:
        """Discover enabled capabilities without granting execution authority.

        Search is intentionally metadata-only. ``authorized`` is reported as a
        separate field and callers must still invoke through the canonical harness.
        """
        wanted_tokens = _tokens(query)
        wanted_categories = {
            str(item).strip().lower() for item in categories if str(item).strip()
        }
        wanted_tags = {str(item).strip().lower() for item in tags if str(item).strip()}
        ranked: list[tuple[int, str, dict]] = []

        for definition in self.definitions():
            if not self._enabled_resolver(tenant_id, definition):
                continue
            if wanted_categories and str(definition.category or "").lower() not in wanted_categories:
                continue
            if wanted_tags and not wanted_tags.issubset(set(definition.tags)):
                continue

            document_tokens = _tokens(definition.discovery_document())
            overlap = len(wanted_tokens & document_tokens)
            phrase_bonus = 3 if str(query or "").strip().lower() in definition.discovery_document() else 0
            semantic_bonus = sum(
                2
                for operation in definition.semantic_operations
                if _tokens(operation) & wanted_tokens
            )
            if wanted_tokens and overlap == 0 and phrase_bonus == 0 and semantic_bonus == 0:
                continue
            score = overlap + phrase_bonus + semantic_bonus
            descriptor = self.descriptor(
                tenant_id,
                definition.id,
                authority=authority,
            )
            ranked.append(
                (
                    score,
                    definition.id,
                    {
                        "id": descriptor.id,
                        "version": descriptor.version,
                        "plugin_id": descriptor.plugin_id,
                        "display_name": descriptor.display_name,
                        "description": descriptor.description,
                        "risk": descriptor.risk,
                        "category": descriptor.category,
                        "tags": list(descriptor.tags),
                        "semantic_operations": list(descriptor.semantic_operations),
                        "installed": descriptor.installed,
                        "configured": descriptor.configured,
                        "healthy": descriptor.healthy,
                        "authorized": descriptor.authorized,
                    },
                )
            )

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [row for _, _, row in ranked[: max(1, min(int(limit), 50))]]


PluginRegistry = CapabilityRegistry
