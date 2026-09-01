from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class PluginKVRecord(Base):
    __tablename__ = "plugin_kv_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False, index=True)
    namespace_id: Mapped[str] = mapped_column(ForeignKey("plugin_storage_namespaces.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(500), nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("namespace_id", "key", name="uq_plugin_kv_namespace_key"),)


class PluginBlobReferenceRecord(Base):
    __tablename__ = "plugin_blob_references"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False, index=True)
    namespace_id: Mapped[str] = mapped_column(ForeignKey("plugin_storage_namespaces.id", ondelete="CASCADE"), nullable=False, index=True)
    logical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("operly_artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("namespace_id", "logical_name", name="uq_plugin_blob_namespace_name"),)
