# financial-bot — Sistema de diseño

## Carácter

El panel de `financial-bot` es un **instrumento de medida**, no un escaparate. Se
abre una o dos veces al día durante un experimento de semanas para responder a
preguntas concretas —¿corrió el ciclo de las 11:20?, ¿la convicción del modelo
predice algo?— y todo lo visual está subordinado a eso: neutros cálidos, bordes
finos, densidad alta de cifras y color reservado para lo que significa algo.

Tres decisiones lo resumen, y ninguna es estética:

1. **El par positivo/negativo es azul/rojo, no verde/rojo.** Es un divergente con
   polo frío y polo cálido, así que se sigue leyendo sin distinguir el verde del
   rojo. En protanopía las dos series separan con ΔE 21,6, muy por encima del
   mínimo de 8.
2. **Los neutros son cálidos** (`#f9f9f7`, no `#ffffff`). Es lo que evita el
   aspecto de hoja de cálculo.
3. **Ninguna receta de clases se escribe dos veces.** Todo lo compartido vive en
   [app/src/components/piezas.tsx](app/src/components/piezas.tsx).

**Stack:** Tailwind CSS v4.3, `system-ui`, iconos Lucide (v1.x), Recharts v3 solo
en Analítica. Sin Framer Motion, sin fuentes externas.

---

## Colores

### Tokens de tema

Los colores dependientes del tema son variables CSS definidas en
[app/src/index.css](app/src/index.css): `:root` es el tema **claro** y `.dark` el
**oscuro**, que es el de partida. La paleta viene del dashboard viejo valor por
valor, con dos correcciones de contraste hechas en F4.9.

| Token | Claro | Oscuro | Papel |
|---|---|---|---|
| `--page` | `#f9f9f7` | `#0d0d0d` | Fondo de página |
| `--surface` | `#fcfcfb` | `#1a1a19` | Tarjetas, tablas, controles |
| `--surface-sunken` | `#f2f2ef` | `#222221` | Hundido: `hover`, logs, estado activo |
| `--text-primary` | `#0b0b0b` | `#ffffff` | Texto principal |
| `--text-secondary` | `#52514e` | `#c3c2b7` | Texto de apoyo |
| `--text-muted` | `#76746f` | `#898781` | Etiquetas y notas |
| `--border-subtle` | `rgba(11,11,11,.1)` | `rgba(255,255,255,.1)` | Todos los bordes |
| `--grid` / `--axis` | `#e1e0d9` / `#c3c2b7` | `#2c2c2a` / `#383835` | Rejilla y ejes de gráfica |
| `--shadow-card` | `0 1px 2px rgba(11,11,11,.04)` | `none` | Elevación de tarjeta |
| `--radius` | `0.5rem` | ídem | Radio base |

> `--text-muted` en claro es `#76746f` y no el `#898781` heredado: el original
> daba 3,50:1 sobre la tarjeta, por debajo del 4,5 que pide AA, y este token se usa
> justo en etiquetas de 11–13 px.

### Colores de significado

Hay **tres pares** y no uno, y confundirlos es el error fácil:

| Familia | Claro | Oscuro | Para qué |
|---|---|---|---|
| `--positive` / `--negative` | `#2a78d6` / `#e34948` | `#3987e5` / `#e66767` | **Marcas de gráfica**: barras, áreas, líneas. Cumplen 3:1, que es lo que pide un relleno |
| `--positive-ink` / `--negative-ink` | `#2874d0` / `#c62726` | `#3987e5` / `#e66767` | **Texto** que tiene que leerse como la serie: `compra`/`venta`, lado de una orden. Cumplen 4,5:1 |
| `--delta-good` / `--delta-bad` | `#006300` / `#d03b3b` | `#0ca30c` / `#e66767` | **Texto** de variación y de estado binario: P&L, `sano`, `aprobado`, `filled`, `VIVO` |
| `--warning` | `#9c6b03` | `#fab219` | Avisos: precio rancio, ciclo detenido, orden cancelada |
| `--series-1` / `--series-2` | `#2a78d6` / `#eb6834` | `#3987e5` / `#d95926` | Series sin polaridad, en este orden |

