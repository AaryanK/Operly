from __future__ import annotations

import base64
import json
import os
import shlex
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from packages.artifacts import ArtifactService
from packages.database.plugin_platform_models import PluginVersionRecord
from packages.plugins.contracts import PluginManifest
from packages.plugins.runtime_profiles import RuntimeProfile
from packages.workspace_modules.agent_computer.sandbox import (
    ComputerRunnerClient,
    ComputerRunnerError,
)


class IsolatedPluginValidationError(RuntimeError):
    def __init__(self, message: str, *, permanent: bool = True) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class IsolatedPluginValidationResult:
    validated_artifact_id: str
    validated_artifact_digest: str
    build_logs_artifact_id: str
    source_artifact_id: str
    source_digest: str
    runtime_profile: str
    file_count: int
    unpacked_bytes: int
    build_commands_run: int
    build_network_policy: str
    evidence: dict[str, Any]


_INSPECTION_SCRIPT_TEMPLATE = r'''
import hashlib
import json
import os
import pathlib
import shutil
import stat
import zipfile

archive = pathlib.Path('/workspace/work/source.zip')
root = pathlib.Path('/workspace/work/src')
markers = __MARKERS__
max_files = __MAX_FILES__
max_unpacked = __MAX_UNPACKED__

if not archive.is_file():
    raise RuntimeError('source archive is missing')
if not zipfile.is_zipfile(archive):
    raise ValueError('executable plugin package must be a ZIP archive')

shutil.rmtree(root, ignore_errors=True)
root.mkdir(parents=True, exist_ok=True)
file_count = 0
total = 0
files = []
with zipfile.ZipFile(archive, 'r') as zf:
    infos = zf.infolist()
    if len(infos) > max_files:
        raise ValueError('plugin archive contains too many entries')
    for info in infos:
        raw_name = str(info.filename or '').replace('\\', '/')
        path = pathlib.PurePosixPath(raw_name)
        if not raw_name or path.is_absolute() or '..' in path.parts:
            raise ValueError('plugin archive contains an unsafe path')
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise ValueError('plugin archive may not contain symbolic links')
        if info.flag_bits & 0x1:
            raise ValueError('encrypted plugin archives are unsupported')
        if info.is_dir():
            (root / pathlib.Path(*path.parts)).mkdir(parents=True, exist_ok=True)
            continue
        file_count += 1
        total += int(info.file_size)
        if file_count > max_files or total > max_unpacked:
            raise ValueError('plugin archive exceeds unpacked size policy')
        target = root / pathlib.Path(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, 'r') as source, target.open('wb') as sink:
            copied = 0
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > int(info.file_size) + 1024 or total > max_unpacked:
                    raise ValueError('plugin archive expansion exceeded declared size')
                sink.write(chunk)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        files.append({'path': path.as_posix(), 'size_bytes': target.stat().st_size, 'sha256': digest})

if file_count == 0:
    raise ValueError('plugin archive is empty')
paths = {item['path'].lower() for item in files}
marker_hits = [marker for marker in markers if marker.lower().lstrip('./') in paths]
if markers and not marker_hits:
    raise ValueError('plugin source does not match the trusted runtime profile markers')

npm_present = 'package.json' in paths
npm_lock = 'package-lock.json' in paths or 'npm-shrinkwrap.json' in paths
if npm_present and not npm_lock:
    raise ValueError('Node plugin packages require package-lock.json or npm-shrinkwrap.json')

python_requirements = 'requirements.txt' in paths
uv_lock = 'uv.lock' in paths
pyproject = 'pyproject.toml' in paths
if pyproject and not (uv_lock or python_requirements):
    raise ValueError('Python pyproject plugin packages require uv.lock or requirements.txt')

if python_requirements:
    requirements = (root / 'requirements.txt').read_text(encoding='utf-8', errors='strict').splitlines()
    for line in requirements:
        clean = line.strip()
        if not clean or clean.startswith('#') or clean.startswith(('-r ', '--requirement ', '--index-url ', '--extra-index-url ', '--find-links ')):
            continue
        if '==' not in clean and '@' not in clean:
            raise ValueError('requirements.txt dependencies must be pinned with == or a direct reference')

print(json.dumps({
    'file_count': file_count,
    'unpacked_bytes': total,
    'marker_hits': marker_hits,
    'npm_lock_present': npm_lock,
    'python_lock_present': bool(uv_lock or python_requirements),
    'files': files,
}, separators=(',', ':'), sort_keys=True))
'''


