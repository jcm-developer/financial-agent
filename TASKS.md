# TASKS.md — Plan de trabajo

Registro de todo lo pendiente. Cada tarea tiene un id (`F1.2`) para referenciarla en
commits y conversaciones. Marcar `[x]` al cerrarla.

Última actualización: 2026-08-08 (noche: F3 completa — API FastAPI; decisiones 1–3 resueltas)

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
  `http.server` ([web/server.py](web/server.py)). **Ya conviven con la API de F3**
  ([api/](api/), FastAPI): `python run.py serve` levanta el viejo y `python run.py api`
  el nuevo. `web/` se retira en F8.2, cuando F4 tenga frontend.
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
- **La ventana operativa no es la sesión** (ver FE.13). En Europa el sistema trabaja de
  **09:15 a 17:45** sobre una sesión de 09:00–17:30. `is_session_open()` sigue diciendo la
  verdad de mercado —se guarda en `cycles.market_open` y se enseña en el dashboard— y
  `is_operating()` responde la otra pregunta: si nos toca capturar y analizar.

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

✅ **Resuelto en F3.3, y mejor de lo planificado.** Lo que se ha puesto no es un test que
compruebe que los endpoints se portan bien, sino el **autorizador de SQLite**
([api/guard.py](api/guard.py)): las escrituras fuera de las tablas de configuración fallan
con "not authorized" al compilar la sentencia. La garantía vuelve a ser una imposibilidad
del motor y no una costumbre del código, que es lo que se había perdido. Los endpoints de
lectura siguen abriendo la base en modo `ro`, o sea que esa mitad no ha cambiado nada.

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
      `prune_bars_1m(keep_days=90)` y se acabó. Ya enganchada al mantenimiento diario del
      ingestor (F2.10).
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
      calendario y, de paso, el fichero de universo, y `--minutes` sin valor toma la
      **ventana operativa** entera (510 minutos en `eu`, de 09:15 a 17:45). Que mida la
      ventana y no la sesión es deliberado: **los 15 minutos posteriores al cierre son justo
      donde se verá si la última barra llega tarde**, que es la pregunta. Comprobado el 2026-08-08 con el mercado cerrado:
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
- [x] **F2.10** Mantenimiento diario completo al cerrar: **relleno de huecos** y luego poda.
      `backfill_gaps` en [src/ingest.py](src/ingest.py), enganchado en
      [tools/ingestor.py](tools/ingestor.py); `INGEST_BACKFILL_DAYS` (5 por defecto, 0 lo
      apaga).

      **La descripción de esta tarea era incorrecta y conviene dejarlo escrito.** Decía que
      una caída de media sesión dejaba el hueco, y que el solape de 3 barras solo cubría
      caídas de pocos minutos. No es así: cada tick pide `period=1d` —la sesión entera— y
      escribe todo lo posterior a la última barra conocida, así que **una caída dentro de la
      sesión se rellena sola en el tick siguiente, por larga que sea**. El solape de 3 barras
      solo entra cuando no hay nada nuevo. Ya había un test que lo fijaba
      (`test_un_hueco_se_rellena_solo`).

      El hueco real es otro: **la sesión perdida entera.** Si el proceso muere el viernes por
      la tarde y vuelve el lunes, `period=1d` solo alcanza al lunes y nadie vuelve a mirar el
      viernes. Eso es lo que rellena esto, pidiendo 5 días en lugar de uno.

      Cuatro decisiones que costaron pensarlo:
      - **Tope de 7 días por petición.** Es lo máximo que Yahoo sirve en intervalo de 1
        minuto (y solo 30 días de histórico en total). Pedir más **no da error: devuelve un
        marco vacío**, que es la peor forma de fallar, así que se recorta antes de pedir.
      - **Escribe solo lo que falta**, comparando contra `bars_1m` símbolo a símbolo. Cinco
        días del universo europeo son ~225.000 filas: reescribirlas cada tarde tampoco
        fallaría, se notaría solo como una tarea que tarda cada vez más.
      - **Y escribe por símbolo, no en un lote final.** Medido contra Yahoo el 2026-08-08: 4
        símbolos × 5 días = 9.585 barras en 11 s, o sea ~3 s por símbolo, **~4–5 minutos con
        los 89 europeos**. Una transacción de ese tamaño coincide con la hora del ciclo del
        agente —las 18:00 de Madrid son "fuera de ventana" para los dos— y es justo la
        contención que vigila R3. Por lotes, además, una parada a media faena deja hecho lo
        que llevaba.
      - **Se puede abandonar entre símbolos** (`should_stop`). Sin eso, un `docker stop` a la
        hora del mantenimiento esperaría esos minutos y acabaría en SIGKILL, con
        `stop_grace_period: 15s`. Una pasada abandonada se registra como tal: `symbols_ok`
        cuenta los revisados, no los que devolvió el proveedor.

      Los rellenos se distinguen de los ticks con `ingest_runs.kind` (más su entrada en
      `ADDED_COLUMNS`, con un test de la migración). Hacía falta: un backfill descarga varios
      días de golpe, así que **una sola de sus filas desplaza cualquier media de latencia** y
      el panel de salud de F3 pasaría a medir otra cosa. `ingest_health(kind=...)` filtra.

      Comprobado de punta a punta contra Yahoo: 510 barras por sesión europea y 390 por
      americana, las cinco sesiones completas, y una segunda pasada que encuentra 0 huecos.
      **Suite: 543 en verde** (16 tests nuevos).
- [x] **F2.11** [tests/test_ingest.py](tests/test_ingest.py): 23 tests con proveedor de
      mentira, sin red (39 tras F2.10). **Suite completa: 330 en verde** (543 tras F2.10).
- [x] **F2.12** Servicio `ingestor` en [docker-compose.yml](docker-compose.yml) (adelanta F7.3).