**Por qué `delta-*` existe aparte de `positive/negative`:** aquí sí puede usarse
verde, porque un texto de variación no compite con ninguna serie de la gráfica. Y
por qué `*-ink` existe aparte: un relleno cumple con 3:1 y una etiqueta necesita
4,5, así que el azul y el rojo de marca (4,30 y 3,85) no valen como letra.

El ámbar de marca es ilegible como texto sobre fondo claro (1,79:1), así que
`--warning` lleva la versión oscura en claro; en oscuro el original ya da 9,49:1.

### Puente con shadcn/ui

El bloque `@theme inline` de `index.css` mapea los nombres que espera shadcn
(`--color-background`, `--color-card`, `--color-primary`, `--color-border`,
`--color-ring`…) sobre la paleta de arriba, y añade el vocabulario del dominio
(`--color-positive`, `--color-delta-good`, `--color-warning`, `--color-grid`…).
Tailwind genera las utilidades solo: `bg-card`, `border-border`,
`text-text-muted`, `text-delta-good`, `bg-surface-sunken`.

Así los componentes que copie el CLI de shadcn encajan sin retocarlos y a la vez
no hay dos paletas compitiendo. Una celda de P&L no es `destructive`: es
negativa, que no significa lo mismo ni se colorea igual.

### Convención de P&L

Nunca a mano: la clase la da `claseSigno()` en
[app/src/lib/formato.ts](app/src/lib/formato.ts).

```ts
claseSigno(valor)  // > 0 → text-delta-good · < 0 → text-delta-bad
                   // === 0 → text-text-secondary · null → text-text-muted
```

El caso nulo importa: «no hay dato» y «cero» se distinguen, porque un P&L de cero
por falta de precio no es un P&L de cero.

### Regla dura

**Ningún hexadecimal en `className`, nunca.** Se usa la utilidad del token
(`text-negative-ink`, no `text-[#c62726]`). En SVG los colores se pasan como
`var(--color-…)` a través de `COLORES` en
[graficas/base.tsx](app/src/components/graficas/base.tsx): los atributos de
presentación de SVG aceptan variables CSS, así que el interruptor de tema repinta
las gráficas solo. Sin eso habría que leer los colores con `getComputedStyle` y
volver a dibujar a mano en cada cambio de tema.

---

## Tipografía

**Familia:** `system-ui, -apple-system, "Segoe UI", sans-serif`. Sin fuente
importada: una web font son 20–40 KB y un fogonazo de texto sin estilar para ganar
nada que se note en una tabla de cifras.

**Base:** 14 px, `line-height: 1.5`, `antialiased`, en `body`.

### Escala real

| Tamaño | Clase | Dónde |
|---|---|---|
| 10 px | `text-[10px]` | `<Etiqueta>`: `VIVO`, `CICLO`, `SIN PRECIO`, `SIN MODELO` |
| 11 px | `text-[11px]` | Notas secundarias dentro de una celda: regla aplicada, nocional aprobado |
| 12 px | `text-xs` | Explicaciones de gráfica, notas al pie, tabla alternativa, logs, insignia compacta |
| 13 px | `text-[13px]` | **El tamaño de la aplicación**: tablas, botones, controles, párrafos, títulos de sección y de bloque |
| 14 px | *(heredado)* | Lo que no dice otra cosa: nombres de experimento |
| 15 px | `text-[15px]` | Marca de la cabecera y título de un aviso a página completa |
| 17 px | `text-[17px]` | `<TituloPagina>` y la cifra grande de una tarjeta de métrica |

No hay `text-sm`, `text-base` ni `text-lg` en el proyecto: los pasos de Tailwind
caen entre los valores útiles para esta densidad, y mezclarlos daría dos escalas.

### Pesos

Solo dos, y es a propósito:

| Peso | Clase | Uso |
|---|---|---|
| 500 | `font-medium` | Símbolos, cabeceras de columna, énfasis dentro de una celda |
| 600 | `font-semibold` | Títulos, cifras, estados |

