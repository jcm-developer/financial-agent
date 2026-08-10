# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Los cuatro documentos que hay que leer

| Documento | Qué es | Cuándo leerlo |
|---|---|---|
| [TASKS.md](TASKS.md) | El plan de trabajo. Cada tarea tiene un id (`F4.9`) que se cita en los commits | **Antes de empezar cualquier cosa**, para saber si lo pedido ya tiene id y contexto |
| [EXPERIMENT.md](EXPERIMENT.md) | La lógica real de un experimento: los dos relojes, la anatomía de un ciclo, los tres precios, qué ve el modelo y qué no | **Obligatorio antes de tocar `src/cycle.py`, el screener, el risk manager o el planificador** |
| [DESIGN.md](DESIGN.md) | **Verdana Health**: paleta, escala, componentes, accesibilidad y lo que se perdió al adoptarlo | **Obligatorio antes de tocar `app/`** |
| [README.md](README.md) | Qué es el proyecto y qué esperar de él | Para entender el porqué del experimento |

`TASKS.md` no es una lista de deseos: lleva las **decisiones de arquitectura** (D1–D8), los
riesgos vigilados (R1–R8) y, en cada tarea cerrada, lo que se descartó y por qué. Si algo del
código parece raro, la explicación suele estar ahí.

`EXPERIMENT.md` describe **cómo opera el agente de verdad**, y existe porque esa lógica
estaba repartida entre docstrings de seis módulos. Lleva además la lista de lo que el sistema
**no** hace y dónde está apuntado, para no volver a deducirlo del código. Cuando algo de ahí
deje de ser cierto, se corrige ahí.

---

## Al cerrar una tarea, siempre

En este orden, y sin que haga falta pedirlo:

1. **Verificar.** `python -m pytest tests -q` y, si se ha tocado `app/`,
   `npm run typecheck` y `npm test`.
2. **Marcar la tarea en TASKS.md** (`[x]`) y anotar lo que se decidió y lo que se
   descartó, con el mismo registro que el resto del fichero. Actualizar también la
   línea «Última actualización» de la cabecera.
3. **Commit y push.** Se hace siempre, al terminar, sin preguntar. Va directo a `main`:
   el historial del repo es lineal y de un solo autor.
4. **Redesplegar en Docker** si el cambio tiene que verse corriendo (ver abajo).

### Estilo de los mensajes de commit

Mira `git log` antes de escribir. La convención es propia y conviene respetarla:

- **Asunto:** id de tarea, dos puntos, y qué se hizo, con el matiz que importa —
  `F4.9: accesibilidad, y el tema claro tenia tres fallos de contraste`. Si el trabajo no
  tiene id en `TASKS.md`, se deja sin id; **no se inventa uno**.
- **En español, sin acentos en vocales pero conservando la ñ** (`graficas`, `tenia`, `añaden`).
- **Cuerpo largo y explicativo**, en prosa: qué se decidió, qué se descartó, qué medición
  lo respalda, qué consecuencia hay que asumir. Los apartes van con ` -- `.
