from packages.capabilities.contracts import CapabilityProvider


class CapabilityRegistry:
    def __init__(self): self._providers: list[CapabilityProvider] = []
    def register(self, provider: CapabilityProvider) -> None:
        if any(item.name == provider.name for item in self._providers):
            raise ValueError(f"Provider already registered: {provider.name}")
        self._providers.append(provider)
    def resolve(self, tenant_id: str, capability: str) -> CapabilityProvider:
        del tenant_id  # reserved for credential-aware deterministic selection
        matches = [provider for provider in self._providers if provider.supports(capability)]
        if not matches: raise LookupError(f"No provider for capability: {capability}")
        return matches[0]
    def definitions(self): return [definition for provider in self._providers for definition in provider.capabilities]