### FE — Mercado europeo ✅ (2026-08-08)

Ver D8. Cerrado salvo lo que depende de la sesión del lunes (F2.1c) y el tope por sector
(FE.12), que sigue bloqueado por lo mismo que F6.5.

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
- [x] **FE.10** [tests/test_markets.py](tests/test_markets.py): 79 tests (83 tras FE.11).
      **Suite completa: 523 en verde** (527 tras FE.11).
- [x] **FE.11** Liquidez mínima del screener a 5.000.000 € en Europa. Medido el 2026-08-08
      sobre las últimas 20 sesiones, el default de 20 M (pensado para el S&P 500) dejaba
      fuera 15 de los 89 — ANE.MC, LOG.MC, COL.MC, PUIG.MC, FDR.MC, ROVI.MC, SCYR.MC,
      MAP.MC… — que son precisamente las medianas españolas por las que se añadió el IBEX.
      Con 5 M pasan los 89: el menos líquido negocia 5,4 M €/día, así que el umbral sigue
      filtrando y no está puesto por debajo de todo.

      **No se ha hecho editando la base, que era el plan.** El número vive en
      `Market.min_turnover` y `new-profile` lo aplica al crear el perfil: es una propiedad
      del universo, no una preferencia del usuario, y como columna con default único
      significaba cosas distintas según el mercado. Lo que había antes era un **aviso
      impreso** pidiendo bajarlo a mano; un aviso que exige trabajo manual acaba sin
      aplicarse, y el síntoma —15 valores menos— no es un error, es un universo más
      pequeño del que uno cree. `_check_markets` rechaza un suelo no positivo por lo mismo:
      un 0 apaga el filtro sin decirlo. **Suite: 527 en verde** (4 tests nuevos).
- [x] **FE.13** **Ventana operativa 09:15–17:45**, distinta de la sesión (09:00–17:30).
      Pedida explícitamente, y con motivo en las dos puntas: los 15 primeros minutos son la
      resaca de la subasta de apertura —las barras más ruidosas del día, y las peores para
      decidir sobre ellas— y los 15 últimos son cuando termina de llegar la barra del
      cierre. **Si se confirma el retraso del feed europeo (R1), parar a las 17:30 perdería
      el último cuarto de hora de cada sesión.**

      Dos decisiones que costaron pensarlo:
      - **No se ha tocado el horario del mercado.** Poner 17:45 como cierre habría hecho que
        `is_session_open()` afirmara que Madrid está abierta a las 17:40, cuando lleva diez
        minutos cerrada y la subasta se cruzó a las 17:35. Ese dato se guarda en
        `cycles.market_open` y se enseña en el dashboard: falsearlo contamina el histórico
        para siempre a cambio de ahorrar una función.
      - **Se guarda como desplazamientos (`warmup_minutes` / `drain_minutes`), no como horas
        absolutas.** Con horas fijas, el 24 de diciembre en Nueva York el sistema seguiría
        esperando barras hasta las 16:00 de una sesión que cerró a las 13:00. Con
        desplazamientos, la media sesión arrastra su ventana sola. Hay un test que lo fija.

      **Estados Unidos se queda con 0 y 0**, o sea ventana = sesión. Nadie pidió cambiarlo,
      y hacerlo de rebote alteraría el comportamiento de un experimento en marcha; el motivo
      de la cola europea es además el retraso del feed en Europa, que allí no aplica.

      Lo consumen el ingestor (`is_operating` / `next_operating_open`), `should_run` para
      los intervalos intradía, `run.py check` y el spike de F2.1c. `should_run("1d")` no
      cambia: sigue siendo "¿hay sesión hoy?".
- [ ] **FE.12** El tope por sector de F6.5 sigue sin aplicarse, y en Europa es **peor**:
      `sp500.txt` al menos traía el reparto sectorial en un comentario, y el fichero
      europeo no trae ninguno. Mismo bloqueo que F6.5 (no hay dato de sector por símbolo);
      solo conviene saber que aquí no hay ni el apaño del comentario.

### F3 — API backend (FastAPI) ✅ (2026-08-08)

Arranca con `python run.py api` (o `uvicorn api.main:app`). Documentación
interactiva en `/docs`; el esquema OpenAPI, en `/openapi.json`.

- [x] **F3.1** Paquete [api/](api/): `deps.py` (configuración y dependencias),
      `guard.py` (la conexión acotada), `queries.py` (lecturas), `models.py`
      (Pydantic), `runner.py` (subproceso del ciclo) y `routes/`.
      `requirements.txt` con `fastapi` y `uvicorn`.

      **Comando nuevo, `run.py api`,** que convive con `serve`. No se ha
      renombrado el servicio de Docker: eso es F7.4, y hacerlo ahora dejaría
      `docker compose up` sirviendo la página de "falta el frontend" en lugar
      del dashboard que hoy funciona.
- [x] **F3.2** Los nueve endpoints de la lista, más los que hacían falta para
      que la interfaz no tenga que deducir nada: detalle de perfil, de ajustes,
      de límites, historial de parámetros, detalle de ciclo y **`/api/markets`**.
      Este último no estaba pedido y se ha añadido porque sin él la interfaz
      tendría que cablear la divisa, el horario y el suelo de liquidez de cada
      bolsa, que es justo lo que D8 sacó a un registro.

      Dos cosas que salieron al escribirlos:
      - **Las posiciones se valoran con `quotes_live` y se dice de dónde sale el
        precio** (`price_source`: `live` o `cycle`). El dashboard viejo usaba
        solo `market_snapshots`, o sea el precio que vio el analista en su
        último ciclo. Sumar una posición valorada con el cierre de anteayer y
        otra con el precio de hace un minuto sin saberlo da un P&L que no
        significa nada.
      - **`/api/quotes` publica `age_seconds`.** Es la medición de F2.1c puesta
        donde se ve: "cada minuto" solo vale si el dato es de hace un minuto.
