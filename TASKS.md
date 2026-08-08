# TASKS.md — Plan de trabajo

Registro de todo lo pendiente. Cada tarea tiene un id (`F1.2`) para referenciarla en
commits y conversaciones. Marcar `[x]` al cerrarla.

Última actualización: 2026-08-08 (tarde: mercado europeo)

---

## 0. Punto de partida y destino

**Hoy:**

- Agente en Python: [src/cycle.py](src/cycle.py) orquesta screener → datos → LLM
  ([src/analyst.py](src/analyst.py)) → risk manager ([src/risk.py](src/risk.py)) → broker
  (simulado en [src/sim_broker.py](src/sim_broker.py)).
- Persistencia en SQLite ([src/db.py](src/db.py) + [schema.sql](schema.sql)).
- Configuración por perfil en `agent_settings`; del `.env` solo sale la
  infraestructura ([src/config.py](src/config.py)).
- Dashboard: HTML de 1.500 líneas ([web/index.html](web/index.html)) servido por
  `http.server` ([web/server.py](web/server.py)).
- Datos de mercado con `yfinance`, barras 1d/1h, caché en `bar_cache`.
- Docker ya montado ([docker-compose.yml](docker-compose.yml)): `dashboard`, `scheduler`, `bot`.
- **Dos bolsas**: cada perfil elige la suya en `agent_settings.market` (`eu` o `us`).
  El universo europeo son 89 valores del EURO STOXX 50 + IBEX 35, todos en euros
  ([universe/eurostoxx50_ibex35.txt](universe/eurostoxx50_ibex35.txt)).

**Destino:** todo local en Docker, SQLite como base, precios refrescados cada minuto en
horario de bolsa US, frontend React + Tailwind, perfiles de experimento con parámetros
editables, y NVIDIA NIM (capa gratuita) como proveedor de modelo.

**Coste: 0 €.** Sin nube, sin servicios de pago.

**Alpaca fuera (2026-08-08).** Se ha eliminado del proyecto entero: el broker, el
proveedor de datos, las claves, el extra de dependencias y la documentación. El
único broker es el simulador de [src/sim_broker.py](src/sim_broker.py), y
[src/broker.py](src/broker.py) queda como el protocolo que el ciclo espera —así
añadir uno real más adelante no toca `cycle.py`. **Consecuencia que hay que
asumir: desaparece el plan B de datos de mercado** (ver R2).

---

## 1. Decisiones de arquitectura

### D1 — Cuatro servicios en el mismo `docker compose` ✅

| Servicio | Qué hace | Estado |
|---|---|---|
| `api` | Sirve el build de React + API REST + SSE de precios en vivo | Sustituye a `dashboard` |
| `ingestor` | Cada minuto en horario de mercado: descarga cotizaciones → SQLite | **Nuevo** |
| `scheduler` | Lanza los ciclos del agente a sus horas | Ya existe |
| `bot` | Comandos puntuales (`check`, `report`, tests) | Ya existe |

Procesos separados a propósito, siguiendo lo que ya hace el repo: si el ingestor se cuelga
contra Yahoo, ni la interfaz ni el agente se enteran.

### D2 — El agente se queda en Python ✅

La razón para portarlo a TypeScript era Cloudflare Workers. Sin Cloudflare, no hay razón:
[src/indicators.py](src/indicators.py), [src/risk.py](src/risk.py),
[src/screener.py](src/screener.py) y [src/market_calendar.py](src/market_calendar.py) se
quedan como están, con sus tests. **Se ahorran ~2.900 líneas de port y toda su validación
cruzada.** El trabajo real pasa a ser el esquema nuevo, el ingestor, la API y el frontend.

### D3 — Datos de mercado: `yfinance`, ya integrado ✅

`yf.download(simbolos, period="1d", interval="1m")` y `yfinance` resuelve por dentro la
cookie y el *crumb* que Yahoo exige desde 2023. Eso elimina el riesgo grande del plan
anterior (hablar con la API no oficial de Yahoo a mano desde un Worker).

⚠️ **Corrección tras el spike (F2.1): no es una petición en lote.** yfinance pide **un
endpoint por símbolo**, así que 50 símbolos son 50 peticiones HTTP a Yahoo por minuto.
Medido el 2026-08-07 con 50 símbolos:

| Configuración | Tiempo | Cobertura |
|---|---|---|
| `threads=False` (lo que usa hoy [src/market_data.py](src/market_data.py)) | 8,4 s | 50/50 |
| `threads=True` | 1,5 s | 50/50 |

Consecuencias: cabe de sobra en el minuto con 50 símbolos, pero **escala mal** — en serie son
~170 ms/símbolo, así que ~200 símbolos agotarían el minuto. Y la exposición al rate limit es
50 peticiones/minuto, no 1 (ver R2).

Sobre `threads`: [src/market_data.py](src/market_data.py) fuerza `threads=False` porque la
caché SQLite interna de yfinance da "database is locked" en Windows y **devuelve símbolos
vacíos sin avisar**. En las pruebas del spike `threads=True` dio 50/50 igual, pero es un fallo
intermitente y unas pocas pasadas en verde no lo descartan. Además vamos a correr en Docker
(Linux), donde probablemente ni aplique. **Decidir el lunes con datos de sesión** (F2.1).

Yahoo sirve unos **30 días de histórico en intervalo de 1 minuto**; para más atrás hay que
acumularlo nosotros, que es justo lo que hace el ingestor.

⚠️ **Con el perfil europeo la cuenta empeora, y hay que rehacerla (D8).** El universo son
89 símbolos, no 50, y la sesión dura 510 minutos en vez de 390. Peor: entre las 16:00 y las
17:30 CET **las dos bolsas están abiertas a la vez**, así que un perfil europeo y uno
americano activos piden 139 símbolos en el mismo minuto. En serie son ~24 s del minuto
disponible —cabe, pero el margen se ha reducido de 7× a 2,5×—. Eso convierte la duda de
`threads` en algo que hay que cerrar (F2.1c) en lugar de dejar apuntado.

