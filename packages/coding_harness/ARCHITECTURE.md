# OPERLY capability harness

OPERLY uses a **graph outside, loops inside, harness around both** architecture.
OpenCode is architectural inspiration for local coding-agent autonomy; OPERLY keeps
its own tenant, approval, source-snapshot, capability and isolated-runner boundaries.

```text
user intent
    |
    v
requirements + genuine ambiguity gate
    |
    v
DYNAMIC CAPABILITY GRAPH
  nodes = bounded capabilities
  edges = explicit dependencies
    |
    v
whole-graph semantic review
    |
    v
approved specification
    |
    v
LOCAL CODING AGENT LOOP
 inspect -> act -> observe -> continue
    |
    v
immutable source snapshot
    |
    v
ISOLATED RUNNER LOOP
 build -> test -> start -> health
    |                         |
    | failure evidence        | pass
    v                         v
repair coding loop         preview/capability
```

The graph determines **what can happen and what depends on what**. Local agent loops
provide autonomy inside one bounded task. Deterministic code validates repetitive
contracts and schedules transitions. The harness enforces source, execution,
permission and tenant reality.

## Planning: compact dynamic graph

The historical per-node recursive planner is no longer the default live planning
engine on this branch. Its validator -> partitioner -> expander -> validator pattern
repeated the same requirement context and could spend tens of thousands of tokens
before coding began.

Normal graph planning is intentionally bounded to three semantic calls:

1. requirements analysis and genuine owner ambiguity,
2. one implementation-ready capability graph,
3. one whole-graph semantic review.

If review fails, OPERLY performs one graph-level repair and one re-review. This keeps
the semantic repair path at five calls rather than creating an LLM loop for every
leaf. Provider/shape retry headroom remains separately bounded by the planning
budget.

Acceptance tests are derived deterministically from the requirement ledger and
attached to linked graph nodes. Artifact identity is also deterministic. The model
is therefore not paid to restate the same acceptance criteria or invent document
artifacts for every node.

Implementation mechanics such as framework, API style, MCP versus internal tool,
database/storage engine, serialization format and deployment mechanics are deferred
to the coding harness unless the user explicitly made them a product requirement.
Pure constraints attach to executable capability nodes instead of becoming fake
subsystems.

## Coding agent: persistent generic tool loop

- **One persistent model/tool session per coding operation.** Tool observations are
  appended to the same message history and the model continues from what actually
  happened.
- **Generic tool registry.** Software domains do not change the tool vocabulary.
- **Permission modes.** `plan` is read-only; `build`, `edit`, and `repair` receive
  project mutation tools. This is a policy distinction rather than a different
  application generator.
- **Bounded reads and tool outputs.** Large files/web responses do not silently
  consume the entire model context.
- **Doom-loop detection.** Three identical consecutive tool calls terminate the
  operation rather than consuming the remaining step budget.
- **Questions are agent actions.** A material ambiguity can surface as a structured
  owner clarification instead of being converted into guessed source code.
- **Research is optional.** Ollama `web_search` / `web_fetch` tools are exposed only
  when enabled and their results are explicitly untrusted evidence.
- **Visual observation is first class.** Studio can pass a selected rendered DOM
  element, computed style, geometry, parent, viewport, and page metadata. The agent
  must map that observation back to source with `grep` / `read` before editing.

## Safety boundaries intentionally different from OpenCode

- **No shell inside the OPERLY control plane.** Generated source is untrusted.
  Build, tests, process startup, health checks, and future interactive shell actions
  belong to the isolated runner.
- **No arbitrary plugin execution in the control plane.** Future extensibility
  registers typed tools whose execution authority is independently scoped.
- **No unrestricted external-directory access.** The editable workspace is the
  project source snapshot represented by `GeneratedSourceBundle`.
- **No domain-specific builders.** Inventory, booking, research, websites, bots,
  unknown future products, and edits to existing software are all data presented to
  the same graph + agent-loop substrate.

## Source and execution lifecycle

```text
approved specification
        |
        v
persistent coding session
  list / glob / read / grep
  visual observation
  optional web research
  write / edit / remove / diff
  question / finish
        |
        v
immutable GeneratedSourceBundle
        |
        v
isolated runner
 build -> tests -> start -> health
        |
   failure evidence
        |
        v
same coding-agent implementation in repair mode
        |
        v
new immutable source bundle
```

The runner repair loop currently spans source versions rather than exposing a shell
call directly inside the model session. This preserves the isolation boundary while
we validate the harness. A later runner-tool bridge may make test/shell feedback
interactive without ever executing generated commands inside the FastAPI process.

## Visual + language editing

A language edit and a visual edit are the same underlying operation. The difference
is observation context:

```text
language instruction -> inspect source -> edit -> snapshot

selected preview element
        + language instruction
        -> inspect_visual
        -> grep/read matching source
        -> edit
        -> snapshot
        -> runner rebuild
        -> refreshed preview
```

The rendered DOM is evidence, not source authority. The approved capability graph,
project files, deterministic source policy, tenant boundary, and runner policy remain
authoritative.
