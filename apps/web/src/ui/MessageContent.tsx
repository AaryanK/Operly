import React from "react";

function inline(text: string, keyPrefix: string): React.ReactNode[] {
  const pattern = /(https?:\/\/[^\s)]+)|(`[^`]+`)|(\*\*[^*]+\*\*)/g;
  const result: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = pattern.exec(text))) {
    if (match.index > cursor) result.push(text.slice(cursor, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${index++}`;
    if (token.startsWith("http")) result.push(<a key={key} href={token} target="_blank" rel="noreferrer">{token}</a>);
    else if (token.startsWith("`")) result.push(<code key={key}>{token.slice(1, -1)}</code>);
    else result.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    cursor = match.index + token.length;
  }
  if (cursor < text.length) result.push(text.slice(cursor));
  return result;
}

type Block = { kind: "code"; value: string; language: string } | { kind: "line"; value: string };

function blocks(value: string): Block[] {
  const lines = String(value || "").replace(/\r\n/g, "\n").split("\n");
  const output: Block[] = [];
  let code: string[] | null = null;
  let language = "";
  for (const line of lines) {
    if (line.trimStart().startsWith("```")) {
      if (code) { output.push({ kind: "code", value: code.join("\n"), language }); code = null; language = ""; }
      else { code = []; language = line.trim().slice(3).trim(); }
      continue;
    }
    if (code) code.push(line);
    else output.push({ kind: "line", value: line });
  }
  if (code) output.push({ kind: "code", value: code.join("\n"), language });
  return output;
}

export function MessageContent({ content }: { content: string }) {
  const parsed = blocks(content);
  const nodes: React.ReactNode[] = [];
  let paragraph: string[] = [];
  let bullets: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const value = paragraph.join(" ").trim();
    if (value) nodes.push(<p key={`p-${nodes.length}`}>{inline(value, `p-${nodes.length}`)}</p>);
    paragraph = [];
  };
  const flushBullets = () => {
    if (!bullets.length) return;
    nodes.push(<ul key={`ul-${nodes.length}`}>{bullets.map((item, index) => <li key={index}>{inline(item, `li-${nodes.length}-${index}`)}</li>)}</ul>);
    bullets = [];
  };

  parsed.forEach((block) => {
    if (block.kind === "code") {
      flushParagraph(); flushBullets();
      nodes.push(<pre key={`code-${nodes.length}`} data-language={block.language || undefined}><code>{block.value}</code></pre>);
      return;
    }
    const line = block.value;
    if (!line.trim()) { flushParagraph(); flushBullets(); return; }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph(); flushBullets();
      const level = Math.min(4, heading[1].length + 2) as 3 | 4;
      nodes.push(React.createElement(`h${level}`, { key: `h-${nodes.length}` }, inline(heading[2], `h-${nodes.length}`)));
      return;
    }
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) { flushParagraph(); bullets.push(bullet[1]); return; }
    flushBullets(); paragraph.push(line.trim());
  });
  flushParagraph(); flushBullets();

  return <div className="message-content">{nodes}</div>;
}
