import re

from packages.channels.presentation import format_for_channel


FORMATS = {
    "json",
    "csv",
    "xlsx",
    "docx",
    "pdf",
    "markdown",
    "md",
    "txt",
    "text",
    "message",
}


def requested_format(request):
    value = request.lower()
    patterns = [
        ("xlsx", r"\b(?:xlsx|excel|spreadsheet)\b"),
        ("docx", r"\b(?:docx|word document)\b"),
        ("pdf", r"\bpdf\b"),
        ("json", r"\bjson\b"),
        ("csv", r"\bcsv\b"),
        ("markdown", r"\bmarkdown\b"),
        ("txt", r"\b(?:txt|plain text)\b"),
    ]
    for fmt, pattern in patterns:
        if re.search(pattern, value):
            return fmt
    return "message"


def operation(request):
    text = request.lower()
    for name, words in [
        ("compare", ("compare", "difference", "inconsisten")),
        ("combine", ("combine", "merge")),
        ("extract_tables", ("table",)),
        ("extract_structured_fields", ("extract", "fields", "details", "information")),
        ("extract_visible_text", ("transcribe", "visible text", "ocr")),
        ("classify", ("classify",)),
        ("convert", ("convert",)),
        ("describe", ("describe",)),
        ("answer_questions", ("question", "which", "what", "who", "when", "where", "why", "how")),
    ]:
        if any(word in text for word in words):
            return name
    return "summarize"


def split_discord_text(text, limit=1900):
    remaining = format_for_channel(text or "Done.", "discord")
    if not remaining:
        return ["Done."]

    chunks = []
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < 1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def processing_manifest(accepted, skipped):
    lines = [f"Processing {len(accepted) + len(skipped)} attachments:"]
    lines += [f"✓ {item}" for item in accepted]
    lines += [f"✗ {item}" for item in skipped]
    return "\n".join(lines)
