# Kernel-v3 model inference boundary

The pre-Kernel `packages/model_runtime` implementation was intentionally removed. Do
not treat its old provider registry, model catalog, role pools, or `model.invoke`
capability as current Operly architecture.

The current Agent Runtime uses one narrow inference substrate:

- `packages/agent_runtime/inference.py`
- `KernelV3AgentModel` is the model-facing facade used by objective interpretation,
  response generation, next-move reasoning, and governed planning.
- `AgentInferenceRuntime` owns retry/failover/budget policy.
- `OpenAICompatibleTransport` owns only one provider request at a time.
- Kernel remains the only capability executor. The inference runtime has no
  `ExecutionContext`, provider capability handle, approval authority, or durable
  mutation identity.

## Fixed provider destinations

Agent inference supports these operator-known routes:

- Groq: `https://api.groq.com/openai/v1`
- OpenRouter: `https://openrouter.ai/api/v1`
- Gemini OpenAI compatibility: `https://generativelanguage.googleapis.com/v1beta/openai`
- NVIDIA NIM: `https://integrate.api.nvidia.com/v1`
- local Ollama: `http://127.0.0.1:11434/v1`

There is no environment variable, user field, planner output, tool result, or model
message that can supply an inference base URL. Redirects and inherited HTTP proxy
configuration are disabled at this boundary.

The primary provider is selected with `OPERLY_AGENT_MODEL_PROVIDER`. When that value
is omitted, Operly chooses the first configured remote provider in deterministic
built-in order. Local Ollama is considered automatically only when
`OPERLY_AGENT_ALLOW_LOCAL_OLLAMA=1`.

## Explicit cross-provider failover

Cross-provider fallback is intentionally opt-in:

```env
OPERLY_AGENT_MODEL_PROVIDER=groq
OPERLY_AGENT_MODEL_FALLBACK_PROVIDERS=openrouter,gemini
```

Only retryable availability/transport failures such as timeouts, 429s and 5xx
responses may advance to the next provider. Authentication failures, invalid
requests, and invalid provider response shapes fail closed and are not sprayed
across vendors.

A provider rejecting OpenAI-compatible JSON response mode may receive one bounded
retry on the same route without `response_format`; that retry consumes the same
global attempt budget.

## Runtime budgets

Every inference call is bounded independently of Kernel execution:

- per-attempt timeout;
- total inference deadline;
- request byte ceiling;
- output byte ceiling;
- output-token ceiling;
- per-route attempt ceiling;
- total attempt ceiling across all providers;
- maximum number of provider routes considered.

An optional estimated-dollar budget can also be enabled. Operly does not invent
prices: when a finite dollar budget is configured, a remote route without explicit
operator-supplied input and output cost metadata is not eligible.

These are inference budgets only. A model retry never creates a new Kernel
capability request ID, approval ID, principal, permission set, or execution identity.

## Qualification

`scripts/benchmark_models.py` is the current manual qualification harness. It imports
only the Kernel-v3 Agent Runtime inference contract and can run text, structured JSON,
and planner-shape probes against configured fixed-destination routes.

Example:

```bash
python scripts/benchmark_models.py --provider groq --suite planner
```

The harness prints redacted structured results; it does not print credentials or raw
provider error bodies and cannot execute capabilities.

Empirical route qualification is still an admission requirement. A model name alone
is not evidence that a route supports structured output, planning, tools, coding, or
reasoning. The runtime substrate therefore must not grow inferred capability claims
from model-family names.

## Compatibility note

`OpenAICompatibleAgentModel` remains as a temporary import-compatible subclass of
`KernelV3AgentModel` so existing Runtime 1.0 callers can migrate without creating a
second inference implementation. New code should use the Kernel-v3 name.

Older `OPERLY_MODEL_*` settings in `.env.example` still belong to surviving non-Agent
compatibility surfaces. They are not the authority for the new Agent Runtime.
