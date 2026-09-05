# Operly Agent Runtime multi-provider inference

This document describes the **current Kernel-v3 Agent Runtime inference path**. The
older `packages/model_runtime` model-card / automatic-role-routing system no longer
exists and is not restored by Runtime 1.0.

## Providers

The Agent Runtime has five fixed destinations:

- Groq
- OpenRouter
- Gemini OpenAI compatibility
- NVIDIA NIM OpenAI compatibility
- local Ollama

Provider transport is replaceable below `AgentInferenceRuntime`, but destinations are
operator code, not model/user data. The model can choose reasoning and capability
strategy; it cannot choose a provider URL or credential.

## Primary route

`OPERLY_AGENT_MODEL_PROVIDER` selects the primary route. A primary model can be
overridden with `OPERLY_AGENT_MODEL_ID`; provider-specific defaults/overrides live in
`OPERLY_AGENT_<PROVIDER>_MODEL_ID`.

If no primary provider is named, the runtime chooses the first configured remote
provider in a fixed order. This is only initial selection; it does not automatically
construct a pool from every secret present in the environment.

## Fallback portfolio

Cross-provider fallback is explicit:

```env
OPERLY_AGENT_MODEL_PROVIDER=groq
OPERLY_AGENT_MODEL_FALLBACK_PROVIDERS=openrouter,gemini
```

The list is ordered and duplicate providers are removed. Every configured remote
fallback must have its credential available at boot/request construction time.
Unsupported provider names fail closed.

Failover policy:

1. retry a retryable failure on the current route only within the route attempt cap;
2. advance to the next explicitly configured provider only for retryable transport,
   timeout, rate-limit, or provider-availability failures;
3. never fan out a bad credential, invalid request, or invalid provider response to
   another vendor;
4. stop when the total attempt budget, provider-route budget, or total inference
   deadline is reached.

This inference retry logic is intentionally below Kernel. It cannot mint or change a
capability `request_id`, approval, workspace/principal, or mutation identity.

## Cost admission

Dollar budgeting is optional because provider prices are external facts that can
change. When `OPERLY_AGENT_INFERENCE_MAX_ESTIMATED_COST_USD` is blank, the runtime
still enforces byte/token/time/attempt budgets but makes no dollar claim.

When a finite dollar budget is configured, each remote route must also have explicit
operator price metadata:

```env
OPERLY_AGENT_GROQ_INPUT_COST_PER_MILLION=
OPERLY_AGENT_GROQ_OUTPUT_COST_PER_MILLION=
```

Unknown-priced routes are skipped under a finite budget. Operly uses a conservative
input-token estimate and reserves the configured output-token ceiling for each
attempt. This is an admission guard, not billing reconciliation.

Local Ollama is treated as zero token-price for this admission calculation; hardware
and infrastructure cost are outside this estimator.

## Qualification, not inferred capability claims

Model family names do not grant capabilities. Before a route is relied on for a
runtime role, qualify that concrete provider/model route empirically:

```bash
python scripts/benchmark_models.py --provider groq --suite probe
python scripts/benchmark_models.py --provider groq --suite smoke
python scripts/benchmark_models.py --provider groq --suite planner
```

The current suites check:

- basic text availability;
- strict JSON-object output;
- a bounded planner-shaped structured response.

The qualification harness is inference-only. It does not execute tools or Kernel
capabilities and does not receive user authority.

## What is deliberately absent

Runtime 1.0 currently does **not** claim the following old architecture is active:

- a global `/api/models` catalog as Agent Runtime authority;
- automatic role pools built from model cards;
- provider discovery deciding runtime permissions;
- `model.invoke` as a built-in capability;
- model/provider metadata granting tool or coding authority.

If richer model discovery returns later, it should be discovery data only. Admission,
budgets, authority, and capability execution must remain separate boundaries.
