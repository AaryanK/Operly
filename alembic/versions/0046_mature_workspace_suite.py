"""expand workspace business OS into mature CRM/ERP and research suite

Revision ID: 0046_mature_workspace_suite
Revises: 0045_workspace_business_os
"""

from alembic import op
import sqlalchemy as sa

revision = "0046_mature_workspace_suite"
down_revision = "0045_workspace_business_os"
branch_labels = None
depends_on = None


def _create(inspector, name, columns, *, fks=(), uniques=(), indexes=()):
    if inspector.has_table(name):
        return
    constraints = [
        sa.ForeignKeyConstraint([local], [remote], ondelete=ondelete)
        for local, remote, ondelete in fks
    ]
    constraints += [sa.UniqueConstraint(*fields, name=constraint_name) for constraint_name, fields in uniques]
    op.create_table(name, *columns, *constraints)
    for fields in indexes:
        suffix = "_".join(fields)
        op.create_index(f"ix_{name}_{suffix}", name, list(fields))


def _id():
    return sa.Column("id", sa.String(length=36), nullable=False, primary_key=True)


def _tenant():
    return sa.Column("tenant_id", sa.String(length=36), nullable=False)


def _created():
    return sa.Column("created_at", sa.DateTime(), nullable=False)


def _updated():
    return sa.Column("updated_at", sa.DateTime(), nullable=False)