`font-bold` y superiores no se usan: a 13 px sobre fondo oscuro el semibold ya
carga suficiente, y el bold engorda la cifra sin hacerla más legible.

### Interletraje

| Clase | Uso |
|---|---|
| `tracking-tight` | `<TituloPagina>` y la marca de la cabecera |
| `tracking-wide` + `uppercase` | `<TituloSeccion>` |

### Cifras tabulares

**Toda cifra que se compare en vertical lleva `.tabular`** (`font-variant-numeric:
tabular-nums`, definido en `index.css`). Sin ancho fijo por dígito las columnas de
dinero bailan y la vista no puede recorrerlas de un salto.

`<Td numerica>` ya la aplica junto con `text-right` y `whitespace-nowrap`, así que
en una tabla no hay que acordarse. Fuera de las tablas —cifras de tarjeta,
recuentos en una insignia, listas de definición— se pone a mano.

---

## Espaciado

Escala estándar de Tailwind. Los valores que usa el proyecto:

| Propósito | Valor |
|---|---|
| Relleno de tarjeta | `p-4` (normal) · `p-3` (métrica) · `p-6` (aviso a página completa) |
| Relleno de celda | `px-3 py-1.5` (dato) · `px-3 py-2` (cabecera) |
| Relleno de control | `px-3 py-1` con `min-h-8` |
| Entre tarjetas de una rejilla | `gap-3` / `gap-4` |
| En línea (icono + texto, píldoras) | `gap-1.5` / `gap-2` |
| Bajo un título de página | `mb-5` |
| Entre secciones | `mb-6` / `mb-8` |
| Bajo un título de sección | `mb-3` |

`min-h-8` en todo lo pulsable: 32 px es el suelo de área táctil que se respeta en
botones, desplegables y campos.

---

## Radios y sombras

| Clase | Uso |
|---|---|
| `rounded-md` (0.5rem) | Botones, controles, logs, avisos, globos de gráfica |
| `rounded-lg` (0.625rem) | Tarjetas, tablas, paneles |
| `rounded-full` | Píldoras e indicadores de punto |

Nada por encima de `rounded-lg`: un radio grande come alto útil y en una tarjeta
de 13 px de texto se nota.

La única sombra es `shadow-[var(--shadow-card)]`, y **la aplica `<Tarjeta>`**, no
cada sitio. En oscuro vale `none` —una sombra negra sobre `#0d0d0d` no se ve y
solo ensucia el borde— y en claro es un `0 1px 2px` al 4 %. No hay sombras de
color ni de elevación alta.

---

## Componentes

Las piezas compartidas están en
[app/src/components/piezas.tsx](app/src/components/piezas.tsx). **No lleva colores
propios**: todo sale de los tokens, que es lo que hace que el interruptor de tema
no tenga que tocar ningún componente.

### Botones

Nunca a mano: `<Boton>` es quien carga el `min-h-8`, el `hover`, el estado
deshabilitado y el `type="button"`.

| Variante | Uso |
|---|---|
| `neutro` *(por defecto)* | Acción normal: lanzar un ciclo, paginar, volver |
| `sutil` | Acción de menor peso al lado de otra: «Lanzar en seco», interruptor de tema |
| `peligro` | Acción destructiva: «Parar» |

**No hay una variante sólida de marca, y es deliberado:** en una pantalla de datos
el relleno de color se reserva para las cifras, y un botón azul competiría con las
series de las gráficas por la misma atención.

```jsx
<Boton variante="peligro" disabled={!estado.running} onClick={parar}>Parar</Boton>
<Boton variante="sutil" icono={Moon} aria-label="Cambiar a tema oscuro">Oscuro</Boton>
```

Cuando quien tiene que llevar la apariencia de botón es un `<Link>` del enrutador
—porque navega, y navegar tiene que poder abrirse en otra pestaña— se comparte la
apariencia y no el elemento:

```jsx
<Link to="/perfiles" className={clasesBoton("neutro", "mt-4")}>Ver los experimentos</Link>
```

### Enlaces

```
underline decoration-border transition-colors hover:decoration-current
```

