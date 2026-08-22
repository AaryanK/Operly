# OPERLY Target Architecture

Status: **normative target specification**

Branch: `architecture/universal-studio-runtime`

This document defines the architecture that future refactors on this branch MUST converge toward. It is intentionally written before runtime changes so that provider fixes, Studio work, plugin work, and software-generation work do not create another parallel generation of Operly architecture.

The target is an end-to-end model-agnostic, capability-agnostic, plugin-first operating layer in which Operly Studio can create and adapt arbitrary software around the tools, services, data, runtimes, and models available to a workspace.

Authorization-policy details beyond the boundary contracts in this document are deliberately deferred to a separate follow-up design. Existing authorization behavior remains authoritative until that pass. No implementation on this branch should invent a replacement authorization policy while performing the structural refactor.

---

## 1. Architectural laws

These rules are non-negotiable unless this document is deliberately revised first.

### 1.1 Everything extensible is a plugin

Models, model providers, connectors, tools, business services, runtime profiles, deployment targets, long-lived adapters, and future capability families are installed through a common plugin lifecycle.

Core Operly contains the harness, registries, security boundaries, persistence, context, audit, and orchestration contracts. It does not contain vendor-specific business logic when that logic can live in a plugin.

### 1.2 The harness consumes capabilities, not vendors

Outside the plugin/model runtime boundary, code MUST NOT branch on provider identities such as OpenRouter, Ollama, Google, Discord, WhatsApp, Slack, Railway, Stripe, or future vendors.

Vendor identity is metadata used by the plugin implementation, health/configuration UI, and audit. It is not an orchestration primitive.

### 1.3 The harness consumes models, not providers

The stable model boundary is `Model.infer(...)`.

The harness, Studio, planning, business-agent, coding, repair, and capability-placement layers MUST NOT know how a model authenticates, which remote API format it uses, or which provider carries it.

### 1.4 Discovery is not authority

A model may discover that a capability exists without being allowed to execute it.

The following states are distinct:

- discoverable;
- installed;
- configured;
- healthy;
- available to the workspace;
- visible to the current principal/session;
- authorized;
- auto-executable;
- approval-required;
- denied.

No API may collapse these states into a single `enabled` boolean.

### 1.5 Generated code never executes in the Operly control plane

All untrusted generated software execution remains outside FastAPI/control-plane processes.

Build, test, start, health, acceptance, and preview execution occur through isolated runner plugins/adapters with explicit resource, dependency, secret, and network policies.

### 1.6 Generated software never receives provider credentials directly

Studio-generated software uses `ServiceBinding` handles that call the Operly Capability Gateway. Raw Gmail, Discord, database, model-provider, payment, or deployment credentials are never written into generated source or delivered to generated application code.

### 1.7 Domain words do not choose architecture

`inventory`, `CRM`, `booking`, `restaurant`, `commerce`, `website`, and future domains are requirements data, not framework-selection switches.

Runtime selection is based on executable requirements, source/runtime constraints, requested interfaces, persistence, networking, deployment needs, and available runtime plugins.

### 1.8 One canonical invocation authority

Every externally meaningful capability invocation must pass through the same capability execution boundary. MCP, Operly AI, Studio-generated software, connectors, automations, and internal agents must not create bypass paths.

### 1.9 Compatibility layers may translate, never own new behavior

Legacy `SiteSchema`, managed-application manifests, old harnesses, and compatibility clients may remain during migration, but no new capability, provider, runtime, or Studio behavior may be implemented primarily in those layers.

---

## 2. Target top-level architecture

```text
Human / External Client / Channel
               |
               v
        Operly Agent Runtime
               |
       +-------+--------+
       |                |
       v                v
 Model Registry    Capability Registry
       |                |
   Model.infer()   discovery / describe
       |                |
       +-------+--------+
               |
       Capability Firewall
               |
       canonical invocation
               |
     Plugin Runtime / Providers
               |
       verified result + audit

               |
               v
          Operly Studio
               |
        SoftwareProject
       /       |        \
    Source  ServiceBindings RuntimePlugin
       \       |        /
        isolated runner
               |
         deployed solution
               |
       Capability Gateway
               |
       real workspace tools
```

