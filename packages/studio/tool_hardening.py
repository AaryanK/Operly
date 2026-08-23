"""Studio-only source mutation and dependency hardening."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
import urllib.request

from packages.studio.context_hardening import digest


APPROVED_DEPENDENCIES = {
    "three": {
        "version": "0.180.0",
        "files": {"package/build/three.module.js": "vendor/three.module.js"},
    },
}


def reject_remote_script(value: str) -> None:
    if re.search(r"(?i)<script\b[^>]*\bsrc\s*=\s*[\"']https?://", str(value or "")):
        raise ValueError(
            "Remote scripts are blocked at mutation time. Use dependency_add with an approved exact package version."
        )


def _approved_label() -> str:
    return ", ".join(
        f"{name}@{policy['version']}" for name, policy in APPROVED_DEPENDENCIES.items()
    )


def install_dependency(workspace, name: str, version: str) -> dict:
    policy = APPROVED_DEPENDENCIES.get(name)
    if not policy or version != policy["version"]:
        raise ValueError(f"Dependency is not approved. Allowed: {_approved_label()}")

    metadata_url = f"https://registry.npmjs.org/{name}/{version}"
    request = urllib.request.Request(
        metadata_url,
        headers={"User-Agent": "Operly-Studio-Dependency/1"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        if not response.geturl().startswith("https://registry.npmjs.org/"):
            raise ValueError("Dependency registry redirect left the approved host")
        metadata = json.loads(response.read(1_000_000).decode("utf-8"))

    dist = metadata.get("dist") or {}
    tarball = str(dist.get("tarball") or "")
    integrity = str(dist.get("integrity") or "")
    if not tarball.startswith("https://registry.npmjs.org/") or not integrity.startswith("sha512-"):
        raise ValueError("Approved package metadata did not provide expected registry integrity")

    request = urllib.request.Request(
        tarball,
        headers={"User-Agent": "Operly-Studio-Dependency/1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if not response.geturl().startswith("https://registry.npmjs.org/"):
            raise ValueError("Dependency tarball redirect left the approved host")
        body = response.read(8_000_000)

    expected = base64.b64decode(integrity.split("-", 1)[1])
    actual = hashlib.sha512(body).digest()
    if actual != expected:
        raise ValueError("Dependency integrity verification failed")

    installed: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        for source_path, target_path in policy["files"].items():
            member = members.get(source_path)
            if member is None or member.size > 4_000_000:
                raise ValueError(
                    f"Approved dependency artifact missing or oversized: {source_path}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"Approved dependency artifact unreadable: {source_path}")
            workspace.write(target_path, extracted.read().decode("utf-8"))
            installed.append(target_path)

    lock = {
        "schemaVersion": 1,
        "dependencies": {
            name: {
                "version": version,
                "integrity": integrity,
                "registry": "https://registry.npmjs.org",
                "files": installed,
            }
        },
    }
    workspace.write(
        "operly.dependencies.json",
        json.dumps(lock, indent=2, sort_keys=True) + "\n",
    )
    return {
        "ok": True,
        "name": name,
        "version": version,
        "integrity": integrity,
        "files": installed,
        "lockfile": "operly.dependencies.json",
    }


def install_tool_policy(runtime_policy, hardened_validator) -> None:
    """Patch the already-installed Studio registry with stronger source tools."""
    original_for_mode = runtime_policy.StudioWebsiteToolRegistry.for_mode

    def hardened_for_mode(self, mode, *, visual: bool, web: bool):
        tools = original_for_mode(self, mode, visual=visual, web=web)

        read_tool = tools.get("read")
        if read_tool is not None:
            async def read_with_revision(args, session, _execute=read_tool.execute):
                result = await _execute(args, session)
                path = str(args.get("path") or "")
                try:
                    result["revision"] = digest(session.workspace.raw(path))
                except Exception:
                    pass
                return result

            tools["read"] = runtime_policy.coding.CodingTool(
                read_tool.name,
                read_tool.description
                + " Returns the current revision digest; pass it to replace_range.",
                read_tool.properties,
                read_tool.required,
                read_tool.modes,
                read_with_revision,
            )

        # A build packet with zero files is authoritative evidence that the source
        # workspace is empty. Do not spend a model round trip re-proving that fact.
        for inspection_name in ("list", "glob"):
            tool = tools.get(inspection_name)
            if tool is None:
                continue

            async def no_empty_rediscovery(args, session, _execute=tool.execute, _name=inspection_name):
                if not session.before and not session.workspace.list():
                    return {
                        "ok": True,
                        "knownEmpty": True,
                        "files": [],
                        "note": (
                            "The build packet already established workspaceFiles=[]; "
                            "begin writing source instead of rediscovering the empty workspace."
                        ),
                    }
                return await _execute(args, session)

            tools[inspection_name] = runtime_policy.coding.CodingTool(
                tool.name,
                tool.description,
                tool.properties,
                tool.required,
                tool.modes,
                no_empty_rediscovery,
            )

        if mode == "plan":
            return tools

        async def replace_revision(args, session):
            path = str(args.get("path") or "")
            expected = str(args.get("revision") or "").strip()
            if not expected:
                raise runtime_policy.coding.WorkspacePolicyError(
                    "replace_range requires the revision returned by read"
                )
            current = session.workspace.raw(path)
            actual = digest(current)
            if actual != expected:
                raise runtime_policy.coding.WorkspacePolicyError(
                    "stale_edit_conflict: expected revision "
                    f"{expected[:12]}, current revision {actual[:12]}; reread before editing"
                )
            reject_remote_script(str(args.get("content") or ""))
            runtime_policy._replace_range(
                session.workspace,
                path,
                int(args.get("start_line") or 0),
                int(args.get("end_line") or 0),
                str(args.get("content") or ""),
            )
            return {
                "ok": True,
                "path": path,
                "previousRevision": expected,
                "revision": digest(session.workspace.raw(path)),
            }

        tools["replace_range"] = runtime_policy.coding.CodingTool(
            "replace_range",
            "Replace an inclusive line range only when the revision still matches. Reread after any mutation.",
            {
                "path": runtime_policy.coding.TEXT,
                "revision": runtime_policy.coding.TEXT,
                "start_line": runtime_policy.coding.INTEGER,
                "end_line": runtime_policy.coding.INTEGER,
                "content": runtime_policy.coding.TEXT,
            },
            ("path", "revision", "start_line", "end_line", "content"),
            frozenset({"build", "edit", "repair"}),
            replace_revision,
        )

        async def dependency_add(args, session):
            return await runtime_policy.asyncio.to_thread(
                install_dependency,
                session.workspace,
                str(args.get("name") or "").strip().lower(),
                str(args.get("version") or "").strip(),
            )

        tools["dependency_add"] = runtime_policy.coding.CodingTool(
            "dependency_add",
            "Install an approved exact-version browser dependency through Operly's governed npm registry path. Never add CDN script tags.",
            {
                "name": {"type": "string", "enum": sorted(APPROVED_DEPENDENCIES)},
                "version": runtime_policy.coding.TEXT,
            },
            ("name", "version"),
            frozenset({"build", "edit", "repair"}),
            dependency_add,
        )

        for mutation_name in ("write", "edit"):
            tool = tools.get(mutation_name)
            if tool is None:
                continue

            async def guarded_mutation(args, session, _execute=tool.execute):
                reject_remote_script(
                    str(args.get("content") or args.get("new") or "")
                )
                return await _execute(args, session)

            tools[mutation_name] = runtime_policy.coding.CodingTool(
                tool.name,
                tool.description,
                tool.properties,
                tool.required,
                tool.modes,
                guarded_mutation,
            )

        return tools

    runtime_policy.StudioWebsiteToolRegistry.for_mode = hardened_for_mode