Exportado como `CLASES_ENLACE` para los `<Link>`, y como `<BotonEnlace>` para
cuando la acción no navega (abrir un detalle, plegar un log, cambiar a la vista de
tabla). El subrayado está **siempre**: quitarlo dejaría el enlace distinguible solo
por el color. Y es tenue en reposo para no competir con la cifra de al lado.

`<BotonEnlace>` es un `<button>` y no un `<a>` sin `href` porque el teclado y los
lectores de pantalla tienen que anunciarlo por lo que hace.

### Tarjeta

```
rounded-lg border border-border bg-card shadow-[var(--shadow-card)]
```

`<Tarjeta>` con `etiqueta` (`div` / `section` / `article`), `relleno` y
`discontinua`. Para un `<Link>` con forma de tarjeta, `clasesTarjeta()`.

**El relleno es una prop y no algo que se sobrescriba desde `className`**: `p-4` y
`px-4 py-6` no son el mismo grupo de utilidades, así que cuál gana lo decide el
orden de la hoja de estilos y no el de las clases. Pasarlo como prop es lo que hace
que `relleno="p-0"` —la tarjeta que envuelve una tabla— sea predecible.

`discontinua` añade `border-dashed` y quita la sombra: es el hueco de algo que
todavía no hay (estado vacío, pantalla pendiente).

### Tablas

[app/src/components/Tabla.tsx](app/src/components/Tabla.tsx): `<Tabla>`,
`<Cabecera>`, `<Th>`, `<Td>`, `<Fila>`, `<Vacio>`, `<Paginacion>`.

- `<Tabla titulo>` — el título es el `<caption class="sr-only">`, obligatorio.
  El contenedor lleva `overflow-x-auto`, **no la página**: una tabla ancha se
  desplaza dentro de su hueco sin arrastrar el resto de la pantalla.
- `<Th numerica>` / `<Td numerica>` — alinea a la derecha y aplica `.tabular`.
- `<Td encabezado>` — la celda que nombra la fila, como `<th scope="row">`. Sin
  ella un lector de pantalla lee «17,42» sin decir de qué símbolo.
- `<Vacio>` — el texto se redacta **caso por caso**. «No hay posiciones» y «no hay
  decisiones» significan cosas muy distintas en un experimento de diez días, y un
  texto genérico obliga a ir a mirar la base de datos para saber cuál de las dos es.
- `<Paginacion>` — enseña «41–80 de 480» y no solo las flechas: saber cuánto hay
  detrás es lo que dice si merece la pena seguir mirando.

Son a mano y no de shadcn a propósito: su tabla son envoltorios sobre `<table>`
sin nada de Radix debajo, así que copiarla aportaría un fichero más y ninguna
capacidad. shadcn se traerá cuando haga falta algo que sí necesita Radix.

La tabla **compacta** de dentro de una gráfica (`<TablaSimple>`, `text-xs`,
`py-1`) es una densidad aparte y legítima: vive dentro de una tarjeta que ya tiene
su propio relleno.

### Estados de carga, error y vacío

`<Seccion titulo consulta>` ([Seccion.tsx](app/src/components/Seccion.tsx))
resuelve los tres estados de una consulta en un sitio. Existe porque la
alternativa —`datos?.map(...)` en cada pantalla— pinta un error de la API como una
sección en blanco, y una sección en blanco se lee como «no hay nada»: en un
experimento de diez días, la diferencia entre «hoy no operó» y «llevo tres días sin
ver los datos».

- `<Cargando>` — `role="status"`, «Cargando…». **Es el único indicador que hay.**
  Las consultas van contra un SQLite local y terminan antes de que un placeholder
  llegue a pintarse; la única espera larga de verdad es la analítica, que avisa con
  su propio texto.
- `<Aviso>` — error en línea, con `role="alert"`. Sin él un lector de pantalla no
  dice nada: el foco sigue en el botón que acaba de pulsarse y el texto nuevo está
  en otra parte del documento.
- El caso vacío no está aquí, porque lo redacta cada pantalla (ver `<Vacio>`).

