/**
 * The small Markdown the chat's answers are written in, parsed into blocks.
 *
 * **Parsed here and not by a library, and never into HTML.** The model is asked
 * for lists and tables when they help, and that is the whole grammar needed:
 * headings, paragraphs, bullet and numbered lists, tables and code fences, with
 * bold and inline code inside. A Markdown package would bring a second renderer
 * and, with it, the temptation of `dangerouslySetInnerHTML` over text a model
 * wrote. This returns plain data and `<Markdown>` draws it with React elements,
 * so whatever the model writes can only ever be text.
 *
 * It also finds the **citations** the prompt asks for —`[SAP.DE 2026-09-29]`—
 * so the screen can turn each into a link to that symbol's decisions.
 */

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "bold"; text: string }
  | { kind: "code"; text: string }
  | { kind: "citation"; symbol: string; date: string };

export type MarkdownBlock =
  | { kind: "heading"; level: number; content: Inline[] }
  | { kind: "paragraph"; content: Inline[] }
  | { kind: "list"; ordered: boolean; items: Inline[][] }
  | { kind: "table"; header: Inline[][]; rows: Inline[][][] }
  | { kind: "code"; text: string };

const INLINE = /\*\*([^*]+)\*\*|`([^`]+)`|\[([A-Z0-9][A-Z0-9.\-]*) (\d{4}-\d{2}-\d{2})\]/g;
const HEADING = /^(#{1,4})\s+(.*)$/;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

/**
 * Splits one line of text into its inline pieces.
 *
 * @param text - A line, or the cell of a table.
 * @return The pieces in order. Plain text is kept whole between the marks.
 */
export function parseInline(text: string): Inline[] {
  const pieces: Inline[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    if (at > last) pieces.push({ kind: "text", text: text.slice(last, at) });
    const [, bold, code, symbol = "", date = ""] = match;
    if (bold !== undefined) pieces.push({ kind: "bold", text: bold });
    else if (code !== undefined) pieces.push({ kind: "code", text: code });
    else pieces.push({ kind: "citation", symbol, date });
    last = at + match[0].length;
  }
  if (last < text.length) pieces.push({ kind: "text", text: text.slice(last) });
  return pieces;
}

/**
 * @param line - One row of a Markdown table.
 * @return Its cells, trimmed, without the outer pipes.
 */
function cells(line: string): Inline[][] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => parseInline(cell.trim()));
}

/**
 * Parses an answer into blocks.
 *
 * Unknown syntax is not an error: it stays as the text it is. A model that
 * writes `> quote` gets a paragraph beginning with `>`, which is still readable.
 *
 * @param source - The answer as the model wrote it.
 * @return The blocks in order.
 */
export function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  // Past the end reads as a blank line, which ends whatever block was open.
  const at = (index: number) => lines[index] ?? "";
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length) {
      blocks.push({ kind: "paragraph", content: parseInline(paragraph.join(" ")) });
      paragraph = [];
    }
  };

  for (let index = 0; index < lines.length; index++) {
    const line = at(index);
    const trimmed = line.trim();

    if (trimmed.startsWith("```")) {
      flush();
      const body: string[] = [];
      index++;
      while (index < lines.length && !at(index).trim().startsWith("```")) {
        body.push(at(index));
        index++;
      }
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }

    if (!trimmed) {
      flush();
      continue;
    }

    const heading = HEADING.exec(trimmed);
    if (heading) {
      flush();
      blocks.push({
        kind: "heading",
        level: (heading[1] ?? "").length,
        content: parseInline(heading[2] ?? ""),
      });
      continue;
    }

    if (trimmed.startsWith("|") && TABLE_SEPARATOR.test(at(index + 1))) {
      flush();
      const header = cells(trimmed);
      const rows: Inline[][][] = [];
      index += 2;
      while (index < lines.length && at(index).trim().startsWith("|")) {
        rows.push(cells(at(index)));
        index++;
      }
      index--;
      blocks.push({ kind: "table", header, rows });
      continue;
    }

    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      flush();
      const ordered = Boolean(numbered);
      const pattern = ordered ? NUMBERED : BULLET;
      const items: Inline[][] = [];
      while (index < lines.length) {
        const item = pattern.exec(at(index));
        if (item) {
          items.push(parseInline(item[1] ?? ""));
        } else if (at(index).trim() && /^\s{2,}/.test(at(index)) && items.length) {
          // An indented continuation belongs to the item above it.
          items[items.length - 1]?.push({ kind: "text", text: ` ${at(index).trim()}` });
        } else {
          break;
        }
        index++;
      }
      index--;
      blocks.push({ kind: "list", ordered, items });
      continue;
    }

    paragraph.push(trimmed);
  }
  flush();
  return blocks;
}
