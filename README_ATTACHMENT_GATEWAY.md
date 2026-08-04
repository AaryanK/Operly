# OPERLY Discord Multimodal Gateway

The shared Discord adapter accepts bounded images, documents, spreadsheets,
presentations, inert text/source files, and ZIP archives. Files are detected by
content signature, parsed without execution, analyzed through the existing
server-side Ollama client, and returned as Discord text or deterministic output
files.

## Limits

```env
OPERLY_MAX_ATTACHMENTS=10
OPERLY_MAX_ATTACHMENT_MB=10
OPERLY_MAX_TOTAL_ATTACHMENT_MB=50
OPERLY_MAX_PDF_PAGES=100
OPERLY_MAX_ARCHIVE_FILES=50
OPERLY_MAX_ARCHIVE_EXPANDED_MB=100
OPERLY_ATTACHMENT_TIMEOUT_SECONDS=300
```

These variables are optional; the shown values are the defaults. Existing
`OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_API_KEY`, `DATABASE_URL`, and
`DISCORD_BOT_TOKEN` settings remain unchanged.

## Supported inputs

- JPEG, PNG, WebP, GIF (first frame)
- PDF, DOCX, PPTX, XLSX
- ODT and ODS where `odfpy` can extract text
- TXT, Markdown, CSV, TSV, JSON, XML, YAML, HTML as inert text
- common source code and log files as inert text
- ZIP archives containing supported files

Executable formats, macro-enabled Office documents, embedded executable Office
objects, unknown binaries, unsafe archive expansion, and mismatched Office
containers are rejected.

## Outputs

Discord messages, Markdown, TXT, JSON, CSV, XLSX, DOCX, and PDF are supported.
Generated files are created in a temporary directory and removed after Discord
uploads them. CSV and XLSX cells beginning with formula characters are prefixed
to prevent execution when opened in spreadsheet software.

## Privacy

Raw bytes and extracted sensitive document contents are not stored in normal
agent history. `attachment_audits` contains tenant/user/message scope, hashes,
safe filenames, detected categories, outcome, and bounded error category only.
