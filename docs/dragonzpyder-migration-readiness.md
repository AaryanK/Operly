# Central identity and data migration: operator readiness contract

Operly remains the authority for real users, memberships, verified Google/
Discord identity associations, encrypted personal connector credentials,
business Kernel capabilities, workflow records, conversation transcripts and
artifacts until each partition is separately verified.

## Read-only source inventory

`python -m scripts.dragonzpyder_migration_inventory` prints ONLY counts and
workflow status totals. Run using a read-only source DB role. No passwords,
identities, OAuth credentials, connector ciphertext, conversation content,
workspace ID, or artifact bytes are exported.

A count reconciliation alone cannot prove identity parity, referential
integrity, or file integrity. Never set activation flags because this command
returns success. Back up and test restore before modifying any production row.

## Fail-closed cutover gates

1. **Identity:** operator-approved canonical UUID mapping; preserve exact
   validated login provider subjects, password-hash compatibility and immutable
   Operly user IDs. No linking by email. Import into a dedicated identity
   database; verify owner/membership counts without activating by default.
   Actual identity importer refuses active accounts until an audited follow-on
   activation migration. Issue and redeem authorization codes only with
   browser consent, client registration and PKCE. Test Google, Discord,
   session revocation and browser cookies under genuine HTTPS.
2. **Workflows:** reconcile every definition/version, schedule with timezone,
   event cursor, in-flight lease, approval and terminal trace. The first
   Hatchet pulse is a non-destructive compatibility clock only. Prove separate
   dispatch and business-effect parity before deleting legacy executor.
3. **AI:** use one DragonZpyder Deep Agents/LangGraph harness for Personal and
   Workspace. Keep signed Personal and Workspace scopes separate. The old
   Operly runtime remains for unported surfaces; do not silently drop uploads
   or break workspace permissions.
4. **Connectors:** migrate encrypted OAuth tokens only via isolated trusted
   operator code and explicit provider scope/user consent, re-key under
   DragonZpyder's own encryption authority; DO NOT reserialize decrypted
   tokens into chat, agent prompts, checkpoints, Hatchet payloads or logs.
   Dual account, revocation, rotation and refresh are required.
5. **Memory and artifacts:** explicit memory user consent, stable canonical
   identity and deletion rights; preserve original conversation and artifact
   bytes/checksums with permissions. A virtual LangGraph artifact readback is
   not proof of an independently stored user file.

Rollback requires restoring old local authentication and workflow driver,
draining remote wakes, preserving original database rows and reconciling
uncertain external mutations. No destructive production mutation is part of
this inventory.