### D4 — SQLite con un escritor cada minuto ✅

Ya está en WAL con `busy_timeout = 30000` ([src/db.py](src/db.py)), así que un escritor y
varios lectores conviven. Lo nuevo es que ahora hay **dos escritores**: el ingestor (cada
minuto, ~50 upserts, milisegundos) y el ciclo del agente. Se pisarán de vez en cuando y
esperarán; a este volumen no es problema, pero conviene medirlo (F2.9).

Volumen: 50 símbolos × 390 barras/día ≈ 19.500 filas/día, ~410.000 al mes, del orden de
50 MB mensuales. SQLite lo lleva sin despeinarse, pero sin retención crece para siempre
(F1.9).

⚠️ **Recalculado con el perfil europeo (D8): 89 símbolos × 510 barras/día ≈ 45.400
filas/día**, unas 2,3 veces la estimación anterior, ~115 MB al mes. Con los dos perfiles
activos, ~65.000 filas/día y ~165 MB al mes; con la retención de 90 días de F1.9, el
fichero se estabiliza en torno a **350–500 MB**. Sigue siendo cómodo para SQLite, pero ya
no es "no te enteras": conviene mirarlo antes de subir el número de perfiles.

**La base sigue en un volumen con nombre, no en un bind mount.** El comentario de
[docker-compose.yml](docker-compose.yml) explica por qué: el bloqueo de ficheros de SQLite
no es fiable sobre bind mounts de Docker Desktop en Windows. Con un escritor por minuto eso
importa más, no menos.

### D8 — El mercado es un parámetro del perfil ✅ (2026-08-08)

**Motivo:** las sesiones europeas (09:00–17:30 CET) caen dentro del horario en que el
ordenador está encendido; la americana (15:30–22:00 CET) no. Para un experimento que se
quiere vigilar, eso decide más que cualquier consideración de mercado.

La alternativa era sustituir US por Europa y ahorrar código. Se descartó porque contradice
la premisa de F6: **todo lo que define un experimento vive en el perfil.** Con el mercado
como columna, comparar el mismo criterio en Madrid y en Nueva York es crear un segundo
perfil; con el mercado cableado, es un fork del repositorio.

Lo que se ha llevado por delante:

| Cableado a NYSE | Ahora |
|---|---|
| `market_calendar.py` con zona, horario y festivos en constantes | Registro `MARKETS` con `us` y `eu`, y un `Market` que lleva zona, horario, festivos, divisa, benchmark y sufijos de bolsa |
| `active_universe()` devolvía una lista plana | `active_universe_by_market()` la reparte, y el ingestor pide solo lo de las bolsas abiertas |
| Un solo universo (`sp500.txt`) | Más `eurostoxx50_ibex35.txt`, 89 valores verificados contra Yahoo |

Tres decisiones con consecuencia:

- **La tabla europea solo lleva los cierres comunes a sus seis bolsas.** Xetra cierra el
  Lunes de Pentecostés y Milán la Epifanía, pero las demás abren. Marcar esos días como
  festivo costaría la sesión entera a los otros 60 y pico símbolos; dejándolos como día de
  mercado, los afectados aparecen como símbolos vacíos, que es un caso que el ingestor ya
  sabe tratar. **Fallo visible y acotado antes que correcto y caro.**
- ⚠️ **Un perfil cubre una sola bolsa, y es una restricción, no un descuido.** De ahí salen
  el horario, el calendario **y la divisa**, y el proyecto no convierte divisa en ningún
  sitio. Por eso el universo europeo es solo de la zona euro: Londres cotiza en peniques,
  así que `min_order_notional=100` significaría cosas distintas según el símbolo sin que
  nada avisara. `resolve_settings` rechaza un perfil cuyo universo mezcle bolsas, nombrando
  los símbolos culpables.
- **Nochebuena y Nochevieja se tratan como cierre completo**, aunque Euronext haga subasta
  hasta las 14:05. Media sesión con liquidez de festivo produce barras que distorsionan los
  indicadores más de lo que aportan.

**Consecuencia que hay que asumir:** `screener_min_dollar_volume` sigue llamándose así pero
la cifra está en la divisa del mercado. Renombrar la columna tocaría esquema, `db.py`,
`profile_settings.py`, el screener y sus tests para no ganar nada funcional; queda en F8.7.

### D5 — API: FastAPI ✅

[web/server.py](web/server.py) es `http.server` de la biblioteca estándar, sin dependencias
a propósito. Para una SPA con ~15 endpoints, escrituras (crear perfiles, editar parámetros)
y SSE, esa decisión deja de compensar: enrutado a mano, validación a mano, sin async.
FastAPI + uvicorn es lo razonable, y dentro de Docker las dependencias son gratis.

**Consecuencia que hay que asumir:** hoy el dashboard abre SQLite en **solo lectura**, y eso
es una garantía real de que la interfaz no puede corromper el histórico. Con perfiles
editables desde la UI, la API necesita escribir. Mitigación (F3.3): la API solo escribe en
las tablas de configuración (`profiles`, `agent_settings`); el histórico de operaciones
sigue siendo de solo lectura para ella, con un test que lo verifica.

### D6 — Tiempo real en la UI: SSE ✅

Sin Supabase no hay Realtime. Un endpoint `GET /api/stream` con **Server-Sent Events** que
emite los precios nuevos cuando el ingestor termina un tick. Unidireccional, va sobre HTTP
normal, y el navegador reconecta solo. Reserva si da guerra: sondeo cada 5 s.

### D7 — Modelo: NVIDIA NIM, capa gratuita ✅

Se mantiene `meta/llama-3.3-70b-instruct` vía [src/llm.py](src/llm.py). Aun así, el cliente
se hace **multi-proveedor desde el principio** (F6.6): cuando el experimento dé señales
interesantes, pasar a un modelo premium debe ser cambiar un parámetro del perfil, no
reescribir el analista.

