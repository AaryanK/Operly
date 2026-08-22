# Model runtime plugins

OPERLY treats model providers and models as replaceable infrastructure plugins.
Business, planning, semantic-routing, capability-placement, and coding harnesses
depend on stable runtime contracts instead of provider-specific APIs.

Ox Alpha (`stealth/ox-alpha`) is the default orchestrator for every current role.
Railway's existing `OPEN_ROUTER_API` variable is accepted directly. No secret
belongs in the repository.

## Provider plugins

Provider adapters own transport details: base URL, authentication, payload/response
translation, tool-call formatting, retries, streaming and provider-specific errors.
The harness never branches on OpenRouter vs Ollama.

To switch every orchestrator role later:

```env
OPERLY_MODEL_PROVIDER=openrouter
OPERLY_MODEL_DEFAULT=<provider-model-id>
```

A role can override either field without touching harness code:

```env
OPERLY_MODEL_CODING_PROVIDER=ollama
OPERLY_MODEL_CODING=some-local-model
```

Provider adapters register with `register_model_provider(name, factory)`. A new
provider only needs to implement the shared model client contract; harnesses then
receive it through `model_client_for_route(model_route(role))`.

## Model resources

Models are catalog resources separate from providers. Each resource declares a
provider, model id, capabilities, free/paid status and routing priority. The
orchestrator is always included automatically. Additional resources can be added
without harness changes through `OPERLY_MODEL_CATALOG_JSON` or
`register_model_resource(...)`.

Example:

```json
[
  {
    "provider": "openrouter",
    "id": "some/free-vision-model",
    "capabilities": ["vision", "reasoning"],
    "free": true,
    "priority": 10
  },
  {
    "provider": "ollama",
    "id": "local-specialist",
    "capabilities": ["coding"],
    "free": true,
    "priority": 20
  }
]
```

Routing asks for a capability, not a model name. Today the selector prefers free
eligible resources and then priority. This policy can later include quality,
latency, privacy and budget without changing the harness.

## Models as tools

When at least one specialist exists beyond the current orchestrator, OPERLY adds a
`model.invoke` plugin to the normal capability registry. The orchestrator supplies
only a capability, objective and bounded context. OPERLY chooses the concrete model
and provider. Delegated models receive no tools, so model-to-model delegation is
one level deep by default and cannot recurse indefinitely.

This keeps the intended boundary:

- harness, context, security, permissions, memory and business tools stay constant;
- model identity is replaceable;
- provider transport is replaceable;
- additional models become capabilities/tools available to the orchestrator;
- future image/audio/video model adapters can join the same resource catalog while
  keeping modality-specific transport inside their provider/model plugin boundary.

Coding and repair retain an independent owner allowlist through
`OPERLY_CODING_ALLOWED_MODELS`, so changing a global model cannot silently grant a
new model authority to execute coding-agent requests.
