from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class BusinessProfile(Base):
    __tablename__ = "business_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    legal_name: Mapped[str] = mapped_column(String(250), default="")
    trading_name: Mapped[str] = mapped_column(String(250), default="")
    industry: Mapped[str] = mapped_column(String(150), default="general")
    description: Mapped[str] = mapped_column(Text, default="")
    country: Mapped[str] = mapped_column(String(100), default="")
    currency: Mapped[str] = mapped_column(String(20), default="USD")
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    operating_hours_json: Mapped[str] = mapped_column(Text, default="{}")
    communication_tone: Mapped[str] = mapped_column(String(150), default="professional")
    goals_json: Mapped[str] = mapped_column(Text, default="[]")
    pain_points_json: Mapped[str] = mapped_column(Text, default="[]")
    approval_rules_json: Mapped[str] = mapped_column(Text, default="[]")
    induction_status: Mapped[str] = mapped_column(String(40), default="not_started")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class BusinessSource(Base):
    __tablename__ = "business_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(80), default="text")
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BusinessAuditRun(Base):
    __tablename__ = "business_audit_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(40), default="completed")
    score: Mapped[int] = mapped_column(Integer, default=100)
    executive_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BusinessAuditFinding(Base):
    __tablename__ = "business_audit_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    audit_run_id: Mapped[str] = mapped_column(
        ForeignKey("business_audit_runs.id"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    title: Mapped[str] = mapped_column(String(300))
    evidence: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    expected_impact: Mapped[str] = mapped_column(Text, default="")
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)


class OperatingPlan(Base):
    __tablename__ = "operating_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(300))
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OperatingPlanNode(Base):
    __tablename__ = "operating_plan_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("operating_plans.id"),
        nullable=False,
        index=True,
    )
    node_key: Mapped[str] = mapped_column(String(80))
    node_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(250))
    description: Mapped[str] = mapped_column(Text, default="")
    position_x: Mapped[float] = mapped_column(Float, default=10)
    position_y: Mapped[float] = mapped_column(Float, default=10)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")

    __table_args__ = (
        UniqueConstraint("plan_id", "node_key", name="uq_plan_node_key"),
    )


class OperatingPlanEdge(Base):
    __tablename__ = "operating_plan_edges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("operating_plans.id"),
        nullable=False,
        index=True,
    )
    source_node_id: Mapped[str] = mapped_column(
        ForeignKey("operating_plan_nodes.id"),
        nullable=False,
    )
    target_node_id: Mapped[str] = mapped_column(
        ForeignKey("operating_plan_nodes.id"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(150), default="")


class OperationalAlert(Base):
    __tablename__ = "operational_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    priority_score: Mapped[int] = mapped_column(Integer, default=50)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "fingerprint",
            name="uq_operational_alert_fingerprint",
        ),
    )


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("operating_plans.id"),
        nullable=True,
        index=True,
    )
    trigger_name: Mapped[str] = mapped_column(String(200), default="manual")
    status: Mapped[str] = mapped_column(String(40), default="completed")
    summary: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