### Insignias

- `<Insignia>` — píldora con borde, para recuentos y estados de cabecera.
  `compacta` la baja a 12 px y `py-0.5`.
- `<Etiqueta>` — la etiqueta diminuta en mayúsculas pegada a una cifra: `VIVO`,
  `CICLO`, `SIN PRECIO`, `SIN MODELO`. Tonos `hereda` / `neutro` / `bueno` /
  `atencion` / `malo`.

**`<Etiqueta>` siempre lleva `title` con la frase entera.** Cuatro letras en
mayúsculas no explican nada por sí solas, y el color menos.

`<Procedencia>` ([Procedencia.tsx](app/src/components/Procedencia.tsx)) es la
aplicación de dominio: de dónde sale el precio de una posición (F3.2). Está
compartida porque las posiciones abiertas salen en dos pantallas y las dos copias
estaban divergiendo.

### Controles de formulario

`<Campo>`, `<Select>` y `<Entrada>`, todos sobre la misma receta:

```
min-h-8 rounded-md border border-border bg-card px-2 py-1 text-[13px]
transition-colors hover:bg-surface-sunken
```

`<Campo>` es un `<label>` que **envuelve** el control, no un `htmlFor` con un `id`
inventado: así la asociación no puede romperse al copiar el bloque, y pulsar el
texto enfoca el campo sin escribir nada más. `fila` pone la etiqueta a la
izquierda, para la cabecera, donde no hay alto que gastar.

`<Select>` sigue siendo el `<select>` nativo del navegador: las listas de esta
aplicación tienen tres o cuatro entradas y el nativo ya es accesible con teclado
sin traerse un popover. Se cambiará cuando haya que enseñar algo dentro de cada
opción.

### Títulos

| Componente | Resultado |
|---|---|
| `<TituloPagina secundario>` | `h1` 17 px semibold tight, con su `mb-5`. `secundario` va a la derecha con las líneas base alineadas |
| `<TituloSeccion>` | `h2` 13 px semibold, `uppercase tracking-wide`, en `text-secondary` |
| `<TituloBloque como>` | 13 px semibold para tarjetas y gráficas. `como` fija el nivel del encabezado sin cambiar la apariencia |

Los niveles se eligen por jerarquía del documento y no por tamaño: un aviso que
ocupa la pantalla entera usa `<TituloBloque como="h1" className="text-[15px]">`,
porque es el `h1` de esa pantalla aunque no tenga el tamaño de un título de página.

### Bloque preformateado

`<Bloque>` — `overflow-auto rounded-md bg-surface-sunken p-3 text-xs`, para logs,
JSON de parámetros y órdenes de consola.

---

## Gráficas

Solo en Analítica, con Recharts v3, cargado con `lazy()` porque pesa casi tanto
como el resto de la aplicación junta.

Lo compartido está en
[graficas/base.tsx](app/src/components/graficas/base.tsx): `COLORES`, `EJE`,
`<Grafica>`, `<Globo>` y `<TablaSimple>`.

- **Los colores se pasan como `var(--color-…)` y nunca como hexadecimal** (ver la
  regla dura de arriba). `COLORES.tenue` y `COLORES.cursor` cubren las etiquetas y
  el resalte que Recharts pinta como SVG.
- **Ejes discretos:** `stroke` en `--axis`, 11 px, sin `tickLine` ni `axisLine`. La
  rejilla es referencia, no protagonista.
- **Trazo de 2 px** en líneas y áreas, y **sin punto por dato**: con un punto por
  ciclo y diez sesiones, marcarlos todos convierte la línea en un collar. El punto
  aparece solo bajo el cursor (`activeDot`).
- **Toda gráfica tiene vista de tabla**, con el conmutador en su cabecera. No es un
  extra: es lo que mantiene el dato disponible cuando el color no basta
  —daltonismo, impresión, lector de pantalla— y es como se comprueba una cifra
  concreta, que en una gráfica se estima y en una tabla se lee.
- **Toda gráfica tiene estado vacío redactado**, diciendo qué falta para que haya
  algo que dibujar.