**Alcance real tras F6.6:** NIM y **OpenAI**, que comparten el formato
`/chat/completions` y por tanto salen gratis en dependencias. **Anthropic no**: su API tiene
otra forma y pide su SDK, así que queda en F9.1.

---

## 2. Fases

### F1 — Esquema SQLite limpio

- [x] **F1.1** **Borrado total**: la base anterior (40 ciclos, 166 decisiones) se aparta en
      `backup/trading.db.pre-F1-20260808` en lugar de borrarse — recuperarla luego sería
      imposible. `backup/` está en `.gitignore`; bórrala cuando quieras. Falta tirar el
      volumen de Docker: `docker compose down -v`.
- [x] **F1.2** `profiles`, `agent_settings`, `agent_settings_history`, `profile_universe`.
      Los límites duros del risk manager nacen **NULL a propósito**: NULL significa "derívalo
      de los sliders". Si nacieran con números, mover el slider de riesgo no cambiaría nada.
- [x] **F1.3** `quotes_live` (una fila por símbolo, se reemplaza), `bars_1m` (histórico, clave
      `(symbol, ts)` para que el refresco sea idempotente) e `ingest_runs`.
- [x] **F1.4** ⚠️ **Simplificado respecto a lo planificado.** Solo `portfolios.profile_id`.
      Poner `profile_id` en las otras seis tablas era denormalizar sin ganar nada: `cycles`,
      `decisions`, `orders`, `positions`, `risk_events` y `equity_snapshots` ya cuelgan de
      `portfolios` con `on delete cascade`, así que borrar un perfil ya arrastra todo. La
      versión original obligaba además a tocar `cycle.py`, `dashboard.py` y los 15 módulos de
      test; esta no toca ninguno.
- [x] **F1.5** Las 4 vistas se quedan **sin cambios** — consecuencia de F1.4: siguen
      agrupando por `portfolio_id`, que sigue siendo el vínculo real.
- [x] **F1.6** Índices puestos (21 en total), incluidos `bars_1m (symbol, ts desc)` y
      `bars_1m (ts)` para la poda.
- [x] **F1.7** Idempotencia verificada: aplicar `schema.sql` dos veces sobre la misma base no
      falla.
- [x] **F1.8** [src/db.py](src/db.py) ampliado: perfiles, parámetros con historial, universo,
      cotizaciones, barras de minuto, salud del ingestor y poda.
- [x] **F1.9** ⚠️ **Simplificado**: solo poda, sin consolidación a `bars_1d`. Las barras
      diarias ya las mantiene `bar_cache`, que es de donde el agente calcula indicadores;
      una segunda tabla diaria sería un duplicado con dos fuentes que podrían discrepar.
      `prune_bars_1m(keep_days=90)` y se acabó. Falta engancharla a una tarea diaria (F2.10).
- [x] **F1.10** [tests/test_profiles.py](tests/test_profiles.py): 23 tests. **Suite completa
      en verde: 307 pasan.** (Tras F6.3–F6.7 la suite va por **444**.)

### F2 — Ingesta de precios cada minuto

- [x] **F2.1a** Spike escrito: [tools/spike_1m.py](tools/spike_1m.py). Mide retraso del dato,
      latencia, 429s y símbolos vacíos; escribe JSONL línea a línea para que un corte no se
      lleve lo medido.
- [x] **F2.1b** Validado el mecanismo contra la sesión del 2026-08-07 (mercado cerrado):
      50/50 símbolos, sin errores, marcas de tiempo correctas. Resultado lateral: la descarga
      **no es en lote** — ver la corrección en D3.
- [ ] **F2.1c** ⚠️ **Pendiente y bloqueante: medir en sesión real.** Reescrito tras D8: la
      medición que importa ahora es **la europea**, y se hace por la mañana en vez de por la
      tarde. El lunes 2026-08-10 a partir de las 09:00 hora de Madrid, una sesión entera:
      ```
      python tools/spike_1m.py --market eu --count 89 --out spike_eu_lunes.jsonl
      ```
      [tools/spike_1m.py](tools/spike_1m.py) ya sabe de mercados: `--market` elige el
      calendario y, de paso, el fichero de universo, y `--minutes` sin valor toma la sesión
      entera del mercado (510 en `eu`). Comprobado el 2026-08-08 con el mercado cerrado:
      3/3 símbolos y la última barra en 17:29 CEST, o sea que el feed europeo llega hasta
      el cierre. Lo que eso **no** dice es con cuánto retraso llega en vivo.

      La pregunta que hay que responder es **el retraso real del dato**: "cada minuto" solo
      vale si el dato es de hace un minuto. Y aquí pesa más que antes: **Yahoo suele servir
      las bolsas europeas con unos 15 minutos de desfase mientras da muchos valores
      americanos en tiempo real.** Si se confirma, el ingestor y el histórico se construyen
      igual —siguen valiendo para backtesting (F9.2)— pero **la ejecución intradía (F9.3)
      deja de tener sentido en Europa** y el experimento se queda en ciclos diarios. Es el
      riesgo que asume D8 y hay que medirlo antes de dar por buena la premisa.

      Segunda pregunta, ahora obligatoria: **`--threads`**. Con 139 símbolos en la franja de
      solape el margen dentro del minuto baja a 2,5× (ver D3), así que ya no vale con
      dejarlo apuntado.
- [x] **F2.2** Lógica en [src/ingest.py](src/ingest.py), bucle en
      [tools/ingestor.py](tools/ingestor.py). Separados para que los tests no necesiten red ni
      esperar minutos reales. Despierta unos segundos **después** del cambio de minuto: la
      barra de un minuto no existe hasta que ese minuto ha terminado.
- [x] **F2.3** Filtro con [src/market_calendar.py](src/market_calendar.py), que ya existía con
      sus tests. Con el mercado cerrado duerme hasta la apertura en tramos de 20 s, para que
      `docker stop` responda en segundos.
