from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(StrictInput):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class SignupInput(StrictInput):
    display_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class ChallengeInput(StrictInput):
    challenge_id: str | None = Field(default=None, min_length=32, max_length=64)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    code: str | None = Field(default=None, pattern=r"^\d{6}$")
    token: str | None = Field(default=None, min_length=32, max_length=256)

    @model_validator(mode="after")
    def valid_proof(self):
        if self.token or (self.code and (self.challenge_id or self.email)):
            return self
        raise ValueError("Provide a valid code or link")


class ResendVerificationInput(StrictInput):
    email: str = Field(min_length=3, max_length=320)


class ForgotPasswordInput(StrictInput):
    email: str = Field(min_length=3, max_length=320)


class ResetPasswordInput(ChallengeInput):
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordInput(StrictInput):
    current_password: str | None = Field(default=None, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class GoogleCredentialInput(StrictInput):
    credential: str = Field(min_length=100, max_length=16_384)


class WorkspaceSwitchInput(StrictInput):
    tenant_id: str = Field(min_length=32, max_length=64)


class WorkspaceCreateInput(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)


class WorkspaceRoleCreateInput(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    key: str | None = Field(default=None, min_length=1, max_length=80)
    permissions: list[str] = Field(default_factory=list, max_length=100)


class WorkspaceRolePermissionsInput(StrictInput):
    permissions: list[str] = Field(default_factory=list, max_length=100)


class WorkspaceMemberAddInput(StrictInput):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(default="employee", min_length=1, max_length=80)


class WorkspaceMemberRoleInput(StrictInput):
    role: str = Field(min_length=1, max_length=80)


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
