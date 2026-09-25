import { useMemo, useState } from "react";

import { useDatabaseSchema } from "@/api/hooks";
import type { DatabaseSchema, SchemaTable } from "@/api/types";
import { BlockTitle, Card, PageTitle, Tag } from "@/components/pieces";
import { Section } from "@/components/Section";
import { Row, Table, TableHead, Td, Th } from "@/components/Table";
import { useTitle } from "@/layout/useTitle";
import { fileSize, integer, percent } from "@/lib/format";
import {
  BOX_WIDTH,
  HEADER_HEIGHT,
  SUBHEADER_HEIGHT,
  layoutSchema,
  totalBytes,
} from "@/lib/schemaLayout";
import { cn } from "@/lib/utils";

const TITLE = "Base de datos";
const UNMEASURED =
  "El SQLite de este servidor no trae dbstat, así que el tamaño de cada tabla no se puede medir.";

/**
 * The database's shape: what hangs off what, and what each table weighs.
 *
 * It depends on no profile: it is the file every experiment writes to. Read
 * from the same read-only connection as every other screen, so looking cannot
 * change anything.
 *
 * Three views of one answer, and each one is there for a different question:
 * the diagram for "what does a delete take with it", the weight table for
 * "where is the space going", and the column list for "what exactly is stored".
 * The diagram has its table view like every chart (DESIGN.md): the relations
 * listed row by row, for whoever cannot or does not want to follow a line.
 *
 * @return The rendered screen.
 */
export function Database() {
  useTitle(TITLE);
  const schema = useDatabaseSchema();

  return (
    <>
      <PageTitle
        aside={
          schema.data &&
          `${schema.data.tables.length} tablas · ${fileSize(schema.data.file_bytes)}`
        }
      >
        {TITLE}
      </PageTitle>
      <Section query={schema}>{(data) => <SchemaView data={data} />}</Section>
    </>
  );
}

/**
 * The three views, once the schema has arrived.
 *
 * @param props - View props.
 * @param props.data - The schema as the API sent it.
 * @return The rendered views.
 */
function SchemaView({ data }: { data: DatabaseSchema }) {
  const layout = useMemo(() => layoutSchema(data.tables), [data.tables]);
  const measured = data.tables.some((t) => totalBytes(t) !== null);

  return (
    <div className="flex flex-col gap-8">
      <Card padding="p-6">
        <BlockTitle as="h2" className="mb-4">
          Relaciones
        </BlockTitle>
        <Diagram layout={layout} />
      </Card>

      <Card padding="p-6">
        <BlockTitle as="h2" className="mb-4">
          Peso de cada tabla
        </BlockTitle>
        {!measured && <p className="mb-3 text-caption text-text-muted">{UNMEASURED}</p>}
        <WeightTable tables={data.tables} />
      </Card>

      <Card padding="p-6">
        <BlockTitle as="h2" className="mb-4">
          Relaciones, una por fila
        </BlockTitle>
        <RelationsTable tables={data.tables} />
      </Card>

      <section className="flex flex-col gap-3">
        <BlockTitle as="h2">Columnas</BlockTitle>
        <div className="grid gap-3 lg:grid-cols-2">
          {[...data.tables]
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((table) => (
              <ColumnsCard key={table.name} table={table} />
            ))}
        </div>
      </section>
    </div>
  );
}

/**
 * The entity-relationship diagram, drawn in SVG.
 *
 * Hovering or focusing a table highlights its lines and dims the rest: with 20
 * tables and 25 relations, following one line across the others by eye is the
 * whole difficulty of reading it.
 *
 * @param props - Diagram props.
 * @param props.layout - Boxes and edges from `layoutSchema`.
 * @return The rendered diagram.
 */
