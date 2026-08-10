# OPERLY capability coding agent

This branch intentionally rewrites the coding agent around a persistent generic
tool loop. OpenCode is used as architectural inspiration, not as a runtime or a
domain template.

## Adopted mechanics

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

## Intentionally not adopted

- **No shell inside the OPERLY control plane.** Generated source is untrusted.
  Build, tests, process startup, health checks, and future interactive shell actions
  belong to the isolated runner.
- **No arbitrary plugin execution in the control plane.** Future extensibility
  should register typed tools whose execution authority is independently scoped.
- **No unrestricted external-directory access.** The editable workspace is the
  project source snapshot represented by `GeneratedSourceBundle`.
- **No domain-specific builders.** Inventory, booking, research, websites, bots,
  unknown future products, and edits to existing software are all data presented to
  the same agent/tool loop.

## Current lifecycle

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
we validate the new agent. A later runner-tool bridge may make test/shell feedback
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

The rendered DOM is evidence, not a source of authority. The approved capability
specification, project files, deterministic source policy, tenant boundary, and
runner policy remain authoritative.