- [x] **F3.3** ⚠️ **No es una convención: lo impide SQLite.** El plan decía
      "limitados a las tablas de configuración, con un test que lo verifique".
      Un test así comprueba que los endpoints de hoy se portan bien, no que los
      de mañana no puedan portarse mal, y la garantía que D5 daba por perdida
      era del segundo tipo.

      [api/guard.py](api/guard.py) usa el **autorizador de SQLite**
      (`set_authorizer`), que se consulta al compilar cada sentencia. Un
      `insert into decisions` no falla por educación: falla con "not
      authorized" antes de tocar el fichero. Escribibles: `profiles`,
      `agent_settings`, `agent_settings_history`, `profile_universe` y
      `portfolios`.

      Cuatro decisiones que costaron pensarlo:
      - **El autorizador también se dispara en los borrados en cascada.** Se
        comprobó antes de escribir nada: al borrar un perfil, SQLite pide
        permiso para cada `delete` que la cascada provoca en `cycles`,
        `decisions`, `positions`… Con la lista de tablas a secas,
        `DELETE /api/profiles` habría fallado a medias. De ahí una ventana que
        abre **un solo método** para **una sola sentencia**, y que se cierra en
        un `finally`; hay un test que comprueba que después vuelve a estar
        cerrada.
      - **`portfolios` admite INSERT y DELETE pero no UPDATE.** Crear un perfil
        crea su cartera y borrarlo la borra, pero nada tiene por qué
        *modificarla*: la única columna interesante es `initial_budget`, y
        cambiarla con la curva de capital ya empezada reescribiría en silencio
        la referencia contra la que se mide el experimento entero.
      - **La API no ejecuta SQL libre.** `Database.execute` está para las
        herramientas de `tools/`; desde la API lanza error. Si pudiera
        construir SQL a mano, el autorizador sería la única barrera.
      - **La lista de pragmas es blanca y no negra.** `pragma writable_schema`
        deja sin efecto todo lo demás de ese módulo.

      Tres tests sostienen la garantía: uno recorre **el esquema real** y
      comprueba que no se puede borrar de ninguna tabla de histórico (una tabla
      nueva entra sola en la prueba), otro es estructural —ninguna ruta de
      escritura recibe una conexión sin acotar— y el tercero ejercita todas las
      escrituras de la API de punta a punta y compara los recuentos del
      histórico antes y después.

      Endpoints: `POST/PATCH/DELETE /api/profiles`, `PATCH .../settings`,
      `PUT .../universe` y `POST .../duplicate`. El duplicado no estaba en la
      lista de F3.3 y se ha incluido porque F5.4 lo llama el gesto central del
      experimento y es una escritura de configuración como las demás.

      **Borrar exige repetir el nombre del perfil** (`?confirm=`). Es la única
      llamada de la API que destruye datos que costó semanas generar.
- [x] **F3.4** `POST /api/cycles/run` y `/stop`, más `GET /api/cycles/control/status`.
      El `CycleRunner` se ha **copiado** a [api/runner.py](api/runner.py) en vez
      de importarse de [web/server.py](web/server.py): ese fichero se borra en
      F8.2 y una dependencia apuntando a un módulo con fecha de caducidad es un
      fallo esperando al día señalado.

      Se comprueban **las dos** fuentes de "ya hay un ciclo": el subproceso
      propio y la tabla `cycles`, porque el planificador puede tener uno
      corriendo del que este proceso no sabe nada.

      Con F3.3 el subproceso pasa de prudente a necesario: la API no puede
      escribir en `orders` ni en `positions` ni queriendo, así que operar tenía
      que salir del proceso de todas formas.
- [x] **F3.5** `GET /api/stream`, con eventos `quotes`, `ingest`, `cycle` y `bye`.

      ⚠️ **Dicho sin adornos: por dentro sondea.** El ingestor corre en otro
      proceso (D1), así que no hay forma de que avise; no hay bus de eventos ni
      se va a montar uno para tres procesos que comparten un fichero. Lo que
      hace SSE aquí es **mover el sondeo del navegador al servidor**: en lugar
      de N pestañas pidiendo `/api/quotes` cada dos segundos por HTTP, hay un
      bucle por conexión mirando un fichero SQLite local, y solo se manda algo
      cuando cambia. Esa es la ganancia real, y conviene tenerla escrita para no
      acabar creyendo que hay empuje de verdad.

      Del ciclo se mandan **solo las líneas nuevas** con su índice: reenviar las
      400 del buffer cada dos segundos convertiría el "en vivo" en un goteo de
      megabytes.

      **Las conexiones caducan** (`API_STREAM_MAX_SECONDS`, 15 min). No es una
      limitación: `EventSource` reconecta solo —que es la razón de elegir SSE en
      D6— así que cortar de vez en cuando devuelve los recursos del servidor sin
      que el cliente se entere, y de paso obliga a releer la lista de símbolos,
      que en un stream eterno se quedaría congelada. Salió de un test que se
      colgaba, y resultó ser un fallo de diseño y no del test.
- [x] **F3.6** Modelos en [api/models.py](api/models.py) y generador propio en
      [tools/gen_api_types.py](tools/gen_api_types.py) → `app/src/api/types.ts`
      (32 tipos). `--check` falla si están desfasados, para engancharlo a F8.6.

      Dos límites asumidos a propósito:
      - **No se usa `openapi-typescript`**, que es la herramienta estándar y lo
        haría mejor, porque necesita Node y hoy no hay ni `package.json`: el
        frontend llega en F4 y los tipos hacen falta **antes**, que es cuando se
        monta F4.1. Cambiar a la herramienta buena será sustituir un fichero.
      - **`/api/dashboard` no tiene modelo Pydantic** y sale como objeto libre.
        Su cuerpo lo arma `build_dashboard`, que es un ensamblado de doce
        consultas y ya es la definición de esa forma; escribirla otra vez sería
        tener dos condenadas a discrepar. El frontend lo verá como
        `Record<string, unknown>`.

      `SettingsUpdate` enumera las 41 columnas de `agent_settings` una a una,
      con sus rangos, porque es el formulario de F6.8 y un `dict` genérico no
      ayuda en una pantalla de cuarenta campos. **Un test compara esa lista con
      las columnas reales de la tabla**, así que una columna nueva no puede
      quedarse fuera de la interfaz sin que salte.
