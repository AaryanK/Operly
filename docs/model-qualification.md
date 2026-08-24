# Empirical model qualification

Operly must not infer runtime model capabilities from model names or vendor claims alone. A concrete route is the pair of provider plus provider model ID, because the same canonical model can behave differently across providers, service tiers, adapters, quotas, and tool protocols.

Use `scripts/benchmark_models.py` in stages:

```bash
# One cheap request per currently known free route.
python scripts/benchmark_models.py --suite probe --free-only

# JSON, reasoning, and one tool call for a bounded candidate set.
python scripts/benchmark_models.py --suite smoke --free-only --max-per-provider 8

# Full tool replay, deterministic coding, repair, and planning qualification.
python scripts/benchmark_models.py --suite deep --provider groq --model qwen/qwen3.6-27b
```

The deep suite measures availability, strict JSON, reasoning, one-step tool calling, multi-turn tool replay, deterministic Python coding, deterministic bug repair, and a Studio-shaped planning task. Generated benchmark code is syntax-checked and executed with restricted built-ins against deterministic cases; it does not get filesystem, network, import, or arbitrary-process access.

`advertisedCapabilities` are catalog metadata. `verifiedCapabilities` are benchmark evidence from that run. A transient provider quota/rate-limit is evidence about route availability, not proof that a model lacks a semantic capability, so operators should rerun inconclusive cases before changing capability metadata.

Do not deep-benchmark every dynamically discovered paid route. Probe broadly, then deep-benchmark only routes that are free or intentionally budgeted and that passed the cheaper qualification stages. Provider calls are scheduled round-robin to reduce quota bursts.