The same model and capability registries are used by the business agent, planning, coding, repair, Studio, MCP, generated applications, and future automation surfaces.

---

# 3. Core contract: `Model`

A model is a configured inference resource. A provider is an implementation detail behind the model.

## 3.1 Public interface

```python
class Model(Protocol):
    id: str
    tags: frozenset[str]
    capabilities: ModelCapabilities
    traits: ModelTraits

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        ...
```

Construction is provider-backed but occurs below the orchestration boundary:

```python
model = model_registry.configure(
    id="local-fast",
    provider="ollama",
    model="gemma...",
    credential_ref=None,
    tags={"default", "fast", "free", "tools"},
)

result = await model.infer(request)
```

Callers must not instantiate `OpenRouterClient`, `OllamaClient`, or future transport clients directly.

## 3.2 `InferenceRequest`

```python
@dataclass(frozen=True)
class InferenceRequest:
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...] = ()
    response_schema: dict | None = None
    modality_inputs: tuple[ModelInput, ...] = ()
    budget: InferenceBudget | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
```

The request is provider-neutral. Provider adapters translate it into vendor payloads.

## 3.3 `InferenceResult`

```python
@dataclass(frozen=True)
class InferenceResult:
    message: Message
    model_resource_id: str
    provider: str
    provider_model_id: str
    latency_ms: int
    usage: ModelUsage | None
    finish_reason: str | None
```

Provider/model identity is returned for provenance and telemetry, not for orchestration branching.

## 3.4 Model tags and traits

Tags are human/operator policy labels such as:

```text
default
fast
free
local
private
coding
reasoning
vision
long-context
heavy
```

Capabilities represent factual support:

```text
text
tools
structured_output
reasoning
vision
audio_input
transcription
speech
image_generation
coding
```

Traits represent selection signals, not guarantees:

```python
class ModelTraits:
    latency_class: str | None
    cost_class: str | None
    quality_class: str | None
    context_tokens: int | None
    locality: str | None
```

## 3.5 Model selection

Callers request requirements and preferences, not concrete provider IDs:

```python
model = await model_registry.resolve(
    ModelSelector(
        requires={"text", "tools"},
        prefer_tags={"fast", "free"},
        avoid_tags=set(),
    )
)
```

A role such as `coding` or `planner` may resolve to a selector policy, but roles do not directly own provider strings.

## 3.6 Failover

Failover is a model-runtime responsibility and MUST support cross-provider candidates.

```python
ModelPool([
    ModelRef("local-fast"),
    ModelRef("openrouter-coder"),
    ModelRef("remote-heavy"),
]).infer(request)
```

A Studio or planning module must never contain `isinstance(OpenRouterClient)` or same-provider fallback mutation.

The model runtime owns:

- timeout classification;
- retry policy;
- per-attempt budgets;
- cross-provider failover;
- provider error normalization;
- streaming translation;
- tool-call translation;
- model-attempt telemetry.

## 3.7 Delegation

`model.invoke` remains a normal capability. Delegation selects a model by required capabilities/tags. Recursive delegation is bounded by the harness, not by provider code.

---

# 4. Core contract: `Plugin`

A plugin is an installable package that contributes resources and/or capabilities to Operly.

## 4.1 Public manifest

```python
@dataclass(frozen=True)
class PluginManifest:
    id: str
    version: str
    display_name: str
    description: str
    capabilities: tuple[CapabilitySpec, ...] = ()
    permissions: tuple[PermissionSpec, ...] = ()
    model_providers: tuple[ModelProviderSpec, ...] = ()
    model_discoverers: tuple[ModelDiscovererSpec, ...] = ()
    runtime_plugins: tuple[RuntimePluginSpec, ...] = ()
    lifecycle: PluginLifecycleSpec | None = None
    events: tuple[EventSpec, ...] = ()
    configuration_schema: dict | None = None
```

## 4.2 Lifecycle

A plugin may implement:

```python
class PluginLifecycle(Protocol):
    async def install(self, context): ...
    async def start(self, context): ...
    async def health(self, context) -> HealthResult: ...
    async def stop(self, context): ...
    async def uninstall(self, context): ...
```