- **Penúltima línea:** la verificación (`665 tests en verde, typecheck limpio`).
- **Última línea:** `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## Idioma: el código en inglés, lo que se lee en español

Hay **tres cubos** y confundirlos es el error fácil, porque la regla no es «todo en un
idioma» sino «cada cosa en el idioma de quien la lee».

| Qué | Idioma | Alcance exacto |
|---|---|---|
| **Código** | **Inglés, estrictamente** | Identificadores (variables, funciones, clases, componentes, props, hooks, columnas, endpoints), **nombres de fichero y de carpeta**, comentarios, docstrings, nombres de test, mensajes de log y de excepción, claves de JSON internas |
| **Texto de la interfaz** | **Español** | Todo lo que aparece en pantalla: rótulos, estados vacíos, avisos, `aria-label`, `title`, títulos de página. Es el idioma del producto, no del código |
| **Documentación y commits** | **Español** | La prosa de los cuatro documentos y los mensajes de commit, con la convención de acentos de arriba |

Nunca se mezclan los dos primeros en una misma línea. El caso típico es una constante de
copy: el **nombre** va en inglés y el **valor** en español.

```tsx
const EMPTY_POSITIONS = "No hay posiciones abiertas en este experimento";
```

**Por qué el código en inglés y no en español, si el proyecto es de un solo autor:** porque
la mitad del vocabulario ya lo impone el dominio y no se puede traducir sin mentir —`stop`,
`fill`, `drawdown`, `equity`, `screener`, y los nombres de columna de `schema.sql`—, así que
un código en español acaba siendo código en dos idiomas con la frontera puesta donde tocó.
`ordenes` junto a `filled_qty` no es español: es ruido.

**Por qué la documentación se queda en español:** es el registro de trabajo, se lee entero y
de una sentada, y traducirlo no le añadiría nada a nadie. Es una decisión distinta de la de
arriba, no una excepción a ella.

El proyecto **cumple la tabla entera** desde F8.8 (2026-08-09): identificadores, nombres
de fichero y de carpeta, comentarios, docstrings y nombres de test están en inglés en
`app/`, `src/`, `api/`, `tools/`, `run.py` y `tests/`, y el texto de pantalla sigue en
español.

**La única excepción son los mensajes de log y de excepción, y es deliberada.** El log del
ciclo se muestra tal cual en la pantalla de Ciclos, así que es texto de pantalla; y
[api/runner.py](api/runner.py) deduce la etapa buscando cadenas dentro de él («Resumen del
ciclo», «RECHAZADA»), de modo que traducirlos apagaría el indicador de progreso sin que
ningún test lo notara. Si algún día se traducen, se tocan los dos sitios a la vez.

---

## Nomenclatura de ficheros y carpetas

La regla que las une todas: **un fichero se llama como lo que exporta.** Si al renombrar el
export no hace falta renombrar el fichero, el nombre está mal puesto.

| Qué | Convención | Ejemplos |
|---|---|---|
| Carpetas | minúsculas, una sola palabra, sin guiones ni `camelCase` | `src`, `api`, `tools`, `tests`, `universe`, `api/routes`, `app/src/components` |
| Módulos Python | `snake_case.py` | `market_calendar.py`, `sim_broker.py`, `bar_cache.py` |
| Tests Python | `test_<módulo>.py`, con el nombre exacto del módulo que prueban | `test_risk.py` ← `src/risk.py` |
| Fichero que exporta **un** componente React | `PascalCase.tsx`, idéntico al componente | `Table.tsx` → `<Table>`, `ProfileSelector.tsx` → `<ProfileSelector>` |
| Fichero que agrupa **varios** exports | `camelCase.tsx` / `.ts`, con nombre de colección | `pieces.tsx`, `charts/base.tsx`, `format.ts`, `keys.ts` |
| Hooks | `useAlgo.ts`, idéntico al hook | `useTitle.ts`, `useActiveProfile.ts` |
| Tests de TypeScript | `<fichero>.test.ts`, al lado del que prueban | `stream.test.ts` ← `stream.ts` |
| Documentos de raíz | `MAYUSCULAS.md` | `README.md`, `TASKS.md`, `DESIGN.md` |

**Sin sufijos de tipo en el nombre:** ni `TablaComponent.tsx`, ni `utilsHelper.ts`, ni
`risk_module.py`. La carpeta ya dice qué es y la extensión ya dice con qué está escrito.

**Una carpeta por concepto, no por variante.** Se añade una carpeta cuando hay algo que
agrupar de verdad (`components/charts/`, `api/routes/`), no para separar dos ficheros que
se parecen.

---

## Comandos

### Backend (Python 3.12)

```bash
python -m pytest tests -q                            # los 665 tests
python -m pytest tests/test_risk.py -q                # un módulo
python -m pytest tests/test_risk.py::test_nombre -q   # un solo test
python -m pytest tests -q -k "calendario"             # por nombre

python run.py check          # diagnóstico; el único comando que corre sin perfil
python run.py api            # API + frontend en http://127.0.0.1:8000 (--host, --port)
python run.py cycle --dry-run  # un ciclo que analiza y registra pero no ordena
python run.py profiles       # listar experimentos
python run.py new-profile --name europa-01 --market eu --watch 89
python run.py report         # analítica en consola
```

No hay linter ni formateador configurado (ni `ruff` ni `eslint`): la verificación son los
tests y el typecheck. No añadas uno sin que se pida.

### Frontend (desde `app/`)

```bash
npm run dev        # Vite en http://localhost:5173, con proxy a la API
npm run typecheck  # tsc --noEmit; strict + noUnusedLocals
npm test           # vitest run
npm run build      # typecheck y luego el bundle en app/dist
```

`npm run dev` necesita la API arriba (`python run.py api`, u `docker compose up -d api
ingestor`). En Windows, **Vite escucha en `localhost` (`::1`), no en `127.0.0.1`**.

Si cambian los modelos Pydantic de [api/models.py](api/models.py), hay que regenerar los
tipos de TypeScript:

```bash
python tools/gen_api_types.py            # reescribe app/src/api/types.ts
python tools/gen_api_types.py --check    # falla si están desfasados
```

### Docker

Una sola imagen para los cuatro servicios (`api`, `ingestor`, `scheduler`, `bot`); lo único
que cambia es el `command`. **El frontend se compila dentro de la imagen** (etapa 1 del
[Dockerfile](Dockerfile)), así que un cambio en `app/` **no se ve reiniciando: hay que
reconstruir**.

```bash
docker compose up -d --build         # reconstruir y redesplegar — el gesto normal al terminar
docker compose up -d --build api     # solo la API
docker compose logs -f api           # o scheduler, ingestor
docker compose ps
docker compose run --rm bot python run.py check
docker compose run --rm bot python -m pytest tests -q
docker compose cp api:/app/data/trading.db ./data/trading.db   # sacar la base
```

La base vive en un volumen con nombre —**`financial-agent-trading-data`**, declarado
`external` desde el 2026-08-10— y no en un bind mount, porque el bloqueo de ficheros de
SQLite no es fiable sobre bind mounts de Docker Desktop en Windows. Al ser externo,
**`docker compose down -v` ya no puede destruirlo**; a cambio, en una máquina nueva hay que
crearlo antes del primer `up`:

```bash
docker volume create financial-agent-trading-data
```

Si no existe, `up` falla en el sitio en vez de arrancar un experimento con la base vacía.
Para borrar el histórico de verdad hace falta pedirlo por su nombre:
`docker volume rm financial-agent-trading-data`.

El puerto se publica en `127.0.0.1:8000` a propósito: son datos de una cuenta de inversión y
la API no tiene autenticación.

---

## Arquitectura

### El ciclo, y por qué el LLM no puede operar

```
screener (liquidez, universo del mercado)
   └─→ market_data + indicators (RSI, ATR, SMA sobre barras de bar_cache)
         └─→ analyst.py — el LLM propone {acción, convicción, tesis, stop, objetivo}
               └─→ risk.py — motor DETERMINISTA: aprueba, dimensiona o rechaza
                     └─→ sim_broker.py — ejecuta
                           └─→ SQLite: cycles, decisions, orders, positions, risk_events
