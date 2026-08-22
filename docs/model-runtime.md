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

There is deliberately no Gemma-only, Ox-only, OpenRouter-only, or Ollama-only
model guardrail in the harness. Defaults choose Ox Alpha today; they do not limit
what registered providers/models may be selected tomorrow.

## Model resources and discovery

Models are catalog resources separate from providers. Each resource can declare a
provider, model id, human name, capabilities, free/paid status, routing priority,
context length, input/output modalities and supported provider parameters.

The orchestrator is always included automatically. Additional resources can come
from three interchangeable sources:

1. static environment configuration through `OPERLY_MODEL_CATALOG_JSON`;
2. runtime registration through `register_model_resource(...)`;
3. provider discovery plugins through `register_model_discoverer(...)`.

OpenRouter discovery is installed now. It reads OpenRouter's live `/api/v1/models`
catalog behind the provider boundary and translates provider metadata into Operly
capabilities such as `text`, `vision`, `video`, `audio_input`, `image_generation`,
`tools`, `reasoning`, `structured_output`, `coding`, `translation`, `reranking`,
`transcription`, and `speech` when the provider metadata supports or identifies
them. Discovery is cached and a provider outage does not replace configured
resources or break the orchestrator.

Example static resource:

```json
[
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

Adding another marketplace/provider catalog is therefore another discovery plugin,
not a harness rewrite.

## Models as tools

`model.invoke` is a normal built-in capability. The orchestrator supplies only a
capability, objective and bounded context. At invocation time Operly refreshes
provider discovery as needed, chooses the concrete model/provider, and invokes it
through the provider registry.

Delegated models currently receive no tools, so model-to-model delegation is one
level deep by default and cannot recurse indefinitely. This is a delegation-loop
safety boundary, not a restriction on which model or provider may be used.

This keeps the intended boundary:

- harness, context, security, permissions, memory and business tools stay constant;
- model identity is replaceable;
- provider transport is replaceable;
- provider model catalogs are discoverable data;
- additional models become capabilities/tools available to the orchestrator;
- future image/audio/video model adapters can join the same resource catalog while
  keeping modality-specific transport inside their provider/model plugin boundary.
