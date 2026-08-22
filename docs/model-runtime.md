# Model runtime plugins

OPERLY treats model providers as infrastructure plugins. Business, planning,
semantic-routing, capability-placement, and coding harnesses depend on the same
`chat(messages, tools)` contract and do not know how a provider authenticates or
formats its HTTP payload.

The default portfolio is OpenRouter using `stealth/ox-alpha` for every model role.
Railway's existing `OPEN_ROUTER_API` variable is accepted directly. No secret
belongs in the repository.

To switch every role later, set only:

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

For models behind the same provider, switching is normally just the model ID. For
a different provider, the harness still stays unchanged; only the provider adapter
owns base URL, API key, payload translation, tool-call format, multimodal encoding,
streaming semantics, retries, and response normalization.

Coding and repair retain an independent owner allowlist through
`OPERLY_CODING_ALLOWED_MODELS`, so changing a global model cannot silently grant a
new model authority to execute coding-agent requests.
