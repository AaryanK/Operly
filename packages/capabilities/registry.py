from packages.capabilities.contracts import CapabilityProvider


class CapabilityRegistry:
    def __init__(self, *, enabled_resolver=None, config_resolver=None):
        self._providers: list[CapabilityProvider] = []
        self._enabled_resolver = enabled_resolver or (lambda tenant_id, definition: True)
        self._config_resolver = config_resolver or (lambda tenant_id, definition: {})
    def register(self, provider: CapabilityProvider) -> None:
        if any(item.name == provider.name for item in self._providers):
            raise ValueError(f"Provider already registered: {provider.name}")
        self._providers.append(provider)
    def resolve(self, tenant_id: str, capability: str, *, authority: set[str] | None = None) -> CapabilityProvider:
        matches = [provider for provider in self._providers if provider.supports(capability)]
        if not matches: raise LookupError(f"No provider for capability: {capability}")
        definition = next(item for item in matches[0].capabilities if item.id == capability or item.name == capability)
        if not self._enabled_resolver(tenant_id, definition): raise LookupError(f"Plugin is disabled for tenant: {capability}")
        if authority is not None and not set(definition.permissions).issubset(authority):
            raise PermissionError(f"Missing authority for plugin: {capability}")
        return matches[0]
    def definitions(self): return [definition for provider in self._providers for definition in provider.capabilities]
    def metadata(self, tenant_id: str, *, authority: set[str] | None = None):
        return [item for item in self.definitions()
                if self._enabled_resolver(tenant_id,item) and (authority is None or set(item.permissions).issubset(authority))]
    def provider_config(self,tenant_id:str,capability:str)->dict:
        provider=self.resolve(tenant_id,capability);definition=next(x for x in provider.capabilities if x.id==capability or x.name==capability)
        return dict(self._config_resolver(tenant_id,definition) or {})


PluginRegistry = CapabilityRegistry
