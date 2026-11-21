"""Add opt-in federated identity mapping (no auth cutover).

Revision ID: 0060_federated_identity_map
Revises: 0059_admin_sse_events
"""
from alembic import op
import sqlalchemy as sa

revision = "0060_federated_identity_map"
down_revision = "0059_admin_sse_events"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("federated_identity_maps"):
        columns = {c["name"] for c in inspector.get_columns("federated_identity_maps")}
        required = {"issuer", "subject", "operly_user_id", "status", "created_at", "activated_at"}
        if not required.issubset(columns):
            raise RuntimeError("Existing federation table has incompatible columns")
        primary = set(inspector.get_pk_constraint("federated_identity_maps").get("constrained_columns") or [])
        if primary != {"issuer", "subject"}:
            raise RuntimeError("Existing federation primary key is incompatible")
        # Compatible table may have been created by Base.metadata.create_all
        # during an older SQLite/dev bootstrap. Do not drop or duplicate it.
        return
    op.create_table(
        "federated_identity_maps",
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("operly_user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("issuer", "subject"),
        sa.ForeignKeyConstraint(["operly_user_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("operly_user_id", name="uq_federated_identity_operly_user"),
        sa.CheckConstraint("status IN ('pending', 'active', 'disabled')", name="ck_federated_identity_status"),
    )
    op.create_index("ix_federated_identity_operly_user_id", "federated_identity_maps", ["operly_user_id"], unique=False)


def downgrade():
    op.drop_index("ix_federated_identity_operly_user_id", table_name="federated_identity_maps")
    op.drop_table("federated_identity_maps")
