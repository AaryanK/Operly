# Channel presentation examples

Canonical model output may contain a Markdown table:

```md
| Name | Email | Created |
| --- | --- | --- |
| Ada Lovelace | ada@example.com | Aug 23 |
```

The web renderer presents that as an actual responsive table. Discord does not support GFM tables, so its adapter presents the same information as:

```md
**Ada Lovelace**
• **Email:** ada@example.com
• **Created:** Aug 23
```

This is a presentation transform only. Stored model output and shared semantics remain canonical Markdown.
