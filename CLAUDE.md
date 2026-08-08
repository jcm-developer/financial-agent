# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Los tres documentos que hay que leer

| Documento | Qué es | Cuándo leerlo |
|---|---|---|
| [TASKS.md](TASKS.md) | El plan de trabajo. Cada tarea tiene un id (`F4.9`) que se cita en los commits | **Antes de empezar cualquier cosa**, para saber si lo pedido ya tiene id y contexto |
| [DESIGN.md](DESIGN.md) | El sistema de diseño: paleta, escala, componentes, accesibilidad | **Obligatorio antes de tocar `app/`** |
| [README.md](README.md) | Qué es el proyecto y qué esperar de él | Para entender el porqué del experimento |

`TASKS.md` no es una lista de deseos: lleva las **decisiones de arquitectura** (D1–D8), los
riesgos vigilados (R1–R8) y, en cada tarea cerrada, lo que se descartó y por qué. Si algo del
código parece raro, la explicación suele estar ahí.

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
- **Penúltima línea:** la verificación (`607 tests en verde, typecheck limpio`).
- **Última línea:** `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## Comandos

### Backend (Python 3.12)

```bash
python -m pytest tests -q                            # los 607 tests
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

⚠️ **`docker compose down -v` destruye el volumen `trading-data`**, o sea el histórico del
experimento. La base vive en un volumen con nombre y no en un bind mount porque el bloqueo de
ficheros de SQLite no es fiable sobre bind mounts de Docker Desktop en Windows.

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

`GET /api/stream` (SSE) **por dentro sondea**: el ingestor está en otro proceso y no hay bus
de eventos. Lo que gana es mover el sondeo del navegador al servidor. Las conexiones caducan a
los 15 minutos a propósito — `EventSource` reconecta solo y así se relee la lista de símbolos.

### Frontend

React 19 + Vite 8 + TypeScript 7 + Tailwind v4, todo en español (`paginas/`, `Tabla`,
`Boton`). Recharts solo en Analítica, cargada con `lazy()` porque pesa casi tanto como el
resto junto.

- **TanStack Query es la única caché.** El SSE escribe en ella con `setQueryData`, nunca en un
  estado paralelo de React: con dos fuentes para el mismo precio la pantalla enseña dos
  números distintos y no hay un sitio donde arreglarlo. Las claves viven solo en
  [app/src/api/keys.ts](app/src/api/keys.ts).
- **El evento `ingest` se funde, no reemplaza**: manda 5 campos y el endpoint devuelve 13.
- **El perfil activo vive en la URL** (`/p/europa-01/posiciones`), por su nombre y no por id.
- Las pantallas se arman con los endpoints tipados, no con `/api/dashboard`, que es legado y se
  borra con `web/` en F8.2.

`web/index.html` (1.510 líneas) es el dashboard viejo, todavía servido por `run.py serve`. Se
retira en F4.11/F8.2; no se le añaden funciones.

---

## Invariantes que no se rompen

- **La paleta no se toca.** El par positivo/negativo es **azul/rojo y no verde/rojo** para que
  se lea sin distinguir el verde del rojo (ΔE 21,6 en protanopía). Los contrastes se midieron
  en F4.9. Cambiarla desharía las dos cosas sin que nada avise. Ver DESIGN.md.
- **Ningún hexadecimal en `className`.** Se usa la utilidad del token; en SVG,
  `var(--color-…)`.
- **Ninguna receta de clases se escribe a mano.** Botón, tarjeta, aviso, insignia, control,
  título y bloque salen de [app/src/components/piezas.tsx](app/src/components/piezas.tsx).
- **El símbolo de divisa se pasa siempre, nunca se asume** (FE.8): un presupuesto europeo
  escrito con `$` invita a compararlo con otro como si fuera la misma unidad.
- **El color nunca es el único portador del significado**: siempre con texto o `title`.
- **Los estados vacíos se redactan caso por caso.** «No hay posiciones» y «no hay decisiones»
  significan cosas distintas, y un texto genérico obliga a ir a mirar la base de datos.
- **Los comentarios explican por qué, y qué se descartó**, no qué hace el código. Es el
  registro real del proyecto; mantén ese registro al escribir código nuevo.
- **Hay un `.env` con claves reales** en el directorio. Está en `.gitignore`: nunca se añade
  al commit ni se imprime su contenido.
