import { Fragment, type ReactNode } from "react";

export function reportInlineText(value: string): string {
  const entities: Record<string, string> = {
    "&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": "\"", "&#39;": "'"
  };
  // 한 번만 해제해 &amp;lt; 같은 재귀 엔티티를 다시 해석하지 않는다.
  return value
    .replace(/&(lt|gt|amp|quot|#39);/g, (match) => entities[match] ?? match)
    .replace(/\\([\\`*{}\[\]()#+.!|>-])/g, "$1")
    .replace(/\\\[|\\\]/g, "")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*([^*\n]+)\*/g, "$1");
}

function tableCells(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => reportInlineText(cell.trim()));
}

function isSeparator(line: string): boolean {
  return /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

export function MarkdownReport({ markdown }: { markdown: string }) {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  let key = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.includes("|") && index + 1 < lines.length && isSeparator(lines[index + 1])) {
      const header = tableCells(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      blocks.push(<div className="table-wrap report-table" key={`b-${key++}`}><table><thead><tr>{header.map((cell, cellIndex) => <th key={cellIndex}>{cell}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      const content = reportInlineText(heading[2]);
      blocks.push(heading[1].length <= 2 ? <h2 key={`b-${key++}`}>{content}</h2> : <h3 key={`b-${key++}`}>{content}</h3>);
      index += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(reportInlineText(lines[index].replace(/^\s*[-*]\s+/, "")));
        index += 1;
      }
      blocks.push(<ul key={`b-${key++}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul>);
      continue;
    }
    if (line.trim()) blocks.push(<p key={`b-${key++}`}>{reportInlineText(line)}</p>);
    else blocks.push(<Fragment key={`b-${key++}`}><span className="report-space" aria-hidden="true" /></Fragment>);
    index += 1;
  }
  return <div className="rendered-report">{blocks}</div>;
}
