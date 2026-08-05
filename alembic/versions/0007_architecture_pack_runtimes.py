"""quotation and inventory architecture pack runtimes

Revision ID: 0007_architecture_pack_runtimes
Revises: 0006_architecture_first_plans
"""
from alembic import op
from packages.database.db import Base
from packages.database import architecture_pack_models  # noqa: F401

revision="0007_architecture_pack_runtimes"
down_revision="0006_architecture_first_plans"
branch_labels=None
depends_on=None

TABLES=["quote_customers","quote_inquiries","quotations","quotation_versions","quotation_line_items","quotation_approvals","quotation_status_events","inventory_products","inventory_suppliers","inventory_locations","inventory_stock_levels","inventory_stock_movements","inventory_purchase_orders","inventory_purchase_order_lines","inventory_reorder_rules"]

def upgrade():
    bind=op.get_bind()
    for name in TABLES:Base.metadata.tables[name].create(bind,checkfirst=True)

def downgrade():
    raise RuntimeError("Architecture-pack business data downgrade is unsafe; restore a verified backup")
