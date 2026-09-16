# Personal task-completion fixtures

These fixtures are deterministic evaluation inputs, not claims that the live Personal runtime already passes them. They use synthetic accounts and `.test` recipients only.

The effect ledger belongs to the harness, not to the agent/executor. A fixture passes only when the independent oracle observes the required external state. Executor text, returned IDs, and success flags are evidence inputs, never the source of truth.

Initial frozen fixtures cover free/busy retrieval, professor email draft, invitation draft, wrong-account access, and a missing Personal Google connector. Live-model and authenticated test-account runs must be reported separately from scripted/fake-provider runs.
