"""Presentation-only transforms for channel adapters.

The model is allowed to answer in normal Markdown. Each delivery surface is then
responsible for presenting that Markdown in a way the client actually supports.
This keeps model semantics shared while avoiding raw syntax leaks on adapters
such as Discord, which does not render GitHub-style Markdown tables.
"""
from __future__ import annotations

import re

_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


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


def _discord_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    rendered: list[str] = []
    for row_index, row in enumerate(rows, 1):
        values = row + [""] * max(0, len(headers) - len(row))
        first_header = headers[0].strip() if headers else "Item"
        first_value = values[0].strip() if values else ""
        if first_value:
            rendered.append(f"**{first_value}**")
        else:
            rendered.append(f"**Row {row_index}**")

        for column_index, header in enumerate(headers[1:], 1):
            label = header.strip() or f"Column {column_index + 1}"
            value = values[column_index].strip() if column_index < len(values) else ""
            rendered.append(f"• **{label}:** {value or '—'}")

        if len(headers) == 1:
            rendered.append(f"• **{first_header}:** {first_value or '—'}")
        if row_index != len(rows):
            rendered.append("")
    return rendered


def discord_markdown(text: str) -> str:
    """Adapt Markdown to Discord without changing the underlying answer meaning.

    Discord supports emphasis, lists, links and code fences but not Markdown
    tables. GFM table blocks are therefore rendered as compact labeled records.
    Code fences are preserved verbatim.
    """
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
                    output.extend(_discord_table(headers, rows))
                    index = cursor
                    continue

        output.append(line)
        index += 1

    return "\n".join(output).strip()


def format_for_channel(text: str, channel: str) -> str:
    """Return presentation-safe text for a delivery channel.

    Web clients receive canonical Markdown and render it structurally. Discord
    receives the same Markdown with unsupported table syntax normalized.
    Unknown/future channels are left unchanged until they define a renderer.
    """
    normalized_channel = str(channel or "").strip().lower()
    if normalized_channel == "discord":
        return discord_markdown(text)
    return str(text or "")