- [x] **F3.7** El build de `app/dist` se sirve con vuelta a `index.html`.
      Mientras F4 no exista, ese hueco lo ocupa una página que dice que falta
      —un 404 pelado se leería como una avería—.

      **La vuelta a `index.html` no se traga los 404 de la API**: cualquier ruta
      bajo `/api/` que no exista responde 404 en JSON. Sin esa excepción, una
      errata en una URL devolvería el HTML de la aplicación con un 200 y el
      síntoma sería un `JSON.parse` fallando tres capas más abajo. Tampoco se
      usa `StaticFiles(html=True)` montado en `/` por lo mismo: se traga el
      enrutado entero.
- [x] **F3.8** `127.0.0.1` por defecto y sin autenticación. Los controles de
      ciclo se apagan con `API_CONTROLS=false`, y **no se deducen de la
      dirección de escucha**: dentro de Docker hay que escuchar en `0.0.0.0`
      para que el mapeo de puertos funcione, así que el host no dice nada sobre
      quién puede llegar. Si se escucha fuera de loopback con los controles
      puestos, el arranque lo avisa por pantalla.
- [x] **F3.9** [tests/test_api.py](tests/test_api.py): 52 tests con `TestClient`,
      sin abrir sockets ni red. **Suite completa: 596 en verde.**

**Tres cambios de fuera de `api/` que trajo F3:**

- **`create_market_profile` y `duplicate_profile` viven ahora en
  [src/profile_settings.py](src/profile_settings.py)**, y `run.py new-profile`
  los usa. Antes la creación de perfiles solo existía dentro del comando; con
  `POST /api/profiles` habría habido dos copias, y la primera regla en divergir
  habría sido la de FE.11 —el suelo de liquidez sale del mercado—, con el
  síntoma de que un perfil creado desde la interfaz descartaría en silencio 15
  valores que el creado desde la consola sí analiza.
- **`db.update_profile()`**, para que la API no construya el `UPDATE` a mano:
  la conexión acotada niega el SQL libre, así que toda escritura pasa por un
  método con nombre.
- ⚠️ **Corregido un huérfano de `delete_profile` que venía de antes.**
  `sim_accounts.id` **es** el `portfolio_id` pero sin `references`, así que la
  cascada de `portfolios` no lo alcanzaba: borrar un perfil dejaba atrás su
  efectivo, sus posiciones simuladas y sus ejecuciones. Existía desde F1.4 y no
  se había notado porque borrar un perfil requería abrir la base a mano; con un
  botón en la interfaz, pasa a ser el camino normal. Tiene test propio.

**No hecho, y es de otra fase:** renombrar el servicio `dashboard` a `api` en
[docker-compose.yml](docker-compose.yml) (F7.4) y retirar `web/` (F8.2). Las dos
esperan a que F4 tenga frontend que servir.

### F4 — Frontend React + Tailwind

**Orden de ataque (decidido 2026-08-08).** Seis tramos, y el primero va solo porque es el que
falla: **A** andamiaje y toolchain (F4.1, F4.2, F4.10) ✅ → **B** capa de datos (F4.4, F4.5) →
**C** layout y selector de perfil (F4.3, F5.5) → **D** pantallas (F4.7, F4.8) → **E** gráficas
(F4.6) → **F** cierre (F4.9, F4.11, F7.2, F7.4).

El tramo A termina en una página que pinta `/api/markets` con los tipos generados. Es
deliberadamente un cartel de "estoy vivo" y no una pantalla: demuestra las cuatro cosas que
tenían que quedar funcionando —React compila, Tailwind aplica la paleta, el proxy alcanza la
API y los tipos de F3.6 encajan con lo que responde el servidor— y desaparece en el tramo D.

**F4 se construye mientras el experimento corre**, no antes. El dashboard viejo funciona y la
API abre el histórico en modo `ro` (D5), así que desarrollar contra la base del experimento en
marcha no lo puede tocar. Lo único que va antes del lunes es **F6.9**: es lo que puede arruinar
diez sesiones en silencio.

**Tres decisiones de diseño, tomadas antes de escribir la primera línea:**

- **Las pantallas se arman con los endpoints tipados, no con `/api/dashboard`.** Ese endpoint
  se quedó sin modelo Pydantic a propósito (F3.6) y llega al frontend como
  `Record<string, unknown>`: castear campo a campo y ningún cambio del backend rompería el
  build, que es exactamente la garantía por la que se generaron los 32 tipos. Cuesta 5-6
  peticiones en paralelo en lugar de una, y TanStack Query las cachea. **`/api/dashboard`
  pasa a legado y se borra con `web/` en F8.2.**
- **El SSE escribe en la caché de TanStack Query** (`setQueryData`), no en un estado paralelo
  de React. Con dos fuentes para el mismo precio, la pantalla acaba enseñando dos números
  distintos y no hay un sitio donde arreglarlo.
- **El perfil activo vive en la URL** (`/p/:profile/posiciones`), no en un contexto. Recargar,
  compartir un enlace o volver atrás tienen que seguir apuntando al mismo experimento: todo
  este proyecto se dedica a no confundir dos experimentos, y un selector en memoria es la
  forma más fácil de mirar el equivocado.

