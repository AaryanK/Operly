"""add modular workspace business operating system

Revision ID: 0045_workspace_business_os
Revises: 0044_human_identity_workspace_invitations
"""

from alembic import op
import sqlalchemy as sa

revision = "0045_workspace_business_os"
down_revision = "0044_human_identity_workspace_invitations"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("workspace_modules"):
        op.create_table(
            "workspace_modules",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("module_key", sa.String(length=60), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("state", sa.String(length=30), nullable=False),
            sa.Column("configuration_json", sa.Text(), nullable=False),
            sa.Column("activated_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("activated_at", sa.DateTime(), nullable=True),
            sa.Column("disabled_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["activated_by_user_id"], ["app_users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "module_key", name="uq_workspace_module_tenant_key"),
        )
        op.create_index("ix_workspace_modules_tenant_id", "workspace_modules", ["tenant_id"])
        op.create_index("ix_workspace_modules_module_key", "workspace_modules", ["module_key"])
        op.create_index("ix_workspace_modules_enabled", "workspace_modules", ["enabled"])
        op.create_index("ix_workspace_modules_activated_by_user_id", "workspace_modules", ["activated_by_user_id"])

    if not inspector.has_table("suppliers"):
        op.create_table(
            "suppliers",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=220), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=True),
            sa.Column("phone", sa.String(length=80), nullable=True),
            sa.Column("website", sa.String(length=1000), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("lead_time_days", sa.Integer(), nullable=False),
            sa.Column("minimum_order_value", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])
        op.create_index("ix_suppliers_status", "suppliers", ["status"])

    if not inspector.has_table("purchase_orders"):
        op.create_table(
            "purchase_orders",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("supplier_id", sa.String(length=36), nullable=True),
            sa.Column("reference", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("subtotal", sa.Float(), nullable=False),
            sa.Column("shipping_cost", sa.Float(), nullable=False),
            sa.Column("total", sa.Float(), nullable=False),
            sa.Column("expected_at", sa.DateTime(), nullable=True),
            sa.Column("received_at", sa.DateTime(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "reference", name="uq_purchase_order_tenant_reference"),
        )
        op.create_index("ix_purchase_orders_tenant_id", "purchase_orders", ["tenant_id"])
        op.create_index("ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"])
        op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])
        op.create_index("ix_purchase_orders_expected_at", "purchase_orders", ["expected_at"])

    if not inspector.has_table("invoices"):
        op.create_table(
            "invoices",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("contact_id", sa.String(length=36), nullable=True),
            sa.Column("order_id", sa.String(length=36), nullable=True),
            sa.Column("number", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("subtotal", sa.Float(), nullable=False),
            sa.Column("tax", sa.Float(), nullable=False),
            sa.Column("total", sa.Float(), nullable=False),
            sa.Column("due_at", sa.DateTime(), nullable=True),
            sa.Column("paid_at", sa.DateTime(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["order_id"], ["business_orders.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "number", name="uq_invoice_tenant_number"),
        )
        op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])
        op.create_index("ix_invoices_contact_id", "invoices", ["contact_id"])
        op.create_index("ix_invoices_order_id", "invoices", ["order_id"])
        op.create_index("ix_invoices_status", "invoices", ["status"])
        op.create_index("ix_invoices_due_at", "invoices", ["due_at"])

    if not inspector.has_table("expenses"):
        op.create_table(
            "expenses",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("vendor", sa.String(length=220), nullable=False),
            sa.Column("category", sa.String(length=100), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("incurred_at", sa.DateTime(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_expenses_tenant_id", "expenses", ["tenant_id"])
        op.create_index("ix_expenses_category", "expenses", ["category"])
        op.create_index("ix_expenses_status", "expenses", ["status"])
        op.create_index("ix_expenses_incurred_at", "expenses", ["incurred_at"])

    if not inspector.has_table("fulfillments"):
        op.create_table(
            "fulfillments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("order_id", sa.String(length=36), nullable=False),
            sa.Column("supplier_id", sa.String(length=36), nullable=True),
            sa.Column("method", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("carrier", sa.String(length=120), nullable=True),
            sa.Column("tracking_number", sa.String(length=200), nullable=True),
            sa.Column("fulfillment_cost", sa.Float(), nullable=False),
            sa.Column("shipped_at", sa.DateTime(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["order_id"], ["business_orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_fulfillments_tenant_id", "fulfillments", ["tenant_id"])
        op.create_index("ix_fulfillments_order_id", "fulfillments", ["order_id"])
        op.create_index("ix_fulfillments_supplier_id", "fulfillments", ["supplier_id"])
        op.create_index("ix_fulfillments_status", "fulfillments", ["status"])
        op.create_index("ix_fulfillments_tracking_number", "fulfillments", ["tracking_number"])

    if not inspector.has_table("returns"):
        op.create_table(
            "returns",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("order_id", sa.String(length=36), nullable=False),
            sa.Column("fulfillment_id", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("reason", sa.String(length=220), nullable=False),
            sa.Column("refund_amount", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("received_back", sa.Boolean(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["fulfillment_id"], ["fulfillments.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["order_id"], ["business_orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_returns_tenant_id", "returns", ["tenant_id"])
        op.create_index("ix_returns_order_id", "returns", ["order_id"])
        op.create_index("ix_returns_fulfillment_id", "returns", ["fulfillment_id"])
        op.create_index("ix_returns_status", "returns", ["status"])

    if not inspector.has_table("support_tickets"):
        op.create_table(
            "support_tickets",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("contact_id", sa.String(length=36), nullable=True),
            sa.Column("subject", sa.String(length=300), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("priority", sa.String(length=20), nullable=False),
            sa.Column("channel", sa.String(length=40), nullable=False),
            sa.Column("assigned_to", sa.String(length=200), nullable=True),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("resolution", sa.Text(), nullable=False),
            sa.Column("opened_at", sa.DateTime(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_support_tickets_tenant_id", "support_tickets", ["tenant_id"])
        op.create_index("ix_support_tickets_contact_id", "support_tickets", ["contact_id"])
        op.create_index("ix_support_tickets_status", "support_tickets", ["status"])
        op.create_index("ix_support_tickets_priority", "support_tickets", ["priority"])
        op.create_index("ix_support_tickets_opened_at", "support_tickets", ["opened_at"])

    if not inspector.has_table("marketing_campaigns"):
        op.create_table(
            "marketing_campaigns",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=240), nullable=False),
            sa.Column("channel", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("budget", sa.Float(), nullable=False),
            sa.Column("spent", sa.Float(), nullable=False),
            sa.Column("attributed_revenue", sa.Float(), nullable=False),
            sa.Column("starts_at", sa.DateTime(), nullable=True),
            sa.Column("ends_at", sa.DateTime(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_marketing_campaigns_tenant_id", "marketing_campaigns", ["tenant_id"])
        op.create_index("ix_marketing_campaigns_channel", "marketing_campaigns", ["channel"])
        op.create_index("ix_marketing_campaigns_status", "marketing_campaigns", ["status"])
        op.create_index("ix_marketing_campaigns_starts_at", "marketing_campaigns", ["starts_at"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    for table in (
        "marketing_campaigns",
        "support_tickets",
        "returns",
        "fulfillments",
        "expenses",
        "invoices",
        "purchase_orders",
        "suppliers",
        "workspace_modules",
    ):
        if inspector.has_table(table):
            op.drop_table(table)
