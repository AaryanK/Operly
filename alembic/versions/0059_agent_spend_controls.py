"""persistent agent spend controls

Revision ID: 0059_agent_spend_controls
Revises: 0058_agent_chat_history
"""

from alembic import op
import sqlalchemy as sa

revision = "0059_agent_spend_controls"
down_revision = "0058_agent_chat_history"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_spend_budgets"):
        op.create_table(
            "agent_spend_budgets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("scope_kind", sa.String(20), nullable=False),
            sa.Column("scope_id", sa.String(200), nullable=False),
            sa.Column("budget_kind", sa.String(40), nullable=False),
            sa.Column("budget_key", sa.String(240), nullable=False),
            sa.Column("limit_micros", sa.BigInteger(), nullable=False),
            sa.Column("spent_micros", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("reserved_micros", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("max_calls", sa.Integer(), nullable=True),
            sa.Column("calls_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("window_start", sa.DateTime(), nullable=True),
            sa.Column("window_end", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "scope_kind", "scope_id", "budget_kind", "budget_key",
                name="uq_agent_spend_budget_scope_key",
            ),
            sa.CheckConstraint("limit_micros >= 0", name="ck_agent_spend_budget_limit_nonnegative"),
            sa.CheckConstraint("spent_micros >= 0", name="ck_agent_spend_budget_spent_nonnegative"),
            sa.CheckConstraint("reserved_micros >= 0", name="ck_agent_spend_budget_reserved_nonnegative"),
            sa.CheckConstraint("calls_used >= 0", name="ck_agent_spend_budget_calls_nonnegative"),
        )
        for column in ("scope_kind", "scope_id", "budget_kind", "budget_key", "window_start", "window_end"):
            op.create_index(f"ix_agent_spend_budgets_{column}", "agent_spend_budgets", [column])
        op.create_index(
            "ix_agent_spend_budget_scope_kind_key",
            "agent_spend_budgets",
            ["scope_kind", "scope_id", "budget_kind", "budget_key"],
        )

    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_spend_reservations"):
        op.create_table(
            "agent_spend_reservations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("reservation_key", sa.String(200), nullable=False),
            sa.Column("run_id", sa.String(120), nullable=False),
            sa.Column("task_id", sa.String(160), nullable=False),
            sa.Column("scope_kind", sa.String(20), nullable=False),
            sa.Column("scope_id", sa.String(200), nullable=False),
            sa.Column("project_id", sa.String(200), nullable=False),
            sa.Column("phase", sa.String(40), nullable=False),
            sa.Column("provider", sa.String(80), nullable=False),
            sa.Column("model_id", sa.String(255), nullable=False),
            sa.Column("price_snapshot_version", sa.String(80), nullable=False),
            sa.Column("input_micros_per_million_tokens", sa.BigInteger(), nullable=False),
            sa.Column("output_micros_per_million_tokens", sa.BigInteger(), nullable=False),
            sa.Column("input_token_cap", sa.Integer(), nullable=False),
            sa.Column("output_token_cap", sa.Integer(), nullable=False),
            sa.Column("reserved_micros", sa.BigInteger(), nullable=False),
            sa.Column("actual_micros", sa.BigInteger(), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="reserved"),
            sa.Column("budget_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("uncertainty_reason", sa.String(120), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("settled_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("reservation_key", name="uq_agent_spend_reservation_key"),
            sa.CheckConstraint("reserved_micros >= 0", name="ck_agent_spend_reservation_reserved_nonnegative"),
            sa.CheckConstraint(
                "actual_micros IS NULL OR actual_micros >= 0",
                name="ck_agent_spend_reservation_actual_nonnegative",
            ),
        )
        for column in (
            "reservation_key", "run_id", "task_id", "scope_kind", "scope_id", "project_id",
            "phase", "provider", "model_id", "price_snapshot_version", "status", "created_at",
        ):
            op.create_index(f"ix_agent_spend_reservations_{column}", "agent_spend_reservations", [column])
        op.create_index(
            "ix_agent_spend_reservation_run_created",
            "agent_spend_reservations",
            ["run_id", "created_at"],
        )
        op.create_index(
            "ix_agent_spend_reservation_scope_created",
            "agent_spend_reservations",
            ["scope_kind", "scope_id", "created_at"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_spend_reservations"):
        op.drop_table("agent_spend_reservations")
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_spend_budgets"):
        op.drop_table("agent_spend_budgets")