```

[src/cycle.py](src/cycle.py) orquesta. **El modelo nunca ejecuta**: propone, y
[src/risk.py](src/risk.py) decide con reglas fijas. Ese corte es la premisa del proyecto, no
una capa de seguridad opcional. [src/broker.py](src/broker.py) es el protocolo que espera el
ciclo, para que añadir un broker real no toque `cycle.py`.

### Cuatro procesos

`api` (FastAPI: sirve `app/dist` + REST + SSE) · `ingestor` (un tick por minuto en ventana de
mercado) · `scheduler` (lanza ciclos a las horas de `CYCLE_TIMES`) · `bot` (comandos
puntuales, perfil `manual` de compose). Separados a propósito: si Yahoo se cuelga, ni la
interfaz ni el agente se enteran.

### La configuración vive en el perfil, no en el `.env`

`agent_settings` tiene 41 columnas por perfil; del `.env` solo sale la infraestructura
([src/config.py](src/config.py)). Los límites duros del risk manager **nacen NULL a
propósito**: NULL significa «derívalo de los sliders», y si nacieran con números mover el
slider de riesgo no cambiaría nada.

`cycles.settings_json` guarda copia de los parámetros con los que corrió cada ciclo, así que
un histórico se puede interpretar después.

### El mercado es una columna del perfil (D8)

`agent_settings.market` es `eu` o `us`, y de ahí salen horario, calendario, divisa, benchmark,
sufijos de bolsa y suelo de liquidez ([src/market_calendar.py](src/market_calendar.py),
registro `MARKETS`).

- **Un perfil cubre una sola bolsa**, y es una restricción, no un descuido: el proyecto **no
  convierte divisa en ningún sitio**. `resolve_settings` rechaza un universo que mezcle bolsas.
- **La ventana operativa no es la sesión** (FE.13): en Europa se trabaja de 09:15 a 17:45
  sobre una sesión de 09:00–17:30. `is_session_open()` dice la verdad de mercado —se guarda en
  `cycles.market_open`— e `is_operating()` responde si nos toca capturar y analizar.

### La API no puede escribir en el histórico, y no por convención

Las lecturas abren SQLite en modo `ro`. Las escrituras pasan por
[api/guard.py](api/guard.py), que usa el **autorizador de SQLite**: un `insert into decisions`
falla con «not authorized» al compilar la sentencia. Escribibles solo `profiles`,
`agent_settings`, `agent_settings_history`, `profile_universe` y `portfolios`. La API tampoco
ejecuta SQL libre. Por eso lanzar un ciclo es un subproceso ([api/runner.py](api/runner.py)) y
no una llamada en proceso.

**Los dos ficheros del volumen compartido.** El ciclo y la API no comparten
proceso —ni contenedor, si lo lanzó el planificador—, así que lo que tienen que
decirse va en dos ficheros junto a la base, y no por la red ni por la base:
[src/cycle_log.py](src/cycle_log.py) (`cycle.log`, lo escribe `run.py` y lo lee la
API para el log en vivo de la pantalla de Ciclos) y
[src/stop_signal.py](src/stop_signal.py) (`stop.request`, lo escribe la API con el
id del ciclo a parar y lo mira el ciclo en cada punto de control). Ninguno es
histórico, así que la garantía de arriba sigue entera: la API pide, el ciclo
decide y registra.

`GET /api/stream` (SSE) **por dentro sondea**: el ingestor está en otro proceso y no hay bus
de eventos. Lo que gana es mover el sondeo del navegador al servidor. Las conexiones caducan a
los 15 minutos a propósito — `EventSource` reconecta solo y así se relee la lista de símbolos.

### Frontend

React 19 + Vite 8 + TypeScript 7 + Tailwind v4, todo en inglés (`pages/`, `Table`,
`Button`), con el texto de pantalla en español. Recharts solo en Analítica, cargada con `lazy()` porque pesa casi tanto como el
resto junto.

El sistema visual es **Verdana Health** desde el 2026-08-10 (ver [DESIGN.md](DESIGN.md)).
Plus Jakarta Sans, DM Sans y Fira Code van **autoalojadas y empaquetadas** —101 KB de woff2,
solo el subconjunto latino—, no por CDN: la API se sirve desde un contenedor en `127.0.0.1`
sin salida a internet garantizada. Los controles de formulario son propios
(`Select`, `Checkbox`, `RadioGroup`, `Tooltip`): el `<select>` nativo dibuja su lista con el
widget del sistema operativo, que es la única superficie a la que el sistema de diseño no
llega.

- **TanStack Query es la única caché.** El SSE escribe en ella con `setQueryData`, nunca en un
  estado paralelo de React: con dos fuentes para el mismo precio la pantalla enseña dos
  números distintos y no hay un sitio donde arreglarlo. Las claves viven solo en
  [app/src/api/keys.ts](app/src/api/keys.ts).
- **El evento `ingest` se funde, no reemplaza**: manda 5 campos y el endpoint devuelve 13.
- **El perfil activo vive en la URL** (`/p/europa-01/positions`), por su nombre y no por id.
- **Las pantallas se arman con los endpoints tipados.** Todos los endpoints tienen modelo
  Pydantic desde F4.11: el único que no lo tenía, `/api/dashboard`, se retiró con el dashboard
  viejo. Un cambio del backend rompe el build del frontend, no la pantalla en caliente.

`web/` ya no existe: el dashboard de 1.510 líneas y su `run.py serve` se borraron en F4.11
(F8.2). La interfaz es `app/`, servida por `run.py api`.

---

## Invariantes que no se rompen

- **La paleta es la de Verdana Health y no se mezcla con otra.** Navy `#0F172A` como ritmo
  primario, salvia `#059669` reservada a lo interactivo y a lo positivo, y los cuatro colores
  de estado **con sus dos niveles**: el saturado es la *marca* (relleno de gráfica, fondo de
  chip al 8 %) y el profundo es la *tinta* (texto). Confundirlos es el error fácil. Ver
  DESIGN.md.
