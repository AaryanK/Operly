"""Public relational-data contracts with a dependency-light import boundary.

The isolated runner imports migration/token contracts without installing the full
Operly SQLAlchemy application stack. Store classes remain available lazily for
control-plane callers that explicitly request them.
"""
from packages.relational_data.contracts import (
    RELATIONAL_CAPABILITY_ID,
    RELATIONAL_MIGRATION_SCHEMA,
    DeleteRequest,
    FilterClause,
    InsertRequest,
    QueryRequest,
    RelationalMigration,
    UpdateRequest,
)
from packages.relational_data.tokens import (
    BindingGrantClaims,
    BindingGrantError,
    issue_binding_grant,
    verify_binding_grant,
)


def __getattr__(name: str):
    if name in {"RelationalDataError", "RelationalDataStore"}:
        from packages.relational_data.store import RelationalDataError, RelationalDataStore

        return {
            "RelationalDataError": RelationalDataError,
            "RelationalDataStore": RelationalDataStore,
        }[name]
    raise AttributeError(name)


__all__ = [
    "RELATIONAL_CAPABILITY_ID",
    "RELATIONAL_MIGRATION_SCHEMA",
    "RelationalMigration",
    "QueryRequest",
    "InsertRequest",
    "UpdateRequest",
    "DeleteRequest",
    "FilterClause",
    "RelationalDataError",
    "RelationalDataStore",
    "BindingGrantClaims",
    "BindingGrantError",
    "issue_binding_grant",
    "verify_binding_grant",
]
