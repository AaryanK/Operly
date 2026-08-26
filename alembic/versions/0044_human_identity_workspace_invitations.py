"""add workspace invitations for identity-first onboarding

Revision ID: 0044_human_identity_workspace_invitations
Revises: 0043_canonical_software_source_versions
"""

from alembic import op
import sqlalchemy as sa

revision = "0044_human_identity_workspace_invitations"
down_revision = "0043_canonical_software_source_versions"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("workspace_invitations"):
        op.create_table(
            "workspace_invitations",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("role", sa.String(length=30), nullable=False),
            sa.Column("target_email", sa.String(length=320), nullable=True),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("invited_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("accepted_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("source", sa.String(length=60), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["accepted_by_user_id"], ["app_users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["invited_by_user_id"], ["app_users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash"),
            sa.UniqueConstraint("tenant_id", "token_hash", name="uq_workspace_invitation_tenant_token"),
        )
        op.create_index("ix_workspace_invitations_tenant_id", "workspace_invitations", ["tenant_id"])
        op.create_index("ix_workspace_invitations_target_email", "workspace_invitations", ["target_email"])
        op.create_index("ix_workspace_invitations_token_hash", "workspace_invitations", ["token_hash"], unique=True)
        op.create_index("ix_workspace_invitations_status", "workspace_invitations", ["status"])
        op.create_index("ix_workspace_invitations_invited_by_user_id", "workspace_invitations", ["invited_by_user_id"])
        op.create_index("ix_workspace_invitations_accepted_by_user_id", "workspace_invitations", ["accepted_by_user_id"])
        op.create_index("ix_workspace_invitations_expires_at", "workspace_invitations", ["expires_at"])
        op.create_index(
            "ix_workspace_invitation_tenant_email_status",
            "workspace_invitations",
            ["tenant_id", "target_email", "status"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("workspace_invitations"):
        op.drop_table("workspace_invitations")
