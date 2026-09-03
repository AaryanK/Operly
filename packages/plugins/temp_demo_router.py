from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from packages.database.plugin_platform_models import PluginInstallationRecord, PluginPackageRecord, PluginVersionRecord
from packages.kernel.contracts import RuntimeRequest
from packages.kernel.ingress import TrustedIngress, resolve_ingress_context
from packages.plugins.contracts import PluginManifest
from packages.plugins.storage import plugin_storage
from packages.security.execution_context import ScopeKind
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


router = APIRouter(prefix="/api/public/plugin-demos", tags=["temporary-plugin-demos"])
_runtime = build_workspace_runtime()
_bootstrap_task: asyncio.Task | None = None
MAX_RECORDS = 250
MAX_STATE_BYTES = 300_000


class DemoStateInput(BaseModel):
    state: dict[str, Any] = Field(default_factory=dict)


class DemoExecuteInput(BaseModel):
    action: str = Field(default="analyze", max_length=80)


def _loads(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bootstrap_done(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception as error:
        print(f"TEMP_APP_SUITE_FAILED {type(error).__name__}: {error}", flush=True)


@router.get("/bootstrap/{key}")
async def bootstrap_temp_app_suite(key: str):
    global _bootstrap_task
    expected = os.getenv("OPERLY_TEMP_APP_BOOTSTRAP_KEY", "").strip()
    if not expected or not hmac.compare_digest(expected, key):
        raise HTTPException(status_code=404, detail="Not found")
    if _bootstrap_task is not None and not _bootstrap_task.done():
        return {"accepted": True, "state": "already_running"}
    from packages.plugins.temp_app_suite import main as run_temp_app_suite
    _bootstrap_task = asyncio.create_task(run_temp_app_suite(), name="operly-temp-functional-app-suite")
    _bootstrap_task.add_done_callback(_bootstrap_done)
    return {"accepted": True, "state": "started"}


async def _demo_context(
    db: AsyncSession,
    *,
    workspace_id: str,
    plugin_id: str,
    token: str | None,
) -> tuple[PluginInstallationRecord, PluginPackageRecord, PluginVersionRecord, PluginManifest, dict[str, Any]]:
    if not token or len(token) < 24 or len(token) > 200:
        raise HTTPException(status_code=404, detail="Temporary app unavailable")
    package = await db.scalar(
        select(PluginPackageRecord).where(
            PluginPackageRecord.owner_tenant_id == workspace_id,
            PluginPackageRecord.plugin_id == plugin_id,
        )
    )
    if package is None or not package.plugin_id.startswith("temp."):
        raise HTTPException(status_code=404, detail="Temporary app unavailable")
    installation = await db.scalar(
        select(PluginInstallationRecord).where(
            PluginInstallationRecord.tenant_id == workspace_id,
            PluginInstallationRecord.package_id == package.id,
            PluginInstallationRecord.status == "active",
            PluginInstallationRecord.enabled.is_(True),
        )
    )
    if installation is None:
        raise HTTPException(status_code=404, detail="Temporary app unavailable")
    configuration = _loads(installation.configuration_json)
    if configuration.get("temporary_demo") is not True:
        raise HTTPException(status_code=404, detail="Temporary app unavailable")
    expected = str(configuration.get("demo_token_hash") or "")
    if not expected or not hmac.compare_digest(expected, _token_hash(token)):
        raise HTTPException(status_code=404, detail="Temporary app unavailable")
    version = await db.get(PluginVersionRecord, installation.version_id)
    if version is None or version.validation_status != "passed":
        raise HTTPException(status_code=503, detail="Temporary app is not ready")
    try:
        manifest = PluginManifest.from_dict(json.loads(version.manifest_json))
    except Exception as error:
        raise HTTPException(status_code=503, detail="Temporary app manifest is invalid") from error
    if manifest.metadata.get("temporary") is not True or manifest.metadata.get("source") != "operly-temp-functional-app-suite":
        raise HTTPException(status_code=404, detail="Temporary app unavailable")
    return installation, package, version, manifest, configuration


def _validate_state(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
        raise HTTPException(status_code=413, detail="Temporary app state is too large")
    records = value.get("records")
    if records is not None and (not isinstance(records, list) or len(records) > MAX_RECORDS):
        raise HTTPException(status_code=422, detail="Temporary app records are invalid")
    return value


async def _read_state(
    db: AsyncSession,
    *,
    workspace_id: str,
    installation: PluginInstallationRecord,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    try:
        value = await plugin_storage.get_json(
            db,
            tenant_id=workspace_id,
            installation_id=installation.id,
            namespace="app",
            key="state",
        )
    except LookupError:
        value = configuration.get("demo_seed") or {"records": []}
    return value if isinstance(value, dict) else {"records": []}


@router.get("/{workspace_id}/apps")
async def list_demo_apps(
    workspace_id: str,
    x_operly_demo_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(PluginInstallationRecord)
            .where(
                PluginInstallationRecord.tenant_id == workspace_id,
                PluginInstallationRecord.status == "active",
                PluginInstallationRecord.enabled.is_(True),
            )
            .order_by(PluginInstallationRecord.installed_at)
        )
    ).all()
    apps: list[dict[str, Any]] = []
    token_valid = False
    for installation in rows:
        package = await db.get(PluginPackageRecord, installation.package_id)
        version = await db.get(PluginVersionRecord, installation.version_id)
        if package is None or version is None or not package.plugin_id.startswith("temp."):
            continue
        config = _loads(installation.configuration_json)
        if config.get("temporary_demo") is not True:
            continue
        expected = str(config.get("demo_token_hash") or "")
        if not x_operly_demo_token or not expected or not hmac.compare_digest(expected, _token_hash(x_operly_demo_token)):
            continue
        token_valid = True
        try:
            manifest = PluginManifest.from_dict(json.loads(version.manifest_json))
        except Exception:
            continue
        if manifest.metadata.get("source") != "operly-temp-functional-app-suite":
            continue
        apps.append({
            "plugin_id": package.plugin_id,
            "name": config.get("demo_name") or package.display_name,
            "category": config.get("demo_category") or "App",
            "description": config.get("demo_description") or package.description,
            "capability_id": next((c.id for c in manifest.capabilities), None),
            "hosted_path": f"/api/public/plugins/{workspace_id}/{package.plugin_id}/",
            "temporary": True,
        })
    if not token_valid:
        raise HTTPException(status_code=404, detail="Temporary app lab unavailable")
    return {"workspace_id": workspace_id, "temporary": True, "apps": apps}


@router.get("/{workspace_id}/{plugin_id}/state")
async def get_demo_state(
    workspace_id: str,
    plugin_id: str,
    x_operly_demo_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    installation, _, _, _, config = await _demo_context(
        db, workspace_id=workspace_id, plugin_id=plugin_id, token=x_operly_demo_token
    )
    return {"state": await _read_state(db, workspace_id=workspace_id, installation=installation, configuration=config)}


@router.put("/{workspace_id}/{plugin_id}/state")
async def put_demo_state(
    workspace_id: str,
    plugin_id: str,
    payload: DemoStateInput,
    x_operly_demo_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    installation, _, _, _, _ = await _demo_context(
        db, workspace_id=workspace_id, plugin_id=plugin_id, token=x_operly_demo_token
    )
    state = _validate_state(payload.state)
    await plugin_storage.put_json(
        db,
        tenant_id=workspace_id,
        installation_id=installation.id,
        namespace="app",
        key="state",
        value=state,
    )
    await db.commit()
    return {"saved": True, "state": state}


@router.post("/{workspace_id}/{plugin_id}/reset")
async def reset_demo_state(
    workspace_id: str,
    plugin_id: str,
    x_operly_demo_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    installation, _, _, _, config = await _demo_context(
        db, workspace_id=workspace_id, plugin_id=plugin_id, token=x_operly_demo_token
    )
    seed = _validate_state(config.get("demo_seed") if isinstance(config.get("demo_seed"), dict) else {"records": []})
    await plugin_storage.put_json(
        db,
        tenant_id=workspace_id,
        installation_id=installation.id,
        namespace="app",
        key="state",
        value=seed,
    )
    await db.commit()
    return {"reset": True, "state": seed}


@router.post("/{workspace_id}/{plugin_id}/execute")
async def execute_demo_capability(
    workspace_id: str,
    plugin_id: str,
    payload: DemoExecuteInput,
    x_operly_demo_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    installation, _, _, manifest, config = await _demo_context(
        db, workspace_id=workspace_id, plugin_id=plugin_id, token=x_operly_demo_token
    )
    if payload.action != "analyze":
        raise HTTPException(status_code=422, detail="Unsupported temporary app action")
    capabilities = tuple(manifest.capabilities)
    if len(capabilities) != 1:
        raise HTTPException(status_code=503, detail="Temporary app capability contract is invalid")
    capability = capabilities[0]
    if (
        not capability.id.startswith(f"{plugin_id}.")
        or capability.permissions
        or capability.approval_required
        or str(capability.risk.value) != "read_only"
    ):
        raise HTTPException(status_code=503, detail="Temporary app capability is not eligible for demo execution")
    state = await _read_state(db, workspace_id=workspace_id, installation=installation, configuration=config)
    context = await resolve_ingress_context(
        db,
        TrustedIngress(
            scope_kind=ScopeKind.WORKSPACE,
            user_id=installation.installed_by,
            workspace_id=workspace_id,
            channel="web",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            conversation_id=None,
            metadata={"ingress": "operly_temporary_app_demo", "temporary_demo": True, "plugin_id": plugin_id},
        ),
    )
    response = await _runtime.execute(
        db,
        context=context,
        request=RuntimeRequest(
            goal=f"Analyze the temporary {manifest.display_name} application state.",
            capability_id=capability.id,
            arguments={"action": "analyze", "state": state},
        ),
    )
    result = response.as_dict()
    await db.commit()
    return {
        "plugin_id": plugin_id,
        "run_id": result.get("run_id") or result.get("id"),
        "result": result.get("result") or result,
    }