- [x] **F4.1** Andamiaje **Vite 8 + React 19 + TypeScript 7** en [app/](app/). El build sale
      en `app/dist`, donde lo busca `APP_DIST` de [api/deps.py](api/deps.py). Comprobado de
      punta a punta: `npm run build` y la API sirviendo **el bundle de verdad** (no la página
      de "falta el frontend"), con el 404 de `/api/...` todavía en JSON y la vuelta a
      `index.html` funcionando para las rutas de la SPA.

      Dos cosas que salieron al montarlo:
      - **TypeScript 7 ha eliminado `baseUrl`.** Ahora `paths` se resuelve relativo al propio
        `tsconfig.json`, que es lo que hacía falta de todas formas; el alias `@/…` que espera
        shadcn funciona igual.
      - **`__dirname` no existe en un config ESM** y `new URL(...).pathname` deja una barra
        delante de la letra de unidad en Windows. Se resuelve con `fileURLToPath`, y aquí
        importa: se desarrolla en Windows y se despliega en Linux dentro de Docker.
- [x] **F4.2** Tailwind CSS v4 (plugin de Vite, sin `tailwind.config.js`) y `components.json`
      listo para que el CLI de **shadcn/ui** copie componentes sin retoques.

      **La paleta es la del dashboard viejo, valor por valor**, mapeada a los nombres que
      espera shadcn en un `@theme inline`. No es nostalgia: el par positivo/negativo es
      **azul/rojo y no verde/rojo a propósito** —un divergente con polo frío y polo cálido se
      sigue leyendo sin distinguir el verde del rojo— y los `delta-good`/`delta-bad` son
      aparte porque el texto de las variaciones sí puede usar verde, donde no compite con
      ninguna serie de la gráfica. Coger la paleta por defecto de shadcn habría deshecho esa
      decisión sin que nadie lo notara hasta tenerlo delante.

      Tema oscuro por defecto, con la clase en `<html>` puesta por un script en línea antes
      de pintar: sin eso hay un fogonazo del tema equivocado en cada carga. El interruptor
      manual gana a la preferencia del sistema en los dos sentidos, igual que el viejo.
- [ ] **F4.3** `react-router` y layout: barra lateral con Perfiles, Dashboard, Posiciones,
      Decisiones, Órdenes, Ajustes.
- [ ] **F4.4** Datos con **TanStack Query** contra la API.
- [ ] **F4.5** Hook de SSE: precios y P&L moviéndose solos, con reconexión e indicador de
      "datos en vivo / desconectado".
- [ ] **F4.6** Gráficas con **Recharts** (decidido 2026-08-08): curva de capital, drawdown,
      histograma de convicción, calibración. visx da más control, pero aquí las gráficas son
      cuatro formas estándar y el trabajo de verdad está en F4.7; el comparador de F5.6
      —varias series de equity en el mismo eje— es lo más exigente y Recharts lo cubre.
- [ ] **F4.7** Portar lo que hoy hace [web/index.html](web/index.html) —4 pestañas, 10 tablas
      y 5 gráficas—: resumen con sus tarjetas, posiciones abiertas y cerradas, decisiones con
      tesis y riesgos, órdenes, eventos de riesgo, ciclos y su log en vivo, más los filtros
      por símbolo y por acción. Todo desde los endpoints tipados (ver la cabecera de F4).

      **Dos cosas que el viejo no hace y hay que añadir**: el `price_source` de las
      posiciones (`live` o `cycle`, F3.2 — sumar una posición valorada con el cierre de
      anteayer y otra con el precio de hace un minuto da un P&L que no significa nada) y el
      `age_seconds` de las cotizaciones, que es la medición de F2.1c puesta donde se ve.
- [ ] **F4.8** Estados de carga, vacío y error decentes en cada pantalla (hoy no existen).
- [ ] **F4.9** Responsive y accesible: foco visible, contraste AA, tablas navegables.
- [x] **F4.10** Modo desarrollo: `npm run dev` con proxy de `/api` y recarga en caliente.
      Verificado contra la API de verdad.

      ⚠️ **El 8000 está ocupado, y no por la API.** Es el default de `run.py api` pero
      también el del dashboard viejo (`run.py serve`), y los dos no caben —lo decía F3.1—.
      Durante el experimento eso deja de ser teórico: **las dos semanas se vigilan con el
      dashboard viejo mientras esto se construye**. Por eso el destino del proxy es
      configurable en lugar de estar fijo:

      ```
      python run.py api --port 8001
      VITE_API_TARGET=http://127.0.0.1:8001 npm run dev
      ```

      Detalle de Windows que cuesta un rato: **Vite escucha en `localhost` (`::1`), no en
      `127.0.0.1`**, así que un `curl` a la IP no responde aunque el servidor esté arriba.
- [ ] **F4.11** Retirar `web/index.html` y `web/server.py`.

### F5 — Pantalla de perfiles / experimentos

- [x] **F5.1** Tabla `profiles` — hecha en F1.2, con sus métodos en
      [src/db.py](src/db.py) (`create_profile`, `list_profiles`, `set_profile_status`,
      `delete_profile`) y tests de cascada.
- [ ] **F5.2** Listado en tarjetas con las métricas clave: capital, P&L total y del día, nº
      de posiciones, win rate, último ciclo. (En consola ya existe: `run.py profiles`.)
