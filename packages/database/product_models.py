from datetime import datetime
from uuid import uuid4
from sqlalchemy import Boolean,DateTime,Float,ForeignKey,String,Text,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from packages.database.db import Base

def uid():return str(uuid4())

class PaymentRecord(Base):
 __tablename__="payment_records"
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
 kind:Mapped[str]=mapped_column(String(40));provider:Mapped[str]=mapped_column(String(60));provider_id:Mapped[str]=mapped_column(String(200));status:Mapped[str]=mapped_column(String(50));amount:Mapped[float]=mapped_column(Float,default=0);currency:Mapped[str]=mapped_column(String(3),default="usd");customer_email:Mapped[str|None]=mapped_column(String(320),nullable=True);description:Mapped[str]=mapped_column(Text,default="");url:Mapped[str|None]=mapped_column(Text,nullable=True);metadata_json:Mapped[str]=mapped_column(Text,default="{}");created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow);updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)
 __table_args__=(UniqueConstraint("tenant_id","provider","provider_id",name="uq_payment_provider_id"),)

class CustomPluginRecord(Base):
 __tablename__="custom_plugins"
 id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);plugin_id:Mapped[str]=mapped_column(String(100));display_name:Mapped[str]=mapped_column(String(200));description:Mapped[str]=mapped_column(Text,default="");base_url:Mapped[str]=mapped_column(Text);allowed_domain:Mapped[str]=mapped_column(String(255));auth_type:Mapped[str]=mapped_column(String(30),default="none");credential_reference:Mapped[str|None]=mapped_column(ForeignKey("connector_secrets.id"),nullable=True);capabilities_json:Mapped[str]=mapped_column(Text,default="[]");status:Mapped[str]=mapped_column(String(40),default="proposed");enabled:Mapped[bool]=mapped_column(Boolean,default=False);test_results_json:Mapped[str]=mapped_column(Text,default="{}");created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow);updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)
 __table_args__=(UniqueConstraint("tenant_id","plugin_id",name="uq_custom_plugin_tenant_id"),)

class CompanyProfile(Base):
 __tablename__="company_profiles"
 tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),primary_key=True);answers_json:Mapped[str]=mapped_column(Text,default="{}");completed_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True);updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)
