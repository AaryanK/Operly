from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.plugin_platform_models import PluginInstallationRecord, PluginVersionRecord
from packages.kernel.contracts import CapabilitySpec
from packages.plugins.contracts import PluginManifest
from packages.security.execution_context import ExecutionContext


class InstalledPluginCapabilitySource:
    """Loads active plugin contracts from durable Workspace installation state.

    This is intentionally a source, not a second registry or execution authority. The
    Workspace runtime can compose these specs into a request-local capability view once
    the generic plugin executor is attached. All resulting invocations still pass
    through Kernel policy/approval/idempotency/audit.
    """

    async def list(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
    ) -> tuple[CapabilitySpec, ...]:
        if not context.workspace_id:
            return ()
        rows = (
            await db.scalars(
                select(PluginInstallationRecord).where(
                    PluginInstallationRecord.tenant_id == context.workspace_id,
                    PluginInstallationRecord.enabled.is_(True),
                    PluginInstallationRecord.status == "active",
                )
            )
        ).all()
        specs: list[CapabilitySpec] = []
        seen: set[str] = set()
        for installation in rows:
            version = await db.get(PluginVersionRecord, installation.version_id)
            if version is None or version.validation_status != "passed":
                continue
            manifest = PluginManifest.from_dict(json.loads(version.manifest_json))
            for spec in manifest.capability_specs():
                if spec.id in seen:
                    raise RuntimeError(f"Installed plugin capability collision: {spec.id}")
                seen.add(spec.id)
                specs.append(spec)
        return tuple(sorted(specs, key=lambda item: item.id))


installed_plugin_capability_source = InstalledPluginCapabilitySource()
