# Operly plugin harness

The business model reasons over a bounded company context and the plugin schemas it is authorized to see. It may inspect, act, observe, and replan within one model session. Deterministic code does not select business strategy.

Every model plugin call crosses `PluginAgentHarness`. The harness validates the input schema, tenant and role authority, enabled state, provider configuration, policy and approval requirement. It persists a `BusinessAction`, invokes the resolved provider with a timeout and idempotency key, validates provider output, verifies the postcondition, and emits lifecycle events.

`CapabilityDefinition` is the compatibility name for the universal `PluginDefinition`. Its contract contains ID, display name, description, version, input/output schemas, risk, permissions, approval policy, execution mode, source/provider, optional integration and credential scopes, and reversibility. Providers implement `execute` and `verify`; they may implement compensation and health checks.

Current built-ins are:

- `company.read_state`, `company.search_events`
- `crm.search_leads`, `crm.create_lead`, `crm.update_lead`
- `analytics.query`
- `website.inspect`, `website.edit`
- `messaging.draft`, `messaging.send`
- `solution.inspect`, `solution.generate`

`messaging.send` currently queues an approved outbound follow-up in Operly and deliberately does not claim external delivery. `solution.generate` enters the existing software-plan and isolated coding-harness workflow; generated code never executes in the FastAPI control plane.
