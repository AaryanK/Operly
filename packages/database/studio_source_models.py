from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class StudioSourceVersion(Base):
    """Immutable source snapshots authored by the Studio coding agent.

    SiteSchema versions remain available for legacy playback. Once a project has
    one of these records, Studio treats source as the primary editable runtime.
    """

    __tablename__ = "studio_source_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    source_version: Mapped[int] = mapped_column(Integer)
    files_json: Mapped[str] = mapped_column(Text)
    provenance_json: Mapped[str] = mapped_column(Text, default="{}")
    change_summary: Mapped[str] = mapped_column(String(500), default="")
    parent_source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "source_version", name="uq_studio_source_version"),
    )
