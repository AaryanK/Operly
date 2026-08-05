from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateProjectInput(Strict):
    prompt: str = Field(min_length=20, max_length=4000)


class VisualChangeInput(Strict):
    request: str = Field(min_length=3, max_length=2000)
    selected_artifact_ids: list[str] = Field(min_length=1, max_length=20)
    viewport: str = Field(default="desktop", pattern="^(desktop|tablet|mobile)$")


class AgenticProjectInput(Strict):
    prompt: str = Field(min_length=20, max_length=8000)


class ServiceRequestInput(Strict):
    name: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=7, max_length=40)
    email: str | None = Field(default=None, max_length=320)
    issue_category: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=2000)
    address: str = Field(min_length=5, max_length=500)
    asset_details: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=120)

    @field_validator("name", "phone", "issue_category", "address")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str | None) -> str | None:
        if not value:
            return None
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid email address")
        return value


class TransitionInput(Strict):
    status: str
    assigned_to: str | None = Field(default=None, max_length=160)
    note: str = Field(default="", max_length=500)
    expected_version: int = Field(ge=1)