def upgrade():
    inspector = sa.inspect(op.get_bind())

    _create(inspector, "business_accounts", [
        _id(), _tenant(), sa.Column("name", sa.String(240), nullable=False),
        sa.Column("account_type", sa.String(60), nullable=False), sa.Column("industry", sa.String(120)),
        sa.Column("website", sa.String(1000)), sa.Column("email", sa.String(320)), sa.Column("phone", sa.String(80)),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("owner", sa.String(200)),
        sa.Column("billing_address", sa.Text(), nullable=False), sa.Column("shipping_address", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False), _created(), _updated(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE")], indexes=[("tenant_id",), ("account_type",), ("status",)])

    _create(inspector, "crm_interactions", [
        _id(), _tenant(), sa.Column("contact_id", sa.String(36)), sa.Column("account_id", sa.String(36)), sa.Column("lead_id", sa.String(36)),
        sa.Column("interaction_type", sa.String(60), nullable=False), sa.Column("channel", sa.String(60), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("owner", sa.String(200)),
        sa.Column("occurred_at", sa.DateTime(), nullable=False), sa.Column("next_action_at", sa.DateTime()), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("contact_id", "contacts.id", "SET NULL"), ("account_id", "business_accounts.id", "SET NULL"), ("lead_id", "leads.id", "SET NULL")], indexes=[("tenant_id",), ("contact_id",), ("account_id",), ("lead_id",), ("interaction_type",), ("occurred_at",), ("next_action_at",)])

    _create(inspector, "quote_items", [
        _id(), _tenant(), sa.Column("quote_id", sa.String(36), nullable=False), sa.Column("catalog_item_id", sa.String(36)),
        sa.Column("description", sa.String(300), nullable=False), sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False), sa.Column("discount", sa.Float(), nullable=False), sa.Column("tax_rate", sa.Float(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("quote_id", "quotes.id", "CASCADE"), ("catalog_item_id", "catalog_items.id", "SET NULL")], indexes=[("tenant_id",), ("quote_id",), ("catalog_item_id",)])

    _create(inspector, "purchase_order_items", [
        _id(), _tenant(), sa.Column("purchase_order_id", sa.String(36), nullable=False), sa.Column("catalog_item_id", sa.String(36)),
        sa.Column("description", sa.String(300), nullable=False), sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit_cost", sa.Float(), nullable=False), sa.Column("tax_rate", sa.Float(), nullable=False),
        sa.Column("received_quantity", sa.Float(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("purchase_order_id", "purchase_orders.id", "CASCADE"), ("catalog_item_id", "catalog_items.id", "SET NULL")], indexes=[("tenant_id",), ("purchase_order_id",), ("catalog_item_id",)])

    _create(inspector, "invoice_items", [
        _id(), _tenant(), sa.Column("invoice_id", sa.String(36), nullable=False), sa.Column("catalog_item_id", sa.String(36)),
        sa.Column("description", sa.String(300), nullable=False), sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False), sa.Column("discount", sa.Float(), nullable=False), sa.Column("tax_rate", sa.Float(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("invoice_id", "invoices.id", "CASCADE"), ("catalog_item_id", "catalog_items.id", "SET NULL")], indexes=[("tenant_id",), ("invoice_id",), ("catalog_item_id",)])

    _create(inspector, "sales_contracts", [
        _id(), _tenant(), sa.Column("contact_id", sa.String(36)), sa.Column("account_id", sa.String(36)),
        sa.Column("title", sa.String(300), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("value", sa.Float(), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("starts_at", sa.DateTime()), sa.Column("ends_at", sa.DateTime()), sa.Column("renewal_at", sa.DateTime()),
        sa.Column("terms", sa.Text(), nullable=False), _created(), _updated(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("contact_id", "contacts.id", "SET NULL"), ("account_id", "business_accounts.id", "SET NULL")], indexes=[("tenant_id",), ("contact_id",), ("account_id",), ("status",), ("renewal_at",)])

    _create(inspector, "subscriptions", [
        _id(), _tenant(), sa.Column("contact_id", sa.String(36)), sa.Column("account_id", sa.String(36)), sa.Column("catalog_item_id", sa.String(36)),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("billing_interval", sa.String(40), nullable=False), sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("next_billing_at", sa.DateTime()), sa.Column("cancelled_at", sa.DateTime()), sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("contact_id", "contacts.id", "SET NULL"), ("account_id", "business_accounts.id", "SET NULL"), ("catalog_item_id", "catalog_items.id", "SET NULL")], indexes=[("tenant_id",), ("contact_id",), ("account_id",), ("catalog_item_id",), ("status",), ("next_billing_at",)])

    _create(inspector, "business_payments", [
        _id(), _tenant(), sa.Column("contact_id", sa.String(36)), sa.Column("invoice_id", sa.String(36)), sa.Column("order_id", sa.String(36)),
        sa.Column("direction", sa.String(20), nullable=False), sa.Column("method", sa.String(60), nullable=False), sa.Column("provider", sa.String(80)),
        sa.Column("reference", sa.String(200)), sa.Column("status", sa.String(40), nullable=False), sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False), sa.Column("paid_at", sa.DateTime(), nullable=False), sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("contact_id", "contacts.id", "SET NULL"), ("invoice_id", "invoices.id", "SET NULL"), ("order_id", "business_orders.id", "SET NULL")], indexes=[("tenant_id",), ("contact_id",), ("invoice_id",), ("order_id",), ("direction",), ("reference",), ("status",), ("paid_at",)])

    _create(inspector, "financial_accounts", [
        _id(), _tenant(), sa.Column("code", sa.String(40)), sa.Column("name", sa.String(220), nullable=False),
        sa.Column("account_type", sa.String(60), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("opening_balance", sa.Float(), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE")], uniques=[("uq_financial_account_tenant_code", ("tenant_id", "code"))], indexes=[("tenant_id",), ("account_type",)])

    _create(inspector, "ledger_entries", [
        _id(), _tenant(), sa.Column("financial_account_id", sa.String(36)), sa.Column("counterparty", sa.String(240)),
        sa.Column("category", sa.String(100), nullable=False), sa.Column("debit", sa.Float(), nullable=False), sa.Column("credit", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False), sa.Column("occurred_at", sa.DateTime(), nullable=False), sa.Column("source_type", sa.String(60)),
        sa.Column("source_id", sa.String(80)), sa.Column("memo", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("financial_account_id", "financial_accounts.id", "SET NULL")], indexes=[("tenant_id",), ("financial_account_id",), ("category",), ("occurred_at",)])

    _create(inspector, "budgets", [
        _id(), _tenant(), sa.Column("name", sa.String(220), nullable=False), sa.Column("category", sa.String(100), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False), sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False), sa.Column("spent", sa.Float(), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE")], indexes=[("tenant_id",), ("category",), ("period_start",), ("period_end",), ("status",)])

    _create(inspector, "marketing_content", [
        _id(), _tenant(), sa.Column("campaign_id", sa.String(36)), sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(60), nullable=False), sa.Column("channel", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("publish_at", sa.DateTime()),
        sa.Column("external_url", sa.String(1000)), sa.Column("notes", sa.Text(), nullable=False), _created(), _updated(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("campaign_id", "marketing_campaigns.id", "SET NULL")], indexes=[("tenant_id",), ("campaign_id",), ("content_type",), ("status",), ("publish_at",)])

    _create(inspector, "warehouses", [
        _id(), _tenant(), sa.Column("name", sa.String(220), nullable=False), sa.Column("code", sa.String(60), nullable=False),
        sa.Column("location", sa.Text(), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE")], uniques=[("uq_warehouse_tenant_code", ("tenant_id", "code"))], indexes=[("tenant_id",)])

    _create(inspector, "inventory_transfers", [
        _id(), _tenant(), sa.Column("item_id", sa.String(36), nullable=False), sa.Column("from_warehouse_id", sa.String(36)), sa.Column("to_warehouse_id", sa.String(36)),
        sa.Column("quantity", sa.Float(), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False), sa.Column("completed_at", sa.DateTime()), sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("item_id", "catalog_items.id", "CASCADE"), ("from_warehouse_id", "warehouses.id", "SET NULL"), ("to_warehouse_id", "warehouses.id", "SET NULL")], indexes=[("tenant_id",), ("item_id",), ("from_warehouse_id",), ("to_warehouse_id",), ("status",)])

    _create(inspector, "business_projects", [
        _id(), _tenant(), sa.Column("contact_id", sa.String(36)), sa.Column("code", sa.String(80)), sa.Column("name", sa.String(260), nullable=False),
        sa.Column("project_type", sa.String(80), nullable=False), sa.Column("status", sa.String(40), nullable=False), sa.Column("owner", sa.String(200)),
        sa.Column("starts_at", sa.DateTime()), sa.Column("due_at", sa.DateTime()), sa.Column("budget", sa.Float(), nullable=False), sa.Column("spent", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False), _created(), _updated(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("contact_id", "contacts.id", "SET NULL")], uniques=[("uq_business_project_tenant_code", ("tenant_id", "code"))], indexes=[("tenant_id",), ("contact_id",), ("project_type",), ("status",), ("due_at",)])

    _create(inspector, "project_milestones", [
        _id(), _tenant(), sa.Column("project_id", sa.String(36), nullable=False), sa.Column("title", sa.String(260), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("owner", sa.String(200)), sa.Column("due_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()), sa.Column("description", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("project_id", "business_projects.id", "CASCADE")], indexes=[("tenant_id",), ("project_id",), ("status",), ("due_at",)])

    _create(inspector, "time_entries", [
        _id(), _tenant(), sa.Column("project_id", sa.String(36)), sa.Column("team_member_id", sa.String(36)),
        sa.Column("work_date", sa.DateTime(), nullable=False), sa.Column("hours", sa.Float(), nullable=False), sa.Column("billable", sa.Boolean(), nullable=False),
        sa.Column("hourly_rate", sa.Float(), nullable=False), sa.Column("description", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("project_id", "business_projects.id", "SET NULL"), ("team_member_id", "team_members.id", "SET NULL")], indexes=[("tenant_id",), ("project_id",), ("team_member_id",), ("work_date",)])

    _create(inspector, "leave_requests", [
        _id(), _tenant(), sa.Column("team_member_id", sa.String(36), nullable=False), sa.Column("leave_type", sa.String(60), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("starts_at", sa.DateTime(), nullable=False), sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False), sa.Column("approved_by", sa.String(200)), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("team_member_id", "team_members.id", "CASCADE")], indexes=[("tenant_id",), ("team_member_id",), ("status",), ("starts_at",), ("ends_at",)])

    _create(inspector, "business_assets", [
        _id(), _tenant(), sa.Column("tag", sa.String(100)), sa.Column("name", sa.String(240), nullable=False),
        sa.Column("asset_type", sa.String(80), nullable=False), sa.Column("status", sa.String(40), nullable=False), sa.Column("serial_number", sa.String(160)),
        sa.Column("location", sa.String(240)), sa.Column("owner", sa.String(200)), sa.Column("acquired_at", sa.DateTime()),
        sa.Column("acquisition_cost", sa.Float(), nullable=False), sa.Column("next_maintenance_at", sa.DateTime()), sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE")], uniques=[("uq_business_asset_tenant_tag", ("tenant_id", "tag"))], indexes=[("tenant_id",), ("asset_type",), ("status",), ("next_maintenance_at",)])

    _create(inspector, "maintenance_records", [
        _id(), _tenant(), sa.Column("asset_id", sa.String(36), nullable=False), sa.Column("title", sa.String(260), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("scheduled_at", sa.DateTime()), sa.Column("completed_at", sa.DateTime()),
        sa.Column("cost", sa.Float(), nullable=False), sa.Column("vendor", sa.String(220)), sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("asset_id", "business_assets.id", "CASCADE")], indexes=[("tenant_id",), ("asset_id",), ("status",), ("scheduled_at",)])

    _create(inspector, "work_orders", [
        _id(), _tenant(), sa.Column("project_id", sa.String(36)), sa.Column("contact_id", sa.String(36)), sa.Column("asset_id", sa.String(36)),
        sa.Column("reference", sa.String(120), nullable=False), sa.Column("title", sa.String(260), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False), sa.Column("assigned_to", sa.String(200)), sa.Column("scheduled_start", sa.DateTime()), sa.Column("scheduled_end", sa.DateTime()),
        sa.Column("estimated_cost", sa.Float(), nullable=False), sa.Column("actual_cost", sa.Float(), nullable=False), sa.Column("description", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("project_id", "business_projects.id", "SET NULL"), ("contact_id", "contacts.id", "SET NULL"), ("asset_id", "business_assets.id", "SET NULL")], uniques=[("uq_work_order_tenant_reference", ("tenant_id", "reference"))], indexes=[("tenant_id",), ("project_id",), ("contact_id",), ("asset_id",), ("status",), ("scheduled_start",)])

    _create(inspector, "risk_records", [
        _id(), _tenant(), sa.Column("title", sa.String(300), nullable=False), sa.Column("category", sa.String(100), nullable=False),
        sa.Column("likelihood", sa.String(30), nullable=False), sa.Column("impact", sa.String(30), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("owner", sa.String(200)), sa.Column("mitigation", sa.Text(), nullable=False), sa.Column("due_at", sa.DateTime()), _created(), _updated(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE")], indexes=[("tenant_id",), ("category",), ("status",), ("due_at",)])

    _create(inspector, "incident_records", [
        _id(), _tenant(), sa.Column("title", sa.String(300), nullable=False), sa.Column("incident_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(30), nullable=False), sa.Column("status", sa.String(40), nullable=False), sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("reported_by", sa.String(200)), sa.Column("owner", sa.String(200)), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False), _created(), _updated(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE")], indexes=[("tenant_id",), ("incident_type",), ("severity",), ("status",), ("occurred_at",)])

    _create(inspector, "audit_records", [
        _id(), _tenant(), sa.Column("title", sa.String(300), nullable=False), sa.Column("audit_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("owner", sa.String(200)), sa.Column("scheduled_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()), sa.Column("score", sa.Float(), nullable=False), sa.Column("findings", sa.Text(), nullable=False),
        sa.Column("corrective_actions", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE")], indexes=[("tenant_id",), ("audit_type",), ("status",), ("scheduled_at",)])

    _create(inspector, "research_projects", [
        _id(), _tenant(), sa.Column("code", sa.String(100)), sa.Column("title", sa.String(300), nullable=False), sa.Column("field", sa.String(160)),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("principal_investigator", sa.String(220)), sa.Column("starts_at", sa.DateTime()),
        sa.Column("ends_at", sa.DateTime()), sa.Column("ethics_status", sa.String(60), nullable=False), sa.Column("funding_source", sa.String(240)),
        sa.Column("objective", sa.Text(), nullable=False), _created(), _updated(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE")], uniques=[("uq_research_project_tenant_code", ("tenant_id", "code"))], indexes=[("tenant_id",), ("status",), ("ethics_status",)])

    _create(inspector, "experiments", [
        _id(), _tenant(), sa.Column("research_project_id", sa.String(36), nullable=False), sa.Column("name", sa.String(300), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("owner", sa.String(200)), sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()), sa.Column("hypothesis", sa.Text(), nullable=False), sa.Column("protocol", sa.Text(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("research_project_id", "research_projects.id", "CASCADE")], indexes=[("tenant_id",), ("research_project_id",), ("status",)])

    _create(inspector, "research_samples", [
        _id(), _tenant(), sa.Column("research_project_id", sa.String(36), nullable=False), sa.Column("experiment_id", sa.String(36)),
        sa.Column("sample_code", sa.String(120), nullable=False), sa.Column("sample_type", sa.String(100), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("storage_location", sa.String(240)), sa.Column("collected_at", sa.DateTime()), sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("research_project_id", "research_projects.id", "CASCADE"), ("experiment_id", "experiments.id", "SET NULL")], uniques=[("uq_research_sample_tenant_code", ("tenant_id", "sample_code"))], indexes=[("tenant_id",), ("research_project_id",), ("experiment_id",), ("sample_type",), ("status",)])

    _create(inspector, "research_datasets", [
        _id(), _tenant(), sa.Column("research_project_id", sa.String(36), nullable=False), sa.Column("experiment_id", sa.String(36)),
        sa.Column("name", sa.String(300), nullable=False), sa.Column("version", sa.String(80), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("storage_uri", sa.String(1000)), sa.Column("license", sa.String(120)), sa.Column("checksum", sa.String(160)),
        sa.Column("description", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("research_project_id", "research_projects.id", "CASCADE"), ("experiment_id", "experiments.id", "SET NULL")], uniques=[("uq_research_dataset_version", ("tenant_id", "research_project_id", "name", "version"))], indexes=[("tenant_id",), ("research_project_id",), ("experiment_id",), ("status",)])

    _create(inspector, "grant_records", [
        _id(), _tenant(), sa.Column("project_id", sa.String(36)), sa.Column("research_project_id", sa.String(36)),
        sa.Column("funder", sa.String(260), nullable=False), sa.Column("program", sa.String(260)), sa.Column("reference", sa.String(140)),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("amount", sa.Float(), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("submitted_at", sa.DateTime()), sa.Column("awarded_at", sa.DateTime()), sa.Column("starts_at", sa.DateTime()), sa.Column("ends_at", sa.DateTime()),
        sa.Column("notes", sa.Text(), nullable=False), _created(),
    ], fks=[("tenant_id", "tenants.id", "CASCADE"), ("project_id", "business_projects.id", "SET NULL"), ("research_project_id", "research_projects.id", "SET NULL")], indexes=[("tenant_id",), ("project_id",), ("research_project_id",), ("status",)])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    for table in (
        "grant_records", "research_datasets", "research_samples", "experiments", "research_projects",
        "audit_records", "incident_records", "risk_records", "work_orders", "maintenance_records", "business_assets",
        "leave_requests", "time_entries", "project_milestones", "business_projects", "inventory_transfers", "warehouses",
        "marketing_content", "budgets", "ledger_entries", "financial_accounts", "business_payments", "subscriptions",
        "sales_contracts", "invoice_items", "purchase_order_items", "quote_items", "crm_interactions", "business_accounts",
    ):
        if inspector.has_table(table):
            op.drop_table(table)
