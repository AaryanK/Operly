# Personal agent runtime migration gate

Operly already delegates explicitly selected Personal users to DragonZpyder
through a signed service request and short-lived, scoped MCP credentials.
The new gate allows all authenticated Personal users to use that same
harness **only when** all three deployment flags are independently set:
- `OPERLY_PERSONAL_HARNESS_OWNER=dragonzpyder`
- `OPERLY_PERSONAL_DRAGONZPYDER_ALL_USERS_ENABLED=1`
- `OPERLY_PERSONAL_DRAGONZPYDER_ALL_USERS_APPROVED=1`

The default continues to support explicit canary users. The model never chooses
a user ID, session, workspace, tool URL or OAuth token. Workspace contexts
remain isolated from Personal delegation. Misconfigured full-migration mode
fails closed rather than silently selecting the legacy agent.

This gate is NOT permission to activate it today. The remote path currently
does not accept Personal attachment-bearing runs; the Operly repository still
stores Personal conversations, uploaded artifacts and legacy memory.
Complete signed attachment transport, source/destination message integrity,
account deletion and effective permission revocation before enabling globally.
The Operly Workspace agent runtime also still needs a distinct trusted
workspace delegation; never send workspace authority through the Personal
harness. No production flag is changed by this PR.