Long-lived Discord/WhatsApp/Slack-style adapters are started through plugin lifecycle registration. Central application startup must not grow vendor-specific `if DISCORD_BOT_TOKEN` branches.

## 4.3 Registration rule

Installing a plugin registers its declared resources. Core `default_registry()` may bootstrap built-ins during migration, but the target registry is manifest-driven.

Adding a new connector or model provider must not require editing the agent loop, Studio, MCP, or capability firewall.

---

# 5. Core contract: `Capability`

A capability is the universal model-visible/actionable unit. A plugin can contribute one or many capabilities.

## 5.1 Public specification

```python
@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    version: str
    display_name: str
    description: str
    input_schema: dict
    output_schema: dict

    permissions: tuple[str, ...] = ()
    risk: str = "read_only"
    execution_mode: str = "internal"
    reversible: bool = False

    plugin_id: str = "core"
    integration_provider: str | None = None
    credential_scopes: tuple[str, ...] = ()

    tags: frozenset[str] = frozenset()
    semantic_operations: frozenset[str] = frozenset()
```

`CapabilityDefinition` should become a compatibility alias or migrate directly to this contract.

## 5.2 Provider interface

```python
class CapabilityProvider(Protocol):
    async def execute(
        self,
        context: CapabilityExecutionContext,
        capability: CapabilitySpec,
        arguments: Mapping[str, JSONValue],
    ) -> CapabilityExecutionResult:
        ...

    async def verify(
        self,
        context: CapabilityExecutionContext,
        capability: CapabilitySpec,
        arguments: Mapping[str, JSONValue],
        result: CapabilityExecutionResult,
    ) -> CapabilityVerificationResult:
        ...
```

Optional provider hooks:

```text
health
compensate
estimate_cost
prepare
```

## 5.3 Capability discovery kernel

The default model does not receive every installed schema. Every capable agent receives a small permanent discovery kernel:

```text
capability.search
capability.describe
context.search
model.invoke
```

`capability.search(query, constraints)` performs semantic discovery over capabilities visible for discovery in the current workspace/session.

`capability.describe(ids)` returns exact schemas plus availability/configuration/risk metadata.

Execution still goes through the normal firewall. Discovery never grants permission.

## 5.4 Progressive exposure

The normal flow is:

```text
small initial toolset
    -> model determines missing operation
    -> capability.search
    -> capability.describe
    -> selected capability schema becomes available
    -> model invokes capability
    -> firewall decides execution
```

This keeps prompts bounded even when a workspace has thousands of capabilities.

---

# 6. Core contract: `CapabilityFirewall`

`CapabilityFirewall` is the single mandatory execution boundary between a request to use a capability and the provider that can perform it.

The detailed authorization strategy will be specified in the next authorization pass. This contract deliberately defines the seam without changing current user/workspace policy yet.

## 6.1 Public interface

```python
class CapabilityFirewall(Protocol):
    async def evaluate(
        self,
        request: CapabilityInvocation,
        execution_context: ExecutionContext,
    ) -> CapabilityDecision:
        ...

    async def invoke(
        self,
        request: CapabilityInvocation,
        execution_context: ExecutionContext,
    ) -> CapabilityInvocationResult:
        ...
```

## 6.2 Decision shape

```python
class CapabilityDecision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
```

The future policy engine must compose guards monotonically:

```text
ALLOW + ALLOW = ALLOW
ALLOW + ASK   = ASK
ALLOW + DENY  = DENY
ASK   + DENY  = DENY
```

No downstream plugin may upgrade `DENY` to `ASK` or `ALLOW`.

## 6.3 Mandatory execution stages

The invocation pipeline has stable stages even while policy details evolve:

```text
resolve trusted execution context
resolve installed capability/plugin
validate input schema
check plugin configuration/health
run authorization/policy evaluation
obtain approval when decision is ASK
enforce execution-mode boundary
enforce scoped credential/network/resource policy
execute provider
validate output schema
verify postcondition
persist authoritative audit/result
deliver normalized observation to caller
```

## 6.4 Universal routing requirement

These surfaces MUST use this boundary:

- Operly AI;
- Studio agent;
- generated software through Capability Gateway;
- MCP;
- Discord/WhatsApp/other channel agents;
- workflows/automations;
- future external clients.

