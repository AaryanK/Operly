import React from "react";

function inline(text: string, keyPrefix: string): React.ReactNode[] {
  const pattern = /(https?:\/\/[^\s<]+)|(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(~~[^~\n]+~~)|(\*[^*\n]+\*)/g;
  const result: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = pattern.exec(text))) {
    if (match.index > cursor) result.push(text.slice(cursor, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${index++}`;
    if (token.startsWith("http")) {
      result.push(<a key={key} href={token} target="_blank" rel="noreferrer">{token}</a>);
    } else if (token.startsWith("`")) {
      result.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      result.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("~~")) {
      result.push(<del key={key}>{token.slice(2, -2)}</del>);
    } else {
      result.push(<em key={key}>{token.slice(1, -1)}</em>);
    }
    cursor = match.index + token.length;
  }

  if (cursor < text.length) result.push(text.slice(cursor));
  return result;
}

type Block =
  | { kind: "code"; value: string; language: string }
  | { kind: "line"; value: string };

function blocks(value: string): Block[] {
  const lines = String(value || "").replace(/\r\n/g, "\n").split("\n");
  const output: Block[] = [];
  let code: string[] | null = null;
  let language = "";

  for (const line of lines) {
    if (line.trimStart().startsWith("```")) {
      if (code) {
        output.push({ kind: "code", value: code.join("\n"), language });
        code = null;
        language = "";
      } else {
        code = [];
        language = line.trim().slice(3).trim();
      }
      continue;
    }
    if (code) code.push(line);
    else output.push({ kind: "line", value: line });
  }

  if (code) output.push({ kind: "code", value: code.join("\n"), language });
  return output;
}

function tableCells(line: string): string[] | null {
  if (!line.includes("|")) return null;
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  const cells = trimmed.split("|").map((cell) => cell.trim());
  return cells.length >= 2 ? cells : null;
}

function isTableSeparator(line: string): boolean {
  const cells = tableCells(line);
  return !!cells?.length && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, "")));
}

export function MessageContent({ content }: { content: string }) {
  const parsed = blocks(content);
  const nodes: React.ReactNode[] = [];
  let paragraph: string[] = [];
  let bullets: string[] = [];
  let ordered: string[] = [];
  let quotes: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const value = paragraph.join(" ").trim();
    if (value) nodes.push(<p key={`p-${nodes.length}`}>{inline(value, `p-${nodes.length}`)}</p>);
    paragraph = [];
  };

  const flushBullets = () => {
    if (!bullets.length) return;
    nodes.push(
      <ul key={`ul-${nodes.length}`}>
        {bullets.map((item, index) => <li key={index}>{inline(item, `li-${nodes.length}-${index}`)}</li>)}
      </ul>,
    );
    bullets = [];
  };

  const flushOrdered = () => {
    if (!ordered.length) return;
    nodes.push(
      <ol key={`ol-${nodes.length}`}>
        {ordered.map((item, index) => <li key={index}>{inline(item, `oli-${nodes.length}-${index}`)}</li>)}
      </ol>,
    );
    ordered = [];
  };

  const flushQuotes = () => {
    if (!quotes.length) return;
    nodes.push(
      <blockquote key={`quote-${nodes.length}`}>
        {quotes.map((item, index) => <p key={index}>{inline(item, `quote-${nodes.length}-${index}`)}</p>)}
      </blockquote>,
    );
    quotes = [];
  };

  const flushText = () => {
    flushParagraph();
    flushBullets();
    flushOrdered();
    flushQuotes();
  };

  for (let index = 0; index < parsed.length; index += 1) {
    const block = parsed[index];

    if (block.kind === "code") {
      flushText();
      nodes.push(
        <pre key={`code-${nodes.length}`} data-language={block.language || undefined}>
          <code>{block.value}</code>
        </pre>,
      );
      continue;
    }

    const line = block.value;
    if (!line.trim()) {
      flushText();
      continue;
    }

    const next = parsed[index + 1];
    const headerCells = tableCells(line);
    if (headerCells && next?.kind === "line" && isTableSeparator(next.value)) {
      flushText();
      const body: string[][] = [];
      index += 2;
      while (index < parsed.length) {
        const row = parsed[index];
        if (row.kind !== "line" || !row.value.trim()) {
          index -= 1;
          break;
        }
        const cells = tableCells(row.value);
        if (!cells) {
          index -= 1;
          break;
        }
        body.push(cells);
        index += 1;
      }
      if (index >= parsed.length) index = parsed.length - 1;

      nodes.push(
        <div className="message-table-wrap" key={`table-${nodes.length}`}>
          <table>
            <thead>
              <tr>{headerCells.map((cell, cellIndex) => <th key={cellIndex}>{inline(cell, `th-${nodes.length}-${cellIndex}`)}</th>)}</tr>
            </thead>
            <tbody>
              {body.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {headerCells.map((_, cellIndex) => (
                    <td key={cellIndex}>{inline(row[cellIndex] || "", `td-${nodes.length}-${rowIndex}-${cellIndex}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushText();
      const level = Math.min(4, heading[1].length + 2) as 3 | 4;
      nodes.push(React.createElement(`h${level}`, { key: `h-${nodes.length}` }, inline(heading[2], `h-${nodes.length}`)));
      continue;
    }

    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushText();
      nodes.push(<hr key={`hr-${nodes.length}`} />);
      continue;
    }

    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      flushBullets();
      flushOrdered();
      quotes.push(quote[1]);
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      flushOrdered();
      flushQuotes();
      bullets.push(bullet[1]);
      continue;
    }

    const orderedItem = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (orderedItem) {
      flushParagraph();
      flushBullets();
      flushQuotes();
      ordered.push(orderedItem[1]);
      continue;
    }

    flushBullets();
    flushOrdered();
    flushQuotes();
    paragraph.push(line.trim());
  }

  flushText();
  return <div className="message-content">{nodes}</div>;
}
