"""Static source helpers for canonical SoftwareProject previews and deployments."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from packages.software_projects.source_bundle import normalized_path


def _bundle_path(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw or raw.startswith(("/", "//", "data:", "http://", "https://", "#")):
        return None
    raw = urlsplit(raw).path
    while raw.startswith("./"):
        raw = raw[2:]
    try:
        return normalized_path(raw)
    except Exception:
        return None


def _escape_style(text: str) -> str:
    return str(text or "").replace("</style", "<\\/style")


def _escape_script(text: str) -> str:
    return str(text or "").replace("</script", "<\\/script")


def inline_local_assets(html: str, records: dict[str, str]) -> str:
    """Inline local CSS/JS files from one immutable canonical source version."""
    source = str(html or "")

    def replace_link(match: re.Match) -> str:
        tag = match.group(0)
        if not re.search(r"(?i)\brel\s*=\s*(['\"]?)stylesheet\1", tag):
            return tag
        href = re.search(r"(?i)\bhref\s*=\s*(['\"])(.*?)\1", tag)
        if not href:
            return tag
        path = _bundle_path(href.group(2))
        if not path or path not in records:
            return tag
        return f'<style data-operly-inline-source="{path}">{_escape_style(records[path])}</style>'

    source = re.sub(r"(?is)<link\b[^>]*>", replace_link, source)

    def replace_script(match: re.Match) -> str:
        open_tag = match.group(1)
        body_close = match.group(2)
        src = re.search(r"(?i)\bsrc\s*=\s*(['\"])(.*?)\1", open_tag)
        if not src:
            return match.group(0)
        path = _bundle_path(src.group(2))
        if not path or path not in records:
            return match.group(0)
        type_attr = re.search(r"(?i)\btype\s*=\s*(['\"])(.*?)\1", open_tag)
        script_type = f' type="{type_attr.group(2)}"' if type_attr else ""
        return f'<script{script_type} data-operly-inline-source="{path}">{_escape_script(records[path])}</script>{body_close}'

    return re.sub(
        r"(?is)(<script\b[^>]*\bsrc\s*=\s*['\"][^'\"]+['\"][^>]*>)(.*?</script\s*>)",
        replace_script,
        source,
    )


def canonical_static_document(files: dict[str, str]) -> str:
    """Return a self-contained static document suitable for preview/deploy."""
    html = files.get("index.html")
    if html is None:
        raise ValueError("Static SoftwareProject source must contain index.html")
    return inline_local_assets(html, files)