No surface may call provider `.execute()` directly as an optimization.

---

# 7. Core contract: `SoftwareProject`

`SoftwareProject` is the canonical Studio artifact. Website projects, managed apps, and generated custom software become runtime/compatibility implementations behind it.

## 7.1 Public model

```python
@dataclass
class SoftwareProject:
    id: str
    workspace_id: str
    name: str
    description: str
    state: ProjectState

    active_source_version_id: str | None
    active_runtime_id: str | None
    service_binding_ids: tuple[str, ...]

    created_by: str
    created_at: datetime
    updated_at: datetime
```

## 7.2 Project lifecycle

```text
draft
planning
building
preview_ready
approved
publishing
live
degraded
failed
archived
```

The lifecycle belongs to the project/solution layer, not to a specific website or manifest implementation.

## 7.3 Source

Source is immutable-versioned, project-scoped, and content-addressed where useful.

All source edits create a new source version. Rollback creates a new version referencing/restoring prior content rather than mutating history.

## 7.4 Studio session

A Studio session operates on:

```python
StudioSession(
    project=SoftwareProject,
    source=SourceVersion,
    runtime=RuntimePlugin | None,
    bindings=tuple[ServiceBinding, ...],
    workspace_context=...,
    selected_ui_context=...,
)
```

Studio itself does not assume the project is a website.

## 7.5 Compatibility runtimes

During migration:

```text
legacy SiteSchema project -> compatibility adapter
ManagedApplication manifest -> compatibility adapter
GeneratedProject -> imported/normalized SoftwareProject
```

No new core functionality should depend on `RuntimeType.STUDIO`, `MANAGED_APP`, or `GENERATED_PROJECT` as product identities.

---

# 8. Core contract: `RuntimePlugin`

A runtime plugin tells trusted Operly infrastructure how to build, test, run, inspect, and deploy a class of source trees.

Models author source. Runtime plugins own executable mechanics.

## 8.1 Public interface

```python
class RuntimePlugin(Protocol):
    spec: RuntimePluginSpec

    def detect(self, source: SourceTree) -> RuntimeMatch:
        ...

    def validate(self, source: SourceTree) -> RuntimeValidation:
        ...

    def build_submission(
        self,
        project: SoftwareProject,
        source: SourceVersion,
        bindings: tuple[ServiceBinding, ...],
    ) -> BuildSubmission:
        ...
```

## 8.2 Runtime specification

```python
@dataclass(frozen=True)
class RuntimePluginSpec:
    id: str
    version: str
    languages: frozenset[str]
    source_markers: tuple[str, ...]
    operations: tuple[str, ...]
    dependency_policy: DependencyPolicy
    network_policy: NetworkPolicy
    resource_policy: ResourcePolicy
    supports_preview: bool
    supports_deploy: bool
    service_binding_modes: frozenset[str]
```

## 8.3 Runtime registry

The current fixed runtime dictionary becomes `RuntimeRegistry`:

```python
runtime = runtime_registry.resolve(source_tree, requirements)
```

Initial runtime plugins may include:

```text
static-web
python-stdlib-web
node-web
react-web
next-fullstack
fastapi-web
worker
```

The exact initial set is an implementation decision. The architecture must not assume only two source shapes.

## 8.4 Deterministic mechanics

The model may choose implementation source, but it cannot invent runner commands, bypass dependency policy, request arbitrary network access, or inject secret values.

Runtime plugins generate typed `BuildSubmission` contracts using trusted configuration.

## 8.5 Runner isolation

Existing runner contracts remain the foundation. The runtime plugin chooses declared operations and policies; the isolated runner enforces them.

Runner feedback is structured and may be returned to the coding model for bounded repair.

---

# 9. Core contract: `ServiceBinding`

A `ServiceBinding` connects generated software to an Operly capability without giving generated code provider credentials or provider-specific APIs.

This is the key bridge between Studio-generated software and the real tools/services a business already uses.

## 9.1 Public model

```python
@dataclass(frozen=True)
class ServiceBinding:
    id: str
    project_id: str
    workspace_id: str

    semantic_name: str
    capability_id: str
    capability_version: str

    binding_mode: str
    principal_scope: str
    configuration: Mapping[str, JSONValue]

    created_at: datetime
```

