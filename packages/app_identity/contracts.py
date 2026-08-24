"""Typed contracts for generated-application end-user identity.

Operly account users and sessions are deliberately separate. These identities belong
only to one generated application and may optionally link to a canonical workspace
Employee or Customer entity.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

APP_IDENTITY_CAPABILITY_ID = "identity.app_users"
APP_IDENTITY_BINDING_NAME = "identity"
APP_IDENTITY_SCHEMA_VERSION = "operly.app-identity/v1"
LinkedEntityKind = Literal["employee", "customer"]

_ROLE = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    displayName: str = Field(min_length=1, max_length=200)


class LoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class SessionRequest(StrictModel):
    sessionToken: str = Field(min_length=32, max_length=4096)


class AcceptInvitationRequest(StrictModel):
    invitationToken: str = Field(min_length=32, max_length=4096)
    password: str = Field(min_length=12, max_length=1024)


class InvitationCreateRequest(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    displayName: str = Field(min_length=1, max_length=200)
    role: str = "user"
    entityKind: LinkedEntityKind | None = None
    entityId: str | None = Field(default=None, min_length=1, max_length=120)
    expiresInSeconds: int = Field(default=86400, ge=300, le=604800)

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        value = str(value or "").strip().lower()
        if not _ROLE.fullmatch(value):
            raise ValueError("App identity role must be a stable lowercase identifier")
        return value

    @model_validator(mode="after")
    def entity_pair(self):
        if bool(self.entityKind) != bool(self.entityId):
            raise ValueError("entityKind and entityId must be supplied together")
        return self


__all__ = [
    "APP_IDENTITY_CAPABILITY_ID",
    "APP_IDENTITY_BINDING_NAME",
    "APP_IDENTITY_SCHEMA_VERSION",
    "RegisterRequest",
    "LoginRequest",
    "SessionRequest",
    "AcceptInvitationRequest",
    "InvitationCreateRequest",
]
