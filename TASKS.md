# TASKS.md — Plan de trabajo

Registro de todo lo pendiente. Cada tarea tiene un id (`F1.2`) para referenciarla en
commits y conversaciones. Marcar `[x]` al cerrarla.

Última actualización: 2026-08-10 (comisiones reales del banco y el P&L realizado corregido, F5.9; confirmado el retraso de 15 min del feed europeo, F2.1c; F8.5 cerrada; los cinco perfiles alineados en 1h con los ocho ciclos; el volumen renombrado a `financial-agent-trading-data` y declarado `external`)

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

**El volumen se llama `financial-agent-trading-data` y es `external` desde el 2026-08-10**,
el día que arrancó el experimento. Antes se llamaba `financial-bot_trading-data`, un nombre
que Compose había derivado de un `name:` anterior del proyecto y que hacía avisar a cada
`compose run`. Se migró copiándolo con los cuatro servicios parados; el viejo se conserva
como respaldo del arranque. Lo que compra el `external` es que **`compose down -v` ya no
puede destruir el histórico** y que, en una máquina donde el volumen no exista, `up` falla
en el sitio en vez de arrancar un experimento sin pasado que parece el de siempre. El precio
es crearlo a mano la primera vez: `docker volume create financial-agent-trading-data`.

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

~~**Consecuencia que hay que asumir:** `screener_min_dollar_volume` sigue llamándose así pero
la cifra está en la divisa del mercado.~~ **Resuelto en F8.7** (2026-08-09): la columna se
llama `screener_min_turnover` y el nombre ya no miente en los perfiles europeos.

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
      imposible. `backup/` estaba en `.gitignore` y **se borró en F8.9** (2026-08-09). Falta
      tirar el volumen de Docker, que ya no es `docker compose down -v` sino
      `docker volume rm` por su nombre: desde el 2026-08-10 está declarado `external`.
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

      ✅ **Confirmado el 2026-08-10: son los 15 minutos.** La barra de las 9:00 la publica
      Yahoo a las 9:15. Confirmado por observación del feed, no por una corrida del spike:
      vale para decidir, pero **no hay `spike_eu_lunes.jsonl` que citar**, así que si algún
      día hace falta el número exacto —desviación, cola, si el desfase se ensancha al
      cierre— hay que medirlo de verdad.

      Consecuencias, y son menos de las que el propio apartado temía:
      - **Los ocho ciclos a las :20 no se tocan.** Esa pauta se eligió antes de medir y
        precisamente para absorber el desfase: la barra de 10:00–11:00 no está completa hasta
        las 11:15 y el ciclo entra a las 11:20. La medición la confirma.
      - **El ingestor y `bars_1m` tampoco.** Un dato retrasado está igual de bien fechado;
        F9.2 sigue en pie sin cambios.
      - **Lo que sí se cae es la premisa de F9.3**, apuntado allí: `quotes_live` no es un
        precio vivo, así que la mejora se encoge de «tiempo real» a «15 minutos».
      - **Bajar de la hora queda descartado sin condicionales**, no solo desaconsejado.

      Segunda pregunta, ahora obligatoria: **`--threads`**. Con 139 símbolos en la franja de
      solape el margen dentro del minuto baja a 2,5× (ver D3), así que ya no vale con
      dejarlo apuntado.

      ⚠️ **Esta sigue abierta, y es lo único que queda de F2.1c.** Lo que ha cambiado es que
      **ya no bloquea el arranque**, y conviene dejar escrito por qué: los 139 símbolos de D3
      exigen un perfil europeo **y** uno americano activos a la vez, y el experimento arranca
      con **uno solo** (`eu-05-muy-agresivo`). Son 89 símbolos, ~15 s en serie dentro del
      minuto, margen de ~4× y no de 2,5×. `threads=False` —lo que hay hoy, y lo que evita los
      símbolos vacíos silenciosos de Windows— cabe de sobra. Vuelve a apretar **el día que se
      active un perfil americano en paralelo**, y ese día hay que medirlo en Docker/Linux
      antes de cambiarlo: el modo de fallo es silencioso y la alternativa no está rota.
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
falla: **A** andamiaje y toolchain (F4.1, F4.2, F4.10) ✅ → **B** capa de datos (F4.4, F4.5,
F4.12) ✅ → **C** layout y selector de perfil (F4.3, F5.5) ✅ → **D** pantallas (F4.7, F4.8) ✅
→ **E** gráficas (F4.6) ✅ → **F** cierre (F4.9, F4.11) ✅. **F4 cerrada (2026-08-09).**

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
  pasó a legado y se borró con `web/` en F4.11**, sin haberse usado nunca desde el frontend.
- **El SSE escribe en la caché de TanStack Query** (`setQueryData`), no en un estado paralelo
  de React. Con dos fuentes para el mismo precio, la pantalla acaba enseñando dos números
  distintos y no hay un sitio donde arreglarlo.
- **El perfil activo vive en la URL** (`/p/:profile/positions`), no en un contexto. Recargar,
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
- [x] **F4.3** `react-router` 7 y layout: cabecera con selector de perfil, indicador de datos
      en vivo e interruptor de tema; barra lateral en dos grupos.

      **La barra lateral separa lo que depende del experimento de lo que no.** Sin esa raya,
      «Experimentos» y «Posiciones» parecen la misma clase de cosa y no lo son: una vale para
      todos y la otra cambia por completo según cuál esté seleccionado.

      Tres decisiones:
      - **El stream se abre en el layout, una sola vez.** Si cada pantalla llamara a
        `useStream()` habría una conexión SSE por pantalla montada y el servidor repetiría el
        sondeo a SQLite tantas veces como pestañas — justo lo que F3.5 evitaba moviendo el
        sondeo del navegador al servidor. Las pantallas leen de la caché.
      - **Las rutas del perfil van todas dentro de `/p/:perfil/`**, no con el perfil en un
        parámetro de consulta opcional: así no se puede perder al navegar. Con un parámetro
        opcional, cualquier enlace que se olvidara de arrastrarlo dejaría al usuario mirando
        otro experimento sin avisar.
      - **Los huecos de las pantallas dicen qué tarea las trae** (F4.7, F6.8). Un
        «próximamente» a secas es el cartel que sobrevive meses porque nadie sabe qué falta; y
        durante el experimento hay que poder distinguir un hueco de una avería.

      La página de comprobación de los tramos A y B **no se ha borrado: se queda como
      pantalla «Ingesta»**. El dashboard viejo es anterior al ingestor, así que su salud y la
      antigüedad de los precios no se ven hoy en ningún sitio, y son los dos números que hay
      que vigilar estas dos semanas.

      Comprobado contra la API: las rutas profundas (`/p/europa-01/ciclos`) devuelven el
      `index.html` y el enrutador del cliente las resuelve.
- [x] **F4.4** Datos con **TanStack Query** contra la API. `client.ts` (una sola puerta,
      rutas relativas), `keys.ts` (fábrica de claves), `hooks.ts` (un hook por endpoint con
      su tipo generado) y `queryClient.ts`.

      Tres decisiones:
      - **Las claves viven en un solo sitio** porque el SSE escribe en la caché por clave: un
        `["quotes"]` frente a un `["quotes", undefined]` serían dos entradas distintas, el
        stream actualizaría una y la pantalla leería la otra, y el síntoma sería "los precios
        no se mueven" sin ningún error por ninguna parte.
      - **Los 4xx no se reintentan.** Pedir dos veces un 422 devuelve el mismo 422 y retrasa
        el mensaje que el usuario necesita. Y **las mutaciones no se reintentan nunca**: en
        esta API las escrituras crean perfiles y lanzan ciclos.
      - **`client.ts` aplana el `detail` de los 422 de Pydantic.** Sin eso el formulario de 41
        campos de F6.8 diría "[object Object]" justo cuando hay que saber qué campo falla.
- [x] **F4.5** Hook de SSE escribiendo en la caché de Query, con el indicador de "datos en
      vivo / reconectando / desconectado".

      Los eventos se han comprobado **contra el servidor de verdad**, no supuestos, y de ahí
      salieron las tres cosas que tenían trampa:
      - ⚠️ **El evento `ingest` manda 5 campos y `/api/ingest-status` devuelve 13**, así que
        hay que **fundir, no reemplazar**: un `setQueryData(evento)` borraría
        `avg_latency_ms`, `symbols_tracked`, `bars_stored` y los ticks recientes, y el panel
        de salud se quedaría a medias en cuanto el ingestor cambiara de veredicto —o sea justo
        cuando se mira—. TypeScript no avisa, porque el evento es un subconjunto válido.
      - ⚠️ **El evento `cycle` llega de dos formas**: el estado completo al abrir y solo las
        líneas nuevas después, con `from`. Si `from` va por delante de lo que tenemos se ha
        perdido un trozo, y entonces **no se empalma a ojo** —un log que se lee como continuo
        sin serlo es peor que no tenerlo— se invalida la consulta y se relee del servidor.
      - ⚠️ **El estado de conexión sale de `readyState` y no de un temporizador de silencio.**
        Los latidos del servidor son comentarios SSE y **no disparan eventos**, así que con el
        mercado cerrado no llega nada durante horas siendo todo correcto. Un temporizador
        marcaría "roto" una conexión sana.

      Y el indicador tiene **tres estados y no dos**: el servidor retira las conexiones cada
      15 minutos a propósito (F3.5) y `EventSource` las restablece solo, así que dos estados
      parpadearían en rojo cada cuarto de hora en una conexión perfecta. Un rojo que se
      enciende sin motivo deja de creerse, y entonces tampoco se cree el de verdad.

      **`age_seconds` se corrige en el cliente.** Lo calcula el servidor al leer, así que en
      caché se congela: enseñarlo tal cual diría "hace 60 s" durante media hora. Se le suma el
      tiempo transcurrido desde que llegó el evento, que además no depende del reloj del
      navegador porque solo usa una diferencia local.
- [x] **F4.13** **`GET /api/analytics`**, que no estaba en el plan. La curva de capital vive
      en `equity_snapshots` y solo salía por `/api/dashboard`, el endpoint que F4 dejó como
      legado; y la calibración necesita un cruce entre decisiones y operaciones cerradas que
      en el navegador habría sido frágil. Tres de las cinco series salen de **vistas que ya
      existían** en `schema.sql` (`v_conviction_calibration`, `v_risk_rejections`,
      `v_performance_by_symbol`), así que la pantalla y `run.py report` no pueden acabar
      contando cosas distintas. El drawdown se calcula en el servidor por lo mismo: tenerlo
      también en TypeScript sería tener dos definiciones condenadas a discrepar.
- [x] **F4.12** **Vitest** y 11 tests de la capa de datos
      ([app/src/api/stream.test.ts](app/src/api/stream.test.ts)). No estaba en el plan y se ha
      añadido porque el empalme de los eventos —la fusión parcial, el hueco en el log, la
      antigüedad corregida— es lógica con casos, y en un repositorio con 606 tests de Python
      escribirla a ojo era el sitio raro para empezar a confiar. `npm test`.
- [x] **F4.6** Seis gráficas con **Recharts** en la pantalla de Analítica: calibración, curva
      de capital, caída desde máximos, convicción declarada, P&L por activo y rechazos por
      regla. Todas de **una sola petición** a `/api/analytics` (nuevo, F4.13).

      **La paleta se validó, no se supuso.** El README afirmaba que era apta para daltonismo;
      pasada por el validador, las dos series cumplen las seis comprobaciones en claro y en
      oscuro, y el par azul/rojo separa con ΔE 21,6 en protanopía frente al mínimo de 8. El
      trío compra/mantener/venta **falla** como paleta categórica —el gris de «mantener» no
      tiene croma— y está bien que falle: es una escala **divergente**, no categórica, y el
      gris es su punto medio.

      Cuatro decisiones:
      - **Los colores van como `var(--color-series-1)`, no como hexadecimal.** Los atributos
        de presentación de SVG aceptan variables CSS, así que el interruptor de tema repinta
        las gráficas solo; con hexadecimales habría que leerlos con `getComputedStyle` y
        redibujar a mano en cada cambio.
      - **Capital y caída son dos gráficas, nunca una con dos ejes.** Un eje doble deja que la
        escala elegida decida cuál de las dos líneas parece dominar, y dos personas leen cosas
        distintas del mismo dibujo.
      - ⚠️ **Cada barra de la calibración lleva su `n=` y las de menos de cinco operaciones
        salen atenuadas.** Sin eso la gráfica miente en su momento más peligroso: un tramo con
        una sola operación ganadora dibuja una barra del 100 % idéntica a la de un tramo con
        treinta, y es justo al principio —cuando hay pocas— cuando más ganas dan de concluir.
        Verificado con la demo: tramos de n=1 y n=3 al 0 % junto a uno de n=5 al 40 %.
      - **Cada gráfica conserva su botón de tabla**, como el dashboard viejo. No es un extra:
        es lo que mantiene el dato disponible cuando el color no basta, y es como se comprueba
        una cifra concreta —en una gráfica se estima, en una tabla se lee—.

      **Recharts se carga aparte y solo al abrir Analítica**: pesa casi tanto como el resto de
      la aplicación junta (350 → 733 KB), y es la única pantalla que lo usa. Quien viene a
      mirar si el ciclo de las 11:20 abrió algo no debería esperar por seis gráficas.