- El globo es propio: el de Recharts trae sus colores y no conoce la paleta.
- El color puede codificar **polaridad** (azul lo que ganó, rojo lo que perdió) o
  **identidad** (serie 1, serie 2), nunca las dos cosas en la misma gráfica. Cuando
  codifica polaridad no hay leyenda, porque no hay series que distinguir: hay una
  línea en el cero, que es donde está el significado.
- Una escala de tres con neutro en medio —comprar / mantener / vender— es un
  **divergente**, y al medio le toca el gris. Un categórico exigiría croma en los
  tres y aquí el gris es lo correcto.
- Barras horizontales cuando las etiquetas son nombres de regla: en vertical se
  solaparían o habría que girarlas, que es peor.

**Las muestras pequeñas se marcan.** En la calibración, los tramos con menos de
cinco operaciones salen al 35 % de opacidad y cada barra lleva su `n=` encima. Sin
eso la gráfica miente en su momento más peligroso: un tramo con una sola operación
ganadora dibuja una barra del 100 % idéntica a la de un tramo con treinta, y es
justo al principio cuando más ganas dan de sacar conclusiones.

---

## Tema claro y oscuro

**El oscuro es el de partida** (F4.2). `<html>` ya viene con `class="dark"` y un
script en línea en [app/index.html](app/index.html) la quita antes de pintar si hay
otra preferencia guardada, para que no haya fogonazo del tema equivocado.

El interruptor manual gana a la preferencia del sistema **en los dos sentidos**:
`@custom-variant dark (&:is(.dark *))` hace que la variante `dark:` sea una clase y
no una media query.

`<BotonTema>` y el script comparten la clave `tema` de `localStorage`. Si alguien
cambia el nombre en un sitio y no en el otro, el síntoma es que la preferencia deja
de recordarse entre recargas sin que nada falle.

Un componente escrito con tokens (`bg-card`, `text-text-muted`) **no necesita
ninguna clase `dark:`**: la variable ya vale otra cosa. La variante `dark:` queda
para los casos raros en los que la escala de tokens no encaja; hoy no se usa en
ningún componente.

---

## Animación

Casi ninguna, y es una decisión.

| Qué | Cómo | Dónde |
|---|---|---|
| Latido | `animate-pulse` | El punto del indicador de datos en vivo, y solo ahí |
| Cambio de color | `transition-colors` | `hover` de botones, controles, filas pulsables |

El cambio de tema es **instantáneo**: no hay transición en `body`. Con una pantalla
llena de bordes y texto, interpolar fondo y color deja medio segundo de estado
intermedio en el que no se lee bien ni un tema ni el otro.

No hay entradas animadas, ni esqueletos, ni barridos, ni orbes: los datos llegan de
un SQLite local y una animación de carga de 200 ms es ruido, no información.

`prefers-reduced-motion: reduce` reduce toda animación y transición a `0.01ms` de
forma global en `index.css`. Quien pide movimiento reducido lo pide en serio: el
punto que late es decorativo —el texto ya dice el estado— y a algunas personas les
provoca mareo.

---

## Distribución

```html
<!-- Marco de la aplicación: cabecera + barra lateral + contenido -->
<div class="mx-auto flex max-w-7xl flex-col gap-6 px-5 py-6 md:flex-row">
  <aside class="md:w-52 md:shrink-0">…</aside>
  <main id="contenido" tabindex="-1" class="min-w-0 flex-1 pb-16">…</main>
</div>
```

`min-w-0` en el `<main>` no es decorativo: sin él una tabla ancha estira el
contenedor flex y rompe el `overflow-x-auto` de la tabla.

### Puntos de ruptura

| Prefijo | Ancho | Uso |
|---|---|---|
| *(ninguno)* | todo | Móvil: una columna, barra lateral encima del contenido |
| `sm:` | ≥ 640 px | Rejillas de 2 columnas |
| `md:` | ≥ 768 px | La barra lateral pasa a un lado |
| `lg:` | ≥ 1024 px | Rejillas de 4 columnas (las métricas del resumen) |
| `xl:` | ≥ 1280 px | Gráficas en dos columnas |

