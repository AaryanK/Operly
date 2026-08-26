"""Connector presentation/tool contracts.

Agents should produce canonical Operly messages and artifacts.  A connector owns the
last-mile rules that make those objects native to its platform: text dialect, message
limits, HTML/MIME composition, attachment upload semantics, blocks/cards, and similar
transport details.  This keeps platform syntax out of business reasoning while still
making each connector's delivery abilities discoverable as a small deterministic tool
contract.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True, slots=True)
class ConnectorPresentationTools:
    provider: str
    text_dialect: str
    max_text_chars: int
    attachment_strategy: str
    supports_native_files: bool
    supports_html: bool = False
    supports_rich_blocks: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


# These are presentation/transport ceilings, not authorization.  Permission to read
# or send data is still decided by the connector/runtime security boundary.
_CONNECTOR_TOOLS: dict[str, ConnectorPresentationTools] = {
    "web": ConnectorPresentationTools(
        "web", "markdown", 24_000, "authenticated_artifact_link", True, supports_html=True, supports_rich_blocks=True
    ),
    "discord": ConnectorPresentationTools(
        "discord", "discord_markdown", 2_000, "native_attachment", True
    ),
    "slack": ConnectorPresentationTools(
        "slack", "slack_mrkdwn", 40_000, "native_file_upload", True, supports_rich_blocks=True
    ),
    "whatsapp": ConnectorPresentationTools(
        "whatsapp", "whatsapp_text", 4_096, "native_media", True
    ),
    "email": ConnectorPresentationTools(
        "email", "html", 1_000_000, "mime_attachment", True, supports_html=True
    ),
    "gmail": ConnectorPresentationTools(
        "gmail", "html", 1_000_000, "mime_attachment", True, supports_html=True
    ),
}

_DEFAULT_TOOLS = ConnectorPresentationTools(
    "unknown", "plain_text", 16_000, "authenticated_artifact_link", False
)


def connector_tools(provider: str) -> ConnectorPresentationTools:
    """Return the deterministic last-mile contract for one connector.

    New connectors should register an explicit contract here (or through a future
    plugin registry) rather than teaching the core agent platform-specific syntax.
    """
    key = str(provider or "").strip().lower()
    return _CONNECTOR_TOOLS.get(key, ConnectorPresentationTools(
        key or _DEFAULT_TOOLS.provider,
        _DEFAULT_TOOLS.text_dialect,
        _DEFAULT_TOOLS.max_text_chars,
        _DEFAULT_TOOLS.attachment_strategy,
        _DEFAULT_TOOLS.supports_native_files,
        supports_html=_DEFAULT_TOOLS.supports_html,
        supports_rich_blocks=_DEFAULT_TOOLS.supports_rich_blocks,
    ))


def connector_tool_context(provider: str) -> dict:
    """Small serializable contract safe to expose to an agent or adapter.

    This describes *how* a platform can present output. It never contains credentials,
    membership, channel ACLs, or permission claims.
    """
    return connector_tools(provider).as_dict()


def _cells(line: str) -> list[str] | None:
    if "|" not in line:
        return None
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    cells = [cell.strip() for cell in value.split("|")]
    return cells if len(cells) >= 2 else None


def _separator(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(_TABLE_SEPARATOR.fullmatch(cell.replace(" ", "")) for cell in cells)


def _record_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    rendered: list[str] = []
    for row_index, row in enumerate(rows, 1):
        values = row + [""] * max(0, len(headers) - len(row))
        first_header = headers[0].strip() if headers else "Item"
        first_value = values[0].strip() if values else ""
        rendered.append(f"**{first_value or f'Row {row_index}'}**")
        for column_index, header in enumerate(headers[1:], 1):
            label = header.strip() or f"Column {column_index + 1}"
            value = values[column_index].strip() if column_index < len(values) else ""
            rendered.append(f"• **{label}:** {value or '—'}")
        if len(headers) == 1:
            rendered.append(f"• **{first_header}:** {first_value or '—'}")
        if row_index != len(rows):
            rendered.append("")
    return rendered


def compact_markdown_tables(text: str) -> str:
    """Render GFM tables as labeled records for chat clients without table support."""
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    output: list[str] = []
    index = 0
    in_fence = False
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            output.append(line)
            index += 1
            continue
        if not in_fence and index + 1 < len(lines):
            headers = _cells(line)
            if headers and _separator(lines[index + 1]):
                rows: list[list[str]] = []
                cursor = index + 2
                while cursor < len(lines):
                    row_line = lines[cursor]
                    if not row_line.strip():
                        break
                    row = _cells(row_line)
                    if not row:
                        break
                    rows.append(row)
                    cursor += 1
                if rows:
                    output.extend(_record_table(headers, rows))
                    index = cursor
                    continue
        output.append(line)
        index += 1
    return "\n".join(output).strip()


def discord_markdown(text: str) -> str:
    return compact_markdown_tables(text)


def format_for_channel(text: str, channel: str) -> str:
    """Apply only deterministic last-mile transformations.

    Semantic content remains canonical. Discord/Slack/WhatsApp normalize unsupported
    table syntax here; email/Gmail composition tools are expected to build HTML/MIME
    from the canonical body instead of asking the model to handcraft MIME envelopes.
    """
    normalized_channel = str(channel or "").strip().lower()
    if normalized_channel in {"discord", "slack", "whatsapp"}:
        return compact_markdown_tables(text)
    return str(text or "")