Example:

```text
semantic_name = "customer_message_send"
capability_id = "gmail.send_email"
```

Another workspace may bind the same semantic project operation to:

```text
whatsapp.send_message
```

without changing the project's domain design.

## 9.2 Binding resolution

During planning/building, Studio may request:

```python
bindings.resolve(
    operation="send customer message",
    requires={"messaging", "external_write"},
)
```

Candidate capabilities come from the workspace capability registry. The user/model can choose among valid installed services where ambiguity matters.

## 9.3 Capability Gateway

Generated applications invoke bindings through a scoped gateway:

```text
generated app
    -> binding handle
    -> Operly Capability Gateway
    -> trusted project/runtime identity
    -> CapabilityFirewall
    -> provider
```

The application never receives the underlying provider token.

## 9.4 Binding safety

A binding may restrict:

```text
allowed capability
allowed argument fields/value shapes
workspace
project/runtime identity
rate/budget
network route
credential alias
approval behavior inherited from firewall
```

Generated software cannot transform one binding into arbitrary access to the full capability registry.

---

# 10. Agent runtime

The business agent and coding agent should share the same model and capability infrastructure while retaining task-specific system prompts, context policies, and session tools.

## 10.1 Generic loop

```python
while budget.remaining:
    model = await model_registry.resolve(session.model_selector)
    result = await model.infer(
        InferenceRequest(
            messages=session.messages,
            tools=await capability_view.schemas(session),
        )
    )

    if not result.message.tool_calls:
        return result

    for call in result.message.tool_calls:
        observation = await capability_firewall.invoke(...)
        session.observe(observation)
```

No model/provider transport logic lives here.

## 10.2 Session capability view

The session chooses a small initial set from:

- permanent discovery kernel;
- task-local capabilities;
- project-local workspace capabilities;
- user/workspace-authorized surfaced capabilities.

The model can discover more instead of receiving the entire global registry.

## 10.3 Coding session capabilities

Current `CodingToolRegistry` mechanics should migrate into session-scoped capabilities such as:

```text
workspace.list
workspace.glob
workspace.read
workspace.search
workspace.write
workspace.edit
workspace.remove
workspace.diff
preview.inspect
runner.build
runner.logs
runner.test
runner.health
```

This does not mean every business agent sees filesystem tools. Capability visibility remains session-scoped.

---

# 11. Studio target behavior

Operly Studio is the environment for creating and evolving software, not a website-specific subsystem.

## 11.1 Universal workflow

```text
user objective
   -> inspect existing SoftwareProject/workspace resources
   -> discover relevant capabilities/services
   -> determine create / modify / compose
   -> plan requirements
   -> select/create ServiceBindings
   -> edit project source through coding session
   -> resolve RuntimePlugin
   -> isolated build/test/start/acceptance
   -> preview
   -> repair/edit loop
   -> explicit production action
```

## 11.2 Create vs modify vs compose

The current capability-placement idea should survive as a generic workspace decision:

```text
create_new
modify_existing
compose_existing
clarify
```

A user request does not automatically become another standalone application.

## 11.3 Website behavior

Current website grounding, accessibility, safe static preview, and native-browser rules remain useful, but become rules of a website runtime/profile/session rather than the definition of Studio itself.

---

# 12. Module-by-module target map

This section is normative for code ownership.

## `packages/model_runtime/`

Target owner of:

- `Model`;
- `InferenceRequest` / `InferenceResult`;
- provider adapters;
- `ModelRegistry`;
- `ModelSelector`;
- model tags/capabilities/traits;
- discovery;
- retry/failover/timeout/error normalization;
- model invocation telemetry.

No provider-specific types should be imported by Studio, planning, capability-placement, business-agent, or coding modules.

Current `ModelRoute` may remain temporarily as a compatibility policy adapter but must not remain the primary public abstraction.

## `packages/plugins/`

Target owner of:

- `PluginManifest`;
- installation/registration;
- plugin lifecycle;
- configuration schema;
- plugin health;
- resource contribution registration.

This becomes the source of truth for installed plugin resources.