- [x] **F4.7** Portadas las seis pantallas: **Resumen** (ocho cifras + abiertas + últimos
      ciclos), **Posiciones**, **Decisiones** (con filtros de símbolo, acción y veredicto),
      **Órdenes**, **Riesgo** y **Ciclos** (con log en vivo, detalle y los controles de
      lanzar/parar de F3.4). Todo desde los endpoints tipados; las gráficas son el tramo E.

      **Tres cosas que el viejo no enseñaba y ahora sí:**
      - El **`price_source`** de cada posición (`VIVO` / `CICLO` / `SIN PRECIO`). Sumar una
        posición valorada con el cierre de anteayer y otra con el precio de hace un minuto da
        un P&L que no significa nada, así que se etiqueta cuál es cuál. Y «sin precio» se dice:
        esa posición se valora a su precio de entrada, o sea que su P&L es cero por falta de
        datos y no por no haberse movido.
      - El **`age_seconds`** de las cotizaciones, en la pantalla de Ingesta, avisando en ámbar
        a partir de 5 minutos. Es la medición de F2.1c puesta donde se ve todos los días.
      - El **matiz de F6.9** en cada fila de ciclo: `SIN MODELO` cuando ninguna llamada obtuvo
        respuesta, y `3/33 SIN RESPUESTA` cuando fallaron algunas. Un `failed` del analista y
        uno del broker se leían igual.

      Cuatro decisiones de detalle:
      - **Toda cifra de dinero lleva el símbolo del perfil** (`lib/formato.ts` lo exige como
        parámetro, no lo asume). Es FE.8: un presupuesto europeo escrito con `$` invita a
        compararlo con otro experimento como si fuera la misma unidad.
      - **Abiertas y cerradas son dos tablas, no una con filtro.** Las columnas que importan
        son distintas —P&L no realizado y distancia al stop frente a precio de salida y
        motivo— y juntarlas dejaría media tabla con guiones.
      - **La tesis y los riesgos se leen en la propia fila**, no detrás de un clic. Un texto
        que hay que ir a buscar no se lee, y entonces la pantalla que debía medir si el modelo
        discrimina entre oportunidades mide otra cosa.
      - **El log solo baja solo si ya estabas abajo.** Un log que se autodesplaza siempre es
        imposible de leer: en cuanto subes a mirar una línea, la siguiente te devuelve al final.

      Y una consecuencia que se resolvió aquí: **cuando el stream ve que un ciclo pasa de
      corriendo a parado, invalida el histórico**. Es el único momento en que cambia de golpe,
      y sin eso la pantalla seguiría enseñando las posiciones de antes del ciclo hasta que
      alguien recargara — que en un experimento que se vigila se confunde con «no ha hecho
      nada». Invalidar en cada línea de log, en cambio, serían peticiones cada dos segundos
      durante veinte minutos; hay un test de cada mitad.

      **`shadcn/ui` no se ha traído todavía, y es a propósito.** Su tabla son envoltorios sobre
      `<table>` sin nada de Radix debajo, así que copiarla habría añadido un fichero y ninguna
      capacidad — y estas ya tienen que llevar la paleta heredada y `tabular-nums`. Se traerá
      cuando haga falta algo que sí necesita Radix: el diálogo de confirmación de F5.4 y los
      avisos.
- [x] **F4.8** Estados de carga, vacío y error en cada pantalla, con `components/Seccion.tsx`.

      **El caso vacío se redacta pantalla por pantalla y no está en el componente**: «no hay
      posiciones» y «no hay decisiones» significan cosas muy distintas en un experimento de
      diez días, y un texto genérico obliga a ir a mirar la base para saber cuál es. El de
      posiciones cerradas explica además que el horizonte en días no cierra nada por sí solo,
      que es la pregunta que salió al revisar `mandatory_exits`.
- [x] **F4.9** Repaso de accesibilidad y responsive.

      ⚠️ **El tema claro tenía tres fallos reales de contraste, y se midieron en vez de
      mirarse.** Sobre la tarjeta (`#fcfcfb`), con el 4,5:1 que pide AA para texto normal:

      | Token | Antes | Ahora |
      |---|---|---|
      | `text-muted` | **3,50:1** | 4,55:1 |
      | `warning` | **1,79:1** | 4,53:1 |
      | `positive` / `negative` como texto | **4,30 / 3,85** | 4,55 / 5,50 |

      El ámbar era el peor y estaba justo en los avisos —antigüedad del precio, «SIN
      RESPUESTA», «reconectando»—, o sea ilegible exactamente donde hay que leer. El tema
      oscuro pasaba todo.

      **El arreglo respeta la separación que la paleta ya hacía**: `delta-good`/`delta-bad`
      existían aparte de `positive`/`negative` porque una cosa es una marca y otra es letra.
      Se añaden `--positive-ink` / `--negative-ink` con el mismo criterio: un relleno de
      gráfica cumple con 3:1 y una etiqueta necesita 4,5, así que los colores de las series
      **no se tocan** —eso habría cambiado las gráficas— y el texto usa la tinta.

      Lo demás del repaso:
      - **Enlace de salto al contenido.** Sin él, llegar al contenido con teclado obliga a
        recorrer la cabecera y las nueve entradas de la barra lateral en **cada** página.
      - **`prefers-reduced-motion`**: el punto que late del indicador es decorativo —el texto
        ya dice el estado— y a algunas personas les provoca mareo.
      - **Título de documento por pantalla**, con el nombre del experimento. En una SPA no
        cambia solo: un lector de pantalla anunciaría siempre lo mismo al navegar, y con
        varias pestañas del mismo experimento todas se llamarían igual.
      - Tablas con `scope`, `caption` para lectores de pantalla y desplazamiento horizontal
        **dentro de su hueco**, sin arrastrar la página.
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
- [x] **F4.11** **`web/` borrado, y con él `run.py serve`.** Se cierra a la vez que F8.2: son
      la misma tarea escrita en dos fases, y separarlas solo habría dejado una de las dos
      esperando a la otra.

      Se ha hecho ahora y no el lunes porque la condición ya se cumplía —F3 verde y F4 con
      todas sus pantallas— y **la razón para conservarlo se ha invertido**: el plan de las dos
      semanas decía vigilar el experimento con el dashboard viejo mientras se construía el
      nuevo, pero el experimento arranca mañana y el nuevo ya cubre las seis pantallas más
      Ingesta, que es justo lo que el viejo **no** tenía. Entrar en el experimento con dos
      paneles vivos era entrar con dos formas de contar lo mismo disputándose el 8000.

      Cuatro decisiones que costaron pensarlo:
      - **`/api/dashboard` se va con él, como estaba escrito.** Era el único endpoint sin
        modelo Pydantic (F3.6) y por tanto el único que llegaba al frontend como
        `Record<string, unknown>`; el frontend nunca lo usó —las pantallas se arman con los
        endpoints tipados, que era la decisión de la cabecera de F4—. Dejarlo habría sido
        mantener una segunda definición del mismo experimento, sin tipos y sin nadie que la
        ejercitara: la clase de código que sigue compilando mientras deja de ser verdad.
        **Desde hoy todos los endpoints tienen modelo**, así que un cambio del backend rompe
        el build del frontend en vez de romper la pantalla en caliente.
      - **`src/dashboard.py` se queda.** `build_dashboard` lo usa `run.py report`, y sus 15
        tests con él. Lo que sobraba era el endpoint, no el ensamblado.
      - **El test que se borra se sustituye por su inverso.** Donde había un
        `test_dashboard_lleva_perfil_y_mercado` ahora hay uno que comprueba que
        `/api/dashboard` responde **404 en JSON** y no el `index.html` del SPA. Sin él, un
        futuro cambio en la vuelta a `index.html` (F3.7) devolvería 200 con HTML a esa ruta y
        el síntoma sería un `JSON.parse` fallando en el navegador. La garantía de solo lectura
        que ese endpoint sostenía se ha movido a `/api/positions`, que es la misma conexión
        `ro`.
      - **Los comentarios que lo citaban se han puesto en pasado, no borrado.** `api/runner.py`
        explica que copió el `CycleRunner` en vez de importarlo *porque* aquel módulo tenía
        fecha de caducidad; borrar la frase al cumplirse la fecha dejaría la copia pareciendo
        una duplicación gratuita. Lo mismo en `api/main.py`, `api/deps.py` y
        `requirements.txt`: el registro de por qué la API es FastAPI y no `http.server` sigue
        valiendo aunque el fichero al que sustituyó ya no esté.

      Lo que también cae: la advertencia del puerto 8000 disputado en
      [app/vite.config.ts](app/vite.config.ts) y en el README (§2.7). `VITE_API_TARGET` se
      conserva —el 8000 lo ocupa cualquier cosa— pero ya no hay dos servidores nuestros
      peleándose por él.

      **607 tests en verde, typecheck limpio, 14 tests del frontend.** Tipos regenerados
      (`app/src/api/types.ts`, 38 → sin la entrada de `/api/dashboard`).

### F5 — Pantalla de perfiles / experimentos

- [x] **F5.1** Tabla `profiles` — hecha en F1.2, con sus métodos en
      [src/db.py](src/db.py) (`create_profile`, `list_profiles`, `set_profile_status`,
      `delete_profile`) y tests de cascada.
- [x] **F5.2** Listado en tarjetas con las métricas clave (2026-08-09): capital, P&L total y
      del día, nº de posiciones, win rate y último ciclo.

      **El backend no ha hecho falta tocarlo.** `ProfileMetrics` ya traía las doce cifras
      calculadas —su docstring dice literalmente «The figures on the profile card (F5.2)»— así
      que la tarjeta no pide nada nuevo. Eso importa más de lo que parece: si la tarjeta
      calculara su propia rentabilidad, la pantalla de Resumen y esta acabarían contando cosas
      distintas del mismo experimento.

      Cuatro decisiones:
      - ⚠️ **La tarjeta no es un `<Link>`; el nombre de dentro sí.** La versión obvia —la
        tarjeta entera navegando, que es lo que hacía el listado mínimo— **no puede sostener
        las acciones de F5.4**: un `<button>` dentro de un `<a>` es HTML inválido y los
        navegadores que lo pintan hacen el clic ambiguo. Con el nombre como enlace, además, el
        lector de pantalla anuncia «enlace: europa-01» y no la tarjeta entera de corrido.
      - **El orden no es el de la API.** `/api/profiles` devuelve por fecha de creación, que a
        las pocas semanas entierra el experimento vivo bajo tres borradores. Aquí van activos,
        pausados, borradores y archivados, y dentro de cada grupo el más reciente: la pregunta
        que contesta esta pantalla es cuál está corriendo.
      - **El número de operaciones viaja siempre pegado al win rate.** Un 100 % sobre dos
        cerradas y un 100 % sobre treinta son el mismo número y no la misma afirmación, y esta
        es justo la pantalla donde se comparan dos experimentos por esa cifra.
      - **Solo se colorea el estado que corre.** Pintar los cuatro dejaría cuatro colores
        compitiendo en una lista cuya única pregunta es cuál está vivo. La insignia lleva la
        frase entera en el `title` (F4.9): «pausado» y «borrador» significan los dos «no está
        corriendo» y son problemas muy distintos.

      **Se ha promovido `Stat` a [pieces.tsx](app/src/components/pieces.tsx) en vez de escribir
      una tercera copia.** El par etiqueta/cifra ya existía dos veces escrito a mano —`Figure`
      en Resumen y `Item` en Ciclos—, que es exactamente la deriva por la que ese módulo
      existe. `Item` se ha retirado y Ciclos usa el compartido; `Figure` se queda porque es
      otra cosa (una tarjeta-cifra, no un `dt`/`dd`).

      **610 tests en verde, typecheck limpio, 24 de front, build correcto**, y comprobado
      contra la API de verdad con la base de demostración (`tools/seed_demo.py`).
