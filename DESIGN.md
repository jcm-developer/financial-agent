# Verdana Health — Sistema de diseño de `financial-agent`

## Carácter

Verdana Health es un sistema sereno y de aire clínico, pensado para plataformas de
salud digital. Su base de azul marino profundo y verdes salvia suaves busca
transmitir precisión clínica templada con calidez, y prioriza la legibilidad, la
accesibilidad y una sensación de tranquilidad en cada punto de contacto.

Se adoptó **entero** el 2026-08-10, sustituyendo al sistema anterior. La adopción
fue completa y deliberada: Verdana manda en color, tipografía, densidad, radios y
elevación, **incluso donde choca con decisiones que el proyecto había medido**. Las
tres consecuencias que hay que tener presentes están en
[Lo que se perdió al adoptarlo](#lo-que-se-perdió-al-adoptarlo), y ninguna es un
descuido: son el precio acordado.

**Tema único, solo claro.** Verdana no especifica una variante oscura, así que no se
inventó una. Con el cambio desaparecieron la clase `.dark`, la variante `dark:`, el
interruptor de tema y el script anti-fogonazo de `app/index.html`.

---

## Colores

### Paleta base

| Papel | Valor | Para qué |
|---|---|---|
| **Primary Navy** | `#0F172A` | Acciones primarias, títulos, texto principal |
| **Secondary Slate** | `#64748B` | Texto secundario, bordes |
| **Tertiary Sage** | `#059669` | Enlaces, CTA, resaltados |
| **Background** | `#F8FAFC` | Fondo de página |
| **Surface Default** | `#FFFFFF` | Fondo de tarjeta |
| **Success** | `#22C55E` | Confirmado, dentro de rango |
| **Warning** | `#EAB308` | Pendiente, precaución |
| **Error** | `#EF4444` | Crítico, fuera de rango |
| **Info** | `#0EA5E9` | Informativo, novedad |

La rampa slate completa (`--slate-50` … `--slate-950`) vive en `:root` de
[app/src/index.css](app/src/index.css), porque las fichas de componente ya citaban
media docena de sus valores —`#E2E8F0` en bordes, `#F1F5F9` en divisores, `#475569`
en texto de ayuda, `#CBD5E1` en controles, `#020617` en el hover del primario— y
tenerlos sueltos era pedir que se adivinaran.

### Los dos niveles de cada color de estado: marca y tinta

Cada hue de estado tiene **dos valores, y confundirlos es el error fácil**:

| Familia | Marca | Tinta | Qué es cada uno |
|---|---|---|---|
| Éxito | `#22C55E` | `#16A34A` | Relleno de gráfica y fondo de chip al 8 % · texto |
| Aviso | `#EAB308` | `#CA8A04` | ídem |
| Error | `#EF4444` | `#DC2626` | ídem |
| Info | `#0EA5E9` | `#0284C7` | ídem |

**El corte lo hace el propio Verdana** en su ficha de chips: fondo `#22C55E15` bajo
letras `#16A34A`. Un relleno cumple con menos contraste que una etiqueta, así que el
verde y el rojo saturados no valen como letra.

En Tailwind: `--color-success` / `--color-success-ink`, `--color-warning-mark` /
`--color-warning`, `--color-error` / `--color-error-ink`.

### Convención de P&L

Nunca a mano: la clase la da `signClass()` en
[app/src/lib/format.ts](app/src/lib/format.ts).

```ts
signClass(value)  // > 0 → text-delta-good (#16A34A) · < 0 → text-delta-bad (#DC2626)
                  // === 0 → text-text-secondary · null → text-text-muted
```

**El caso nulo importa**: «no hay dato» y «cero» se distinguen, porque un P&L de
cero por falta de precio no es un P&L de cero.

### Regla dura

**Ningún hexadecimal en `className`, nunca.** Se usa la utilidad del token
(`text-error-ink`, no `text-[#dc2626]`). En SVG los colores se pasan como
`var(--color-…)` a través de `COLORS` en
[app/src/components/charts/base.tsx](app/src/components/charts/base.tsx): los
atributos de presentación de SVG aceptan variables CSS, así que la paleta se cambia
editando `index.css` y nada más.

---

## Tipografía

| Papel | Familia |
|---|---|
| Títulos | **Plus Jakarta Sans** |
| Cuerpo | **DM Sans** |
| Monoespaciada | **Fira Code** |

Las tres son variables y están **autoalojadas**: entran por `@fontsource-variable` y
se empaquetan en `app/dist` dentro de la imagen. Nada de CDN, y no es purismo — la
aplicación se sirve desde un contenedor publicado en `127.0.0.1` y no hay salida a
internet garantizada, así que un enlace a Google Fonts dejaría toda la interfaz en
la familia de respaldo justo al desplegarla.

**Solo el subconjunto latino, y solo la variante normal.** Las declaraciones
`@font-face` están escritas a mano en `index.css` en vez de importar el `index.css`
de cada paquete, porque ese importa además cirílico, griego y vietnamita: 262 KB de
woff2 contra los **101 KB** que pesan los latinos. Todo lo que necesita el español
—ñ, las vocales acentuadas, ¿ y ¡— está en `latin`.

### Escala

| Nombre | Clase | Tamaño | Interlineado | Peso |
|---|---|---|---|---|
| Display | `text-display` | 40 px | 1,15 | 700 |
| H1 | `text-h1` | 32 px | 1,2 | 700 |
| H2 | `text-h2` | 24 px | 1,25 | 600 |
| H3 | `text-h3` | 20 px | 1,3 | 600 |
| H4 | `text-h4` | 16 px | 1,35 | 500 |
| Body LG | `text-body-lg` | 18 px | 1,6 | 400 |
| Body | `text-body` | 16 px | 1,6 | 400 |
| Body SM | `text-body-sm` | 14 px | 1,5 | 400 |
| Caption | `text-caption` | 12 px | 1,4 | 500 |
| Code | `text-code` | 14 px | 1,6 | 400 |

Cada paso lleva su peso y su interlineado dentro del token, así que un título es
`text-h2` y nunca un tamaño más un peso más un `leading-` montados a mano. **No se
usan `text-sm`, `text-base` ni `text-lg`** de Tailwind: son una segunda escala.

### Cifras

**Toda cifra va en Fira Code**, que es la regla 10 de Verdana. El interruptor es
`.tabular` (`font-family` mono + `tabular-nums`), y `<Td numeric>` la aplica sola,
así que en una tabla no hay que acordarse. Fuera de las tablas —cifras de tarjeta,
recuentos— se pone a mano.

---

## Espaciado

Unidad base **8 px**. La escala de Verdana es un subconjunto de la de Tailwind, así
que se usan las clases estándar:

| Verdana | Valor | Clase |
|---|---|---|
| xs | 4 px | `1` |
| sm | 8 px | `2` |
| md | 16 px | `4` |
| lg | 24 px | `6` |
| xl | 32 px | `8` |
| 2xl | 48 px | `12` |
| 3xl | 64 px | `16` |

---

## Radios y elevación

| Clase | Valor | Uso |
|---|---|---|
| `rounded-sm` | 4 px | Chips y etiquetas |
| `rounded-md` | 8 px | Botones, tarjetas, campos |
| `rounded-lg` | 12 px | Diálogos y paneles desplegables |
| `rounded-xl` | 16 px | Contenedores grandes |
| `rounded-full` | — | Avatares e indicadores de punto |

Sombras difusas, todas proyectadas desde el azul marino. **Nunca una sombra dura:**
la difusión es lo que la mantiene clínica.

| Clase | Valor | Uso |
|---|---|---|
| `shadow-sm` | 1 px / 3 px / 3 % | Botones y chips |
| `shadow-md` | 2 px / 6 px / 5 % | Tarjetas y desplegables |
| `shadow-lg` | 4 px / 16 px / 7 % | Tarjetas elevadas |
| `shadow-xl` | 8 px / 32 px / 10 % | Diálogos y paneles |

---

## Componentes

Todo lo compartido vive en
[app/src/components/pieces.tsx](app/src/components/pieces.tsx), y **ninguna receta
de clases se escribe dos veces**. El fichero no lleva colores propios: todo sale de
los tokens.

### Botones — `<Button>`, `buttonClasses()`

| Variante | Relleno | Texto | Borde | Hover |
|---|---|---|---|---|
| `primary` | `#0F172A` | blanco | — | `#020617` |
| `secondary` *(por defecto)* | transparente | `#0F172A` | 1 px navy | navy al 4 % |
| `ghost` | transparente | `#475569` | — | `#F1F5F9` |
| `destructive` | `#EF4444` | blanco | — | `#DC2626` |

Tamaños `sm` (32 px), `md` (42 px, por defecto) y `lg` (48 px). Deshabilitado:
opacidad 0,4, cursor de bloqueo y **todos los estados de hover y foco suprimidos**.

`primary` es **la acción que compromete de cada pantalla, y solo una**: «Lanzar
ciclo», «Guardar», «Crear y activar», el confirmar de un diálogo.

Cuando quien lleva la apariencia de botón es un `<Link>` del enrutador —porque
navega, y navegar tiene que poder abrirse en otra pestaña— se comparte la apariencia
y no el elemento, con `buttonClasses()`.

### Tarjetas — `<Card>`, `cardClasses()`, `<CardHeaderStrip>`

Dos formas, y son **alternativas y no una escala**: `default` lleva borde y ningún
sombreado; `elevated` quita el borde y toma la sombra `lg` en su lugar. Una tarjeta
con las dos cosas se leería como dos niveles a la vez. Relleno de 24 px (`p-6`).

`padding` es una **prop y no algo que se sobrescriba desde `className`**: `p-6` y
`px-6 py-8` no son el mismo grupo de utilidades, así que cuál gana lo decidiría el
orden de la hoja de estilos. Pasarlo como prop es lo que hace predecible el
`padding="p-0"` de la tarjeta que envuelve una tabla.

`<CardHeaderStrip>` es la banda teñida de navy con texto blanco en mayúsculas para
la etiqueta de categoría de un bloque.

### Campos — `<Input>`, `<Field>`, `<FieldHint>`, `CONTROL_CLASSES`

42 px de alto, 10×14 de relleno, 8 px de radio. Borde `#E2E8F0` que pasa a navy en
hover; en foco, borde navy con su halo de 3 px; en error, lo mismo en rojo. Etiqueta
14 px/500, ayuda 12 px/400 en `#475569`, error 12 px/400 en `#EF4444`.

El halo es un `ring` y el segundo píxel del borde es una sombra interior, los dos
fuera del modelo de caja: **especificar el foco como un borde de 2 px literal
movería cada carácter del campo un píxel al enfocarlo.**

`<Field>` es un `<label>` que **envuelve** el control, no un `htmlFor` con un `id`
inventado: así la asociación no puede romperse al copiar el bloque.

### Desplegable — `<Select>`

⚠️ **Ya no es el `<select>` nativo**, y ese es el cambio de fondo respecto al sistema
anterior. Un `<select>` dibuja su lista con el widget del sistema operativo, que es
la única superficie de la aplicación a la que Verdana no llega: sin radio de 12 px,
sin elevación difusa, sin DM Sans, sin marca de verificación en la fila elegida.

Lo que **no** se perdió al sustituirlo, porque es para lo que servía el nativo:

- **El contrato de teclado entero.** `↓`/`↑`/`Inicio`/`Fin` mueven, `Enter` y
  `Espacio` eligen, `Esc` cierra, `Tab` cierra y sigue, y escribir letras salta a la
  opción que empieza por ellas — incluido el ciclado por letra repetida.
- **El anuncio correcto.** `combobox` con `aria-expanded` en el disparador,
  `listbox` en el panel y `option` con `aria-selected` en cada fila, cosidos con
  `aria-activedescendant` para que el foco no salga nunca del disparador y no haga
  falta atraparlo.

El panel se pinta en un **portal**, y no es un detalle: varios de estos viven dentro
de tarjetas con `overflow-x-auto` por sus tablas, y un panel colocado dentro quedaría
recortado.

⚠️ Se apila con `z-index`, así que quedaría **por debajo** de un `<dialog>` abierto
con `showModal()`, que vive en la capa superior. Hoy ningún diálogo lleva uno.

Sus tres decisiones puras —dónde cae el panel, a dónde salta el resaltado, qué
encuentra lo tecleado— están fuera del componente y con tests en `Select.test.ts`.

### Chips — `<Chip>`, y sus alias `<Badge>` y `<Tag>`

4×12 de relleno, radio 4 px, 12 px/500, mayúsculas y 0,5 px de interletraje. Sus
variantes son **dos trabajos distintos**: `filter`/`filterActive` son un control que
se pulsa, y `success`/`warning`/`error`/`info`/`neutral` son un estado que se lee.

`<Badge>` y `<Tag>` son **alias finos sobre `<Chip>`** y no componentes propios:
renombrar treinta sitios de llamada habría sido tocar las pantallas, y el sentido de
`pieces.tsx` es que la receta viva en un solo sitio independientemente de cómo la
pidan.

**`<Tag>` nunca es el único portador del significado**: lleva siempre la frase
entera, y la sirve como tooltip de Verdana en vez de como `title` del navegador.

### Listas y tablas — [app/src/components/Table.tsx](app/src/components/Table.tsx)

Filas de 48 px, 8×16 de relleno, divisor de 1 px en `#F1F5F9` y `#F8FAFC` al pasar
por encima. La cabecera es el único sitio fuera de un chip donde se usan mayúsculas
e interletraje: es lo que separa las etiquetas de las cifras sin gastar una regla.

- `<Table title>` — el título es el `<caption class="sr-only">`, obligatorio. El
  contenedor lleva `overflow-x-auto`, **no la página**.
- `<Td header>` — la celda que nombra la fila, como `<th scope="row">`. Sin ella un
  lector de pantalla lee «17,42» sin decir de qué símbolo.
- `<Empty>` — el texto se redacta **caso por caso**. «No hay posiciones» y «no hay
  decisiones» significan cosas muy distintas, y un texto genérico obliga a ir a
  mirar la base de datos.
- `<Pagination>` — enseña «41–80 de 480» y no solo las flechas.
- `<Row expanded>` + `<DetailRow columns>` — el plegable de una fila: la prosa que no
  cabe en una columna baja a un segundo `<tr>` a todo lo ancho, y la de arriba
  suelta su raya inferior para que las dos se lean como una. El disparador va en la
  celda que nombra la fila, con `aria-expanded`. **Es un `<tr>` y no una celda más
  alta**: `align-top` sobre una celda de cuatro líneas deja las cifras de las otras
  columnas flotando arriba.

### Casillas y radios — `<Checkbox>`, `<RadioGroup>`

18×18, radio 4 px la casilla y círculo el radio. Vacíos: borde de 1,5 px en
`#CBD5E1` sobre blanco. Marcada: relleno navy sin borde y marca blanca. Elegido:
anillo navy de 2 px con punto interior de 8 px. Deshabilitados al 40 %. Etiqueta a
8 px.

Son **`<input>` de verdad con `appearance-none`**, no `<div>` con `role`: así el
teclado, el formulario, la asociación con la etiqueta y el anuncio del lector de
pantalla siguen siendo cosa del navegador, y lo único que queda por escribir es la
pintura. Lo exportado en el caso del radio es el **grupo** y no el control suelto,
porque un radio solo no se puede desmarcar y es entonces una casilla que miente.

### Tooltips — `<Tooltip>`

Fondo navy, texto `#F8FAFC` de 12 px, flecha de 6 px, 240 px de ancho máximo, radio
8 px. **150 ms para aparecer y nada para irse**, que es la asimetría que pide
Verdana: sin el retardo, arrastrar el puntero por una fila de chips enciende cuatro
globos.

⚠️ **Un tooltip no es el único sitio donde vive un significado.** La frase que
respalda un chip de color tiene que llegar a quien no está apuntando a nada: por eso
el globo se ata a su disparador con `aria-describedby` y el disparador conserva
`tabIndex={0}`. Sustituye al `title` del navegador, y tiene que sustituirlo entero
—incluida la parte que el `title` hacía para quien no lo ve nunca.

### Deslizadores — `<Slider>`

El deslizador 1–10 de perfil de riesgo y diversificación. **Lleva el valor siempre a
la vista y los dos extremos nombrados**, y no es adorno: un deslizador cuyo número no
se lee es un control que no se puede poner a propósito, y «1» y «10» no dicen por sí
solos hacia dónde hay más riesgo.

La mitad rellena de la pista es una parada de gradiente gobernada por la propiedad
`--fill` que fija el componente: CSS no puede leer el valor de un `input`, así que
sin eso la pista es una barra uniforme y el control deja de enseñar por dónde va.

### Avisos — `<Alert>`

Cuatro tonos (`error` por defecto, `warning`, `success`, `info`), fondo al 8 % del
hue y texto en su tinta. Lleva `role="alert"` porque casi siempre aparece después de
una acción, y sin él un lector de pantalla no dice nada: el foco sigue en el botón
que se acaba de pulsar y el texto nuevo está en otra parte del documento.

### Títulos

| Componente | Resultado |
|---|---|
| `<PageTitle aside>` | `h1` en `text-h1`, con su `mb-8`. `aside` va a la derecha con las líneas base alineadas |
| `<SectionTitle>` | `h2` en `text-h3` |
| `<BlockTitle as>` | `text-h4` para tarjetas y gráficas. `as` fija el nivel del encabezado sin cambiar la apariencia |

Los niveles se eligen por jerarquía del documento y no por tamaño: un aviso que
ocupa la pantalla entera usa `<BlockTitle as="h1">`, porque es el `h1` de esa
pantalla aunque no tenga el tamaño de un título de página.

---

## Gráficas

En Analítica y en el comparador, con Recharts v3, cargado con `lazy()` porque pesa
casi tanto como el resto de la aplicación junta. Lo compartido está en
[charts/base.tsx](app/src/components/charts/base.tsx).

- **La polaridad es el par éxito/error** (`#22C55E` / `#EF4444`), el nivel de marca,
  que es el que necesita un relleno.
- **La identidad es `series-1` y luego `series-2`** (navy, y después el azul de
  info), asignados en ese orden fijo y **nunca ciclados**. La salvia no está en esa
  lista a propósito: Verdana la reserva para lo interactivo y lo positivo, así que
  una serie pintada de salvia se leería como un enlace.
- El color codifica polaridad **o** identidad en una gráfica dada, nunca las dos.
- **A partir de tres series, múltiplos pequeños**: una gráfica por entidad, una sola
  serie cada una y **dominio vertical compartido**. Con autoescala, un vaivén del
  0,4 % y una carrera del 12 % dibujarían la misma forma.
- **Toda gráfica tiene vista de tabla**, con el conmutador en su cabecera. No es un
  extra: es lo que mantiene el dato disponible cuando el color no basta —daltonismo,
  impresión, lector de pantalla— y es como se comprueba una cifra concreta.
- **Toda gráfica tiene estado vacío redactado**, diciendo qué falta para que haya
  algo que dibujar.
- **Dos experimentos con divisas distintas no comparten eje en dinero, nunca.** El
  proyecto no convierte divisa en ningún sitio (D8), así que un eje compartido solo
  puede llevar una cifra indexada.
- **Las muestras pequeñas se marcan**: los tramos con menos de cinco operaciones
  salen al 35 % de opacidad y cada barra lleva su `n=` encima.

---

## Idioma y nombres

La pantalla habla **español** y el código que la dibuja se escribe **en inglés**. No
es una contradicción: son dos audiencias distintas y la frontera está en el signo de
igual.

```tsx
const EMPTY_POSITIONS = "No hay posiciones abiertas en este experimento";
```

Va en inglés todo lo que no se ve: nombres de componente, props, hooks, variables,
comentarios, **nombres de fichero y de carpeta**. Va en español todo lo que se lee:
rótulos, estados vacíos, textos de aviso, `aria-label`, `title` y títulos de página.
Un `aria-label` es texto de pantalla aunque no se pinte.

Los nombres siguen la tabla de [CLAUDE.md](CLAUDE.md). Lo que afecta a `app/`:

- **Un componente por fichero → `PascalCase.tsx` con su nombre exacto**
  (`Select.tsx`, `Checkbox.tsx`, `Tooltip.tsx`).
- **Una colección de exports → `camelCase.tsx`** (`pieces.tsx`, `charts/base.tsx`).
- **Hooks → `useAlgo.ts`**, y las carpetas en minúscula y una sola palabra.

---

## Accesibilidad

- **Foco visible en todo:** el borde navy de 2 px con `outline-offset: 2px` está en
  la capa base de `index.css`, así que cubre también cualquier control escrito a
  mano.
- **Enlace de salto:** `.skip-link` es `sr-only` hasta que recibe foco. Sin él,
  llegar al contenido con teclado obliga a recorrer la cabecera y las once entradas
  de la barra lateral **en cada página**.
- **El color nunca es el único portador del significado.** Todo estado con color
  lleva además texto (`activo`, `aprobado`, `datos en vivo`) o un tooltip que lo
  explica. El indicador en vivo tiene tres estados y no dos, porque agrupar
  «reconectando» con «desconectado» parpadearía en rojo cada cuarto de hora en una
  conexión sana —el servidor retira las conexiones cada 15 minutos a propósito.
- **Tablas:** `<caption>` obligatorio, `scope="col"` en cabeceras y `scope="row"` en
  la celda que nombra la fila.
- **Botones de solo icono:** `aria-label` obligatorio. Iconos y puntos decorativos:
  `aria-hidden`.
- **Estados:** carga con `role="status"`, errores con `role="alert"`, conmutadores
  con `aria-pressed`, plegables con `aria-expanded`, fila elegida con
  `aria-current`.
- **Logs en vivo:** `aria-live="polite"` y nunca `assertive` — son cientos de líneas.
- **Movimiento reducido:** `prefers-reduced-motion: reduce` baja toda animación y
  transición a `0,01 ms` de forma global.

### Lo que se perdió al adoptarlo

Escrito aquí para que nadie lo descubra por su cuenta dentro de seis meses:

1. **El par de P&L es verde/rojo, y bajo protanopía y deuteranopía los dos polos
   quedan cerca.** El sistema anterior usaba azul/rojo justamente por eso, con una
   separación medida de ΔE 21,6. Lo que sostiene la lectura ahora es lo que no es
   color: el signo escrito en cada cifra, la línea del cero y la vista de tabla que
   toda gráfica tiene debajo.
2. **La densidad bajó.** El cuerpo es de 16 px y las filas de 48, contra 13 px y
   ~30 px de antes: en una tabla caben en torno a nueve filas donde cabían quince.
   Es lo que pide la regla 7 de Verdana y es coherente con el resto del sistema.
3. **No hay tema oscuro.**

---

## Do's and Don'ts

1. **Sí** al contraste navy + blanco como ritmo visual primario; la salvia queda
   reservada para elementos interactivos y estados positivos.
2. **Sí** al espacio en blanco generoso — nunca debe sentirse apretado.
3. **Sí** al radio suave y consistente de 8 px.
4. **No** a los neones ni a los acentos saturados.
5. **No** a las tipografías condensadas o decorativas.
6. **Sí** a las etiquetas de chip en mayúsculas con interletraje.
7. **No** a sobrecargar de datos: divulgación progresiva y secciones plegables.
8. **Sí** a la iconografía junto al texto, nunca en su lugar.
9. **No** a las sombras duras; la elevación difusa es lo que mantiene el aire
   clínico.
10. **Sí** a Fira Code en resultados y constantes vitales, por la alineación de sus
    cifras tabulares.

---

## Trampas conocidas

### `tailwind-merge` se come los colores si no se le enseña la escala

⚠️ **Costó una captura de pantalla encontrarlo y no lo detecta nada automático.**
`tailwind-merge` clasifica una clase `text-*` desconocida adivinando, y para
`text-body-sm` adivina *color*. Mezclar `text-primary-foreground` con `text-body-sm`
parecía entonces dos colores compitiendo, ganaba el último y el primero desaparecía:
el síntoma era **un botón navy sin etiqueta encima** —texto navy sobre relleno
navy— con el typecheck, los tests y el build en verde.

La escala está declarada en `cn()` ([app/src/lib/utils.ts](app/src/lib/utils.ts)) y
clavada con tests en `utils.test.ts`. **Cada tamaño que se añada al bloque `@theme`
de `index.css` hay que añadirlo también a esa lista.**

---

## Lo que no hay, y por qué

| No hay | Por qué |
|---|---|
| Tema oscuro | Verdana es un sistema de tema único y no se inventó una variante |
| Glassmorfismo (`backdrop-blur`, superficies translúcidas) | Un fondo translúcido bajo una tabla de cifras baja el contraste del texto justo donde más se lee |
| Sombras duras o de elevación alta | La escala difusa es lo que sostiene el carácter clínico |
| Radios por encima de 16 px | Comen alto útil |
| Esqueletos, barridos, orbes de carga | Las consultas van contra un SQLite local: no hay espera que amortice el placeholder |
| Animaciones de entrada de datos | Retrasan la lectura del dato, que es lo único que se viene a hacer. Las únicas transiciones son el hover, el desplegable y el tooltip |
| `text-sm`, `text-base`, `text-lg` | Son una segunda escala tipográfica compitiendo con la de arriba |
| shadcn/ui | Sigue configurado para poder traerlo (`components.json`, el vocabulario `--color-*`), pero nada ha necesitado Radix todavía: el diálogo de confirmación es el `<dialog>` nativo y el desplegable está escrito a mano |