- **Tema único, solo claro.** No hay `.dark`, ni variante `dark:`, ni interruptor. Añadir un
  `dark:` es reabrir un sistema que se cerró a propósito.
- **Ningún hexadecimal en `className`.** Se usa la utilidad del token; en SVG,
  `var(--color-…)`.
- **La escala tipográfica son los diez pasos de Verdana** (`text-h1`, `text-body-sm`,
  `text-caption`…), cada uno con su peso y su interlineado dentro del token. No se usan
  `text-sm`, `text-base` ni `text-lg`: serían una segunda escala. ⚠️ **Todo tamaño nuevo hay
  que declararlo además en `cn()`** ([app/src/lib/utils.ts](app/src/lib/utils.ts)) o
  `tailwind-merge` lo tomará por un color y borrará el color de al lado, en silencio.
- **Ninguna receta de clases se escribe a mano.** Botón, tarjeta, aviso, insignia, control,
  título y bloque salen de [app/src/components/pieces.tsx](app/src/components/pieces.tsx).
- **El símbolo de divisa se pasa siempre, nunca se asume** (FE.8): un presupuesto europeo
  escrito con `$` invita a compararlo con otro como si fuera la misma unidad.
- **El color nunca es el único portador del significado**: siempre con texto o `title`.
- **El código va en inglés y el texto de pantalla en español**, sin mezclarlos en la misma
  línea. Los nombres de fichero y de carpeta son código, así que van en inglés y siguen la
  tabla de nomenclatura de arriba.
- **Los estados vacíos se redactan caso por caso.** «No hay posiciones» y «no hay decisiones»
  significan cosas distintas, y un texto genérico obliga a ir a mirar la base de datos.
- **Los comentarios explican por qué, y qué se descartó**, no qué hace el código. Es el
  registro real del proyecto; mantén ese registro al escribir código nuevo.
- **Hay un `.env` con claves reales** en el directorio. Está en `.gitignore`: nunca se añade
  al commit ni se imprime su contenido.