## `packages/capabilities/`

Target owner of:

- `CapabilitySpec`;
- `CapabilityRegistry`;
- semantic discovery/search;
- description/schema expansion;
- session capability views;
- provider interface;
- `CapabilityFirewall` integration.

`default_registry()` becomes migration/bootstrap code rather than the long-term extension mechanism.

## `packages/actions/`

Target owner of:

- persisted invocation/action lifecycle;
- approval lifecycle integration;
- verification state;
- idempotency/action provenance;
- authoritative result ledger.

It should be called from the firewall rather than act as an alternative capability path.

## `packages/security/`

Target owner of trusted `ExecutionContext` resolution and future authorization-policy components.

Authorization redesign is explicitly deferred to the next pass. Structural changes in this architecture pass should preserve the seam and current behavior.

## `packages/business_brain/`

Target: task-specific business agent policy/context built on generic `AgentRuntime` + `Model` + capability session view.

Provider compatibility classes named `OllamaClient` are migration-only and should disappear from business code.

## `packages/harness/`

Legacy harness. No new behavior. Migrate callers to the canonical agent/capability/model runtime and remove once unused.

## `packages/coding_harness/`

Keep:

- persistent inspect/edit/observe loop;
- virtual workspace semantics;
- context compaction/working set;
- immutable source interaction;
- repair loop concepts.

Change:

- use `Model.infer()`;
- source tools become session-scoped capabilities;
- runner observations become capabilities/results;
- no provider-specific web/model behavior;
- no runtime identity hardcoded into generic coding loop.

## `packages/studio/`

Target: generic Studio orchestration over `SoftwareProject`.

Website-specific source rules move to runtime/session policy modules.

Provider-specific model latency/fallback code is prohibited here.

## `packages/custom_software/`

Keep and migrate valuable mechanisms:

- requirement planning;
- source bundles;
- runner contracts/adapters;
- build/test/repair;
- plan coverage;
- previews.

Move runtime-profile registration into the runtime plugin system. Retire domain-to-framework authority.

## `packages/application_builder/`

Compatibility/runtime implementation only. No new architectural authority. Existing managed applications should become importable/resolvable through `SoftwareProject`/solution compatibility adapters.

## `packages/solutions/`

Promote toward canonical project/solution lifecycle facade. Over time it should stop normalizing three product generations and instead represent `SoftwareProject` directly.

## `packages/connectors/`

Each connector is a plugin contributing capabilities, configuration, credential scopes, lifecycle hooks/events, and health.

Central connector runtime must not know vendor names.

## `packages/mcp/`

Remain a transport/presentation surface over the canonical capability registry + firewall. MCP never becomes a second tool execution authority.

## `apps/api/`

Routers are transport boundaries only. They resolve trusted request context and call domain services. They must not contain provider/model routing or direct capability-provider execution.

---

# 13. Dependency direction

Preferred high-level import direction:

```text
apps/api
   -> domain services / agent runtime / studio

studio / business agent / coding / planning
   -> model_runtime public contracts
   -> capabilities public contracts
   -> project/runtime services

capabilities
   -> actions + security contracts + plugin runtime

model_runtime
   -> provider plugins only

runtime plugins
   -> runner contracts

provider plugins
   -> external SDK/API implementation
```

Forbidden examples after migration:

```text
studio -> OpenRouterClient
planning -> OllamaClient
business_agent -> provider HTTP API
coding_harness -> provider-specific fallback settings
MCP -> provider.execute()
generated app -> provider credential
```

---

# 14. Observability contracts

Every model attempt records, at minimum:

```text
model resource id
provider
provider model id
attempt number
selection reason/tags
start/end/latency
failure classification
retry/failover decision
usage when available
```

Every capability invocation records, at minimum:

```text
workspace/project/session identity
principal/execution context reference
capability id/version/plugin
arguments digest or safely auditable representation
firewall decision
approval/action id when relevant
execution start/end
provider result state
verification state
external reference when available
```

Generated software and Studio actions should be debuggable without exposing secrets.

---

# 15. Migration invariants

During migration, tests MUST preserve these properties:

1. A future model provider can be registered without editing coding, planning, Studio, or business-agent logic.
2. A future capability plugin can be installed without editing the agent loop.
3. Model fallback can cross provider boundaries.
4. Studio does not import concrete model provider classes.
5. Discovery of a capability does not imply permission to execute it.
6. MCP and generated applications route through the same capability invocation authority.
7. Generated code receives no raw provider secrets.
8. Generated code never executes in the control plane.
9. Runtime execution commands come from trusted runtime plugins, not model-authored arbitrary shell policy.
10. Existing website source projects remain previewable/editable through compatibility migration.
11. Existing managed applications and generated projects remain accessible during project unification.
12. Provider failure, capability failure, runner failure, and validation failure remain distinguishable in telemetry.

---

# 16. Migration sequence

Implementation must proceed in dependency order rather than by UI symptom.

## Phase A — contracts only

- add the new public contracts and tests;
- no production behavior change;
- define compatibility adapters.

## Phase B — model E2E abstraction

- introduce `Model`, `InferenceRequest`, `InferenceResult`, `ModelRegistry`, selectors and pools;
- adapt OpenRouter/Ollama underneath;
- migrate coding, planning, capability placement, business agent and Studio callers;
- remove provider checks outside `model_runtime`;
- add cross-provider failover telemetry/tests.

## Phase C — plugin and capability discovery foundation

- canonical `PluginManifest`;
- manifest-driven resource registration;
- `capability.search` / `capability.describe`;
- session capability views;
- keep current authorization behavior behind `CapabilityFirewall` seam.

## Phase D — coding capability convergence

- adapt workspace/preview/runner coding tools into session capabilities;
- retain current coding loop semantics through compatibility adapters;
- remove the second independent execution registry as an authority.

## Phase E — `SoftwareProject` + Studio unification

- introduce canonical project/source/session service;
- migrate website/generated/managed-app paths behind compatibility adapters;
- Studio stops branching on product generation as its core orchestration model.

## Phase F — runtime plugin registry

- migrate existing static/Python profiles;
- add additional trusted full-stack runtime plugins;
- enable typed dependencies/network/secrets only through runtime policy.

## Phase G — `ServiceBinding` + Capability Gateway

- bind generated software to real workspace capabilities;
- provide project-scoped runtime identities/handles;
- route runtime calls through `CapabilityFirewall`;
- never expose provider secrets.

## Phase H — connector/runtime lifecycle pluginization

- migrate Discord/Google and future connectors to common plugin lifecycle;
- remove vendor-specific central startup branches.

## Phase I — retire compatibility architecture

Only after parity tests:

- remove legacy `packages/harness`;
- remove provider-named compatibility clients from callers;
- demote/remove SiteSchema as primary Studio generation path;
- retire domain-to-framework routing authority;
- retire independent ApplicationBuilder orchestration where replaced;
- simplify `SolutionService` around canonical `SoftwareProject`.

---

# 17. Explicitly deferred authorization pass

The structural architecture intentionally leaves authorization policy implementation behind `CapabilityFirewall` and trusted `ExecutionContext`.

The next authorization pass may redefine approval UX, channel identity behavior, Discord-style authority, delegation, grants, or principal models. Until then:

- current trusted workspace/user resolution remains authoritative;
- current capability permission/approval behavior is preserved where possible;
- this branch may introduce interfaces and adapters but must not silently broaden authority;
- no generated runtime or new plugin gets an authorization bypass for convenience.

---

# 18. Definition of success

The refactor is complete when this scenario requires no special-case architecture:

> A business installs Gmail, Discord, an inventory service, a database, a deployment provider, and several models. The owner asks Operly Studio to build a tailored operations application. Studio discovers the existing capabilities, chooses or asks about useful service bindings, uses a fast default model for ordinary work and delegates difficult coding/reasoning to stronger model plugins, writes real source, selects an installed runtime plugin, builds/tests it in isolation, repairs failures, previews it, and later deploys it. At runtime the application sends email, posts Discord alerts, and updates inventory through scoped Operly service bindings. The generated application never receives vendor credentials. Changing Gmail to WhatsApp, OpenRouter to Ollama, or one deployment provider to another does not require rewriting the harness or the application architecture.

That is the architectural target for this branch.
