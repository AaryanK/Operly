from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path


ASSET_ROOT = Path(
    os.getenv(
        "OPERLY_ASSET_DIR",
        os.getenv(
            "STUDIO_ASSET_DIR",
            Path(__file__).resolve().parents[2] / "studio_assets",
        ),
    )
).resolve()

WORKSPACE_ICON_LIMIT = 2 * 1024 * 1024
_KEY = re.compile(r"^[a-f0-9]{40}\.(?:jpg|png|webp)$")


@dataclass(frozen=True)
class StoredWorkspaceIcon:
    key: str
    content_type: str
    path: Path


def detect_image_type(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def _workspace_dir(tenant_id: str) -> Path:
    safe_id = str(tenant_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", safe_id):
        raise ValueError("Invalid workspace asset scope")
    folder = (ASSET_ROOT / "workspaces" / safe_id / "icon").resolve()
    root = ASSET_ROOT.resolve()
    if folder != root and root not in folder.parents:
        raise ValueError("Invalid workspace asset path")
    return folder


def store_workspace_icon(*, tenant_id: str, data: bytes, declared_content_type: str) -> StoredWorkspaceIcon:
    if not data:
        raise ValueError("Workspace icon is empty")
    if len(data) > WORKSPACE_ICON_LIMIT:
        raise OverflowError("Workspace icon is larger than 2 MB")
    detected = detect_image_type(data)
    declared = str(declared_content_type or "").split(";", 1)[0].strip().lower()
    if not detected or declared != detected[0]:
        raise TypeError("Only matching JPEG, PNG, and WebP workspace icons are accepted")

    content_type, extension = detected
    key = f"{secrets.token_hex(20)}.{extension}"
    folder = _workspace_dir(tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = (folder / key).resolve()
    if folder not in target.parents:
        raise ValueError("Invalid workspace asset path")
    temporary = folder / f".{key}.{secrets.token_hex(5)}.tmp"
    temporary.write_bytes(data)
    temporary.replace(target)
    return StoredWorkspaceIcon(key=key, content_type=content_type, path=target)


def workspace_icon_path(*, tenant_id: str, key: str) -> Path:
    if not _KEY.fullmatch(str(key or "")):
        raise LookupError("Workspace icon not found")
    folder = _workspace_dir(tenant_id)
    path = (folder / key).resolve()
    if folder not in path.parents or not path.is_file():
        raise LookupError("Workspace icon not found")
    return path


def remove_workspace_icon(*, tenant_id: str, key: str | None) -> None:
    if not key or not _KEY.fullmatch(str(key)):
        return
    folder = _workspace_dir(tenant_id)
    path = (folder / key).resolve()
    if folder in path.parents:
        path.unlink(missing_ok=True)