- [ ] **F5.3** Alta de perfil **en un solo formulario**: nombre, descripción, **mercado**
      (`eu` / `us`), capital inicial, universo, cuántos símbolos seguir en vivo, y las
      decisiones de estrategia y modelo —perfil de riesgo 1–10, diversificación 1–10,
      proveedor, modelo, clave— sin pasar por una segunda pantalla. Decidido 2026-08-08:
      **el mercado se elige al crear, no se cambia después**; de él salen horario,
      calendario, divisa, benchmark y suelo de liquidez (D8, FE.11), así que cambiarlo a
      mitad de experimento reinterpreta el histórico ya grabado.

      El backend ya lo admite: `ProfileCreate` lleva `market` (defecto `eu`) y
      `SettingsUpdate` los 41 campos. **Son dos llamadas, y así se queda**: el alta pasa
      por `create_market_profile`, que aplica las reglas del mercado (universo,
      `profile_universe`, benchmark, `min_turnover`), y meter los 41 campos en el alta
      duplicaría esa validación. Para que las dos llamadas no dejen basura, el perfil nace
      en `draft` —que ya es el default del esquema— y **solo se activa cuando el `PATCH`
      de ajustes ha cuajado**: si falla, lo que queda es un borrador visible y borrable, no
      un experimento corriendo con parámetros que el usuario no eligió.

      La UI lee `/api/markets` para el selector: divisa, horario y suelo de liquidez de
      cada bolsa salen del registro, no cableados (F3.2).
- [ ] **F5.4** Acciones: activar, pausar, archivar, **duplicar** (clonar y cambiar un solo
      parámetro es el gesto central del experimento) y borrar con confirmación por nombre.
