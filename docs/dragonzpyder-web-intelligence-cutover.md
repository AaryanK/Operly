# Web intelligence cutover

Personal and workspace chat (including uploads) use signed DragonZpyder endpoints exclusively. Operly retains business permissions, scoped artifact handles, conversation UI projections, MCP capability selection, approvals and business workflows. There is no local chat fallback. Every first-party MCP delegation issue/use revalidates central identity status for migrated accounts; local users retain their current authentication during staged migration.

Deploy only with configured DragonZpyder endpoints, existing backend credentials, both MCP delegation gates and both DragonZpyder harness flags. Verify real tool calls and checkpoints, attachment readback, personal/workspace scope isolation and revocation before retiring dependencies. Existing mutation restrictions remain in force.

Discord still uses its existing channel authority and must be migrated through a distinct signed channel delegation, not a synthetic browser session. Old queued intelligence tasks and remaining local inference entry points require a separate audit. No local agent worker or database deletion is authorized by this change.