- [x] **F5.3** Alta de perfil **en un solo formulario** (2026-08-09): nombre, descripción, **mercado**
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

      **Hecho tal como estaba planificado**, en
      [app/src/components/NewProfileForm.tsx](app/src/components/NewProfileForm.tsx), y con
      la secuencia de tres llamadas comprobada contra la API de verdad: el alta responde
      `draft`, el `PATCH` de ajustes devuelve qué cambió y solo entonces se activa.

      Tres decisiones que salieron al escribirlo:
      - ⚠️ **Un fallo después del alta se dice nombrando el borrador que ha quedado.** Es el
        caso que el plan preveía —perfil en `draft` si el `PATCH` falla— pero faltaba la
        consecuencia de interfaz: un formulario que dice «error» después de haber creado algo
        invita a volver a pulsar, y el segundo intento falla con «ya existe un perfil
        llamado…», que es un mensaje imposible de relacionar con el anterior. Ahora el aviso
        distingue los tres puntos de fallo y, en los dos últimos, dice que el borrador está
        en la lista, que no está corriendo y que se puede completar o borrar.
      - **Una clave vacía no se manda.** Con NIM, columna vacía significa «usa
        `NVIDIA_API_KEY` del entorno» (F6.7), y eso lo lleva el NULL: mandar `""` escribiría
        una cadena vacía donde el NULL es el que carga el significado.
      - **Las consecuencias del mercado se enseñan mientras se elige, no después.** El
        mercado no se puede cambiar más adelante, así que la nota bajo el selector trae
        sesión, ventana operativa, divisa, benchmark, tamaño del universo y suelo de
        liquidez, todo de `/api/markets`. Una decisión irreversible tiene que enseñar lo que
        decide antes de tomarse.

      **Se añade `Slider` a [pieces.tsx](app/src/components/pieces.tsx)**, que F6.8 reutiliza.
      Lleva el valor siempre a la vista y los dos extremos nombrados: un deslizador cuyo
      número no se lee es un control que no se puede poner a propósito, y «1» y «10» no dicen
      por sí solos hacia dónde hay más riesgo.

      **610 tests en verde, typecheck limpio, 24 de front, build correcto.**
- [x] **F5.4** Acciones: activar, pausar, archivar, **duplicar** (clonar y cambiar un solo
      parámetro es el gesto central del experimento) y borrar con confirmación por nombre
      (2026-08-09). Las cinco comprobadas contra la API de verdad, incluidos los dos rechazos
      del borrado —sin `confirm` y con el nombre equivocado, 400 los dos—.

      ⚠️ **shadcn/ui sigue sin entrar, y eso contradice lo que F4.7 anticipó.** Aquella tarea
      lo dejó fuera diciendo que se traería «cuando haga falta algo que sí necesita Radix: el
      diálogo de confirmación de F5.4». Con el diálogo delante, lo que Radix aporta aquí
      —trampa de foco, Esc, el resto de la página inerte, apilado por encima de todo— es
      exactamente lo que hace `dialog.showModal()` de forma nativa, en la capa superior, con
      `::backdrop` incluido. Traerlo habría sido añadir un árbol de dependencias para
      reimplementar lo que el navegador ya trae. Lo único que Radix daba de más son las
      entradas animadas, y **el proyecto no las tiene a propósito** (DESIGN.md, «Lo que no
      hay»). La regla de F4.7 sigue en pie tal como se escribió; simplemente esto no era.

      Cuatro decisiones:
      - ⚠️ **El aviso de pausar dice que no cierra las posiciones**, con el número de las que
        hay abiertas. Es lo que nadie espera: un experimento pausado con cuatro posiciones
        sigue expuesto al mercado y **sus stops y objetivos dejan de comprobarse**, porque eso
        solo pasa dentro de un ciclo (`mandatory_exits`). Pausar sin saberlo es dejar el
        dinero fuera con el vigilante apagado.
      - **Archivar y «ver archivados» van juntos.** Archivar saca el experimento del listado,
        y sin la casilla al lado el botón parecería que ha borrado algo. Además
        **`useActiveProfile` pasa a pedir la lista con archivados**: archivar lo saca de la
        *lista*, no de la existencia, y sin eso todos los enlaces guardados a ese experimento
        dirían «no existe», que es una afirmación distinta y falsa. El selector ya lo etiqueta
        `(archived)`.
      - **Cada estado ofrece solo las transiciones que significan algo.** Un botón que está
        siempre y a veces falla enseña a ignorar la fila entera: activar lo que ya corre no
        hace nada, pausar un borrador que nunca corrió tampoco, y archivar algo en marcha son
        dos decisiones a la vez. **Duplicar está siempre**, archivados incluidos: lo más
        interesante de clonar suele ser el que ya terminó.
      - **Duplicar abre la copia en sus Ajustes.** El gesto no es clonar: es clonar *y cambiar
        un parámetro*, y dejar la copia en el listado obliga a acordarse de cuál era el
        siguiente paso.

      La clave de caché del listado lleva ahora `include_archived`: compartir clave dejaría
      los archivados dentro después de apagar la casilla, y seguirían enseñándose.

      **610 tests en verde, typecheck limpio, 24 de front, build correcto.**
- [x] **F5.5** Selector de perfil global en la cabecera, **con el perfil en la URL por su
      nombre**: `/p/europa-01/posiciones`. Con un UUID ahí nadie sabría qué experimento está
      mirando, que era justo el motivo de sacarlo de la memoria de React; la API acepta nombre
      o id (`find_profile`), así que no hay que traducir nada. Se ha corregido el docstring de
      [api/deps.py](api/deps.py), que decía que la interfaz mandaría el id.

      Dos decisiones:
      - **Cambiar de perfil conserva la sección.** De `/p/europa-01/posiciones` se salta a
        `/p/otro/posiciones`, no al resumen: comparar la misma pantalla de dos experimentos es
        el gesto que F5.6 llama central, y volver al inicio en cada salto lo convertiría en
        cuatro clics.
      - **Un perfil que no existe no redirige en silencio.** Un enlace guardado que apunta a
        un experimento renombrado o borrado lo dice, con la lista de los que hay. Mandar al
        inicio dejaría al usuario creyendo que falló el clic, y si el perfil se borró por
        accidente esa sería la única señal que existía.

      **Consecuencia asumida: renombrar un perfil rompe los enlaces guardados.** Es el precio
      de una URL legible, y está bien que se rompa de forma visible en lugar de resolverse a
      un experimento distinto.
- [x] **F5.6** **Comparador**: varios perfiles en la misma gráfica de equity, con tabla de
      métricas lado a lado (2026-08-09). Es lo que convierte esto en un experimento y no en
      un bot. Ruta `/compare`, en el grupo de abajo de la barra lateral: comparar es por
      definición de más de un experimento, así que colgarlo del perfil seleccionado sugeriría
      que solo va de ese.

      ⚠️ **La gráfica va en % y la tabla en la divisa de cada uno, y son dos respuestas al
      mismo problema.** El proyecto no convierte divisa en ningún sitio (D8), así que un eje
      compartido solo puede llevar una cifra indexada; una tabla, en cambio, tiene una columna
      por experimento y cada celda puede llevar su símbolo. Poner euros y dólares en el mismo
      eje es el error de FE.8 dibujado a escala. Y aun dentro de una divisa, indexar es lo que
      hace comparables dos presupuestos distintos: 200 sobre 10.000 y 200 sobre 1.000 son el
      mismo dinero y no el mismo resultado. Comprobado que el indexado del cliente coincide
      con el `total_return_pct` que calcula el servidor.

      ⚠️ **Como mucho dos curvas comparten eje, y eso salió de medir, no de estimar.** La
      paleta validada tiene dos tonos categóricos; el verde está reservado a `delta-good` y el
      ámbar a `warning`, así que un tercero tendría que salir de lo que queda, y lo que queda
      choca con el azul que ya está en uso: **el morado da ΔE 3,7–5,8 en deuteranopía contra
      `--series-1`**, frente a un objetivo de 8 y un suelo duro de 15 en visión normal. Se
      probaron tres combinaciones y las tres fallan. Inventar un tercer tono igualmente es
      cómo una gráfica acaba afirmando que dos experimentos son distintos cuando el lector no
      los distingue. Así que a partir de tres se pasa a **múltiplos pequeños**: una gráfica por
      experimento, una serie cada una, **compartiendo dominio vertical** —con autoescala, un
      vaivén del 0,4 % y una carrera del 12 % dibujarían la misma forma, que es justo lo que
      una comparación no puede hacer—. La paleta no se ha tocado.

      **La tabla pone un experimento por columna y no por fila**: las filas son las métricas,
      así que dos capitales quedan uno al lado del otro, que es de lo que se trata. Con los
      experimentos como filas, comparar una cifra sería recorrer una columna de unidades
      mezcladas. El nombre de la métrica va como `th scope="row"`, porque cada fila es una
      cifra y sin eso un lector de pantalla dice «10.240,00 €» sin decir de qué.

      **Recharts sigue fuera del bundle principal**: la comparación se carga con `lazy()` igual
      que Analítica, y las dos comparten el trozo de Recharts.

      ⚠️ ~~**Lo que F6.10 bloquea, y lo que no.**~~ **Resuelto el mismo día**: F6.10 se cerró
      después, así que ya se pueden **correr** dos experimentos con horarios distintos y no
      solo comparar los históricos que ya existen.

      **616 tests en verde, typecheck limpio, 24 de front, build correcto.**
- [x] **F5.7** Perfil de control: screener en modo `random`, para tener contra qué medir el
      criterio del LLM (2026-08-09).

      ⚠️ **El modo `random` existía desde antes y estaba roto de una forma que ningún test
      veía.** Puntuaba con `hash(symbol)` y el docstring prometía «un orden estable pero
      arbitrario». Python **aleatoriza el hash de `str` por proceso** (`PYTHONHASHSEED`), y
      **cada ciclo corre como su propio subproceso** —lo lanzan el planificador y
      [api/runner.py](api/runner.py)—. Medido el 2026-08-09 con ocho símbolos: tres procesos
      seguidos dieron tres «top 4» distintos.

      Rompía dos cosas, y ninguna habría fallado en voz alta. El grupo de control analizaría
      un conjunto arbitrario **distinto en cada ciclo**, así que la comparación contra el
      perfil puntuado llevaría encima el ruido de un universo móvil además del efecto que
      quiere aislar (R7). Y el histórico dejaba de ser reproducible: `cycles.settings_json`
      guarda los parámetros con los que corrió cada ciclo justamente para poder reinterpretarlo
      después, y con una semilla por proceso los mismos parámetros sobre los mismos datos no
      dan los mismos candidatos. El test que había —`test_random_mode_is_stable_across_calls`—
      pasaba porque comparaba dos llamadas **del mismo proceso**.

      Arreglado con `arbitrary_score()` sobre `zlib.crc32`, que no es un buen hash y no
      necesita serlo: hace falta que sea el mismo en todos los procesos, que reparta y que esté
      en la biblioteca estándar. **El test nuevo lanza dos subprocesos con `PYTHONHASHSEED`
      distinto y compara**, que es lo que faltaba.

      **El gesto de crear un control es duplicar y cambiar un parámetro**, así que va como
      casilla dentro del diálogo de duplicar de F5.4 y no como un botón aparte: un control
      *es* un duplicado con el parámetro ya decidido. Si el duplicado sale bien y el cambio de
      modo falla, el aviso lo dice nombrando la copia — la diferencia entre arreglar un campo
      y empezar de nuevo, y sobre todo entre saber y no saber que el «control» que hay en
      pantalla no lo es.

      **`screener_mode` sube a `ProfileSummary`** para que la tarjeta lleve la etiqueta
      `CONTROL` y el comparador tenga su fila. No es cosmético: **un control que no se
      identifica es lo mismo que no tenerlo**, y sus números están pensados para ser peores,
      así que sin etiqueta se leen como un experimento fallido en vez de como la referencia.

      **617 tests en verde** (1 nuevo), typecheck limpio, 24 de front, build correcto, y el
      flujo comprobado contra la API.

