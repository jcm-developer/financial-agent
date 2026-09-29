import { Fragment } from "react";
import { Link } from "react-router";

import { Block, LINK_CLASSES } from "@/components/pieces";
import { Row, Table, TableHead, Td, Th } from "@/components/Table";
import { type Inline, parseMarkdown } from "@/lib/markdown";
import { cn } from "@/lib/utils";

const HEADING_CLASSES: Record<number, string> = {
  1: "text-h3",
  2: "text-h4",
  3: "text-h4",
  4: "text-body font-medium",
};

/**
 * One line's inline pieces.
 *
 * A citation links to the symbol's decisions and not to one decision: the model
 * cites by symbol and day, and the Decisions filter by symbol shows that day's
 * row in its cycle, with the rest of the symbol's history around it.
 *
 * @param props - Inline props.
 * @param props.content - The pieces, from `parseInline`.
 * @param props.profile - Profile name, for the citation links.
 * @return The rendered pieces.
 */
function InlineText({ content, profile }: { content: Inline[]; profile: string }) {
  return (
    <>
      {content.map((piece, index) => {
        if (piece.kind === "bold") {
          return <strong key={index} className="font-semibold">{piece.text}</strong>;
        }
        if (piece.kind === "code") {
          return <code key={index} className="font-mono text-caption">{piece.text}</code>;
        }
        if (piece.kind === "citation") {
          return (
            <Link
              key={index}
              to={`/p/${encodeURIComponent(profile)}/decisions?symbol=${encodeURIComponent(piece.symbol)}`}
              className={cn(LINK_CLASSES, "whitespace-nowrap")}
              title={`Ver las decisiones de ${piece.symbol}`}
            >
              {piece.symbol} {piece.date}
            </Link>
          );
        }
        return <Fragment key={index}>{piece.text}</Fragment>;
      })}
    </>
  );
}

/**
 * A chat answer, drawn from the blocks `parseMarkdown` returns.
 *
 * Everything becomes a React element, never HTML: the text is the model's, so
 * nothing in it may be interpreted as markup.
 *
 * @param props - Markdown props.
 * @param props.source - The answer as the model wrote it.
 * @param props.profile - Profile name, for the citation links.
 * @return The rendered answer.
 */
export function Markdown({ source, profile }: { source: string; profile: string }) {
  return (
    <div className="flex flex-col gap-3 text-body-sm">
      {parseMarkdown(source).map((block, index) => {
        switch (block.kind) {
          case "heading":
            return (
              <p key={index} className={HEADING_CLASSES[block.level] ?? "text-h4"}>
                <InlineText content={block.content} profile={profile} />
              </p>
            );
          case "list": {
            const ListTag = block.ordered ? "ol" : "ul";
            return (
              <ListTag
                key={index}
                className={cn(
                  "flex flex-col gap-1 pl-5",
                  block.ordered ? "list-decimal" : "list-disc",
                )}
              >
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>
                    <InlineText content={item} profile={profile} />
                  </li>
                ))}
              </ListTag>
            );
          }
          case "table":
            return (
              <Table key={index} title="Tabla de la respuesta">
                <TableHead>
                  {block.header.map((cell, cellIndex) => (
                    <Th key={cellIndex}>
                      <InlineText content={cell} profile={profile} />
                    </Th>
                  ))}
                </TableHead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <Row key={rowIndex}>
                      {row.map((cell, cellIndex) => (
                        <Td key={cellIndex} header={cellIndex === 0}>
                          <InlineText content={cell} profile={profile} />
                        </Td>
                      ))}
                    </Row>
                  ))}
                </tbody>
              </Table>
            );
          case "code":
            return (
<Block key={index}>{block.text}</Block>
            );
          default:
            return (
              <p key={index}>
                <InlineText content={block.content} profile={profile} />
              </p>
            );
        }
      })}
    </div>
  );
}
