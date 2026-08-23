# OPERLY multi-provider model portfolio

OPERLY treats models as provider-neutral resources. A harness requests capabilities
and traits; the model runtime chooses a concrete route. Provider credentials and
model ids stay below that boundary.

## Providers

The built-in runtime supports five providers:

- OpenRouter
- Ollama
- Groq
- Gemini OpenAI compatibility
- NVIDIA NIM OpenAI compatibility

The direct-provider adapters use the same `chat(messages, tools)` compatibility
contract as OpenRouter/Ollama. Railway lower-case secret names
`groq_api_key`, `gemini_api_key`, and `nvidia_api_key` are accepted as aliases.

## Model cards

`ModelResource` is the model card. It records:

- provider route and provider model id
- canonical model id for equivalent routes across providers
- capabilities and selection tags
- free/paid classification
- billing mode and optional per-million-token costs
- verified latency when a route has been probed
- context/modality metadata when known
- quality, locality, and priority traits

OpenRouter routes explicitly verified as `$0` are marked `free-route` and carry
numeric zero input/output costs. Other verified free-access routes are marked
`free-tier`, because quota/free-tier access is not the same statement as a permanent
zero token price.

The authenticated `GET /api/models` endpoint exposes provider status, role routing
profiles, compatibility overrides, and all active model cards.

## Automatic role routing

With `OPERLY_MODEL_AUTO_PORTFOLIO=1` and at least two configured providers, roles are
resolved from capabilities and traits instead of one global model:

- `business_agent`: tool-capable, fast, reliable orchestrators
- `coding`: tool-capable coding models, preferring fast verified routes
- `repair`: coding + stronger reasoning/heavy models
- `planner`: heavy/reliable reasoning
- `global_validator`: heavy/reliable reasoning
- `requirements_analyst`: fast/reliable reasoning
- `capability_placement`: fast reasoning
- `bounded_task`: small/fast/free models

The selector first ranks eligible cards, then builds a provider-diverse pool: one
route per provider before a second route from a provider is considered, up to five
models by default. Explicit `OPERLY_MODEL_<ROLE>_CANDIDATES_JSON` still wins when an
operator wants a fixed chain.

Capabilities remain truthful. A model that was verified only for text/reasoning is
not automatically declared tool-capable just because another route or model family
supports tools. This means all five providers can participate in reasoning/bounded
work while a tool-using coding-agent pool may contain fewer providers until those
specific routes are verified for tool calling.

## Failure routing

`ModelPool` maintains run-local health state:

1. a successful fallback becomes the preferred candidate for subsequent turns;
2. a failed model enters a short cooldown;
3. provider-wide failures such as 429, quota, or 5xx cool down the provider, so the
   next candidate is chosen from another provider;
4. invalid requests and bad credentials still fail closed rather than being sprayed
   across vendors.

This prevents a Studio run from paying the same full timeout for an unhealthy
primary model on every agent turn.

## Same-model redundancy

`canonical_id` identifies equivalent routes. For example the verified Nemotron 3
Ultra routes are represented as two provider routes for one canonical model:

- NVIDIA direct: `nvidia/nemotron-3-ultra-550b-a55b`
- OpenRouter free: `nvidia/nemotron-3-ultra-550b-a55b:free`

The direct route can therefore be preferred for latency while the OpenRouter route
remains a redundant path to the same underlying model family.