- [ ] **F5.5** Selector de perfil global en el layout, **con el perfil en la URL**
      (`/p/:profile/...`, ver la cabecera de F4); todas las pantallas filtran por él. Con un
      solo experimento activo (decisión nº 5) el selector no parece urgente, pero los
      perfiles archivados se acumulan y son justo lo que hay que poder mirar después.
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
- [x] **F6.9** **Un ciclo sin modelo ya no se parece a un día tranquilo.** Encontrado al
      calcular el gasto del experimento de 10 días (decisión nº 2): `evaluate_entry` y
      `evaluate_exit` capturan `LLMError`, dejan un `log.warning` y devuelven `None`. Es lo
      correcto por candidato —un 429 en AAPL no debe tumbar el ciclo entero— pero con la
      cuota agotada falla **en las 33 llamadas seguidas**, y el ciclo terminaba en
      `completed` diciendo "Analizados: 20, propuestas de compra: 0", indistinguible de un
      día en que el modelo no vio nada. `report.analyzed` cuenta snapshots, no análisis
      logrados.

      `Analyst` cuenta ahora `calls` y `failures`; `TradingCycle._grade_analyst` decide qué
      hacer con la diferencia, y las dos cifras van a `cycles.analyst_calls` /
      `analyst_failures`, al `CycleReport` y a `CycleRow`/`CycleDetail` de la API.

      Tres decisiones que costaron pensarlo:
      - ⚠️ **No hay estado `degraded`, y era el plan.** `cycles.status` tiene un `check` con
        cuatro valores y **SQLite no sabe alterar una restricción**: añadirlo obligaría a
        reconstruir la tabla de la que cuelgan otras seis con `on delete cascade`. Y en una
        base ya creada el CHECK viejo rechazaría el valor nuevo, así que el fallo aparecería
        **el día que se agota la cuota**, o sea el día que esto tiene que funcionar. Se
        reutiliza `failed`, que además hace que [run.py:558](run.py#L558) devuelva código 1 y
        el planificador lo registre como error; el matiz lo dan las dos columnas nuevas.
      - **Solo el fallo total degrada el estado.** Un ciclo con 3 fallos de 33 sí analizó y
        sí pudo operar; marcarlo `failed` mentiría en la otra dirección. Queda el recuento en
        la fila y una nota en `error`.
      - **Un `halted` no se convierte en `failed`.** El kill switch es el titular de su
        ciclo y no evalúa entradas por definición, así que sus pocas llamadas no son
        representativas. Y **0 de 0 llamadas no es un fallo**: sin esa distinción, un día en
        que el screener no selecciona nada se marcaría como ciclo roto.

      [tests/test_analyst_failures.py](tests/test_analyst_failures.py): 10 tests, incluido
      el de la migración sobre una base creada antes de las columnas. **Suite: 606 en
      verde.**
- [ ] **F6.10** ⚠️ **`cycle_times` y `cycle_tz` están en el esquema y no los lee nadie.**
      Existen en `agent_settings` (con defecto `22:15` / `Europe/Madrid`) y en
      `SettingsUpdate`, así que la API los acepta y los guarda — pero
      [src/profile_settings.py](src/profile_settings.py) no los pasa a `Settings` y
      [tools/scheduler.py](tools/scheduler.py) lee `CYCLE_TIMES` **del entorno**. O sea que
      el horario que se ponga en la interfaz no cambia nada, en silencio.

      Es la misma trampa que FE.7 (`profile_universe`) y tiene dos consecuencias reales:
      hoy los ciclos se configuran en el `.env` del contenedor `scheduler`, **uno para todos
      los perfiles**, así que un perfil europeo con 8 ciclos intradía y uno americano con
      uno al cierre no se pueden expresar a la vez; y contradice la premisa de F6, que es
      que todo lo que define un experimento vive en el perfil.

      El planificador tiene que recorrer los perfiles activos, leer el horario de cada uno y
      lanzar `run.py cycle --profile X` a su hora.

      ⚠️ **No bloquea el primer experimento** (decisión nº 5: un solo perfil activo, así que
      `CYCLE_TIMES=10:20,11:20,…` en el entorno del `scheduler` hace exactamente lo que hace
      falta). Sí bloquea el comparador de F5.6, que es dos perfiles a la vez por definición.
      Lo que no se puede dejar como está es la columna muda: o la lee el planificador, o la
      API deja de aceptarla.

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
- [x] **F8.4** `.gitignore` con `node_modules/`, `app/dist/` y `.vite/`, añadidos en F3
      porque `app/` ya existe: ahí deja `tools/gen_api_types.py` los tipos del frontend.
      **`app/src/api/types.ts` sí se sube**, para que el frontend compile sin tener que
      ejecutar Python antes. Ya estaban `backup/` y `spike_*.jsonl`.
- [ ] **F8.5** ⚠️ **Hay un `.env` con claves reales en el directorio.** Está en `.gitignore`,
      pero conviene confirmar que nunca llegó a subirse.
- [ ] **F8.6** Suite de tests entera en verde: `docker compose run --rm bot python -m pytest tests -q`.
- [ ] **F8.7** Renombrar `screener_min_dollar_volume` → `screener_min_turnover`. Desde D8 la
      cifra está en la divisa del mercado, así que el nombre miente en los perfiles
      europeos. No es urgente —hay un comentario en el esquema y otro en el fichero de
      universo— pero toca esquema, `db.py`, `profile_settings.py`, `config.py`, el screener
      y sus tests, así que conviene hacerlo de una vez y no a medias. FE.11 ya usa el
      nombre bueno en `Market.min_turnover`, así que hoy el código convive con los dos.

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

**Dónde estamos (2026-08-08, noche):** F1, F2 (salvo F2.1c), FE, F6 (salvo F6.8, F6.9 y
F6.10) y **F3** cerradas. Lo siguiente es **F4**, y ya tiene contra qué programar: 24
endpoints publicados en `/openapi.json` y sus 32 tipos de TypeScript en
`app/src/api/types.ts`.

**Plan de las dos próximas semanas:**

1. ~~**Antes del lunes: F6.9.**~~ ✅ **Hecho (2026-08-08).** Un ciclo sin modelo se registra
   como `failed` con el recuento de llamadas, no como una sesión tranquila.
2. **Lunes 2026-08-10, 09:00 Madrid: F2.1c.** La medición del feed europeo, que decide entre
   `1d` con un ciclo y `1h` con ocho.
3. **Lunes: arranca el experimento** con el dashboard viejo, un solo perfil europeo.
4. **Los diez días siguientes: F4**, en los seis tramos de su cabecera. La API abre el
   histórico en modo `ro`, así que desarrollar no puede tocar el experimento en marcha.

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
  ⚠️ **El relleno diario de F2.10 es el único escritor grande del proyecto**: hasta ~225.000
  filas en la primera pasada con el universo europeo, y a la hora en que corre el ciclo del
  agente. Por eso escribe símbolo a símbolo en vez de en un lote: son ~90 transacciones
  cortas donde había una de minutos. Si aparece contención de verdad, la palanca es bajar
  `INGEST_BACKFILL_DAYS`.
- **R4 — Crecimiento del fichero.** ~50 MB al mes sin retención. Cómodo durante un año, no
  para siempre. Lo resuelve F1.9. ⚠️ **Con D8 son ~115 MB al mes solo con el perfil
  europeo** (89 símbolos × 510 barras) y ~165 MB con los dos; con la poda de 90 días el
  fichero se estabiliza sobre 350–500 MB. Sigue siendo asumible, pero ya no es
  despreciable: mirarlo antes de añadir un tercer perfil.
- **R5 — La API pasa a poder escribir.** ✅ **Cubierto (F3.3).** No se ha perdido la
  garantía: se ha movido. Las lecturas siguen abriendo SQLite en modo `ro` y las escrituras
  van por una conexión con **autorizador**, que rechaza cualquier `INSERT`/`UPDATE`/`DELETE`
  fuera de `profiles`, `agent_settings`, `agent_settings_history`, `profile_universe` y
  `portfolios`. No es una convención que haya que recordar al escribir el siguiente
  endpoint: lo impide el motor. La única excepción es borrar un perfil, que arrastra su
  histórico a propósito y exige repetir el nombre para confirmar.
- **R6 — Parámetros editables en caliente.** ✅ **Cubierto.** `agent_settings_history`
  registra cada cambio real (F6.2) y cada ciclo guarda copia de los parámetros con los que
  corrió en `cycles.settings_json` (F6.3). Queda un hueco pequeño: el ciclo hace la copia
  **al arrancar**, así que editar un parámetro con un ciclo de 20 minutos ya en marcha deja
  ese ciclo registrado con los valores de antes. Es el comportamiento correcto —el ciclo lee
  los ajustes una vez y no los recarga— pero conviene saberlo.
- **R8 — Cuota de NVIDIA NIM en el experimento de 10 días.** ✅ **Comprobado en la cuenta
  (2026-08-08): "Up to 40 rpm" y ningún contador de créditos.** O sea que el régimen es por
  minuto y no por consumo acumulado; los 1.000 créditos que documentaban las fuentes de
  terceros ya no aplican a esta cuenta.

  **El límite por minuto no aprieta, ni de lejos.** Las llamadas al analista son
  secuenciales, una cada ~30–60 s: **1–2 por minuto contra 40**. La demanda del experimento
  —hasta 33 llamadas por ciclo (20 candidatos de `screener_top_n` + 13 posiciones con
  diversificación 5), ~264 al día con 8 ciclos, ~2.600 en diez sesiones— es irrelevante
  cuando no hay tope acumulado.

  Queda una reserva pequeña: **"up to" no es un SLA** —depende del modelo y del tráfico— así
  que un 429 suelto puede aparecer. [src/llm.py](src/llm.py) ya reintenta con espera
  exponencial respetando `Retry-After`. Lo que no hay es un muro que corte el experimento a
  mitad. Y ver **F6.9**: mientras un fallo del analista se registre como "0 propuestas", una
  tanda de 429 se leería como una sesión sin oportunidades.
- **R7 — Calidad del modelo gratuito.** Llama 3.3 70B puede no dar señal útil, y entonces el
  experimento mide el modelo, no la estrategia. Por eso F5.7 (perfil de control aleatorio):
  sin algo contra lo que comparar, no se sabe distinguir un caso del otro.

---

## 5. Decisiones pendientes

1. ~~**Librería de gráficas**: Recharts o visx.~~ **Resuelto: Recharts** (F4.6).
2. **Frecuencia de los ciclos del agente.** ⚠️ **Medio resuelto: el suelo es la hora.**
   Se preguntó por un ciclo cada 5 minutos y **no cabe**, por tres techos independientes, y
   el más bajo no es la cuota del modelo:

   - **Un ciclo tarda ~20 minutos** ([src/cycle.py](src/cycle.py), `STALE_CYCLE_MINUTES`).
     El coste son las llamadas al analista, **en serie**: una por candidato
     (`screener_top_n`, 20 por defecto) más una por posición abierta para la revisión de
     salida (3–25 según diversificación). Son 20–45 llamadas a Llama 3.3 70B con
     `max_tokens=1600`, timeout de 120 s y reintentos con espera de hasta 30 s por los 429
     de la capa gratuita. Y hay cerrojo: `_check_no_other_cycle_running` **salta** un ciclo
     si ya hay otro en marcha sobre la misma cartera, así que uno cada 5 minutos se
     auto-saltaría tres de cada cuatro veces y el histórico registraría el intento.
   - **La barra de 5 minutos no existe en el analista.** `CYCLE_INTERVALS = ("1d", "1h")`
     ([src/profile_settings.py](src/profile_settings.py)), y `bar_cache` solo conoce esos
     dos. Aunque el ciclo fuera instantáneo, doce ciclos sobre la misma barra horaria verían
     los mismos indicadores y decidirían lo mismo, gastando cuota. `bars_1m` es del
     ingestor y de F9.2, no del analista.
   - **R1.** Si se confirma el desfase de ~15 minutos en Europa, un ciclo de 5 minutos
     decidiría sobre datos más viejos que su propio periodo.

   **Techo real: 8 ciclos al día.** Con `bar_interval=1h` la ventana europea (09:15–17:45)
   da 8 barras horarias; a ~20 minutos cada uno caben en las 8,5 horas, pero sin margen y
   con el gasto de modelo multiplicado por ocho. **Lo que se hará son 2–3 por sesión** —el
   README ya lo dice, y por una razón estadística y no de rentabilidad: 30 operaciones
   cerradas en semanas en lugar de meses—. Con `market=eu`, algo como
   `cycle_times=11:00,14:00,17:40`.

   **La cuota no es el límite** (R8: 40 rpm, sin créditos, y pedimos 1–2 rpm), así que los 8
   caben. Para el experimento de 10 días la pauta es **`bar_interval=1h` con los ciclos ~20
   minutos pasada la hora** —10:20, 11:20 … 17:20, más uno al final de la ventana—: con el
   desfase de ~15 minutos del feed europeo, la barra de 10:00–11:00 no está completa hasta
   las 11:15, y arrancar en punto analizaría una barra a medias. Ocho ciclos de ~20 minutos
   son 160 de los 510 de la ventana, así que cabe sin que dos se pisen (y si uno se alarga,
   el cerrojo salta el siguiente en lugar de solaparlo).

   **Se fija en 8 ciclos**, que es también el techo. El argumento no es "más datos" —ocho
   análisis de la misma barra horaria están muy correlacionados— sino la **rotación**: las
   salidas obligatorias (stop y objetivo) se comprueban en cada ciclo y **no gastan modelo**
   ([src/risk.py](src/risk.py), `mandatory_exits`). Con un ciclo al día, un stop perforado a
   las 11:00 no se detecta hasta el cierre y se sale al precio de cierre; con ocho, se
   detecta esa misma hora. Como `horizon_days` **no cierra nada al expirar** —se registra y
   se le cuenta al analista, pero las únicas salidas automáticas son stop y objetivo—, el
   número de operaciones cerradas en diez días depende de con qué frecuencia se miran esos
   niveles. Ahí está el valor de los ocho ciclos, y no en preguntarle ocho veces al modelo.

   ⚠️ **Hoy ese horario no se pone en el perfil: ver F6.10.** `cycle_times` existe en el
   esquema y no lo lee nadie; con un solo perfil activo (decisión nº 5) basta
   `CYCLE_TIMES` en el entorno del `scheduler`.

   **Lo que sigue dependiendo de F2.1c** es solo si se queda en `1d` (un ciclo a las 18:00
   de Madrid) o pasa a `1h` con esos ciclos intradía. Bajar de la hora exigiría añadir
   `5m`/`15m` a `CYCLE_INTERVALS` y a `bar_cache`, y sobre todo **paralelizar las llamadas al
   analista**; es trabajo, no un parámetro, y hoy no hay ninguna razón que lo pida.
3. ~~**Tamaño del universo a seguir minuto a minuto**: 50 es el punto de partida.~~
   **Resuelto para Europa: 89** (EURO STOXX 50 + IBEX 35, D8). Para el perfil americano
   sigue abierto y ahora se elige explícitamente con `--watch`. Condiciona R2 y R4, que ya
   están recalculados.
6. ~~**¿Dónde se elige el mercado?**~~ **Resuelto: en el alta del perfil**, junto al resto
   de las decisiones del experimento (riesgo, diversificación, modelo). No es editable
   después: ver F5.3 para el motivo y para cómo se reparte entre las dos llamadas de la API.
5. ~~**¿Se mantiene un perfil americano activo?**~~ **Resuelto (2026-08-08): un solo
   experimento a la vez**, por decisión de método —los experimentos se hacen de uno en uno
   para poder mirarlos— y el primero es el europeo. Eso quita de golpe las dos presiones que
   vigilaban R2 y R4: no hay franja de solape (89 símbolos por minuto, no 139) y el fichero
   crece a ~115 MB/mes en vez de ~165. También rebaja **F6.10** de bloqueo a trampa
   latente: con un perfil, `CYCLE_TIMES` del entorno basta.
4. ~~¿Se conserva el broker simulado, o se pasa a Alpaca paper?~~ **Resuelto: solo
   simulador.** Alpaca fuera del proyecto.