function Diagram({ layout }: { layout: ReturnType<typeof layoutSchema> }) {
  const [active, setActive] = useState<string | null>(null);
  const touches = (edge: { from: string; to: string }) =>
    active !== null && (edge.from === active || edge.to === active);

  return (
    <div className="overflow-x-auto">
      <svg
        role="img"
        aria-label="Diagrama de relaciones entre las tablas: cada línea va de la tabla que tiene la clave foránea a la tabla a la que apunta."
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        className="block"
      >
        {layout.edges.map((edge) => (
          <path
            key={`${edge.from}.${edge.column}`}
            d={edge.path}
            fill="none"
            stroke={touches(edge) ? "var(--color-accent)" : "var(--color-axis)"}
            strokeWidth={touches(edge) ? 2 : 1.25}
            opacity={active === null || touches(edge) ? 1 : 0.25}
          >
            <title>{`${edge.from}.${edge.column} → ${edge.to} (al borrar: ${edge.onDelete})`}</title>
          </path>
        ))}

        {layout.boxes.map((box) => {
          const dimmed =
            active !== null &&
            active !== box.table.name &&
            !layout.edges.some(
              (e) =>
                (e.from === active && e.to === box.table.name) ||
                (e.to === active && e.from === box.table.name),
            );
          return (
            <g
              key={box.table.name}
              transform={`translate(${box.x} ${box.y})`}
              opacity={dimmed ? 0.35 : 1}
              tabIndex={0}
              onMouseEnter={() => setActive(box.table.name)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(box.table.name)}
              onBlur={() => setActive(null)}
              className="cursor-default outline-none"
            >
              <title>{`${box.table.name}: ${integer(box.table.rows)} filas, ${fileSize(totalBytes(box.table))}`}</title>
              <rect
                width={BOX_WIDTH}
                height={box.height}
                rx={8}
                fill="var(--color-card)"
                stroke={active === box.table.name ? "var(--color-accent)" : "var(--color-border-strong)"}
              />
              <path
                d={`M 0 8 a 8 8 0 0 1 8 -8 h ${BOX_WIDTH - 16} a 8 8 0 0 1 8 8 v ${HEADER_HEIGHT - 8} h ${-BOX_WIDTH} z`}
                fill={box.table.writable_by_api ? "var(--color-text-secondary)" : "var(--color-primary)"}
              />
              <text
                x={12}
                y={HEADER_HEIGHT / 2 + 5}
                fill="var(--color-primary-foreground)"
                className="font-mono text-caption"
              >
                {box.table.name}
              </text>
              <text
                x={12}
                y={HEADER_HEIGHT + SUBHEADER_HEIGHT / 2 + 4}
                fill="var(--color-text-muted)"
                className="text-caption"
              >
                {`${integer(box.table.rows)} filas · ${fileSize(totalBytes(box.table))}`}
              </text>
              {box.rows.map((row) => (
                <text
                  key={row.column}
                  x={12}
                  y={row.y - box.y + 4}
                  fill="var(--color-foreground)"
                  className="font-mono text-caption"
                >
                  <tspan fill={row.kind === "pk" ? "var(--color-warning)" : "var(--color-info-ink)"}>
                    {row.kind === "pk" ? "PK " : "FK "}
                  </tspan>
                  {row.column}
                </text>
              ))}
            </g>
          );
        })}
      </svg>
      <p className="mt-3 text-caption text-text-muted">
        <span className="font-mono">PK</span> clave primaria ·{" "}
        <span className="font-mono">FK</span> clave foránea · cabecera gris: tabla de
        configuración, la única que la API puede escribir · la línea va de la tabla hija a la que
        apunta
      </p>
    </div>
  );
}

/**
 * Rows and size of each table, heaviest first, with its share of the file.
 *
 * @param props - Table props.
 * @param props.tables - The schema's tables.
 * @return The rendered table.
 */
