# Manual Agent Runtime Testing

Operly no longer needs a live-model evaluation suite to run automatically during production startup or pull-request CI.

Use one of these explicit operator-controlled paths instead.

## GitHub Actions

1. Open **Actions** in the Operly repository.
2. Select **Manual Agent Runtime Test**.
3. Choose **Run workflow**.
4. Enter one raw prompt exactly as a user would write it.
5. Choose `personal` or `workspace` scope.
6. Run the workflow and inspect the JSON result.

The workflow performs one model request through the configured objective interpreter. It does **not** execute capabilities or mutate external state.

The output shows:

- configured provider and model
- canonical objective
- objective kind and operations
- resource hints
- external-state / mutation / future-wait flags
- complexity and dispatch path
- capability retrieval query
- bounded capability candidates

## Local shell

With the repository dependencies and provider environment configured:

```bash
python scripts/test_agent_request.py "what am i doing tomorrow after lunch" --scope personal
```

Workspace example:

```bash
python scripts/test_agent_request.py "who still owes us money" --scope workspace
```

Context-dependent example:

```bash
python scripts/test_agent_request.py \
  "mark that one done" \
  --scope personal \
  --context "assistant: The selected task has task_id task-42 and is titled Submit report."
```

## Batch evaluation

The existing raw-prompt batch evaluator remains available only as an explicit operator command:

```bash
python scripts/evaluate_real_world_objectives.py --limit 10
```

Use a non-zero interval for larger runs so a provider rate limit does not interfere with normal Operly traffic.

## Production rule

Live semantic benchmarks must never be a production startup dependency. Production should boot the runtime and serve users; synthetic model evaluation is operator-controlled work.
