# Studio model failover hotfix

The universal model-runtime migration removed Studio's concrete OpenRouter client mutation, but the compatibility coding route no longer carried the former default fallback models. With no `OPERLY_MODEL_CODING_CANDIDATES_JSON` configured, Studio therefore had a one-model pool (`stealth/ox-alpha`): a single 60-second timeout ended the run before any tool call.

This hotfix restores provider-local default coding/repair fallbacks inside `packages.model_runtime` while preserving E2E model agnosticism above that boundary. The defaults apply only when the role remains on the default OpenRouter provider; switching the provider disables those IDs automatically unless explicit fallbacks are configured. Cross-provider failover remains represented by `*_CANDIDATES_JSON` Model pools.

Production is also configured with an explicit three-model coding candidate pool so failover is data-driven:

1. `openrouter / stealth/ox-alpha`
2. `openrouter / openai/gpt-oss-120b:free`
3. `openrouter / qwen/qwen3-coder-flash`

Regression coverage verifies timeout fall-through, default provider-local fallbacks, suppression when the provider changes, and explicit multi-model candidate resolution.
