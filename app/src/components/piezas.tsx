import { createElement, type ComponentProps, type ComponentType, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Las piezas de interfaz compartidas.
 *
 * Existe porque la alternativa —copiar la cadena de clases en cada pantalla— ya
 * había empezado a divergir, y de una forma que no se ve mirando una pantalla sino
 * dos: el mismo botón llevaba `px-3 py-1` en la paginación, `px-2.5 py-1` en el
 * interruptor de tema y `px-3 py-1.5` cuando era un enlace; de los cuatro
 * desplegables, solo uno tenía el `hover`; y el mismo aviso de error salía con
 * `p-3` y fondo en una pantalla y con `p-2` y sin fondo en otra.
 *
 * La regla que impone DESIGN.md es que ninguna de estas recetas se vuelva a
 * escribir a mano. Si algo necesita una variante, se añade aquí.
 *
 * **No lleva colores propios**: todo sale de los tokens de `index.css`, que es lo
 * que hace que el interruptor de tema no tenga que tocar ningún componente.
 */

/* -------------------------------------------------------------------------- */
/* Botones                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * Los tres botones que existen, y no hay un cuarto sólido de marca a propósito:
 * en una pantalla de datos el relleno de color se reserva para las cifras, y un
 * botón azul competiría con las series de las gráficas por la misma atención.
 */
type VarianteBoton = "neutro" | "sutil" | "peligro";

const BOTON_BASE =
  "inline-flex min-h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-card px-3 py-1 text-[13px] transition-colors hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-card";

const BOTON_TONO: Record<VarianteBoton, string> = {
  neutro: "",
  sutil: "text-text-secondary",
  peligro: "text-delta-bad",
};

/**
 * Para cuando quien lleva la apariencia de botón es un `<Link>` del enrutador.
 *
 * Un botón que navega tiene que ser un enlace de verdad —abrir en otra pestaña,
 * copiar la dirección, verlo en la barra de estado—, así que en esos sitios se
 * comparte la apariencia y no el elemento.
 *
 * @param variante - Which of the three button tones to use.
 * @param className - Extra classes, merged so they win over the recipe.
 * @return The class string a `<Link>` needs to look like a button.
 */
export function clasesBoton(variante: VarianteBoton = "neutro", className?: string) {
  return cn(BOTON_BASE, BOTON_TONO[variante], className);
}

/**
 * The application's button.
 *
 * @param props - Button props, on top of everything a `<button>` accepts.
 * @param props.variante - Which of the three button tones to use.
 * @param props.icono - Decorative icon rendered before the label.
 * @return The rendered button.
 */
export function Boton({
  variante = "neutro",
  icono: Icono,
  className,
  children,
  ...resto
}: ComponentProps<"button"> & {
  variante?: VarianteBoton;
  /** Icono a la izquierda del texto. Siempre decorativo: lo que dice el botón es su texto. */
  icono?: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}) {
  return (
    <button type="button" className={clasesBoton(variante, className)} {...resto}>
      {Icono && <Icono className="size-3.5 shrink-0" aria-hidden />}
      {children}
    </button>
  );
}

/**
 * El subrayado tenue que llevan los enlaces dentro de tablas y textos.
 *
 * `decoration-border` en reposo y `decoration-current` al pasar por encima: el
 * subrayado está siempre —quitarlo dejaría el enlace distinguible solo por el
 * color, que es justo lo que F4.9 no permite— pero no compite con la cifra de al
 * lado hasta que se apunta a él.
 */
export const CLASES_ENLACE =
  "underline decoration-border transition-colors hover:decoration-current";

/**
 * Un `<button>` que se lee como un enlace.
 *
 * Se usa cuando la acción no navega —abrir un detalle, plegar un log, cambiar a la
 * vista de tabla— pero visualmente pertenece al texto. Es un botón y no un `<a>`
 * sin `href` porque el teclado y los lectores de pantalla tienen que anunciarlo
 * como lo que hace.
 *
 * @param props - Button props, on top of everything a `<button>` accepts.
 * @param props.variante - Whether the text inherits the colour or is muted.
 * @return The rendered button.
 */
export function BotonEnlace({
  variante = "sutil",
  className,
  children,
  ...resto
}: ComponentProps<"button"> & { variante?: "neutro" | "sutil" }) {
  return (
    <button
      type="button"
      className={cn(
        CLASES_ENLACE,
        "text-[13px]",
        variante === "sutil" && "text-text-secondary",
        className,
      )}
      {...resto}
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Superficies                                                                */
/* -------------------------------------------------------------------------- */

/**
 * La tarjeta: borde fino, fondo de superficie y una sombra que en oscuro es
 * `none`.
 *
 * La sombra va aquí y no en cada sitio porque antes la llevaban dos tarjetas de
 * las siete, y en tema claro eso hacía que unas flotaran y otras no sin ningún
 * criterio.
 */
const TARJETA_BASE = "rounded-lg border border-border bg-card shadow-[var(--shadow-card)]";

/**
 * The card recipe as a class string, for when the card is a `<Link>`.
 *
 * Para cuando la tarjeta es un `<Link>`: la lista de experimentos son tarjetas
 * que navegan.
 *
 * @param relleno - Padding utility. `p-0` for a card that wraps a table.
 * @param className - Extra classes, merged so they win over the recipe.
 * @return The class string.
 */
export function clasesTarjeta(relleno = "p-4", className?: string) {
  return cn(TARJETA_BASE, relleno, className);
}

/**
 * The card: thin border, surface background, and a shadow that is `none` in dark.
 *
 * @param props - Card props, on top of everything a `<div>` accepts.
 * @param props.etiqueta - Element to render, so a card can carry the right
 *     semantics without changing how it looks.
 * @param props.relleno - Padding utility.
 * @param props.discontinua - Dashed border, for the gap left by something that
 *     does not exist yet.
 * @return The rendered card.
 */
export function Tarjeta({
  etiqueta = "div",
  relleno = "p-4",
  discontinua = false,
  className,
  children,
  ...resto
}: ComponentProps<"div"> & {
  etiqueta?: "div" | "section" | "article";
  /**
   * El relleno, como prop y no como algo que se sobrescriba desde `className`.
   *
   * `p-4` y `px-4 py-6` no son el mismo grupo de utilidades, así que cuál gana lo
   * decide el orden de la hoja de estilos y no el de las clases: pasarlo aquí es lo
   * que hace que una tarjeta sin relleno (`p-0`, la que envuelve una tabla) sea
   * predecible.
   */
  relleno?: string;
  /** Borde discontinuo: el hueco de algo que todavía no hay (vacíos, pantallas pendientes). */
  discontinua?: boolean;
}) {
  return createElement(
    etiqueta,
    {
      className: clasesTarjeta(
        relleno,
        cn(discontinua && "border-dashed shadow-none", className),
      ),
      ...resto,
    },
    children,
  );
}

/**
 * Preformatted text: logs, parameter JSON and console commands.
 *
 * @param props - Everything a `<pre>` accepts.
 * @return The rendered block.
 */
export function Bloque({ className, children, ...resto }: ComponentProps<"pre">) {
  return (
    <pre
      className={cn("overflow-auto rounded-md bg-surface-sunken p-3 text-xs", className)}
      {...resto}
    >
      {children}
    </pre>
  );
}

/* -------------------------------------------------------------------------- */
/* Títulos                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * El `<h1>` de una pantalla, con su hueco inferior incluido.
 *
 * `secundario` es para lo que acompaña al título en la misma línea —el resumen de
 * riesgo del perfil, por ejemplo— y se coloca a la derecha con las líneas base
 * alineadas, que es lo que evita que un texto de 13 px al lado de uno de 17
 * parezca descolgado.
 *
 * @param props - Title props.
 * @param props.children - The title itself.
 * @param props.secundario - What sits beside it on the same baseline.
 * @return The rendered heading, with its bottom margin.
 */
export function TituloPagina({
  children,
  secundario,
}: {
  children: ReactNode;
  secundario?: ReactNode;
}) {
  const titulo = <h1 className="text-[17px] font-semibold tracking-tight">{children}</h1>;

  if (!secundario) return <div className="mb-5">{titulo}</div>;

  return (
    <div className="mb-5 flex flex-wrap items-baseline justify-between gap-3">
      {titulo}
      <p className="text-[13px] text-text-secondary">{secundario}</p>
    </div>
  );
}

/**
 * The `<h2>` heading a block inside a screen.
 *
 * @param props - Heading props.
 * @param props.children - The heading text.
 * @param props.className - Extra classes.
 * @return The rendered heading.
 */
export function TituloSeccion({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <h2
      className={cn(
        "text-[13px] font-semibold tracking-wide text-text-secondary uppercase",
        className,
      )}
    >
      {children}
    </h2>
  );
}

/**
 * El título de una tarjeta o de una gráfica.
 *
 * `como` existe porque el nivel del encabezado depende de dónde cuelgue —una
 * gráfica dentro de una sección es `h3`, y un aviso que ocupa la pantalla entera
 * es el `h1` de esa pantalla— mientras la apariencia es la misma.
 *
 * @param props - Heading props.
 * @param props.children - The heading text.
 * @param props.como - Heading level, chosen by where the block hangs.
 * @param props.className - Extra classes.
 * @return The rendered heading.
 */
export function TituloBloque({
  children,
  como = "h3",
  className,
}: {
  children: ReactNode;
  como?: "h1" | "h2" | "h3";
  className?: string;
}) {
  return createElement(
    como,
    { className: cn("text-[13px] font-semibold", className) },
    children,
  );
}

/* -------------------------------------------------------------------------- */
/* Estados y avisos                                                           */
/* -------------------------------------------------------------------------- */

/**
 * Un error en línea.
 *
 * Lleva `role="alert"` porque casi siempre aparece después de una acción —lanzar
 * un ciclo, pararlo, una consulta que falla al recargar— y sin él un lector de
 * pantalla no dice nada: el foco sigue en el botón que acaba de pulsarse y el
 * texto nuevo está en otra parte del documento.
 *
 * @param props - Everything a `<div>` accepts.
 * @return The rendered alert, already carrying `role="alert"`.
 */
export function Aviso({
  className,
  children,
  ...resto
}: ComponentProps<"div">) {
  return (
    <div
      role="alert"
      className={cn(
        "rounded-md border border-negative/40 bg-card p-3 text-[13px] text-negative-ink",
        className,
      )}
      {...resto}
    >
      {children}
    </div>
  );
}

/**
 * «Cargando…».
 *
 * `role="status"` para que la espera se anuncie sin robar el foco. Es el único
 * indicador de carga que hay: no hay esqueletos ni orbes porque las consultas de
 * esta aplicación van contra un SQLite local y terminan antes de que un
 * placeholder llegue a pintarse — salvo la analítica, que avisa con su propio
 * texto.
 *
 * @param props - Loading props.
 * @param props.texto - What to announce. Worth overriding when the wait is long
 *     enough that the generic wording would not say what is being waited for.
 * @param props.className - Extra classes.
 * @return The rendered notice, already carrying `role="status"`.
 */
export function Cargando({
  texto = "Cargando…",
  className,
}: {
  texto?: string;
  className?: string;
}) {
  return (
    <p role="status" className={cn("text-[13px] text-text-muted", className)}>
      {texto}
    </p>
  );
}

/* -------------------------------------------------------------------------- */
/* Insignias                                                                  */
/* -------------------------------------------------------------------------- */

/**
 * Bordered pill: counts, header states.
 *
 * @param props - Badge props, on top of everything a `<span>` accepts.
 * @param props.compacta - Tighter variant, for when the pill sits inside a row.
 * @return The rendered badge.
 */
export function Insignia({
  compacta = false,
  className,
  children,
  ...resto
}: ComponentProps<"span"> & { compacta?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-[13px]",
        compacta && "px-[9px] py-0.5 text-xs font-semibold",
        className,
      )}
      {...resto}
    >
      {children}
    </span>
  );
}

/**
 * La etiqueta diminuta en mayúsculas que se pega a una cifra: `VIVO`, `CICLO`,
 * `SIN PRECIO`, `SIN MODELO`.
 *
 * **Nunca es el único portador del significado** (F4.9): siempre lleva `title` con
 * la frase entera, porque cuatro letras en mayúsculas no explican nada por sí
 * solas y el color menos.
 */
type TonoEtiqueta = "hereda" | "neutro" | "bueno" | "atencion" | "malo";

const ETIQUETA_TONO: Record<TonoEtiqueta, string> = {
  hereda: "",
  neutro: "text-text-muted",
  bueno: "text-delta-good",
  atencion: "text-warning",
  malo: "text-delta-bad",
};

/**
 * The tiny uppercase tag stuck to a figure.
 *
 * @param props - Tag props, on top of everything a `<span>` accepts. Callers
 *     must pass `title` with the full sentence: the colour and four uppercase
 *     letters never carry the meaning on their own (F4.9).
 * @param props.tono - Which token colours the tag, or `hereda` to inherit.
 * @return The rendered tag.
 */
export function Etiqueta({
  tono = "hereda",
  className,
  children,
  ...resto
}: ComponentProps<"span"> & { tono?: TonoEtiqueta }) {
  return (
    <span
      className={cn(
        "ml-1.5 align-middle text-[10px] font-semibold uppercase",
        ETIQUETA_TONO[tono],
        className,
      )}
      {...resto}
    >
      {children}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Controles de formulario                                                    */
/* -------------------------------------------------------------------------- */

const CLASES_CONTROL =
  "min-h-8 rounded-md border border-border bg-card px-2 py-1 text-[13px] transition-colors hover:bg-surface-sunken";

/**
 * La etiqueta de un control, envolviéndolo.
 *
 * Es un `<label>` que contiene al control y no un `htmlFor` con un `id` inventado:
 * así la asociación no puede romperse al copiar el bloque, y pulsar el texto
 * enfoca el campo sin escribir nada más.
 *
 * @param props - Field props.
 * @param props.etiqueta - Label text, in the interface language.
 * @param props.fila - Label to the left instead of above.
 * @param props.className - Extra classes for the `<label>`.
 * @param props.children - The control being wrapped.
 * @return The rendered label with its control inside.
 */
export function Campo({
  etiqueta,
  fila = false,
  className,
  children,
}: {
  etiqueta: string;
  /** Etiqueta a la izquierda en lugar de encima. Para la cabecera, donde no hay alto que gastar. */
  fila?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label
      className={cn(
        "text-[13px]",
        fila ? "flex items-center gap-2" : "flex flex-col gap-1",
        className,
      )}
    >
      <span className="text-text-muted">{etiqueta}</span>
      {children}
    </label>
  );
}

/**
 * Desplegable nativo.
 *
 * Sigue siendo el `<select>` del navegador a propósito: las listas de esta
 * aplicación tienen tres o cuatro entradas y el nativo ya es accesible con teclado
 * sin traerse un popover. Se cambiará cuando haya que enseñar algo dentro de cada
 * opción.
 *
 * @param props - Select props, on top of everything a `<select>` accepts.
 * @param props.etiqueta - Label text, in the interface language.
 * @param props.fila - Label to the left instead of above.
 * @param props.claseCampo - Extra classes for the wrapping `<label>`.
 * @param props.opciones - Options as `[value, text]` pairs, text already in the
 *     interface language.
 * @return The rendered select inside its label.
 */
export function Select({
  etiqueta,
  fila,
  claseCampo,
  className,
  opciones,
  ...resto
}: Omit<ComponentProps<"select">, "children"> & {
  etiqueta: string;
  fila?: boolean;
  /** Clases para el `<label>` que envuelve, no para el desplegable. */
  claseCampo?: string;
  opciones: readonly (readonly [valor: string, texto: string])[];
}) {
  return (
    <Campo etiqueta={etiqueta} fila={fila} className={claseCampo}>
      <select className={cn(CLASES_CONTROL, className)} {...resto}>
        {opciones.map(([valor, texto]) => (
          <option key={valor} value={valor}>
            {texto}
          </option>
        ))}
      </select>
    </Campo>
  );
}

/**
 * Text field.
 *
 * @param props - Input props, on top of everything an `<input>` accepts.
 * @param props.etiqueta - Label text, in the interface language.
 * @param props.claseCampo - Extra classes for the wrapping `<label>`.
 * @return The rendered input inside its label.
 */
export function Entrada({
  etiqueta,
  claseCampo,
  className,
  ...resto
}: ComponentProps<"input"> & {
  etiqueta: string;
  /** Clases para el `<label>` que envuelve, no para la caja. */
  claseCampo?: string;
}) {
  return (
    <Campo etiqueta={etiqueta} className={claseCampo}>
      <input className={cn(CLASES_CONTROL, className)} {...resto} />
    </Campo>
  );
}