- [x] **F5.8** **«Cerrar experimento»: vender las posiciones y dejar el resultado
      realizado** (2026-08-10, el mismo día que se pidió). Era un hueco real: hoy solo se puede **pausar**, y
      pausar no cierra nada. El resultado que se lee al final de la semana es **no
      realizado** —la cartera valorada a mercado— y no el resultado de las decisiones que el
      agente tomó. Peor: un experimento pausado con posiciones vivas **deja de comprobar
      stops y objetivos**, porque eso solo pasa dentro de un ciclo.

      **Tiene que vender por el broker, no por SQL.** Un `UPDATE` a `positions` dejaría el
      histórico mintiendo: sin órdenes, sin fills, sin precio de salida y sin motivo. Y como
      la API **no puede escribir en el histórico** (F3.3, y lo impide SQLite), esto sale por
      subproceso como el ciclo — `run.py close-experiment --profile X` o un modo del ciclo.

      Cuatro cosas que decidir al hacerlo:
      - **A qué precio se vende.** La misma regla que todo lo demás: apertura de la barra
        siguiente más deslizamiento en contra. Inventarse aquí un precio mejor que el que
        tuvo el resto del experimento falsearía justo la cifra que se quiere leer.
      - **Qué estado queda.** `profiles.status` tiene un CHECK de cuatro valores y **SQLite
        no sabe alterar una restricción** —es lo que bloqueó el `degraded` de F6.9—, así que
        un `closed` nuevo obligaría a reconstruir la tabla. Lo barato es reutilizar `paused`
        o `archived` y que el cierre se note en que no quedan posiciones abiertas.
      - **Es irreversible y hay que confirmarlo**, con el mismo criterio que el borrado:
        vender la cartera entera no se deshace.
      - **Qué pasa con el mercado cerrado.** O se espera a la apertura siguiente, o se dice
        que no se puede cerrar ahora. Lo que no vale es vender a un precio inventado.

      **Hecho así, y las cuatro decisiones se resolvieron como estaban previstas.**
      `TradingCycle.close_all_positions()` reutiliza el camino de salida del ciclo entero
      —`_execute_exit`—, así que la venta deja orden, precio de salida, motivo y evento de
      riesgo, y la liquidación **queda registrada como un ciclo más**, con su copia de
      parámetros y su punto en la curva de capital. Se distingue por la regla
      `experiment_closed`, y cuenta como salida **forzada**: agruparla con las
      discrecionales haría que la analítica leyera una liquidación como una decisión del
      modelo.

      Tres cosas que salieron al escribirlo:
      - **Al modelo no se le pregunta**, y no es por ahorrar cuota: no es una decisión de
        mercado, es el final del experimento. Consultarlo registraría un «sell» como si se
        hubiera juzgado, y no se juzgó. Hay test.
      - **Con el mercado cerrado o en `dry_run` se rechaza por delante**, en lugar de dejar
        un montón de órdenes canceladas: las posiciones siguen abiertas y se dice, que es lo
        que evita leer como resultado final una cartera que no se liquidó.
      - **Sin posiciones abiertas no escribe nada.** «No había nada que cerrar» no es un
        fallo y no debe dejar un ciclo vacío en el histórico.

      **El test estructural de F3.3 hizo su trabajo**: la ruta nueva no compilaba hasta
      declararla como control, porque escribe en el histórico y por eso mismo **no puede ser
      un endpoint que escriba** — sale por subproceso, igual que el ciclo. Reutiliza el
      `CycleRunner`, así que comparte cerrojo y log: una sola operación por cartera a la vez.

      El botón vive **junto a los controles de ciclo** y no en las acciones del perfil,
      porque es una operación sobre la cartera y comparte ese cerrojo.

      **640 tests en verde** (6 nuevos), typecheck limpio, 24 de front.