- [x] **F2.4** `active_universe()`: unión de perfiles activos más posiciones abiertas. Lo
      segundo importa — una posición no deja de necesitar precio porque su símbolo salga del
      screener. Se relee cada `INGEST_REFRESH_MIN`.
- [x] **F2.5** Reintentos con backoff exponencial y *jitter*, solo ante rate limit; un error de
      red normal falla rápido y lo reintenta el minuto siguiente.
- [x] **F2.6** Escritura incremental: solo lo nuevo **más la última barra conocida**, porque la
      del minuto en curso sigue cambiando. Verificado en real: 1.950 filas el primer tick,
      5 el segundo.
- [x] **F2.7** Una fila en `ingest_runs` por tick, con latencia de descarga y de escritura
      separadas.
- [x] **F2.8** Contador de fallos seguidos; a los `INGEST_MAX_FAILURES` sube a error y sugiere
      qué mirar.
- [x] **F2.9** Medida de contención **por coste de fila**, no por tiempo total. La primera
      versión avisaba por tiempo absoluto y saltaba en la carga inicial (1.950 filas, 2,2 s)
      sin que hubiera contención ninguna: un aviso que cría lobos se acaba ignorando. Base
      medida: ~1,1 ms/fila sin competencia; avisa a partir de 5.
- [ ] **F2.10** ⚠️ **A medias.** La poda diaria sí está (se ejecuta al cerrar el mercado). Falta
      el relleno de huecos al cierre: si el ingestor estuvo caído media sesión, el hueco se
      queda. El solape de 3 barras solo cubre caídas de pocos minutos.
- [x] **F2.11** [tests/test_ingest.py](tests/test_ingest.py): 23 tests con proveedor de
      mentira, sin red. **Suite completa: 330 en verde.**
- [x] **F2.12** Servicio `ingestor` en [docker-compose.yml](docker-compose.yml) (adelanta F7.3).

### FE — Mercado europeo ✅ (2026-08-08)

Ver D8. Todo cerrado salvo lo que depende de la sesión del lunes (F2.1c).

- [x] **FE.1** [src/market_calendar.py](src/market_calendar.py) reescrito como registro:
      `Market` (zona, horario, festivos, divisa, benchmark, sufijos de bolsa) y `MARKETS`
      con `us` y `eu`. Las funciones aceptan `market=` de palabra clave con `us` por
      defecto, y se conservan los alias de módulo (`EASTERN`, `HOLIDAYS`, `EARLY_CLOSES`,
      `LAST_COVERED_YEAR`): **los 47 tests del calendario americano pasan sin tocar una
      línea**, que era la forma de saber que el refactor no cambiaba la semántica.
- [x] **FE.2** [universe/eurostoxx50_ibex35.txt](universe/eurostoxx50_ibex35.txt): 89
      símbolos. **Verificados uno a uno contra Yahoo**: los 89 devuelven barras y los 89
      cotizan en EUR. No es ceremonia — un sufijo mal escrito no da error, el símbolo
      aparece vacío y desaparece del análisis en silencio.
- [x] **FE.3** `agent_settings.market` con `check (market in ('us','eu'))`, más su entrada
      en `ADDED_COLUMNS` para que la columna llegue también a una base ya creada.
- [x] **FE.4** El mercado llega a `Settings` y por tanto a `cycles.settings_json`. Sin eso
      un histórico no se puede interpretar: las mismas horas significan cosas distintas
      según la bolsa.
- [x] **FE.5** `resolve_settings` **rechaza un perfil cuyo universo mezcle bolsas**,
      nombrando los símbolos. Es error y no aviso porque el síntoma sin la comprobación es
      caro y mudo: el símbolo forastero no revienta, se queda con el cierre del día
      anterior y el analista decide sobre datos rancios.
- [x] **FE.6** `db.active_universe_by_market()` y bucle multi-mercado en
      [tools/ingestor.py](tools/ingestor.py): cada tick pide solo los símbolos de las
      bolsas abiertas, y duerme hasta la apertura más temprana de las que sigue.
- [x] **FE.7** `python run.py new-profile --name X --market eu`. **Hacía falta**: hasta
      ahora la única forma de crear un perfil era `import-profile` desde un `.env`, así que
      el soporte europeo habría sido inalcanzable sin abrir la base a mano.

      Dos detalles que salieron al probarlo:
      - **Rellena `profile_universe` además de `universe_file`.** Son cosas distintas y es
        una trampa heredada de F2.4: el fichero es lo que criba el screener para el ciclo,
        la tabla es lo que el ingestor sigue minuto a minuto. Un perfil con solo lo primero
        **no aparece en `active_universe_by_market` y se queda sin precios en vivo sin que
        nada lo diga.** Hay un test que fija ese comportamiento.
      - **Se niega a seguir el S&P 500 entero** (503 símbolos > 120) y exige `--watch N`.
        Es R2: son peticiones por minuto desde una IP doméstica.
- [x] **FE.8** El símbolo de moneda sale del mercado en `Settings.describe()` y en los
      cuatro comandos que enseñan dinero: `profiles`, `check`, `status` y `report`. Un
      presupuesto europeo escrito con `$` invita a compararlo con el de otro perfil como si
      fuera la misma unidad, y con dos experimentos en paralelo eso pasa solo. `report` no
      recibe `Settings` —mirar el histórico no debe exigir la clave del modelo—, así que
      resuelve la divisa por la cartera; sin perfil cae al default, que es lo que esas
      carteras eran.
- [x] **FE.9** `tools/spike_1m.py` acepta `--market` y `--universe`, y `--minutes` sin
      valor toma la sesión entera del mercado elegido. Sin esto, F2.1c no se podía medir el
      lunes: el spike habría consultado el calendario de NYSE y habría dicho "mercado
      cerrado" a las 9 de la mañana.
- [x] **FE.10** [tests/test_markets.py](tests/test_markets.py): 67 tests. **Suite completa:
      511 en verde.**
