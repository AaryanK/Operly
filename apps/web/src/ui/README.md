# React UI presentation

The React frontend owns web rendering for canonical `/channels/**` surfaces.

- `MessageContent.tsx` renders canonical assistant Markdown into safe React elements. It supports paragraphs, emphasis, links, lists, headings, quotes, code fences, horizontal rules, and GFM-style tables without injecting HTML.
- `messages.css` styles structured assistant output, including horizontally scrollable tables on narrow screens.
- `premium.css` is the canonical cosmetic layer for the React product. It does not participate in the legacy static frontend cascade.
- Chat layouts are viewport applications: the transcript scrolls independently while the composer remains visible.

Channel adapters should not ask the model to produce completely different semantics. Instead, adapter presentation belongs in `packages/channels/presentation.py`. For example, Discord receives canonical Markdown with unsupported GFM tables converted to readable labeled records before Discord message chunking.