- [x] **F5.9** ⚠️ **Comisiones reales, y el P&L realizado se estaba declarando de más.**
      Pedido y cerrado el 2026-08-10, antes de arrancar el experimento y no por casualidad:
      es lo único que no se puede retrofitar, porque una operación ya ejecutada no se puede
      recobrar.

      **La tarifa.** La del banco desde el que se opera de verdad, por orden y **por lado**:
      **4,11 € en las españolas (`.MC`) y 3,00 € en el resto de Europa**; Estados Unidos
      sigue en cero, que para acciones es cierto. Vive en [src/fees.py](src/fees.py) y se
      resuelve **por símbolo**, no por cartera: un perfil europeo lleva las dos a la vez, así
      que un número por cartera no puede ser correcto para ambas.

      **Lo que había era una mentira heredada del mercado americano.** `sim_commission`
      nacía a 0 y el docstring de [src/sim_broker.py](src/sim_broker.py) decía por qué —«US
      brokers do not charge for shares»—. Con D8 el experimento se mudó a Europa y ese
      defecto se quedó: un perfil muy agresivo con ocho ciclos diarios habría operado gratis,
      y operar gratis favorece justo a la conducta que el experimento quiere medir.

      **Se descartó ponerla en el perfil**, que era la opción que cumple F6 al pie de la
      letra. La razón es que **no define un experimento**: es un hecho del banco, y el mismo
      para todo perfil que opere esa bolsa. Con cinco perfiles y cinco tarifas, comparar dos
      mediría la tarifa en vez de la estrategia. Lo que sí se queda en el perfil es
      `sim_commission`, que pasa a ser un **recargo sobre la tarifa**: 0 es «el estándar del
      banco», y otro valor es una desviación deliberada para un supuesto. La pantalla de
      Ajustes lo dice —el rótulo es «Recargo de comisión por orden»— porque un campo que ya
      no significa lo que decía es exactamente la trampa de FE.7.

      ⚠️ **El fallo que apareció al medir esto, y que llevaba ahí desde el principio.** La
      venta hacía `realized = (price - entry) * qty - commission` y restaba **solo la
      comisión de venta**. La de compra había salido de la caja pero nunca entró en
      `avg_entry_price`, así que **no llegaba nunca al P&L realizado**: cada operación cerrada
      declaraba de más exactamente lo que costó abrirla. La caja y el equity siempre
      estuvieron bien; lo que estaba mal era la cifra por operación, que es justo la que el
      experimento existe para medir. **Con comisión 0 era invisible**, y por eso ha
      sobrevivido a 640 tests.

      La arregla una columna nueva, `sim_positions.entry_commission`, con su entrada en
      `ADDED_COLUMNS` para que la base viva la reciba sola. Se descartó meter la comisión en
      `avg_entry_price`, que no habría necesitado columna: ese campo es **el precio al que se
      ejecutó**, se enseña en pantalla y lo miran el stop y el objetivo, así que ensuciarlo
      habría cambiado en silencio dónde salta un stop. La venta parcial **prorratea** por
      cantidad; cobrarla entera en la primera dejaría esa operación peor y la última gratis,
      y el total seguiría cuadrando, que es lo que lo haría difícil de ver.

      **`MIN_ORDER_NOTIONAL` se queda en 100 €, y es deliberado.** Con la tarifa, una ida y
      vuelta de 100 € paga 8,22 € en españolas y 6,00 € en el resto: **6–8 % de fricción**,
      o sea que la posición tiene que subir un 8 % solo para empatar. Para bajarlo al 1 %
      harían falta órdenes de ~850 €, y al 0,5 %, de ~1.650 €. Se deja en 100 € a propósito
      —queda escrito aquí para no descubrirlo dentro de seis semanas leyendo un P&L— porque
      con el capital del experimento subirlo dejaría la cartera en dos o tres posiciones y
      mataría la diversificación que F6.5 dimensiona. El comentario de
      [src/risk_presets.py](src/risk_presets.py) que dice que el mínimo existe «para que la
      comisión no se coma el resultado» se escribió con comisión 0; **ahora se la come, y es
      una decisión, no un descuido.**

      **665 tests en verde** (25 nuevos, [tests/test_fees.py](tests/test_fees.py) entero),
      typecheck limpio, 24 de front. Uno de los nuevos es de cobertura: si alguien añade una
      bolsa a `MARKETS` y no le pone tarifa, falla ahí y no seis semanas después en un
      resultado que salió halagador.

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
- [x] **F6.8** Formulario con sliders y **valores derivados visibles en vivo** ("con estos
      ajustes: máx. 8 posiciones, 1,5 % de riesgo por operación") — 2026-08-09.
      [app/src/pages/Settings.tsx](app/src/pages/Settings.tsx).

      **Los dos deslizadores son la pantalla; los 41 campos son la letra pequeña.** Es la
      forma que pide F6.5: riesgo y diversificación deciden los nueve límites duros, y el
      panel de al lado dice cuáles son mientras el deslizador se mueve. El resto va agrupado
      debajo en las cuatro familias que los parámetros tienen de verdad —modelo, estrategia,
      ejecución y límites duros—, y la última solo se abre en modo avanzado.

      ⚠️ **«En vivo» obligaba a una decisión, y las dos alternativas obvias eran malas.**
      Aplicar un `PATCH` en cada movimiento sería escribir en la base para contestar una
      pregunta; y reimplementar `derive_limits` en TypeScript sería una segunda fórmula
      condenada a discrepar el día que se toque un ancla, con la pantalla prometiendo límites
      que el Risk Manager no aplica —que es la única mentira que este formulario no puede
      decir—. De ahí **`GET /api/profiles/limits-preview`**, que llama a la misma
      `queries.derived_limits` sobre un diccionario que solo existe para esa llamada y no
      escribe nada. Tiene tres tests: que reproduce las tres anclas de la tabla de F6.5, que
      la ruta literal no queda tapada por `/{profile_ref}`, y que no deja el perfil tocado.

      ⚠️ **Encontrado de paso: `GET /api/profiles/{ref}/settings` no tenía modelo Pydantic** y
      llegaba al frontend como `Record<string, unknown>` — justo lo que F4.11 dio por
      eliminado al retirar `/api/dashboard`, y lo que la cabecera de F4 prohíbe. Construir
      encima el formulario de 41 campos habría repetido el error entero: un cambio del backend
      no rompería el build, rompería la pantalla en caliente. Se añaden **`AgentSettings` y
      `SettingsBundle`**, con su test comparando los campos contra las columnas reales, igual
      que el que ya existía para `SettingsUpdate`. Dos matices que el modelo de lectura fija y
      el de escritura no:
      - **Los booleanos se convierten una vez, aquí.** SQLite no tiene booleano y devolvía
        0/1; un `dry_run: 0` leído como verdadero diría que un experimento está en seco
        cuando está operando.
      - **Un límite derivado sigue llegando NULL.** El NULL es el dato (F6.5): significa
        «vuelve a derivarlo». Un modelo que lo rellenara con un número borraría la diferencia
        entre un límite elegido y uno heredado, y el modo avanzado se quedaría sin nada que
        encender.

      Tres decisiones más:
      - **Solo se manda lo que cambió.** `update_settings` ya ignora un campo que llega con el
        valor que tenía, pero mandar los 41 en cada guardado llenaría
        `agent_settings_history` de filas «5 → 5», y ese historial es lo que hace legible un
        experimento después. Comprobado: reenviar el mismo valor devuelve `applied: []`.
      - **El formulario se remonta cuando cambian los ajustes guardados** (`key` en
        `updated_at`). Un formulario que discrepa en silencio de la base es peor que uno que
        se recarga.
      - **El tope por sector se enseña diciendo que no se aplica.** Es el caso de FE.12, y es
        el peor de los tres posibles si se calla: un límite cuya ausencia no falla, solo
        acumula posiciones en un sector. `cycle_times` y `cycle_tz` llevaban el mismo tipo de
        aviso —«hoy se guardan y no los lee nadie»— y **dejó de ser cierto el mismo día**: al
        cerrar F6.10 el aviso se sustituyó por el formato que espera el planificador.

      **`Pending.tsx` se borra**: F5.3 y F6.8 han tapado los dos huecos que anunciaba, y era
      el único uso que tenía.

      **616 tests en verde** (6 nuevos), typecheck limpio, 24 de front, build correcto, y
      comprobado contra la API de verdad.
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
- [x] **F6.10** ⚠️ **`cycle_times` y `cycle_tz` estaban en el esquema y no los leía nadie.**
      Cerrada el 2026-08-09, **pedida explícitamente**: «el nombre del experimento activo
      debería definirse en la app, no en un `.env`, porque si no cada vez que quiera iniciar
      uno nuevo tendría que modificar el `.env` y luego volver a actualizarlo en Docker».

      [tools/scheduler.py](tools/scheduler.py) recorre ahora los perfiles **activos**, lee el
      horario y la zona de cada uno y lanza `run.py cycle --profile X` a su hora. Del entorno
      solo quedan `RUN_ON_START` y `SCHEDULER_REFRESH_SECONDS`; **`PROFILE`, `CYCLE_TIMES` y
      `CYCLE_TZ` se han retirado** de [docker-compose.yml](docker-compose.yml) y del `.env`.

      **Lo que resuelve de verdad es la molestia, no la columna muda:** el plan **se relee
      cada minuto**, así que activar un experimento, pausarlo o cambiarle las horas desde las
      pantallas de Experimentos y Ajustes **surte efecto sin tocar ningún fichero y sin
      reiniciar el contenedor**. Comprobado con el planificador corriendo: activar un segundo
      perfil lo recoge en segundos, cambiar `cycle_times` reprograma el siguiente ciclo, y
      pausar el primero lo saca del plan.

      Cuatro decisiones que salieron al escribirlo:
      - ⚠️ **Una errata en el horario ya no mata el planificador.** `parse_times` lanzaba
        `SystemExit`, y eso era una bomba desde F6.8: `cycle_times` pasó a ser un campo que
        escribe la interfaz, así que un error de tecleo en un perfil habría dejado **sin
        planificar a todos los demás**, con el contenedor pareciendo vivo. Ahora es
        `ScheduleError`, se salta ese perfil y se dice en el log.
      - **Y además se rechaza al guardar.** `SettingsUpdate` valida el formato y lo devuelve
        **normalizado**, así que «9:5» y «09:05» no acaban siendo dos cadenas distintas en
        `agent_settings_history`, que se lee a ojo.
      - ⚠️ **Aviso de horario que analizaría la barra equivocada.** Con barras diarias, un
        ciclo **antes del cierre** analiza la barra del día **sin terminar** —el «cierre» que
        lee es el precio que hubiera en ese momento—; con barras horarias, uno fuera de la
        ventana operativa lee la barra de ayer. Ninguno de los dos falla: los dos deciden
        sobre datos que no son lo que parecen.
      - **Los ciclos van uno detrás de otro y no a la vez.** Dos perfiles que caigan en el
        mismo minuto doblarían las peticiones a Yahoo y al modelo justo entonces.

      **Consecuencia que hay que asumir, y hay que mirarla antes del lunes:** el horario que
      mandaba era `CYCLE_TIMES=16:30,18:30,20:30` del `.env` —la sesión **americana**— y
      `europa-01` tiene en su columna el default del esquema, `22:15`, que es el cierre de
      **Nueva York**. Ninguno de los dos es un horario europeo, así que no se ha «preservado
      el comportamiento» porque no había ninguno que preservar: **había que elegirlo en
      Ajustes**. **Resuelto al día siguiente** (2026-08-10): `europa-01` se borró y los cinco
      perfiles de la decisión nº 5 nacen con `18:00 Europe/Madrid`, tras el cierre de Madrid y
      con margen para el desfase del feed. Si F2.1c manda pasar a `1h`, hay que rehacer el
      horario **y** contar cinco ciclos de veinte minutos, que ya no caben seguidos.

      ⚠️ **Superado el mismo 2026-08-10:** los cinco están ya en `1h` con los ocho ciclos de
      `10:20` a `17:20 Europe/Madrid`. El problema de que cinco rondas no quepan seguidas
      **no llega a darse porque solo hay un perfil activo**: los otros cuatro se corren
      después, de uno en uno.

      **626 tests en verde** (9 nuevos).
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

      ~~⚠️ **No bloquea el primer experimento**… Sí bloquea el comparador de F5.6.~~
      **Resuelto:** la lee el planificador, que era una de las dos salidas que esta tarea
      dejaba escritas. Dos experimentos con horarios distintos ya se pueden correr a la vez.

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

- [x] **F7.1** Estructura del repo tal como se planificó: `app/`, `api/`, `src/`, `tools/`,
      `tests/`. `web/` ya no está: se borró en F4.11.
- [x] **F7.2** [Dockerfile](Dockerfile) multietapa: etapa `node:22-slim` que compila React y
      etapa Python que copia `app/dist`. **Una imagen (507 MB), un puerto.**

      **Node no llega a la imagen final**, que es la razón de las dos etapas: la de
      compilación se lleva `node_modules` entera y en producción el frontend son tres
      ficheros estáticos que sirve FastAPI. `npm ci` y no `npm install`, para que construir
      dos veces dé lo mismo las dos veces.

      Y **`app/dist` y `app/node_modules` están en el `.dockerignore`**. No es por tamaño:
      el `node_modules` del anfitrión es de Windows y sus binarios no valen en Linux, y un
      `dist` compilado a mano hace semanas se copiaría encima del recién construido sin que
      nada avisara. Que el único build que existe sea el de la imagen es lo que hace que
      `docker compose up` sirva siempre lo que hay en el código.
- [x] **F7.3** Servicio `ingestor` en [docker-compose.yml](docker-compose.yml) — hecho en
      F2.12, con `restart: unless-stopped` y `stop_grace_period: 15s`.
- [x] **F7.4** `dashboard` → **`api`**, con `run.py api`, la publicación en `127.0.0.1:8000`
      y `API_CONTROLS` configurable.

      **El healthcheck ahora apunta a `/api/markets` y no a `/`.** La raíz devuelve el
      `index.html` incluso cuando falta el build, así que daba por sano un contenedor que
      sirve la página de "falta el frontend"; `/api/markets` recorre el enrutado de verdad y
      **no toca la base**, así que tampoco se pone en rojo porque el ingestor tenga el
      fichero ocupado un instante.
- [x] **F7.5** Volumen con nombre `trading-data` intacto, y el comando de extracción
      actualizado al servicio nuevo: `docker compose cp api:/app/data/trading.db ./data/`.
- [x] **F7.6** `docker compose up -d` levanta los cuatro servicios y la aplicación está en
      `http://localhost:8000`. Verificado de punta a punta: `api` en *healthy* sirviendo el
      bundle compilado dentro de la imagen, `ingestor` durmiendo hasta la apertura del lunes
      y `scheduler` con el perfil europeo cargado.

      ⚠️ **Un tropiezo que costará a cualquiera que repita esto:** el `.env` trae
      `PORTFOLIO_NAME=experimento-01`, heredado de antes de F6.4, y el planificador arrancaba
      diciendo que ese perfil no existe. Se ha añadido `PROFILE` como variable de compose
      —`PROFILE=europa-01 docker compose up -d scheduler`— porque sin ella no había forma de
      elegir perfil sin editar el `.env`. La limpieza de verdad es F8.3.
- [x] **F7.7** Modo desarrollo documentado en el README (§2.7): `docker compose up -d api
      ingestor` y `npm run dev` en el anfitrión. Funciona sin configurar nada porque el proxy
      de Vite apunta al 8000, que es donde publica el contenedor. Documentados los dos
      detalles que cuestan un rato solos: **`localhost` y no `127.0.0.1`** (Vite escucha en
      `::1`) y `VITE_API_TARGET` si la API se mueve de puerto.
- [x] **F7.8** [README.md](README.md) actualizado: capítulo de la interfaz reescrito entero
      (las seis pantallas, el perfil en la URL, los datos en vivo y qué significa `SIN
      MODELO`), estructura del repo con `api/` y `app/`, modo desarrollo, y las 21 menciones
      al `dashboard` puestas al día.

### F8 — Limpieza

- [x] **F8.1** Base a cero (2026-08-08): `data/trading.db` apartada en
      `backup/trading.db.pre-F6-20260808` (mismo criterio que F1.1: borrarla sería
      irreversible) y `docker compose down -v` ejecutado, volumen `trading-data` eliminado.
      **`backup/` borrado el 2026-08-09** en F8.9, por decisión explícita y sabiendo que es
      irreversible: son copias de esquemas anteriores a F6, o sea de antes de que existiera
      el experimento que se está midiendo.
- [x] **F8.2** Hecho en **F4.11**, donde está el registro. Eran la misma tarea vista desde F4
      («cerrar el frontend») y desde F8 («limpiar»), y se cerraron juntas.
- [x] **F8.3** Las variables de estrategia ya no las lee el ciclo (F6.4) y
      [.env.example](.env.example) queda **solo con infraestructura** (2026-08-09).

      ⚠️ **La premisa de esta tarea era falsa, y conviene dejarlo escrito.** Decía que la
      mitad de abajo seguía ahí «porque `run.py import-profile` la necesita», y que se
      borraría cuando no quedara ningún `.env` que migrar. No era eso: **`import-profile` lee
      el `.env` de la versión anterior —uno que ya trae esas treinta variables escritas— y no
      esta plantilla.** Rellenarlas aquí para luego importarlas sería crear un perfil por el
      camino largo, teniendo `new-profile` desde FE.7. Así que la condición de borrado nunca
      fue «que no quede nada que migrar»: la sección sobraba desde que existió `new-profile`.
      El comando se queda, porque sigue haciendo su trabajo para quien sí tenga ese `.env`.

      Comprobado antes de borrar, y es lo que lo hace seguro: **la única variable obligatoria
      del fichero es `NVIDIA_API_KEY`** (el único `_require` de [src/config.py](src/config.py));
      las demás tienen valor por defecto en el código, así que `run.py check` sigue
      funcionando en una instalación recién clonada sin perfil, que era la garantía de F6.4.

      **Y había un defecto de fondo en la mitad de arriba, la que sí está viva:** documentaba
      `DASHBOARD_CONTROLS`, que **no lo lee nadie** desde que F7.4 lo renombró a
      `API_CONTROLS`. Poner a `false` lo que decía la plantilla dejaba los controles de ciclo
      encendidos, en el único fichero que se consulta justo antes de publicar el puerto fuera
      de loopback. `CYCLE_TIMES` también seguía con `22:15` —el cierre de Nueva York— cuando
      el experimento es europeo; ahora trae los tres ciclos de la decisión nº 2.

      **Tres mensajes mandaban al camino equivocado** y se han corregido: el de `run.py check`
      sin perfil, el de `run.py profiles` con la base vacía y el de `select_profile` en
      [src/profile_settings.py](src/profile_settings.py). Los tres ofrecían `import-profile` a
      quien no tiene nada que importar, y el resultado habría sido un perfil del experimento
      americano heredado. Ahora ofrecen `new-profile` primero e `import-profile` como el caso
      de migración que es. El test que los sostenía comprobaba literalmente `import-profile`,
      así que se ha invertido: ahora fija que lo que se ofrece es `new-profile`.

      El [README.md](README.md) §2.3 hacía lo mismo en el paso 2 del arranque —y tenía dos
      pasos numerados «2»—; ahora crea el perfil con `new-profile --market eu` y menciona la
      importación entre paréntesis. En §3.3 se ha quitado `LLM_MODEL` del `.env`: el modelo es
      un parámetro del perfil, porque cambiar de modelo es cambiar de experimento.

      **607 tests en verde.**
- [x] **F8.4** `.gitignore` con `node_modules/`, `app/dist/` y `.vite/`, añadidos en F3
      porque `app/` ya existe: ahí deja `tools/gen_api_types.py` los tipos del frontend.
      **`app/src/api/types.ts` sí se sube**, para que el frontend compile sin tener que
      ejecutar Python antes. Ya estaban `backup/` y `spike_*.jsonl`.
- [x] **F8.5** ⚠️ **Hay un `.env` con claves reales en el directorio.** Está en `.gitignore`,
      pero conviene confirmar que nunca llegó a subirse. ✅ **Confirmado el 2026-08-10:
      nunca se subió.** No aparece en el índice (`git ls-files --error-unmatch .env` falla)
      ni en ninguna revisión de toda la historia (`git log --all -- .env`, vacío), y sigue
      en `.gitignore`. **No hay que reescribir historia ni rotar ninguna clave**, que era la
      pregunta real detrás de la tarea.
- [x] **F8.6** **606 en verde dentro de Docker.** Y no fue un trámite: encontró un fallo real
      que el anfitrión no veía.

      `test_carga_inicial_grande_no_dispara_el_aviso` medía el disco de verdad, así que decía
      la verdad solo en la máquina donde se escribió: pasaba en Windows (~1,1 ms/fila) y
      fallaba en el contenedor, que es **donde el código corre**. Un test que se rompe en el
      entorno de destino y pasa en el de desarrollo es el peor reparto posible; ahora usa
      reloj falso, igual que su vecino.

      Debajo había algo más serio: **el umbral de contención de F2.9 estaba mal calibrado
      para el entorno real.** Medido dentro del contenedor, una escritura tranquila cuesta
      **3,6–3,9 ms/fila** frente a los 1,1 del anfitrión, y el aviso estaba en 5 — o sea que
      habría saltado en cargas iniciales perfectamente sanas, que es justo el aviso que cría
      lobos contra el que se escribió esa medición. Subido a 15, unas 4 veces el coste
      tranquilo del contenedor: lo que se busca detectar es una espera por `busy_timeout`,
      que son cientos de ms por fila, no un disco lento.
- [x] **F8.7** `screener_min_dollar_volume` → `screener_min_turnover` (2026-08-09). Desde D8
      la cifra está en la divisa del mercado, así que el nombre mentía en los perfiles
      europeos. Tocaba esquema, `db.py`, `profile_settings.py`, `config.py`, el screener,
      `universe_data.py`, `api/models.py`, los tipos generados y cuatro tests, y se ha hecho
      de una vez como estaba previsto.

      ⚠️ **Lo que no estaba previsto, y era lo único peligroso: un renombrado no es un alta,
      y tratarlo como tal destruye el ajuste en silencio.** `schema.sql` es idempotente para
      tablas pero `create table if not exists` no renombra nada sobre una base ya creada, así
      que cambiar solo el esquema habría dejado la columna vieja con el valor bueno,
      `_add_missing_columns` habría **añadido** la nueva con el default de 20 M, y el código
      habría leído la vacía. En este caso concreto eso es **FE.11 deshecha**: el suelo del
      perfil europeo vuelve de 5.000.000 € a 20 M, y el síntoma no es un error sino 15 de los
      89 símbolos saliendo del análisis sin que nada lo diga.

      De ahí `RENAMED_COLUMNS` en [src/db.py](src/db.py), separado de `ADDED_COLUMNS` porque
      son problemas distintos, y `_rename_columns()` **antes** de `_add_missing_columns()`:
      cuando la segunda mira, el nombre nuevo ya está y no hace nada. Los tres estados se
      distinguen a propósito en vez de intentar el `ALTER` y ver qué pasa —ninguno de los dos
      nombres, solo el nuevo, o **los dos a la vez**— y el tercero **para en seco con
      `DatabaseError`** en lugar de elegir: si están las dos columnas, una lleva el valor y la
      otra es la que leería el código, así que seguir usaría un número que nadie eligió, que
      es justo lo que este mecanismo existe para evitar.

      Dos decisiones más:
      - **La variable de entorno `SCREENER_MIN_DOLLAR_VOLUME` conserva su nombre.** Solo la
        lee `Settings.load()`, o sea `import-profile`, y lo que ese comando lee es un `.env`
        escrito por la versión anterior. Renombrarla habría dejado al único código que existe
        para leer ese fichero buscando una clave que el fichero no tiene.
      - **El historial y los snapshots no se reescriben.** `agent_settings_history` guarda el
        nombre del campo como texto y `cycles.settings_json` lleva la copia de los parámetros
        con los que corrió cada ciclo: son el registro de lo que pasó, y cambiarles el nombre
        del campo a posteriori diría que un ciclo de agosto corrió con una columna que
        entonces no existía.

      **610 tests en verde** (3 nuevos: que el valor sobrevive al renombrado, que repetirlo no
      hace nada y que las dos columnas a la vez paran en seco), typecheck limpio, tipos
      regenerados.
- [x] **F8.8** Pasar el código existente al inglés (2026-08-09). La convención quedó escrita el
      2026-08-09 en [CLAUDE.md](CLAUDE.md) («El código va en inglés, lo que se lee en
      español») y en [DESIGN.md](DESIGN.md), y **rige para el código nuevo desde ya**; esta
      tarea es la deuda de lo que ya estaba escrito.

      **Qué toca:** `app/src/paginas/` → `pages/`, `perfil/` → `profile/`,
      `components/graficas/` → `charts/`, `piezas.tsx` → `pieces.tsx`, y con ellos los ~20
      componentes (`Tabla`, `Boton`, `Insignia`, `BarraLateral`, `SelectorPerfil`…), sus
      props y `lib/formato.ts`. En Python no hay identificadores que cambiar —ya están en
      inglés— pero sí **todos los comentarios y docstrings**, que son varios miles de líneas
      y son el registro real del proyecto: traducirlos mal cuesta más que dejarlos.

      **Lo que NO se toca, y es la mitad del trabajo:** el texto de pantalla se queda en
      español. Un renombrado ciego que traduzca también los literales convierte la interfaz
      al inglés sin que nadie lo haya pedido.

      ⚠️ **Cambia las rutas de la URL** (`/p/europa-01/posiciones` → `/positions`), que es
      estado que el navegador recuerda en marcadores e historial. Es la razón de peso para
      hacerlo **de una vez y en un commit propio**: a medias deja el código en dos idiomas,
      que es exactamente lo que la convención evita.

      **Hecho (2026-08-09): el frontend entero.** Las tres carpetas (`paginas/`→`pages/`,
      `perfil/`→`profile/`, `graficas/`→`charts/`), los 27 ficheros con nombre español, los
      74 exports, las props, los locales y los comentarios. Las rutas cambiaron con ello, y
      también el parámetro de consulta `?ciclo=` → `?cycle=`. Dos nombres no salieron del
      diccionario: `Globo` es `ChartTooltip` y no `Tooltip`, porque cada gráfica importa el
      `Tooltip` de Recharts en la misma línea para montar el nuestro dentro; y las filas de
      tabla son `DecisionTableRow`, `OrderTableRow`… y no `DecisionRow`, que ya es el nombre
      del tipo generado que reciben. Verificado con typecheck, los 14 tests de front y el
      build. Se renombraron también las claves internas de `BarCache.stats()`
      (`simbolos`/`barras`/`caidos` → `symbols`/`bars`/`stale`), que son claves de JSON y
      por tanto código.

      **Hecho también: Python entero.** Los ~740 bloques de comentario y docstring de
      `src/`, `api/`, `tools/`, `run.py` y `tests/`, los 247 nombres de test en español y
      35 identificadores sueltos que se habían colado (`mercado`, `filas`, `huecos`,
      `MAX_UNIVERSO_SEGUIDO`…). Los docstrings alimentan el OpenAPI, así que hubo que
      regenerar `app/src/api/types.ts`.

      **Dos herramientas y un error del que aprender.** El renombrado se hizo tokenizando
      con `tokenize` y sustituyendo solo tokens `NAME`, nunca por texto plano: un primer
      intento con reemplazo directo convirtió «las barras» en «las bars», porque contiene
      «as barras». La traducción de comentarios se aplicó por rango de líneas, y ahí se
      coló el fallo de verdad: un rango mal calculado en `test_api.py` borró tres líneas de
      código —dos `assert` incluidos— **sin romper ningún test**, porque lo que quedaba
      seguía pasando. Se detectó comparando el árbol sintáctico de los 65 ficheros contra
      `HEAD`, ignorando docstrings, y comprobando que la lista de sentencias fuera idéntica.
      Esa comparación es la verificación que faltaba: los tests en verde no bastan cuando
      lo que se borra son los propios tests.

      **Decisión sobre los literales:** los mensajes de log y de excepción **no se traducen**
      pese a lo que dice la tabla de CLAUDE.md, y no es un descuido. El log del ciclo se
      muestra tal cual en la pantalla de Ciclos, así que es texto de pantalla; y además
      `api/runner.py::_stage` deduce la etapa buscando cadenas dentro de él («Resumen del
      ciclo», «RECHAZADA»), de modo que traducirlos rompería el indicador de progreso sin
      que ningún test lo note. Si algún día se traducen, se cambian los dos sitios a la vez.
- [x] **F8.9** Limpieza de lo que sobraba en el directorio (2026-08-09).

      **Basura en disco, ninguna en el repositorio:** `SERSJAUMEAPPDATAocaltemp/f64smoke/`
      —una carpeta nacida de una ruta mal expandida en una consola durante el smoke de
      F6.4, no de ningún código: no hay una sola lectura de `TEMP` ni de `APPDATA` en el
      proyecto—, `backup/` (ver F8.1), `.pytest_cache/` y los cinco `__pycache__/`.

      **Dos dependencias de npm que nadie importaba**, ambas restos del `shadcn init`:
      `class-variance-authority` (cero apariciones en `app/src`) y `tw-animate-css`, que sí
      se importaba en `index.css` pero solo aporta las utilidades `animate-in`/`fade-in` de
      shadcn, y la única animación del proyecto es `animate-pulse`, que es de Tailwind. Se
      quedan `clsx` y `tailwind-merge`, que son lo que hay debajo de `cn()`.

      **Se conserva `app/components.json`** aunque hoy no haya ni un componente de shadcn:
      es la configuración que hace que traerse uno sea un comando y no una tarde, que es
      justamente el plan que documenta DESIGN.md.

      **No se encontró código muerto**, y se buscó: los 21 módulos de `src/` y `tools/`
      tienen quien los importe, los 34 ficheros de `app/src` tienen quien los use, y las
      siete dependencias de `requirements.txt` están todas en uso. `src/dashboard.py`
      parecía huérfano tras F4.11 y no lo es: lo usa `run.py report`, que es la analítica de
      consola.
- [x] **F8.10** Redirigir las rutas viejas en español a las nuevas, para que un enlace
      guardado antes de F8.8 siga llevando a su sitio. **Hecho (2026-08-09).**

      **Qué pasa hoy, comprobado contra la aplicación en marcha y no supuesto:** el servidor
      devuelve 200 con el `index.html` —la vuelta al SPA de F3.7 no distingue rutas del
      router— y el enrutador cae en la ruta comodín, así que se pinta `NotFound`. Nombra la
      ruta pedida y ofrece volver al inicio, o sea que no es una pantalla en blanco, pero es
      un callejón sin salida: el enlace no lleva a donde llevaba.

      **Las diez rutas que cambiaron.** Ocho del perfil, bajo `/p/:profile/`:

      | Antes | Ahora |
      |---|---|
      | `resumen` | `summary` |
      | `analitica` | `analytics` |
      | `posiciones` | `positions` |
      | `decisiones` | `decisions` |
      | `ordenes` | `orders` |
      | `riesgo` | `risk` |
      | `ciclos` | `cycles` |
      | `ajustes` | `settings` |

      Y dos generales: `/perfiles` → `/profiles` y `/diagnostico` → `/diagnostics`.

      **Cómo:** una ruta de redirección por cada una en [app/src/App.tsx](app/src/App.tsx),
      con `<Navigate replace />` para no dejar el salto en el historial —si no, el botón de
      volver atrás rebota entre la ruta vieja y la nueva, que es el mismo motivo por el que
      `Home` ya usa `replace`.

      **Tres cosas que hay que mirar, porque fallan en silencio:**

      - **El `..` de una ruta anidada resuelve por ruta, no por URL.** Dentro de
        `p/:profile`, un `to="../positions"` da `/p/:profile/positions`, que es lo que se
        quiere; pero si alguien le pone `relative="path"` pasa a resolver sobre el segmento
        y acaba en `/p/positions`. Conviene un test que abra una ruta vieja y compruebe
        dónde termina, en lugar de fiarse de leerlo.
      - **`<Navigate>` no arrastra la query.** `/p/x/ciclos?ciclo=<id>` perdería el ciclo
        seleccionado por el camino, así que la redirección tiene que conservar el
        `location.search`.
      - **El parámetro también se renombró** (`?ciclo=` → `?cycle=`), de modo que además hay
        que traducirlo: o en la redirección, o aceptando `ciclo` como respaldo en
        [Cycles.tsx](app/src/pages/Cycles.tsx). Sin esto, el enlace de un ciclo concreto
        redirige bien pero abre la pantalla sin el detalle desplegado.

      **Consecuencia que se asume:** vuelven los diez nombres en español al fichero que F8.8
      dejó entero en inglés. Es una excepción a la convención de idioma y conviene que esté
      escrita aquí y no solo en el código, porque una capa de compatibilidad no la retira
      nadie por su cuenta. Si algún día se quita, se quita entera y de una vez, igual que se
      hizo la migración.

      **Cómo quedó, y en qué se apartó del plan.** Las tres cosas que fallaban en silencio
      se han resuelto de una vez sacando el cálculo del destino a una función pura,
      `legacyTarget(pathname, search)` en [app/src/legacyRoutes.tsx](app/src/legacyRoutes.tsx),
      en lugar de escribir un `<Navigate to="..">` por ruta:

      - **El destino se construye absoluto**, así que la trampa del `..` no existe en vez de
        estar evitada. Era la más fea de las tres: `/p/europa-01/ciclos` con `relative="path"`
        acaba en `/p/cycles`, que **es una ruta válida** —`:profile` casa con `cycles`— así que
        el síntoma no habría sido un error sino un experimento inexistente llamado «cycles».
      - **La query viaja entera** y `?ciclo=` se traduce a `?cycle=` por el camino, que era la
        opción de las dos que TASKS proponía. Se ha hecho en la redirección y no aceptando
        `ciclo` como respaldo en [Cycles.tsx](app/src/pages/Cycles.tsx) para que la
        compatibilidad viva en un solo fichero: repartida entre la ruta y la pantalla, quitarla
        algún día obligaría a acordarse de los dos sitios.
      - **El nombre del perfil se pasa tal cual llegó.** Re-codificarlo convertiría un `%20` en
        `%2520` y el perfil dejaría de encontrarse con la URL pareciendo casi correcta.

      Las diez rutas **no se escriben en `App.tsx`**: se recorren desde las tablas del propio
      módulo con un `.map`, para que un nombre añadido a la tabla no pueda quedarse sin registrar
      — que es justo el fallo que una capa de compatibilidad no sabe denunciar.

      **Diez tests** en [app/src/legacyRoutes.test.ts](app/src/legacyRoutes.test.ts), y son de
      función pura y no de router montado: el proyecto no tiene `jsdom` ni
      `@testing-library/react`, y añadir dos dependencias para comprobar un mapa de cadenas
      habría sido más aparato que garantía. Lo que pedía la tarea —abrir una ruta vieja y
      comprobar dónde termina— se responde igual, porque el destino es exactamente lo que la
      función devuelve. Incluido el caso inverso: una ruta que nunca existió sigue cayendo en
      `NotFound`, porque redirigirla a algo plausible taparía la errata.

      **607 tests en verde, typecheck limpio, 24 tests de front** (14 → 24).

      **No se tocan** las menciones a las rutas viejas dentro de los registros de F4.3 y
      F5.5: eran ciertas cuando se escribieron y este fichero es un registro, no un espejo
      del código. Es el mismo criterio con el que F4.11 dejó en pie las menciones a `web/`
      en lugar de borrarlas de las tareas anteriores. La línea de la lista de decisiones sí
      se corrigió, porque esa describe el presente.

**Repaso del sistema de diseño (2026-08-09, sin id: no estaba en el plan).** Hecho al
cerrar F5 y F6.8, comprobando en lugar de mirando. Tres comprobaciones automáticas —ningún
hexadecimal en `className`, ninguna receta de tarjeta escrita fuera de
[pieces.tsx](app/src/components/pieces.tsx), ningún identificador en español en `app/src`—
más una lectura de DESIGN.md contra el código. Salieron cuatro cosas:

- ⚠️ **DESIGN.md nombraba los componentes en español**, que es lo que F8.8 renombró:
  `<Tarjeta>`, `<Insignia>`, `<Etiqueta>`, `<Campo>`, `<Boton>`, `<Seccion>`, `<Vacio>`,
  `clasesTarjeta()`, `Procedencia.tsx`… **El documento que CLAUDE.md declara obligatorio antes
  de tocar `app/` describía una interfaz que ya no existe**, y eso es peor que no tenerlo:
  quien lo leyera primero se pondría a buscar `Tarjeta.tsx`. Sincronizado entero; la prosa se
  queda en español, que es lo correcto.
- ⚠️ **Tres restos de F8.8 en el código**, todos de la categoría «esto también es código»: los
  **comentarios de [index.css](app/src/index.css)** —el fichero que define la paleta— seguían
  en español, la clave de `localStorage` era `tema` y la clase del enlace de salto era
  `.salto` con su `id="contenido"`. Una clave de almacenamiento y un nombre de clase CSS son
  código, igual que las claves de JSON que F8.8 sí renombró en `BarCache.stats()`. El script
  en línea de `index.html` **lee `tema` como respaldo**: cambiar la clave sin más le habría
  puesto el tema oscuro a quien tenía guardado el claro, sin que nada lo explicara.
- **`ConfirmDialog` escribía a mano la receta de tarjeta**, que es exactamente lo que
  `pieces.tsx` existe para evitar. Pasa por `cardClasses()` y solo añade lo propio de un
  diálogo.
- **DESIGN.md no recogía las piezas nuevas ni dos reglas que salieron de medir.** Añadidos
  `Stat`, `Slider`, `ConfirmDialog`, `ProfileCard`, `ProfileStatus` y `Chart height`, y dos
  secciones nuevas en Gráficas: **cuántas series caben** (dos, con el ΔE medido) y **comparar
  entre unidades** (nunca dinero de dos divisas en el mismo eje).

**Una posición sin precio dejaba de estar vigilada, en silencio (2026-08-10, sin id).**
Salió al verificar una pregunta —si el modelo ve el precio de lo que tiene en cartera cuando
el screener no lo selecciona—. La respuesta es que **sí**: las posiciones abiertas entran en
cada ciclo como símbolo obligatorio y **sin ocupar una de las 20 plazas** (se excluyen de la
puntuación del screener y se reincorporan para el análisis), así que su stop se comprueba y su
tesis se revisa ocho veces al día aunque no vuelvan a salir cribadas.

⚠️ **Pero si un símbolo en cartera no traía cotización, fallaban tres cosas a la vez y ninguna
avisaba.** `SimBroker._mark` cae al **precio de entrada**, así que el P&L sale 0 como si no se
hubiera movido; `mandatory_exits` compara ese precio congelado contra el stop, de modo que
**el stop no puede saltar por mucho que el valor se desplome**; y la revisión discrecional
hacía `continue` sin log. El ciclo terminaba `completed`. La pantalla sí era honesta —etiqueta
`SIN PRECIO`— pero **el histórico no**, y el histórico es lo que se lee después.

No es hipotético con este universo: FE.2 documenta que un sufijo mal escrito **aparece vacío**,
y D8 que la tabla europea deja pasar los festivos locales de Xetra y Milán como días de
mercado, con esos símbolos vacíos.

Ahora: aviso en el log nombrando los símbolos, línea **`SIN PRECIO`** en el resumen del ciclo
—que es lo que se ve en la pantalla de Ciclos— y un **`risk_event` con regla `no_price`**, que
entra solo en la pantalla de Riesgo y en la gráfica de rechazos por regla. Es `rejected` porque
`risk_events.verdict` solo admite dos valores y **SQLite no sabe alterar un CHECK** (la
lección de F6.9); encaja, además, porque no se pudo aprobar nada para ese símbolo.

**La posición no se cierra automáticamente**, y es deliberado: vender al precio que justamente
no tenemos sería peor que aguantar. Lo que cambia es que ya no se calla. **644 tests en verde**
(4 nuevos).

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
- [ ] **F9.3** Ejecución intradía real aprovechando los datos de 1 minuto. **Concretado el
      2026-08-10**, que era una línea suelta y ahora tiene un fallo medido detrás.

      ⚠️ **El precio de ejecución no es el del momento de la orden.** `fill_price` es la
      **apertura de la barra siguiente** (`MarketSnapshot`), y eso se diseñó para barras
      diarias: decidir con el cierre de ayer y ejecutar en la apertura de hoy es realista, y
      evita regalarse el hueco de apertura. **Con barras horarias esa protección casi
      desaparece** —el cierre de las 10:00 y la apertura de las 10:00 son casi el mismo
      precio— y aparece un desfase nuevo: el ciclo de las 10:20 decide con la barra
      09:00–10:00 y **ejecuta al precio de las 10:00**, que para cuando el modelo ha
      contestado tiene 20–40 minutos. No es sesgo de anticipación —no ve el futuro— pero
      **tampoco es un precio al que se hubiera podido comprar**.

      **La solución está ya escrita a medias en el proyecto:** `quotes_live` lleva el último
      precio de cada símbolo, refrescado **cada minuto** por el ingestor, y hoy no llega al
      circuito de operar (ver [EXPERIMENT.md](EXPERIMENT.md) §3). Usarlo como precio de
      ejecución hace la orden ejecutable de verdad **y** conecta los dos relojes.

      Dos condiciones antes de tocarlo:
      - **La decisión sigue usando solo barras completas.** Lo que cambia es dónde se
        ejecuta, no lo que ve el analista; si no, vuelve el sesgo que los tres precios
        evitan.
      - ⚠️ ~~**Depende de F2.1c.**~~ **Resuelto, y en contra: el 2026-08-10 se confirmó el
        desfase de 15 minutos**, así que `quotes_live` **no es un precio vivo** y la mejora
        se encoge de «tiempo real» a pasar de 20–40 minutos de retraso a 15. **Sigue
        mereciendo la pena** —conecta los dos relojes y hace la orden ejecutable de verdad,
        que era el otro motivo— pero **esta tarea no se puede llamar «ejecución intradía
        real» en Europa**, y el nombre importa porque es lo que la vendía. Renombrarla es
        parte de hacerla.
- [ ] **F9.4** Noticias / sentimiento como entrada adicional del analista. **Depende de
      F9.7**: hasta saber qué fuentes hay y con qué cobertura, no se puede diseñar.
- [ ] **F9.5** Notificaciones (Telegram) al abrir o cerrar posición.
- [ ] **F9.6** Publicar en internet: sería el momento de Supabase + Cloudflare del plan
      anterior, con autenticación. Hoy no hace falta y costaría dinero.
- [ ] **F9.7** ⚠️ **Spike: ¿se pueden conseguir noticias y fundamentales, y a qué precio?**
      Pedido el 2026-08-10. Hoy el sistema es **100 % técnico**: los ~30 indicadores que ve
      el analista salen todos de barras OHLCV, y el prompt le **prohíbe expresamente** citar
      resultados trimestrales, catalizadores o titulares. La pregunta es si eso se puede
      levantar sin romper nada.

      **Es un spike y no una implementación**, por lo mismo que F2.1: es lo único que puede
      invalidar una decisión ya tomada, y lo barato es medir antes.

      **Qué hay que responder, en este orden:**

      1. **Cobertura, y es la pregunta que hunde el resto si sale mal.** El universo son 89
         valores europeos, buena parte medianas españolas (ANE.MC, LOG.MC, SCYR.MC…). La
         cobertura de noticias de Yahoo para eso **no se parece a la de una large cap
         americana**, y un dato que existe para 20 de 89 no es una entrada del analista: es
         un sesgo, porque el modelo vería contexto solo de las grandes y lo interpretaría
         como señal. **Hay que medirlo símbolo a símbolo**, igual que FE.2 verificó los 89
         contra Yahoo uno a uno.
      2. **Qué expone `yfinance` sin clave**: `.info` (PER, capitalización, márgenes),
         estados trimestrales y `.get_news()`. Cuánto de eso llega relleno para los 89.
      3. **Coste.** ⚠️ **Rompe la premisa de 0 €** en cuanto haga falta una API de noticias
         de verdad. Es la misma advertencia de F9.1: es el momento de decidirlo a
         propósito, no de descubrirlo en la factura.
      4. **Cadencia.** No es «cada X minutos». Los fundamentales cambian con los resultados,
         así que un refresco diario sobra; las noticias son continuas y son otro problema.
         Mezclarlos en la misma tabla y el mismo reloj sería el error fácil.
      5. **Peticiones por símbolo.** Cada fuente nueva son más llamadas a Yahoo desde la
         misma IP doméstica: recalcular R2 antes, no después.

      **Dos trampas que el spike tiene que dejar escritas, porque no fallan solas:**

      - ⚠️ **Los fundamentales de `yfinance` son los de HOY, no los de la fecha.** Usarlos
        para el backtesting de F9.2 sería **sesgo de anticipación** en su forma más pura:
        backtestear marzo con el PER de agosto es saber el futuro. O se guardan con su
        fecha desde el primer día —construyendo el histórico nosotros, como hace el
        ingestor con `bars_1m`— o F9.2 no puede tocarlos.
      - ⚠️ **La regla de honestidad del prompt es lo que hace interpretable el
        experimento.** Hoy, si el modelo cita un catalizador, es una alucinación y se ve.
        En cuanto pueda citar noticias de verdad, «se lo inventó» y «lo leyó» dejan de
        distinguirse, y con ellas la mitad del valor de la pantalla de Decisiones. Levantar
        esa regla exige decidir **cómo se marca en el registro** qué titular vio y cuándo.

      **Consecuencia de método si sale adelante:** deja de ser el mismo experimento. El
      grupo de control de F5.7 compara screener con y sin criterio; comparar «con noticias»
      contra «sin noticias» pide **otro perfil más**, no cambiar el que corre.

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

**Dónde estamos (2026-08-09, tarde):** F1, F2 (salvo F2.1c), FE, F3, F4, **F5**, **F6**, F7
y F8 cerradas. La interfaz nueva es la única que hay —`web/` se borró en F4.11— y
corre en Docker sobre 24 rutas de la API, todas con modelo Pydantic. **Ya no queda ningún
cartel de «pendiente» en la interfaz**: `Pending.tsx` se borró al cerrar F6.8.

**Queda pendiente, en total:**

1. ~~**F2.1c** — lo único bloqueante…~~ **Ya no bloquea (2026-08-10).** El retraso está
   confirmado en 15 minutos y no cambia la pauta de ciclos; lo que queda abierto es
   `--threads`, y con un solo perfil europeo el margen dentro del minuto es de ~4×, así que
   se decide con datos y sin prisa. Ver F2.1c.
2. ~~**Elegir el horario de `europa-01`.**~~ **Hecho el 2026-08-10**: `europa-01` se borró y
   los cinco perfiles nuevos nacieron con `18:00 Europe/Madrid`. **Ese mismo día pasaron los
   cinco a `bar_interval=1h` con los ocho ciclos de `10:20` a `17:20`**, que es la pauta
   decidida; los cuatro en pausa se cambian ahora y no al activarlos para que una comparación
   entre niveles de riesgo no acabe midiendo también la cadencia.
3. **FE.12 / el tope por sector de F6.5** — se calcula y no se aplica, por falta de dato de
   sector por símbolo. La pantalla de Ajustes ya lo dice en voz alta.
4. ~~**F8.5**~~ ✅ **Hecho el 2026-08-10**: el `.env` no está en el índice ni en ninguna
   revisión de la historia. Nada que rotar.
5. **F1.1 (resto)** — tirar los volúmenes viejos de Docker: el `financial-bot_trading-data`
   que quedó como respaldo del arranque, y cualquier resto anterior a F8.1. Ya no se hace con
   `docker compose down -v` —`financial-agent-trading-data` es `external` y ese comando no lo
   toca— sino con `docker volume rm` por su nombre. ⚠️ **Nunca el del experimento en marcha**:
   es el histórico entero.
6. **F9** entera, que no bloquea nada — con **F9.7** recién añadida (spike de noticias y
   fundamentales, del que depende F9.4) y **F9.3** concretada (el precio de ejecución
   arrastra 20–40 minutos con barras horarias).

**Plan de las dos próximas semanas:**

1. ~~**Antes del lunes: F6.9.**~~ ✅ **Hecho (2026-08-08).** Un ciclo sin modelo se registra
   como `failed` con el recuento de llamadas, no como una sesión tranquila.
2. **Lunes 2026-08-10, 09:00 Madrid: F2.1c.** La medición del feed europeo, que decide entre
   `1d` con un ciclo y `1h` con ocho.
3. **Lunes: arranca el experimento**, con **un solo perfil activo** —`eu-05-muy-agresivo`—
   y **ocho ciclos horarios** de 10:20 a 17:20. Los otros cuatro niveles de riesgo quedan
   creados y en pausa, para correrlos después uno a uno (decisiones nº 2 y nº 5, revisadas
   el 2026-08-10). ⚠️ **Ya no con el dashboard
   viejo:** F4 se cerró antes de tiempo, así que el experimento se vigila con la interfaz
   nueva desde el primer día. Es lo que había que conseguir —y de paso la única que enseña la
   salud del ingestor y la antigüedad de los precios, que son los dos números de estas dos
   semanas— pero conviene saber que se estrena con datos de verdad y sin la de repuesto.
4. **Los diez días siguientes: F5 y F6.8**, las dos pantallas que faltan. Las lecturas de la
   API abren el histórico en modo `ro` y las escrituras solo alcanzan la configuración (F3.3),
   así que desarrollar no puede tocar el experimento en marcha.

---

## 4. Riesgos y puntos a vigilar

- ~~**R1 — Latencia real del dato.**~~ ✅ **Confirmado el 2026-08-10 y deja de ser un riesgo:
  son los 15 minutos** (la barra de las 9:00 llega a las 9:15). Es el precio que paga D8 a
  cambio del horario, y ya se sabe.

  ⚠️ **Este apartado decía que entonces «el experimento se queda en ciclos diarios», y eso
  ya no es verdad**: la decisión del 2026-08-10 fija `bar_interval=1h` con ocho ciclos a las
  :20, tomada antes de medir y precisamente para absorber el desfase. Confirmarlo **no
  empuja de vuelta a `1d`**. Se corrige aquí y no se borra porque la frase estuvo escrita y
  alguien podría acordarse de ella.

  Lo que el desfase sí se lleva por delante está en F9.3: `quotes_live` no es un precio
  vivo. Y lo que deja tocado, aunque no lo diga ninguna tarea, es el **techo de
  granularidad**: por debajo de la hora, un ciclo decidiría sobre datos más viejos que su
  propio periodo, y eso ya no es una sospecha.
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

   ⚠️ ~~**Hoy ese horario no se pone en el perfil: ver F6.10.**~~ **Ya sí** (F6.10,
   2026-08-09): se pone en la pantalla de Ajustes de cada experimento y el planificador lo
   recoge sin reiniciar nada. ~~Los cinco perfiles en marcha (decisión nº 5) van a las
   **18:00 de Madrid** con barras diarias.~~ **Ya no** (2026-08-10): los cinco están en `1h`
   con `10:20,11:20,…,17:20 Europe/Madrid`, aplicado por `PATCH .../settings`, así que queda
   en `agent_settings_history` y se puede deshacer desde Ajustes.

   ⚠️ **Pasar a `1h` no es solo cambiar el intervalo**: cinco ciclos de ~20 minutos seguidos
   son ~100, así que u ocho rondas no caben en la ventana, o hay que bajar el número de
   perfiles. **No llega a morder porque solo hay un perfil activo** — ocho rondas de ~20
   minutos son 160 de los 510 de la ventana—. El aviso sigue vigente para el día que se
   active un segundo perfil sin pausar el primero.

   ~~**Lo que sigue dependiendo de F2.1c** es solo si se queda en `1d` o pasa a `1h`…~~
   ⚠️ **Decidido el 2026-08-10, y decidido antes de F2.1c a propósito:** `bar_interval=1h`
   con los ocho ciclos, `10:20,11:20,…,17:20 Europe/Madrid`. Es la pauta que este mismo
   apartado ya proponía —veinte minutos pasada la hora, para que el desfase de ~15 min del
   feed europeo no deje la barra a medias—, así que la medición de F2.1c no la cambia: la
   confirma o dice cuánto hay que correr el minuto. Lo que F2.1c sigue decidiendo es si el
   intradía **real** de F9.3 tiene sentido en Europa, que es otra pregunta.

   Bajar de la hora seguiría exigiendo añadir `5m`/`15m` a `CYCLE_INTERVALS` y a `bar_cache`,
   y sobre todo **paralelizar las llamadas al analista**; es trabajo, no un parámetro.

   **Consecuencia asumida:** con `1h`, `SMA200` pasa a ser 200 **horas** (~24 sesiones) y no
   200 días. El indicador deja de significar lo mismo y las tesis del modelo lo notarán.
   `lookback_days=200` no hace falta tocarlo: con intervalo horario son 200 días naturales de
   barras, ~1.200, muy por encima de las 200 que pide el SMA200.
3. ~~**Tamaño del universo a seguir minuto a minuto**: 50 es el punto de partida.~~
   **Resuelto para Europa: 89** (EURO STOXX 50 + IBEX 35, D8). Para el perfil americano
   sigue abierto y ahora se elige explícitamente con `--watch`. Condiciona R2 y R4, que ya
   están recalculados.
6. ~~**¿Dónde se elige el mercado?**~~ **Resuelto: en el alta del perfil**, junto al resto
   de las decisiones del experimento (riesgo, diversificación, modelo). No es editable
   después: ver F5.3 para el motivo y para cómo se reparte entre las dos llamadas de la API.
5. ~~**¿Se mantiene un perfil americano activo?**~~ ~~**Resuelto (2026-08-08): un solo
   experimento a la vez**, por decisión de método…~~

   ⚠️ **Revisado dos veces el 2026-08-10, y la segunda deshace la primera.** Primero se
   borró `europa-01` —estaba vacío: 0 ciclos, 0 decisiones, 0 posiciones— y se crearon cinco
   perfiles europeos para correrlos a la vez. **Horas después se decidió lo contrario: un
   solo experimento activo a la vez**, y los cinco quedan **guardados en pausa** para
   ejecutarlos uno detrás de otro. El activo hoy es `eu-05-muy-agresivo`.

   **El motivo es el que hace de bisagra con la decisión nº 2:** un solo perfil es lo que
   deja sitio a los **ocho ciclos horarios**. Cinco perfiles × 8 ciclos × ~20 minutos no
   caben en una ventana de 510; uno solo ocupa 160. Se cambia **amplitud por profundidad**:
   en vez de cinco niveles de riesgo sobre los mismos días, un nivel con ocho comprobaciones
   de stop y objetivo al día, que es lo que acumula operaciones cerradas deprisa.

   ⚠️ **Corregido el 2026-08-10, y la corrección es del objetivo, no del método.** Aquí se
   escribió que ejecutarlos en serie era «la cara mala del cambio» porque los cinco no
   quedarían comparables sobre los mismos datos. **Eso daba por supuesto un objetivo que no
   es el de este experimento.** Dicho por quien lo dirige: lo que se quiere observar es **el
   comportamiento de cada agente** —qué compra, qué mantiene y qué vende en cada ciclo— y
   **el resultado final de cada uno**, no aislar el efecto del riesgo con todo lo demás
   igual. Con ese objetivo, correrlos en serie no pierde nada: cada perfil es su propia
   tanda y se juzga por su propio registro.

   Lo que sí conviene tener presente es de qué **no** sirve entonces el comparador: pintará
   las cinco curvas juntas sin saber que arrancaron en fechas distintas, así que **la
   diferencia entre dos curvas no es atribuible al perfil de riesgo**. Sirve para ver el
   resultado final de cada uno al lado del de los demás, que es justo para lo que se quiere.

   Los cinco, que **se diferencian solo en el perfil de riesgo**, de 1 a 10:

   | Perfil | Riesgo | Riesgo/op. | Máx. posición | Exposición | Convicción mín. | Stop | R/R | Kill switch |
   |---|---|---|---|---|---|---|---|---|
   | `eu-01-muy-conservador` | 1/10 | 0,25 % | 5 % | 30 % | 85 | 3,0× ATR | 2,5 | −2 % |
   | `eu-02-conservador` | 3/10 | 0,63 % | 12,5 % | 50 % | 75 | 2,5× ATR | 2,0 | −3,5 % |
   | `eu-03-equilibrado` | 5/10 | 1,00 % | 20 % | 70 % | 65 | 2,0× ATR | 1,5 | −5 % |
   | `eu-04-agresivo` | 8/10 | 2,20 % | 32 % | 88 % | 53 | 1,52× ATR | 1,2 | −8 % |
   | `eu-05-muy-agresivo` | 10/10 | 3,00 % | 40 % | 100 % | 45 | 1,2× ATR | 1,0 | −10 % |

   **Todo lo demás es idéntico** —bolsa `eu`, universo de 89, 10.000 € de capital, 89
   símbolos en vivo, `nvidia/meta/llama-3.3-70b-instruct`, screener `score`, barras `1h`,
   **diversificación 5/10** y los **ocho ciclos de 10:20 a 17:20 Europe/Madrid**—. Que la
   cadencia sea también idéntica en los cinco es parte del mismo argumento: si el que se
   corre después llevara un ciclo diario, la brecha entre las dos curvas sería del nivel de
   riesgo **y** del número de veces que se miran los stops. Que la diversificación
   también sea fija es lo que hace atribuible una diferencia: moviendo las dos, una brecha
   entre dos curvas no sería ni del riesgo ni del reparto.

   **Los niveles son las tres anclas de la tabla de F6.5 (1, 5, 10) más dos intermedios.**
   La interpolación es por tramos y con distinta pendiente a cada lado del 5, así que 3 y 8
   son los que reparten el recorrido sin inventar puntos.

   **Lo que esto cambia de R2, R4 y R8, y lo que no:**
   - **R2 y R4 no empeoran, ni con cinco activos ni con uno.** El ingestor sigue la
     **unión** de los universos de los perfiles **activos**, y los cinco comparten el mismo:
     son **89 símbolos por minuto**, no 445. Verificado con los cinco activos y con uno:
     `symbols_tracked: 89` en los dos casos. ⚠️ Y de ahí sale una trampa: **pausarlos todos
     deja el ingestor sin nada que seguir** y `bars_1m` deja de crecer, que es histórico que
     Yahoo no devuelve después (solo sirve ~30 días de intervalo de 1 minuto). Con uno
     activo basta para seguir acumulándolo.
   - **R8 aguanta de sobra con un perfil.** Hasta 33 llamadas por ciclo × 8 ciclos = ~264 al
     día, secuenciales: 1–2 por minuto contra las 40 rpm de NIM, y sin tope acumulado.

   ⚠️ **Riesgo de método que hay que vigilar:** `eu-01` exige **convicción 85**, que es un
   listón alto. Es posible que no abra ni una posición en diez sesiones. Sería un resultado
   válido —«el modelo casi nunca declara 85»— pero no informativo sobre el riesgo, así que
   conviene mirarlo pronto y no al final.
4. ~~¿Se conserva el broker simulado, o se pasa a Alpaca paper?~~ **Resuelto: solo
   simulador.** Alpaca fuera del proyecto.
