"""promote legacy human memory to global personal scope

Revision ID: 0027_global_human_memory
Revises: 0026_action_provenance
"""

from alembic import op
import sqlalchemy as sa

revision = "0027_global_human_memory"
down_revision = "0026_action_provenance"
branch_labels = None
depends_on = None


def upgrade():
    # Before workspace-private human memory existed, every `human` record was
    # conceptually person-level but was incidentally stamped with the active
    # tenant. Promote those legacy records to the intended global-private scope.
    op.execute(
        sa.text(
            "UPDATE context_records SET tenant_id = NULL "
            "WHERE scope_type = 'human' AND visibility = 'private'"
        )
    )


def downgrade():
    # The former incidental tenant association cannot be reconstructed safely.
    # Keeping these records global-private is safer than assigning a guessed tenant.
    pass
