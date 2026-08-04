from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.application_builder.catalog import ALLOWED_ACTIONS, ALLOWED_FIELDS, COMPONENTS, MODULES


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldDefinition(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    name: str = Field(max_length=120)
    type: str
    required: bool = False

    @model_validator(mode="after")
    def supported(self):
        if self.type not in ALLOWED_FIELDS:
            raise ValueError("Unsupported managed field type")
        return self


class EntityDefinition(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    name: str = Field(max_length=120)
    fields: list[FieldDefinition] = Field(default_factory=list, max_length=50)


class ModuleInstallation(Strict):
    moduleId: str
    version: int = Field(default=1, ge=1)
    configuration: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def registered(self):
        if self.moduleId not in MODULES:
            raise ValueError("Unknown capability module")
        return self


class ComponentDefinition(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,119}$")
    type: str
    label: str = Field(max_length=200)
    parentId: str | None = None
    regionId: str | None = None
    order: int = Field(default=0, ge=0, le=10000)
    properties: dict[str, Any] = Field(default_factory=dict)
    overrides: dict[str, Any] = Field(default_factory=dict)
    hiddenFor: list[Literal["owner", "manager", "employee"]] = Field(default_factory=list)
    locked: bool = False

    @model_validator(mode="after")
    def registered(self):
        if self.type not in COMPONENTS:
            raise ValueError("Unknown component type")
        forbidden = {"html", "css", "script", "javascript", "sql", "className", "onClick"}
        if forbidden.intersection(self.properties) or forbidden.intersection(self.overrides):
            raise ValueError("Arbitrary code and styling are not allowed")
        return self


class PageDefinition(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    name: str = Field(max_length=150)
    route: str = Field(pattern=r"^/[a-zA-Z0-9/_-]*$")
    protected: bool = True
    componentIds: list[str] = Field(default_factory=list)


class WorkflowBinding(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    componentId: str
    event: Literal["on_click", "on_submit", "on_success", "on_error", "on_row_select", "on_create", "on_update", "on_delete_request", "on_load"]
    action: str
    configuration: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def safe_action(self):
        if self.action not in ALLOWED_ACTIONS:
            raise ValueError("Unsupported workflow action")
        return self


class Theme(Strict):
    primary: str = "emerald"
    secondary: str = "slate"
    success: str = "emerald"
    warning: str = "orange"
    danger: str = "red"
    background: str = "white"
    surface: str = "white"
    text: str = "slate"
    mutedText: str = "slate"
    border: str = "slate"
    radius: Literal["none", "small", "medium", "large"] = "medium"
    spacing: Literal["compact", "comfortable", "spacious"] = "comfortable"
    typography: Literal["system", "modern", "classic"] = "system"
    shadow: Literal["none", "soft", "strong"] = "soft"
    density: Literal["compact", "comfortable"] = "comfortable"


class ApplicationManifest(Strict):
    schemaVersion: Literal[1] = 1
    application: dict[str, str]
    theme: Theme = Field(default_factory=Theme)
    modules: list[ModuleInstallation] = Field(default_factory=list)
    pages: list[PageDefinition] = Field(default_factory=list, max_length=12)
    regions: list[dict[str, Any]] = Field(default_factory=list)
    components: list[ComponentDefinition] = Field(default_factory=list, max_length=400)
    entities: list[EntityDefinition] = Field(default_factory=list, max_length=30)
    permissions: list[dict[str, Any]] = Field(default_factory=list)
    workflows: list[WorkflowBinding] = Field(default_factory=list, max_length=100)
    integrations: list[dict[str, Any]] = Field(default_factory=list)
    routes: list[dict[str, Any]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def references(self):
        ids = [component.id for component in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate component id")
        known = set(ids)
        for component in self.components:
            if component.parentId and component.parentId not in known:
                raise ValueError("Unknown component parent")
            if component.parentId:
                parent = next(x for x in self.components if x.id == component.parentId)
                allowed = COMPONENTS[component.type][0]
                if allowed and parent.type not in allowed:
                    raise ValueError("Component parent is not allowed")
        if len({page.route for page in self.pages}) != len(self.pages):
            raise ValueError("Duplicate application route")
        return self


class BuilderContext(Strict):
    workspaceId: str
    applicationId: str
    route: str = "/"
    pageId: str | None = None
    mode: Literal["studio"] = "studio"
    selectionScope: Literal["application", "page", "section", "component", "multi", "region", "workflow", "entity"] = "application"
    selectedIds: list[str] = Field(default_factory=list, max_length=50)
    selectedMetadata: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    activeVersionId: str
    viewport: Literal["desktop", "tablet", "mobile"] = "desktop"
    userRole: Literal["owner", "manager", "employee"]


class ProposalRequest(Strict):
    message: str = Field(min_length=1, max_length=4000)
    context: BuilderContext


class RecordInput(Strict):
    data: dict[str, Any]


def blank_manifest(application_id: str, name: str) -> ApplicationManifest:
    return ApplicationManifest(application={"id": application_id, "name": name}, permissions=[{"role": "owner", "actions": ["manage_application"]}])