_NORMALIZE_SCRIPT_TEMPLATE = r'''
import hashlib
import json
import pathlib
import zipfile

source = pathlib.Path('/workspace/work/__SOURCE_DIR__')
out = pathlib.Path('/workspace/work/validated.zip')
exclude_parts = {'.git', '.hg', '.svn', '.pytest_cache', '__pycache__', '.mypy_cache', '.ruff_cache'}
exclude_top = {'node_modules', '.venv', 'venv'}
files = []
for path in source.rglob('*'):
    if not path.is_file():
        continue
    rel = path.relative_to(source)
    if rel.parts and rel.parts[0] in exclude_top:
        continue
    if any(part in exclude_parts for part in rel.parts):
        continue
    files.append((rel.as_posix(), path))
files.sort(key=lambda pair: pair[0])
if not files:
    raise ValueError('validated plugin output is empty')
with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for name, path in files:
        raw = path.read_bytes()
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        zf.writestr(info, raw)
raw = out.read_bytes()
print(json.dumps({
    'path': str(out),
    'file_count': len(files),
    'size_bytes': len(raw),
    'sha256': hashlib.sha256(raw).hexdigest(),
}, separators=(',', ':'), sort_keys=True))
'''


class SandboxPluginValidator:
    """Validate/build user plugin source only inside the existing Sandbox Runner.

    The control plane provides immutable bytes and trusted runtime-profile commands.
    It never imports the uploaded package. The sandbox receives no Workspace/provider
    credentials and is never joined to Operly's private service network.
    """

    MAX_FILES = 5000
    MAX_UNPACKED_BYTES = 200 * 1024 * 1024

    def __init__(self, runner: ComputerRunnerClient | None = None) -> None:
        self.runner = runner or ComputerRunnerClient()

    @staticmethod
    def _network_policy() -> str:
        configured = os.getenv("OPERLY_PLUGIN_BUILD_NETWORK_POLICY", "off").strip().lower()
        if configured not in {"off", "web"}:
            raise IsolatedPluginValidationError(
                "OPERLY_PLUGIN_BUILD_NETWORK_POLICY must be off or web",
                permanent=False,
            )
        return configured

    @staticmethod
    def _command(command: tuple[str, ...]) -> str:
        if not command:
            raise IsolatedPluginValidationError("Trusted runtime profile contains an empty build command")
        return shlex.join(command)

    async def validate(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        version: PluginVersionRecord,
        manifest: PluginManifest,
        profile: RuntimeProfile,
    ) -> IsolatedPluginValidationResult:
        if not version.package_artifact_id:
            raise IsolatedPluginValidationError("Executable plugin package artifact is missing")
        artifacts = ArtifactService(db)
        source_row = await artifacts.assert_workspace_artifact(
            tenant_id=tenant_id,
            artifact_id=version.package_artifact_id,
        )
        source = await artifacts.read_bytes(
            scope=__import__('packages.artifacts', fromlist=['ArtifactScope']).ArtifactScope(
                "workspace", tenant_id, tenant_id=tenant_id
            ),
            artifact_id=source_row.id,
        )
        if not (source_row.filename.lower().endswith(".zip") or source_row.content_type in {
            "application/zip",
            "application/x-zip-compressed",
        }):
            raise IsolatedPluginValidationError(
                "Executable plugin package must be uploaded as a ZIP artifact"
            )

        session_id = f"plugin-build-{uuid4().hex[:24]}"
        runtime_id: str | None = None
        logs: list[str] = []
        network_policy = self._network_policy()
        try:
            started = await self.runner.start(
                computer_session_id=session_id,
                workspace_id=tenant_id,
                principal_id=version.created_by or "operly-platform-worker",
                profile="coding",
                ttl_seconds=max(300, min(profile.default_resources.max_runtime_seconds + 300, 7200)),
                network_policy=network_policy,
            )
            runtime_id = str(started.get("session_id") or started.get("id") or "").strip()
            if not runtime_id:
                raise ComputerRunnerError("Sandbox Runner did not return a runtime identity")

            imported = await self.runner.tool(
                runtime_id,
                "artifact.import",
                {
                    "path": "source.zip",
                    "content_base64": base64.b64encode(source).decode("ascii"),
                    "content_type": source_row.content_type,
                },
            )
            if str(imported.get("sha256") or "").lower() != source_row.sha256.lower():
                raise IsolatedPluginValidationError("Sandbox source digest differs from Workspace artifact")

            inspect_code = (
                _INSPECTION_SCRIPT_TEMPLATE
                .replace("__MARKERS__", repr(list(profile.source_markers)))
                .replace("__MAX_FILES__", str(self.MAX_FILES))
                .replace("__MAX_UNPACKED__", str(self.MAX_UNPACKED_BYTES))
            )
            inspection_packet = await self.runner.tool(
                runtime_id,
                "python.exec",
                {"code": inspect_code, "cwd": ".", "timeout_seconds": 180},
            )
            if int(inspection_packet.get("exit_code") or 0) != 0:
                raise IsolatedPluginValidationError(
                    "Plugin archive inspection failed: "
                    + str(inspection_packet.get("stderr") or inspection_packet.get("stdout") or "unknown error")[:2000]
                )
            try:
                inspection = json.loads(str(inspection_packet.get("stdout") or "{}"))
            except json.JSONDecodeError as error:
                raise IsolatedPluginValidationError("Sandbox inspection returned invalid evidence", permanent=False) from error
            if not isinstance(inspection, dict):
                raise IsolatedPluginValidationError("Sandbox inspection returned invalid evidence", permanent=False)

            for command in profile.build_commands:
                rendered = self._command(command)
                packet = await self.runner.tool(
                    runtime_id,
                    "terminal.exec",
                    {
                        "command": rendered,
                        "cwd": "src",
                        "timeout_seconds": min(profile.default_resources.max_runtime_seconds, 900),
                    },
                    timeout_seconds=min(profile.default_resources.max_runtime_seconds + 60, 930),
                )
                logs.append(
                    f"$ {rendered}\n{str(packet.get('stdout') or '')}\n{str(packet.get('stderr') or '')}"[:200_000]
                )
                if packet.get("timed_out") or int(packet.get("exit_code") or 0) != 0:
                    raise IsolatedPluginValidationError(
                        f"Trusted profile build command failed: {rendered}"
                    )

            source_dir = "src"
            if profile.id == "react-vite":
                listing = await self.runner.tool(
                    runtime_id,
                    "files.list",
                    {"path": "src/dist", "recursive": False, "max_entries": 20},
                )
                if not listing.get("items"):
                    raise IsolatedPluginValidationError("React/Vite build produced no dist output")
                source_dir = "src/dist"

            normalize_code = _NORMALIZE_SCRIPT_TEMPLATE.replace("__SOURCE_DIR__", source_dir)
            normalized_packet = await self.runner.tool(
                runtime_id,
                "python.exec",
                {"code": normalize_code, "cwd": ".", "timeout_seconds": 180},
            )
            if int(normalized_packet.get("exit_code") or 0) != 0:
                raise IsolatedPluginValidationError(
                    "Validated artifact normalization failed: "
                    + str(normalized_packet.get("stderr") or "unknown error")[:2000],
                    permanent=False,
                )
            exported = await self.runner.tool(
                runtime_id,
                "artifact.export",
                {"path": "validated.zip", "max_bytes": 25 * 1024 * 1024},
            )
            try:
                validated_bytes = base64.b64decode(
                    str(exported.get("content_base64") or ""), validate=True
                )
            except Exception as error:
                raise IsolatedPluginValidationError("Sandbox exported invalid artifact bytes", permanent=False) from error
            exported_digest = str(exported.get("sha256") or "").lower()
            if not validated_bytes or exported_digest != __import__('hashlib').sha256(validated_bytes).hexdigest():
                raise IsolatedPluginValidationError("Validated artifact digest verification failed", permanent=False)

            validated = await artifacts.create_bytes(
                __import__('packages.artifacts', fromlist=['ArtifactScope']).ArtifactScope(
                    "workspace", tenant_id, tenant_id=tenant_id
                ),
                filename=f"plugin-{version.id}-validated.zip",
                content=validated_bytes,
                content_type="application/zip",
                source="plugin_isolated_validation",
                created_by=version.created_by,
                parent_artifact_id=source_row.id,
                metadata={
                    "plugin_version_id": version.id,
                    "runtime_profile": profile.id,
                    "source_sha256": source_row.sha256,
                    "validation": "isolated_sandbox",
                },
            )
            log_bytes = ("\n\n".join(logs) or "No build commands were required.\n").encode("utf-8")
            build_logs = await artifacts.create_bytes(
                __import__('packages.artifacts', fromlist=['ArtifactScope']).ArtifactScope(
                    "workspace", tenant_id, tenant_id=tenant_id
                ),
                filename=f"plugin-{version.id}-build.log",
                content=log_bytes[: 2 * 1024 * 1024],
                content_type="text/plain; charset=utf-8",
                source="plugin_isolated_validation",
                created_by=version.created_by,
                parent_artifact_id=source_row.id,
                metadata={"plugin_version_id": version.id, "runtime_profile": profile.id},
            )
            return IsolatedPluginValidationResult(
                validated_artifact_id=validated.id,
                validated_artifact_digest=validated.sha256,
                build_logs_artifact_id=build_logs.id,
                source_artifact_id=source_row.id,
                source_digest=source_row.sha256,
                runtime_profile=profile.id,
                file_count=int(inspection.get("file_count") or 0),
                unpacked_bytes=int(inspection.get("unpacked_bytes") or 0),
                build_commands_run=len(profile.build_commands),
                build_network_policy=network_policy,
                evidence={
                    "marker_hits": list(inspection.get("marker_hits") or []),
                    "npm_lock_present": bool(inspection.get("npm_lock_present")),
                    "python_lock_present": bool(inspection.get("python_lock_present")),
                    "sandbox_isolation": started.get("isolation"),
                    "sandbox_private_network": bool(started.get("private_network", False)),
                },
            )
        except ComputerRunnerError as error:
            raise IsolatedPluginValidationError(str(error), permanent=False) from error
        finally:
            if runtime_id:
                try:
                    await self.runner.stop(runtime_id)
                except Exception:
                    pass


sandbox_plugin_validator = SandboxPluginValidator()

__all__ = [
    "IsolatedPluginValidationError",
    "IsolatedPluginValidationResult",
    "SandboxPluginValidator",
    "sandbox_plugin_validator",
]