- [ ] **FE.11** ⚠️ **Pendiente, y es lo único que queda:** bajar
      `screener_min_dollar_volume` a 5.000.000 en el perfil europeo. Medido el 2026-08-08
      sobre las últimas 20 sesiones, el default de 20 M (pensado para el S&P 500) deja
      fuera 15 de los 89 — ANE.MC, LOG.MC, COL.MC, PUIG.MC, FDR.MC, ROVI.MC, SCYR.MC,
      MAP.MC… — que son precisamente las medianas españolas por las que se añadió el IBEX.
      Con 5 M pasan los 89: el menos líquido negocia 5,4 M €/día.
- [ ] **FE.12** El tope por sector de F6.5 sigue sin aplicarse, y en Europa es **peor**:
      `sp500.txt` al menos traía el reparto sectorial en un comentario, y el fichero
      europeo no trae ninguno. Mismo bloqueo que F6.5 (no hay dato de sector por símbolo);
      solo conviene saber que aquí no hay ni el apaño del comentario.

### F3 — API backend (FastAPI)

- [ ] **F3.1** `api/` con FastAPI + uvicorn; `requirements.txt` actualizado.
- [ ] **F3.2** Endpoints de lectura: `/api/profiles`, `/api/dashboard`, `/api/positions`,
      `/api/decisions`, `/api/orders`, `/api/risk-events`, `/api/cycles`, `/api/quotes`,
      `/api/ingest-status`.
- [ ] **F3.3** Endpoints de escritura, **limitados a las tablas de configuración**:
      `POST/PATCH/DELETE /api/profiles`, `PATCH /api/profiles/{id}/settings`. Con un test que
      verifique que la API no puede escribir en el histórico de operaciones (ver D5).
- [ ] **F3.4** Control de ciclos: `POST /api/cycles/run` y `/stop`, reaprovechando el patrón
      de subproceso de `CycleRunner` en [web/server.py](web/server.py), que ya está resuelto.
- [ ] **F3.5** `GET /api/stream` (SSE): precios en vivo y estado del ciclo en curso.
- [ ] **F3.6** Modelos Pydantic para request y response, y tipos TypeScript generados desde
      el OpenAPI que FastAPI publica solo — así el frontend no repite las definiciones.
- [ ] **F3.7** Servir el build de React como estáticos, con *fallback* a `index.html` para
      que funcionen las rutas del SPA.
- [ ] **F3.8** Escuchar solo en loopback por defecto, como ahora. Sin autenticación: es local.
- [ ] **F3.9** Tests de la API con `httpx` + `TestClient`.

### F4 — Frontend React + Tailwind

- [ ] **F4.1** Andamiaje **Vite + React + TypeScript** en `app/`.
- [ ] **F4.2** Tailwind CSS v4 + tema propio (dark por defecto) y **shadcn/ui** para tablas,
      diálogos, selects y toasts.
- [ ] **F4.3** `react-router` y layout: barra lateral con Perfiles, Dashboard, Posiciones,
      Decisiones, Órdenes, Ajustes.
- [ ] **F4.4** Datos con **TanStack Query** contra la API.
- [ ] **F4.5** Hook de SSE: precios y P&L moviéndose solos, con reconexión e indicador de
      "datos en vivo / desconectado".
- [ ] **F4.6** Gráficas: curva de capital, drawdown, histograma de convicción, calibración
      (Recharts o visx, por decidir).
- [ ] **F4.7** Portar lo que hoy hace [web/index.html](web/index.html): resumen, posiciones
      abiertas y cerradas, decisiones con tesis y riesgos, órdenes, eventos de riesgo, ciclos
      y su log en vivo.
- [ ] **F4.8** Estados de carga, vacío y error decentes en cada pantalla (hoy no existen).
- [ ] **F4.9** Responsive y accesible: foco visible, contraste AA, tablas navegables.
- [ ] **F4.10** Modo desarrollo: `vite dev` con proxy a la API, recarga en caliente.
- [ ] **F4.11** Retirar `web/index.html` y `web/server.py`.

### F5 — Pantalla de perfiles / experimentos

- [x] **F5.1** Tabla `profiles` — hecha en F1.2, con sus métodos en
      [src/db.py](src/db.py) (`create_profile`, `list_profiles`, `set_profile_status`,
      `delete_profile`) y tests de cascada.
- [ ] **F5.2** Listado en tarjetas con las métricas clave: capital, P&L total y del día, nº
      de posiciones, win rate, último ciclo. (En consola ya existe: `run.py profiles`.)
- [ ] **F5.3** Alta de perfil: nombre, descripción, capital inicial, universo y parámetros
      (formulario de F6).
- [ ] **F5.4** Acciones: activar, pausar, archivar, **duplicar** (clonar y cambiar un solo
      parámetro es el gesto central del experimento) y borrar con confirmación por nombre.
- [ ] **F5.5** Selector de perfil global en el layout; todas las pantallas filtran por él.
- [ ] **F5.6** **Comparador**: varios perfiles en la misma gráfica de equity, con tabla de
      métricas lado a lado. Es lo que convierte esto en un experimento y no en un bot.
- [ ] **F5.7** Perfil de control: screener en modo `random`, para tener contra qué medir el
      criterio del LLM.

### F6 — Parámetros del agente

- [x] **F6.1** Tabla `agent_settings` — hecha en F1.2, con `get_settings` / `update_settings`
      y validación de nombres de campo contra las columnas reales.
- [x] **F6.2** **Historial de cambios** — hecho en F1.2. Registra solo los cambios reales:
      reescribir el mismo valor no ensucia el historial.
- [x] **F6.3** El ciclo volca `settings.snapshot()` en `cycles.settings_json`. Se hizo
      con F6.4 porque son la misma cosa: en cuanto los parámetros son editables en
      caliente, un ciclo sin copia de los suyos deja de ser interpretable. El snapshot
      **excluye la clave del modelo** a propósito — el histórico se exporta y se abre con
      DB Browser, y una clave dentro de una columna JSON no se ve venir.
