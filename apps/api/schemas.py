from datetime import datetime

from pydantic import BaseModel, Field


class LoginInput(BaseModel):
    email: str
    password: str


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    due_at: datetime | None = None


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    kind: str = Field(default="fact", max_length=50)


class ApprovalDecision(BaseModel):
    status: str


class TenantUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="UTC", max_length=100)