function WeightTable({ tables }: { tables: SchemaTable[] }) {
  const sorted = [...tables].sort(
    (a, b) => (totalBytes(b) ?? b.rows) - (totalBytes(a) ?? a.rows) || a.name.localeCompare(b.name),
  );
  const whole = tables.reduce((sum, t) => sum + (totalBytes(t) ?? 0), 0);

  return (
    <Table title="Filas y tamaño en disco de cada tabla, de la que más pesa a la que menos">
      <TableHead>
        <Th>Tabla</Th>
        <Th numeric>Filas</Th>
        <Th numeric>Datos</Th>
        <Th numeric>Índices</Th>
        <Th numeric>Total</Th>
        <Th>Peso</Th>
      </TableHead>
      <tbody>
        {sorted.map((table) => {
          const total = totalBytes(table);
          const share = total !== null && whole > 0 ? (total / whole) * 100 : null;
          return (
            <Row key={table.name}>
              <Td header>
                <span className="font-mono">{table.name}</span>
                {table.writable_by_api && (
                  <Tag tone="neutral" title="La API puede escribir en esta tabla: es configuración, no histórico">
                    config
                  </Tag>
                )}
              </Td>
              <Td numeric>{integer(table.rows)}</Td>
              <Td numeric>{fileSize(table.table_bytes)}</Td>
              <Td numeric>{fileSize(table.index_bytes)}</Td>
              <Td numeric>{fileSize(total)}</Td>
              <Td>
                {share === null ? (
                  <span className="text-caption text-text-muted">—</span>
                ) : (
                  <div className="flex items-center gap-2">
                    <div className="h-2 w-32 rounded-full bg-surface-sunken" aria-hidden>
                      <div
                        className="h-2 rounded-full bg-series-1"
                        style={{ width: `${Math.max(share, 0.5)}%` }}
                      />
                    </div>
                    <span className="tabular text-caption text-text-secondary">
                      {percent(share)}
                    </span>
                  </div>
                )}
              </Td>
            </Row>
          );
        })}
      </tbody>
    </Table>
  );
}

/**
 * Every foreign key as a row: the diagram's table view.
 *
 * @param props - Table props.
 * @param props.tables - The schema's tables.
 * @return The rendered table.
 */
function RelationsTable({ tables }: { tables: SchemaTable[] }) {
  const relations = tables
    .flatMap((t) => t.foreign_keys.map((fk) => ({ table: t.name, ...fk })))
    .sort((a, b) => a.table.localeCompare(b.table) || a.column.localeCompare(b.column));

  return (
    <Table title="Claves foráneas: qué columna de qué tabla apunta a cuál, y qué pasa al borrar">
      <TableHead>
        <Th>Tabla</Th>
        <Th>Columna</Th>
        <Th>Apunta a</Th>
        <Th>Al borrar</Th>
      </TableHead>
      <tbody>
        {relations.map((r) => (
          <Row key={`${r.table}.${r.column}`}>
            <Td header>
              <span className="font-mono">{r.table}</span>
            </Td>
            <Td>
              <span className="font-mono">{r.column}</span>
            </Td>
            <Td>
              <span className="font-mono">
                {r.references_table}.{r.references_column}
              </span>
            </Td>
            <Td>
              <span
                className={cn(
                  "text-caption",
                  r.on_delete === "CASCADE" ? "text-warning" : "text-text-secondary",
                )}
              >
                {r.on_delete.toLowerCase()}
              </span>
            </Td>
          </Row>
        ))}
      </tbody>
    </Table>
  );
}

/**
 * One table's full column list, folded.
 *
 * @param props - Card props.
 * @param props.table - The table.
 * @return The rendered card.
 */
function ColumnsCard({ table }: { table: SchemaTable }) {
  return (
    <Card padding="p-4">
      <details>
        <summary className="cursor-pointer text-body-sm">
          <span className="font-mono font-medium">{table.name}</span>
          <span className="text-text-muted">
            {` · ${table.columns.length} columnas · ${integer(table.rows)} filas`}
          </span>
        </summary>
        <ul className="mt-3 flex flex-col gap-1">
          {table.columns.map((column) => (
            <li key={column.name} className="flex flex-wrap items-baseline gap-x-2 text-caption">
              <span className="font-mono text-foreground">{column.name}</span>
              <span className="font-mono text-text-muted">{column.type || "sin tipo"}</span>
              {column.primary_key && <span className="text-warning">PK</span>}
              {column.not_null && !column.primary_key && (
                <span className="text-text-secondary">not null</span>
              )}
              {column.default !== null && column.default !== undefined && (
                <span className="text-text-muted">= {column.default}</span>
              )}
            </li>
          ))}
        </ul>
      </details>
    </Card>
  );
}