- [x] **F6.4** El ciclo lee del perfil, no del `.env`.
      [src/profile_settings.py](src/profile_settings.py) resuelve una fila de
      `agent_settings` + el universo del perfil + la infraestructura a los `Settings` que
      ya consumían `cycle.py` y `market_data.py`. **`Settings` no desaparece**: sigue
      siendo el contrato, solo cambia de dónde se rellena, y por eso el refactor no toca
      el código del ciclo ni sus tests.

      [src/config.py](src/config.py) queda con `Infra` (DB_PATH, NVIDIA_API_KEY,
      LOG_LEVEL, PROFILE) y nada de estrategia. `Settings.load()` sobrevive con un único
      propósito: `run.py import-profile`, que convierte un `.env` heredado en un perfil.

      Detalles que costaron una decisión:
      - **Elegir perfil es explícito.** Con varios activos, el comando exige `--profile`
        en lugar de coger uno «razonable»: operar contra el experimento equivocado ensucia
        dos históricos a la vez y no se deshace.
      - **`cycle` y `status` exigen perfil; `check` puede caer al `.env`.** `check` es el
        diagnóstico y tiene que funcionar en una instalación recién clonada.
      - **Hicieron falta 6 columnas nuevas** en `agent_settings` (`universe_file`,
        `lookback_days`, `skip_when_market_closed` y los tres descartes del screener), y
        con ellas una migración real: `create table if not exists` **no añade columnas a
        una tabla que ya existe**, así que sin `_add_missing_columns` una columna nueva
        funcionaría en una base recién creada y faltaría en la que está corriendo.
      - Se ha quitado la columna `broker`: ya no hay nada que elegir.
      - Comandos nuevos: `run.py profiles`, `import-profile`, `activate`.
- [x] **F6.5** [src/risk_presets.py](src/risk_presets.py): `derive_limits(risk_profile,
      diversification)` → los 9 límites, con **modo avanzado** campo a campo. Las tres
      filas de la tabla son las anclas y los niveles intermedios se **interpolan** por
      tramos, para que mover el deslizador un punto siempre cambie algo — una tabla
      escrita a ojo tiende a repetir valores y entonces el deslizador parece roto.
      | Perfil | `risk_per_trade` | `max_position` | `max_exposure` | `min_conviction` | `stop_atr` | `min_rr` | kill switch |
      |---|---|---|---|---|---|---|---|
      | 1 muy conservador | 0,25 % | 5 % | 30 % | 85 | 3,0 | 2,5 | −2 % |
      | 5 equilibrado | 1,0 % | 20 % | 70 % | 65 | 2,0 | 1,5 | −5 % |
      | 10 muy agresivo | 3,0 % | 40 % | 100 % | 45 | 1,2 | 1,0 | −10 % |

      Diversificación 1 → máx. 3 posiciones; 10 → máx. 25. `min_order_notional` queda fijo
      en 100 $: es fricción de ejecución, no apetito de riesgo.

      Dos decisiones con consecuencia:
      - **`advanced_overrides` es el interruptor maestro.** Con él apagado mandan los
        deslizadores *aunque las columnas conserven números de una sesión anterior*. Si los
        viejos siguieran ganando, apagarlo no haría nada visible y se seguiría operando con
        límites que el usuario cree descartados.
      - ⚠️ **El tope por sector se calcula pero NO se aplica.** `sector_cap()` da el número
        y la interfaz puede enseñarlo, pero el Risk Manager no lo hace cumplir porque **no
        hay dato de sector por símbolo en tiempo de ejecución**: `universe/sp500.txt` solo
        lleva el reparto en un comentario. Falta llevar el sector a una tabla; hasta
        entonces, diversificación limita el número de posiciones pero no su concentración
        sectorial.
- [x] **F6.6** ⚠️ **Dos de tres proveedores.** [src/llm.py](src/llm.py) lleva una tabla
      `PROVIDERS` con **NVIDIA NIM** (por defecto) y **OpenAI**. Los dos exponen
      `/chat/completions` con el mismo formato, así que no hay dos implementaciones: hay una
      con una fila por proveedor. Cambiar de uno a otro es un parámetro del perfil, y **no
      añade ninguna dependencia**.

      **Anthropic se queda fuera, y es una decisión, no un olvido.** Su API tiene otra forma
      (`/v1/messages`, otras cabeceras, el `system` fuera de `messages`, `input_tokens` en
      lugar de `prompt_tokens`) y su documentación pide usar el SDK oficial en vez de hablar
      HTTP a mano. Eso es una dependencia nueva y una segunda implementación de verdad, para
      un proveedor que hoy nadie va a usar. Pasa a F9.1.

      Para que el fallo sea honesto, `llm_provider='anthropic'` —que el esquema sigue
      admitiendo— se rechaza **al resolver el perfil** con "no implementado todavía (F9.1)",
      no con "proveedor desconocido": lo primero es una tarea pendiente, lo segundo sería una
      errata.
- [x] **F6.7** Clave de API por perfil en `agent_settings.llm_api_key`, con
      `mask_secret()` para enseñarla sin enseñarla (`nvapi-...7f3a`). Ya la usa
      `run.py profiles`; la UI de F6.8 tirará de la misma función.

      Dos detalles que salieron al probarlo:
      - **`NVIDIA_API_KEY` del entorno sigue valiendo como respaldo, pero solo para NIM.**
        Aceptarla para OpenAI no fallaría al resolver: fallaría a mitad del ciclo con un 401
        que nadie relaciona con el perfil. Igual con `NVIDIA_BASE_URL`.
      - Con NIM, una columna vacía **no** significa "sin clave" sino "usa la del entorno", y
        la pantalla lo dice así. Poner "(sin clave)" mandaba a buscar un problema inexistente.
