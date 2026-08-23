"""Deterministic, secret-free source bundle construction shared with runners."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass

MAX_FILES = 100
MAX_BYTES = 2_000_000


class BundlePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class SourceFile:
    path: str
    content: bytes
    generated_by: str


@dataclass(frozen=True)
class SourceBundle:
    files: tuple[SourceFile, ...]
    manifest: dict
    digest: str


def normalized_path(path: str) -> str:
    if (
        not path
        or "\\" in path
        or path.startswith("/")
        or re.match(r"^[A-Za-z]:", path)
    ):
        raise BundlePolicyError("Bundle paths must be relative POSIX paths")
    value = posixpath.normpath(path)
    if (
        value in {".", ".."}
        or value.startswith("../")
        or "/../" in value
        or value.startswith(".")
    ):
        raise BundlePolicyError("Path traversal and hidden paths are forbidden")
    return value


def build_bundle(
    files: list[SourceFile],
    workspace_id: str,
    application_id: str,
    plan_id: str,
    plan_version: int,
    source_version: int,
    prompt_digest: str,
) -> SourceBundle:
    if len(files) > MAX_FILES:
        raise BundlePolicyError("Bundle file-count limit exceeded")
    rows = []
    seen = set()
    total = 0
    clean = []
    for item in files:
        path = normalized_path(item.path)
        if path in seen:
            raise BundlePolicyError("Duplicate bundle path")
        seen.add(path)
        total += len(item.content)
        if total > MAX_BYTES:
            raise BundlePolicyError("Bundle size limit exceeded")
        if (
            b"BEGIN PRIVATE KEY" in item.content
            or b"OPERLY_SANDBOX_RUNNER_TOKEN" in item.content
        ):
            raise BundlePolicyError("Secrets are forbidden in source bundles")
        digest = hashlib.sha256(item.content).hexdigest()
        rows.append(
            {
                "path": path,
                "bytes": len(item.content),
                "digest": f"sha256:{digest}",
                "generatedBy": item.generated_by,
            }
        )
        clean.append(SourceFile(path, item.content, item.generated_by))
    rows.sort(key=lambda row: row["path"])
    clean.sort(key=lambda item: item.path)
    manifest = {
        "schemaVersion": 1,
        "workspaceId": workspace_id,
        "applicationId": application_id,
        "planId": plan_id,
        "planVersion": plan_version,
        "sourceVersion": source_version,
        "promptDigest": prompt_digest,
        "files": rows,
        "totalBytes": total,
    }
    digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return SourceBundle(tuple(clean), manifest, f"sha256:{digest}")
