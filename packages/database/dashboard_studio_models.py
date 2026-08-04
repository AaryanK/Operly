from datetime import datetime
from uuid import uuid4
from sqlalchemy import Boolean,DateTime,ForeignKey,Integer,String,Text,UniqueConstraint,Index
from sqlalchemy.orm import Mapped,mapped_column
from packages.database.db import Base
def uid():return str(uuid4())
class DashboardCustomization(Base):
    __tablename__="dashboard_customizations"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);screen_id:Mapped[str]=mapped_column(String(100),index=True);component_id:Mapped[str]=mapped_column(String(120),index=True);override_json:Mapped[str]=mapped_column(Text,default="{}");updated_by:Mapped[str]=mapped_column(ForeignKey("app_users.id"));updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)
    __table_args__=(UniqueConstraint("tenant_id","screen_id","component_id",name="uq_dashboard_customization"),)
class DashboardChangeSet(Base):
    __tablename__="dashboard_change_sets"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);screen_id:Mapped[str]=mapped_column(String(100),index=True);originating_chat_message:Mapped[str]=mapped_column(Text);target_component_ids_json:Mapped[str]=mapped_column(Text);before_json:Mapped[str]=mapped_column(Text);after_json:Mapped[str]=mapped_column(Text);explanation:Mapped[str]=mapped_column(Text);validation_json:Mapped[str]=mapped_column(Text,default="{}");status:Mapped[str]=mapped_column(String(30),default="proposed",index=True);created_by:Mapped[str]=mapped_column(ForeignKey("app_users.id"));applied_version_id:Mapped[str|None]=mapped_column(ForeignKey("app_configuration_versions.id",name="fk_dashboard_change_set_applied_version",use_alter=True),nullable=True);rollback_json:Mapped[str]=mapped_column(Text,default="{}");created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow);updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)
class DashboardChangeOperation(Base):
    __tablename__="dashboard_change_operations"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);change_set_id:Mapped[str]=mapped_column(ForeignKey("dashboard_change_sets.id",ondelete="CASCADE"),index=True);position:Mapped[int]=mapped_column(Integer);operation:Mapped[str]=mapped_column(String(50));component_id:Mapped[str]=mapped_column(String(120));changes_json:Mapped[str]=mapped_column(Text)
    __table_args__=(UniqueConstraint("change_set_id","position",name="uq_dashboard_change_operation_position"),)
class AppConfigurationVersion(Base):
    __tablename__="app_configuration_versions"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);version_number:Mapped[int]=mapped_column(Integer);snapshot_json:Mapped[str]=mapped_column(Text);summary:Mapped[str]=mapped_column(String(500));affected_json:Mapped[str]=mapped_column(Text);originating_change_set_id:Mapped[str|None]=mapped_column(ForeignKey("dashboard_change_sets.id"),nullable=True);source_version_id:Mapped[str|None]=mapped_column(ForeignKey("app_configuration_versions.id"),nullable=True);created_by:Mapped[str]=mapped_column(ForeignKey("app_users.id"));active:Mapped[bool]=mapped_column(Boolean,default=True,index=True);created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    __table_args__=(UniqueConstraint("tenant_id","version_number",name="uq_app_config_version"),)
class DashboardStudioAudit(Base):
    __tablename__="dashboard_studio_audits"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);actor_id:Mapped[str]=mapped_column(ForeignKey("app_users.id"));action:Mapped[str]=mapped_column(String(80));entity_id:Mapped[str|None]=mapped_column(String(36),nullable=True);details_json:Mapped[str]=mapped_column(Text,default="{}");created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