- [ ] **F6.8** Formulario con sliders y **valores derivados visibles en vivo** ("con estos
      ajustes: máx. 8 posiciones, 1,5 % de riesgo por operación").

**Parámetros propuestos** (los tres que pediste en negrita, más los que creo que faltan):

*Modelo*
- **Proveedor** (NVIDIA NIM por defecto / Anthropic / OpenAI) y **modelo**
- **API key** por perfil
- Temperatura, máx. tokens, timeout, reintentos
- Instrucciones extra al analista (persona: "value investor", "momentum", …)

*Estrategia*
- **Perfil de riesgo 1–10**
- **Diversificación 1–10**
- Horizonte objetivo en días (intradía / swing / posición)
- Universo: watchlist manual o universo + `top_n` del screener
- Modo del screener: `score` o `random` (control)
- Permitir cortos: sí/no
- Sectores excluidos / permitidos
- Reserva mínima de caja (%)
- Benchmark de comparación (por defecto SPY)

*Ejecución*
- Capital inicial
- Frecuencia del ciclo y horas de ejecución
- Intervalo de barras (1m / 1h / 1d)
- Slippage y comisión simulados
- `dry_run`

*Límites duros del risk manager* (derivados de F6.5, editables en modo avanzado)
- Riesgo por operación %, tamaño máx. de posición %, exposición total %
- Nº máximo de posiciones abiertas
- Pérdida diaria máxima % (kill switch)
- Convicción mínima, múltiplo de ATR del stop, reward/risk mínimo, notional mínimo

### F7 — Docker y puesta en marcha

- [ ] **F7.1** Estructura del repo:
      ```
      app/        React + Vite (se compila en el build de Docker)
      api/        FastAPI
      src/        el agente, tal cual está hoy
      tools/      ingestor.py, scheduler.py, fetch_universe.py
      tests/
      ```
- [ ] **F7.2** [Dockerfile](Dockerfile) multietapa: etapa Node que hace `npm run build`, etapa
      Python que copia `app/dist` y arranca uvicorn. **Una imagen, un puerto.**
- [x] **F7.3** Servicio `ingestor` en [docker-compose.yml](docker-compose.yml) — hecho en
      F2.12, con `restart: unless-stopped` y `stop_grace_period: 15s`.
- [ ] **F7.4** Renombrar `dashboard` → `api` con el comando de uvicorn, manteniendo la
      publicación en `127.0.0.1:8000` y el healthcheck.
- [ ] **F7.5** Conservar el volumen con nombre para la base (ver D4) y el comando documentado
      para extraer el fichero.
- [ ] **F7.6** `docker compose up` levanta todo y la app está en `http://localhost:8000` sin
      más pasos.
- [ ] **F7.7** Modo desarrollo documentado: contenedores para API e ingestor, `vite dev` en el
      anfitrión con proxy.
- [ ] **F7.8** Reescribir el [README.md](README.md) con la arquitectura nueva.

### F8 — Limpieza

- [x] **F8.1** Base a cero (2026-08-08): `data/trading.db` apartada en
      `backup/trading.db.pre-F6-20260808` (mismo criterio que F1.1: borrarla sería
      irreversible) y `docker compose down -v` ejecutado, volumen `trading-data` eliminado.
      Bórralos de `backup/` cuando quieras.
- [ ] **F8.2** Borrar `web/index.html` y `web/server.py` cuando F3 y F4 estén verdes.
- [ ] **F8.3** ⚠️ **A medias.** Las variables de estrategia ya **no las lee el ciclo** (F6.4)
      y [.env.example](.env.example) está partido en dos mitades rotuladas: infraestructura
      arriba, heredadas abajo. La mitad de abajo sigue ahí porque `run.py import-profile` la
      necesita; se borra cuando ya no haya ningún `.env` que migrar.
- [ ] **F8.4** `.gitignore`: falta `node_modules/`, `app/dist/`, `.vite/` (cuando exista el
      frontend). Ya añadidos `backup/` y `spike_*.jsonl`.
- [ ] **F8.5** ⚠️ **Hay un `.env` con claves reales en el directorio.** Está en `.gitignore`,
      pero conviene confirmar que nunca llegó a subirse.
- [ ] **F8.6** Suite de tests entera en verde: `docker compose run --rm bot python -m pytest tests -q`.
- [ ] **F8.7** Renombrar `screener_min_dollar_volume` → `screener_min_turnover`. Desde D8 la
      cifra está en la divisa del mercado, así que el nombre miente en los perfiles
      europeos. No es urgente —hay un comentario en el esquema y otro en el fichero de
      universo— pero toca esquema, `db.py`, `profile_settings.py`, `config.py`, el screener
      y sus tests, así que conviene hacerlo de una vez y no a medias.

### F9 — Futuro (no bloquea)

- [ ] **F9.1** Modelo premium cuando el experimento dé señales.
      - **GPT: ya se puede.** F6.6 lo dejó operativo — `llm_provider='openai'`, la clave en
        el perfil y el modelo que quieras. Cero código pendiente.
      - **Claude: falta implementarlo.** Su API no es compatible con `/chat/completions`, así
        que hay que añadir el SDK `anthropic` a `requirements.txt` y un dialecto propio en
        [src/llm.py](src/llm.py) que traduzca `system`, `max_tokens` y los campos de `usage`.
      - ⚠️ **Cualquiera de los dos rompe la premisa de 0 €** del plan. Es el momento de
        decidirlo a propósito, no de descubrirlo en la factura.
- [ ] **F9.2** Backtesting sobre el histórico de `bars_1m` que se vaya acumulando — es la
      razón de peso para empezar a guardarlo ya.
- [ ] **F9.3** Ejecución intradía real aprovechando los datos de 1 minuto.
- [ ] **F9.4** Noticias / sentimiento como entrada adicional del analista.
- [ ] **F9.5** Notificaciones (Telegram) al abrir o cerrar posición.
- [ ] **F9.6** Publicar en internet: sería el momento de Supabase + Cloudflare del plan
      anterior, con autenticación. Hoy no hace falta y costaría dinero.

---

## 3. Orden de ejecución

```
F2.1 (spike yfinance 1m)  ──┐
F1 (esquema limpio) ────────┴─→ F2 (ingestor)  ─┐
                             ├─→ F6 (parámetros) ┤
                             ├─→ FE (mercado eu) ┼─→ F3 (API) ─→ F4 (React) ─→ F7 → F8
                             └─→ F5 (perfiles) ──┘
```

El spike va primero: es lo único que puede invalidar una decisión ya tomada. F1 bloquea todo
lo demás. F3 y F4 pueden solaparse en cuanto los endpoints estén definidos.

FE se coló delante de F3 porque cambiaba el esquema (`agent_settings.market`) y porque
reescribía la pregunta de F2.1c: medir la sesión americana ya no era lo que interesaba.
Hacerlo después habría significado rehacer los endpoints de F3 y la medición del lunes.

---

## 4. Riesgos y puntos a vigilar

- **R1 — Latencia real del dato.** ⚠️ **Ha subido de nivel con D8.** "Cada minuto" solo vale
  si el dato es de hace un minuto, y **Yahoo suele servir las bolsas europeas con unos 15
  minutos de retraso mientras da muchos valores americanos en tiempo real**. Hay que
  **medirlo** (F2.1c), no asumirlo. Si se confirma: el ingestor y el histórico valen igual
  —siguen sirviendo para backtesting (F9.2)— pero **la ejecución intradía (F9.3) deja de
  tener sentido en Europa** y el experimento se queda en ciclos diarios. Es el precio que
  paga D8 a cambio del horario, y conviene saberlo antes y no en octubre.
- **R2 — Yahoo puede limitar por IP.** Es una API no oficial, y el spike desmontó la
  mitigación que yo daba por buena: **son ~50 peticiones por minuto, no 1** (ver D3). En una
  sesión son ~19.500 peticiones al día desde la misma IP doméstica. No apareció ningún 429 en
  las pruebas, pero fueron pasadas sueltas con el mercado cerrado; el riesgo real solo se ve
  sosteniendo el ritmo una sesión entera (F2.1). Palancas si aparece: bajar el número de
  símbolos o espaciar las peticiones dentro del minuto en vez de lanzarlas de golpe.
  ⚠️ **Ya no hay plan B de proveedor**: al quitar Alpaca, Yahoo es la única fuente. Si
  empieza a limitar de verdad, hay que integrar otra fuente, y eso es trabajo, no una
  variable de entorno.
  ⚠️ **Y con D8 la exposición sube.** El universo europeo son 89 símbolos, no 50, y entre
  las 16:00 y las 17:30 CET se solapa con el americano: **139 peticiones en el mismo
  minuto** si hay un perfil activo de cada. Sigue cabiendo (~24 s en serie), pero el margen
  pasa de 7× a 2,5×. Palanca inmediata si aparece un 429: `--watch` más bajo en el perfil
  americano, que es el que menos aporta al experimento nuevo.
- **R3 — Contención de escritura en SQLite.** Dos escritores (ingestor y ciclo) sobre el
  mismo fichero. WAL y `busy_timeout` ya lo cubren a este volumen, pero hay que medirlo
  (F2.9) antes de dar por hecho que escala a más perfiles.
- **R4 — Crecimiento del fichero.** ~50 MB al mes sin retención. Cómodo durante un año, no
  para siempre. Lo resuelve F1.9. ⚠️ **Con D8 son ~115 MB al mes solo con el perfil
  europeo** (89 símbolos × 510 barras) y ~165 MB con los dos; con la poda de 90 días el
  fichero se estabiliza sobre 350–500 MB. Sigue siendo asumible, pero ya no es
  despreciable: mirarlo antes de añadir un tercer perfil.
- **R5 — La API pasa a poder escribir.** Se pierde la garantía de solo lectura del dashboard
  actual. Acotado a las tablas de configuración y verificado con un test (F3.3).
- **R6 — Parámetros editables en caliente.** ✅ **Cubierto.** `agent_settings_history`
  registra cada cambio real (F6.2) y cada ciclo guarda copia de los parámetros con los que
  corrió en `cycles.settings_json` (F6.3). Queda un hueco pequeño: el ciclo hace la copia
  **al arrancar**, así que editar un parámetro con un ciclo de 20 minutos ya en marcha deja
  ese ciclo registrado con los valores de antes. Es el comportamiento correcto —el ciclo lee
  los ajustes una vez y no los recarga— pero conviene saberlo.
- **R7 — Calidad del modelo gratuito.** Llama 3.3 70B puede no dar señal útil, y entonces el
  experimento mide el modelo, no la estrategia. Por eso F5.7 (perfil de control aleatorio):
  sin algo contra lo que comparar, no se sabe distinguir un caso del otro.

---

## 5. Decisiones pendientes

1. **Librería de gráficas**: Recharts (rápido de montar) o visx (más control).
2. **Frecuencia de los ciclos del agente**: ¿se mantiene 1 al día tras el cierre, o se
   aprovechan los datos de 1 minuto para varios ciclos intradía? Afecta al gasto de modelo.
   **Depende de F2.1c**: si el feed europeo llega con 15 minutos de retraso, la pregunta se
   responde sola. Con `market=eu` el "tras el cierre" son las 18:00 de Madrid, no las 22:15.
3. ~~**Tamaño del universo a seguir minuto a minuto**: 50 es el punto de partida.~~
   **Resuelto para Europa: 89** (EURO STOXX 50 + IBEX 35, D8). Para el perfil americano
   sigue abierto y ahora se elige explícitamente con `--watch`. Condiciona R2 y R4, que ya
   están recalculados.
5. **¿Se mantiene un perfil americano activo?** Con los dos, la franja de solape pide 139
   símbolos por minuto y el fichero crece a ~165 MB/mes. Si el experimento europeo es el
   que importa, pausar el americano quita las dos presiones de golpe.
4. ~~¿Se conserva el broker simulado, o se pasa a Alpaca paper?~~ **Resuelto: solo
   simulador.** Alpaca fuera del proyecto.
