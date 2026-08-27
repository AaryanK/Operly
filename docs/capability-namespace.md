# Capability namespace contract

Operly's `CapabilityRegistry` remains the canonical execution, permission, approval,
audit, and verification catalog. It is **not** the model's menu.

The model navigates a scope-aware namespace tree:

```text
Personal AI
user
├── settings
├── connections
│   └── google
│       ├── gmail
│       │   ├── messages
│       │   ├── drafts
│       │   └── attachments
│       └── calendar
│           ├── events
│           ├── availability
│           └── calendars
└── workspaces
    ├── manage
    └── delegate

Workspace surface
workspace
├── crm
├── operations
├── activity
├── presence
├── solutions
│   └── studio
│       ├── projects
│       ├── build
│       └── bindings
├── connections
│   └── google
│       ├── gmail
│       │   ├── messages
│       │   ├── drafts
│       │   └── attachments
│       └── calendar
│           ├── events
│           ├── availability
│           └── calendars
├── plugins
├── members_roles
├── ai
├── mcp
└── settings
```

Chats are deliberately absent from the capability tree. Conversation history belongs
to the application context layer rather than becoming another tool family.

The trusted interaction surface chooses the root. A workspace surface cannot search
or expand `user.*`; a personal surface cannot search or expand `workspace.*` directly.
Personal-to-workspace delegation remains a governed operation and does not convert a
private conversation into workspace scope.

Model navigation is progressive:

1. `runtime.context` reports trusted scope/time.
2. `capability.search` searches namespace nodes only.
3. `capability.expand` reveals immediate child nodes and any governed operation IDs
   mounted directly at the selected node.
4. `capability.describe` accepts a namespace plus operation IDs and rejects IDs that
   are not mounted at that namespace.
5. Only successfully described operation schemas become executable in the model
   session.

This keeps low-level worker primitives and unrelated registered capabilities out of a
business-agent prompt while preserving fine-grained internal capability IDs for the
firewall, permissions, approvals, verification, audit, and workflow events.

New product areas are mounted deliberately, one branch at a time. Registering a
provider must never automatically make every provider operation model-visible.