**Regla móvil primero:** se parte del diseño de una columna y se ensancha con
prefijos. Nunca al contrario.

El `<Layout>` abre el stream SSE **una sola vez**, y las pantallas leen de la caché
de Query. Si cada pantalla llamara a `useStream()` habría una conexión por pantalla
montada y el servidor haría el mismo sondeo a SQLite tantas veces como pestañas
abiertas.

---

## Iconos

**Lucide React** (`lucide-react` v1.x), solo en la barra lateral y el interruptor
de tema.

- Tamaño `size-3.5` (14 px), que es el que cuadra con texto de 13 px.
- Grosor de trazo por defecto.
- El color **siempre se hereda** del texto de alrededor.
- `shrink-0` cuando van dentro de un flex con texto que puede partirse.
- Siempre `aria-hidden`: lo que dice el elemento es su texto. `<Boton icono>` ya lo
  pone.

---

## Accesibilidad

Es la mitad de F4.9 y está en el sistema, no en un repaso final.

- **Foco visible en todo:** `:focus-visible { outline-2 outline-offset-1
  outline-ring }` en la capa base de `index.css`, así que cubre también cualquier
  control que se escriba a mano.
- **Enlace de salto:** `.salto` es `sr-only` hasta que recibe foco. Sin él, llegar
  al contenido con teclado obliga a recorrer la cabecera y las nueve entradas de la
  barra lateral **en cada página**.
- **El color nunca es el único portador del significado.** Todo estado con color
  lleva además texto (`sano`, `aprobado`, `datos en vivo`) o un `title` que lo
  explica. El indicador en vivo tiene tres estados y no dos, porque agrupar
  «reconectando» con «desconectado» parpadearía en rojo cada cuarto de hora en una
  conexión sana —el servidor retira las conexiones cada 15 minutos a propósito— y
  entonces tampoco se creería el rojo de verdad.
- **Tablas:** `<caption>` obligatorio, `scope="col"` en cabeceras y
  `scope="row"` en la celda que nombra la fila.
- **Botones de solo icono:** `aria-label` obligatorio. Iconos y puntos decorativos:
  `aria-hidden`.
- **Estados:** carga con `role="status"`, errores con `role="alert"`, conmutadores
  con `aria-pressed`, plegables con `aria-expanded`, fila elegida con
  `aria-current`.
- **Logs en vivo:** `aria-live="polite"` y nunca `assertive` — son cientos de
  líneas y un lector de pantalla las anunciaría todas.
- **Área táctil:** `min-h-8` en todo lo pulsable.
- **Desplazamiento contenido:** el `overflow-x-auto` va en el contenedor de la
  tabla, para que la página nunca se desplace en horizontal.

---

## Lo que no hay, y por qué

Escrito para que no se reintroduzca por descuido:

| No hay | Por qué |
|---|---|
| Glassmorfismo (`backdrop-blur`, superficies translúcidas) | Un fondo translúcido bajo una tabla de cifras baja el contraste del texto justo donde más se lee |
| Verde de marca | El verde está reservado para `delta-good`. Un verde de marca haría que «botón» y «sube» compartieran color |
| Verde/rojo como par de P&L | Rompería el divergente azul/rojo, que es lo que hace la paleta legible en daltonismo |
| Fuente importada | 20–40 KB y un fogonazo para no ganar nada legible en una tabla de cifras |
| Radios grandes (`rounded-2xl`+) | Comen alto útil en tarjetas densas |
| Sombras de color o de elevación alta | En oscuro no se ven y en claro despegan tarjetas que están al mismo nivel |
| Esqueletos, barridos, orbes de carga | Las consultas van contra SQLite local: no hay espera que amortice el placeholder |
| Animaciones de entrada | Retrasan la lectura del dato, que es lo único que se viene a hacer |

El dashboard viejo, [web/index.html](web/index.html), es el **origen** de esta
paleta pero no lo gobierna este documento: es un fichero de 1.510 líneas que
`run.py serve` todavía sirve y que se borra cuando el panel nuevo lo cubra.
