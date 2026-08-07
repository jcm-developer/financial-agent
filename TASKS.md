# TASKS.md — Plan de trabajo

Registro de todo lo pendiente. Cada tarea tiene un id (`F1.2`) para referenciarla en
commits y conversaciones. Marcar `[x]` al cerrarla.

Última actualización: 2026-08-08

---

## 0. Punto de partida y destino

**Hoy:**

- Agente en Python: [src/cycle.py](src/cycle.py) orquesta screener → datos → LLM
  ([src/analyst.py](src/analyst.py)) → risk manager ([src/risk.py](src/risk.py)) → broker
  (simulado en [src/sim_broker.py](src/sim_broker.py) o Alpaca).
- Persistencia en SQLite ([src/db.py](src/db.py) + [schema.sql](schema.sql)).
- Configuración por variables de entorno ([src/config.py](src/config.py)) — un solo
  experimento por `.env`.
- Dashboard: HTML de 1.500 líneas ([web/index.html](web/index.html)) servido por
  `http.server` ([web/server.py](web/server.py)).
- Datos de mercado con `yfinance`, barras 1d/1h, caché en `bar_cache`.
- Docker ya montado ([docker-compose.yml](docker-compose.yml)): `dashboard`, `scheduler`, `bot`.

**Destino:** todo local en Docker, SQLite como base, precios refrescados cada minuto en
horario de bolsa US, frontend React + Tailwind, perfiles de experimento con parámetros
editables, y NVIDIA NIM (capa gratuita) como proveedor de modelo.

**Coste: 0 €.** Sin nube, sin servicios de pago.

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

### D4 — SQLite con un escritor cada minuto ✅

Ya está en WAL con `busy_timeout = 30000` ([src/db.py](src/db.py)), así que un escritor y
varios lectores conviven. Lo nuevo es que ahora hay **dos escritores**: el ingestor (cada
minuto, ~50 upserts, milisegundos) y el ciclo del agente. Se pisarán de vez en cuando y
esperarán; a este volumen no es problema, pero conviene medirlo (F2.9).

Volumen: 50 símbolos × 390 barras/día ≈ 19.500 filas/día, ~410.000 al mes, del orden de
50 MB mensuales. SQLite lo lleva sin despeinarse, pero sin retención crece para siempre
(F1.9).

**La base sigue en un volumen con nombre, no en un bind mount.** El comentario de
[docker-compose.yml](docker-compose.yml) explica por qué: el bloqueo de ficheros de SQLite
no es fiable sobre bind mounts de Docker Desktop en Windows. Con un escritor por minuto eso
importa más, no menos.

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
      en verde: 307 pasan.**

### F2 — Ingesta de precios cada minuto

- [x] **F2.1a** Spike escrito: [tools/spike_1m.py](tools/spike_1m.py). Mide retraso del dato,
      latencia, 429s y símbolos vacíos; escribe JSONL línea a línea para que un corte no se
      lleve lo medido.
- [x] **F2.1b** Validado el mecanismo contra la sesión del 2026-08-07 (mercado cerrado):
      50/50 símbolos, sin errores, marcas de tiempo correctas. Resultado lateral: la descarga
      **no es en lote** — ver la corrección en D3.
- [ ] **F2.1c** ⚠️ **Pendiente y bloqueante: medir en sesión real.** El lunes 2026-08-10 desde
      las 15:30 UTC (9:30 ET), una sesión entera:
      ```
      python tools/spike_1m.py --minutes 390 --out spike_lunes.jsonl
      ```
      La pregunta que hay que responder es **el retraso real del dato**: "cada minuto" solo
      vale si el dato es de hace un minuto. Si Yahoo sirve el feed con 15 minutos de desfase,
      el ingestor se construye igual pero cambia lo que se puede concluir del experimento.
      Medir también `--threads` para cerrar la duda de D3.
- [ ] **F2.2** `tools/ingestor.py`: bucle que despierta al inicio de cada minuto.
- [ ] **F2.3** Filtro de calendario con [src/market_calendar.py](src/market_calendar.py), que
      **ya existe y tiene tests**: festivos NYSE, medias sesiones, 9:30–16:00 ET, DST. Con el
      mercado cerrado, duerme hasta la próxima apertura sin pedir nada.
- [ ] **F2.4** Resolver los símbolos a seguir: unión de los universos de los perfiles activos
      (`profile_universe`) más las posiciones abiertas. Se relee cada N minutos por si se
      activa un perfil nuevo.
- [ ] **F2.5** Descarga en lote con `yf.download(..., interval="1m")`, con reintentos y
      backoff exponencial ante 429.
- [ ] **F2.6** Escritura: `insert or replace` en `quotes_live` (una fila por símbolo) y en
      `bars_1m` por `(symbol, ts)` — idempotente, que es lo que importa porque la barra del
      minuto en curso cambia mientras se consulta.
- [ ] **F2.7** Registrar cada tick en `ingest_runs`, para poder ver en la UI si la ingesta
      está sana.
- [ ] **F2.8** Aviso cuando fallan K minutos seguidos, en el log y en la UI.
- [ ] **F2.9** **Medir la contención de escritura** entre ingestor y ciclo: registrar esperas
      por `busy_timeout` y confirmar que no se acumulan (ver D4).
- [ ] **F2.10** Consolidación al cierre: completar huecos del día y volcar a `bars_1d`.
- [ ] **F2.11** Tests: calendario, idempotencia del upsert, comportamiento ante 429 y ante
      símbolo desconocido.

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

- [ ] **F5.1** Tabla `profiles`: `id`, `name`, `description`, `status`
      (`draft|active|paused|archived`), `created_at`, `archived_at`.
- [ ] **F5.2** Listado en tarjetas con las métricas clave: capital, P&L total y del día, nº
      de posiciones, win rate, último ciclo.
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

- [ ] **F6.1** Tabla `agent_settings` (1:1 con `profiles`): columnas tipadas para lo que se
      consulta, `extra_json` para el resto.
- [ ] **F6.2** **Historial de cambios** (`agent_settings_history`): qué cambió y cuándo. Sin
      esto, un experimento cuyos parámetros se editan a mitad no es interpretable — y se pide
      que sean editables en cualquier momento.
- [ ] **F6.3** Cada `cycle` guarda un **snapshot de los parámetros usados**, para atribuir
      cada decisión a su configuración exacta.
- [ ] **F6.4** El ciclo lee de aquí, no del `.env`. [src/config.py](src/config.py) queda solo
      para infraestructura (rutas, claves, log level).
- [ ] **F6.5** Función determinista `risk_profile (1–10) + diversification (1–10)` → límites
      del risk manager, con **modo avanzado** para sobreescribir cada uno a mano. Propuesta:
      | Perfil | `risk_per_trade` | `max_position` | `max_exposure` | `min_conviction` | `stop_atr` | `min_rr` |
      |---|---|---|---|---|---|---|
      | 1 muy conservador | 0,25 % | 5 % | 30 % | 85 | 3,0 | 2,5 |
      | 5 equilibrado | 1,0 % | 20 % | 70 % | 65 | 2,0 | 1,5 |
      | 10 muy agresivo | 3,0 % | 40 % | 100 % | 45 | 1,2 | 1,0 |

      Diversificación 1 → máx. 3 posiciones, concentración permitida; 10 → máx. 25 posiciones
      y tope por sector.
- [ ] **F6.6** Cliente LLM multi-proveedor en [src/llm.py](src/llm.py): NVIDIA NIM (por
      defecto), Anthropic y OpenAI tras la misma interfaz. Hoy solo se usa NVIDIA; el día que
      convenga probar un modelo premium es cambiar un parámetro del perfil.
- [ ] **F6.7** Clave de API por perfil, guardada en la base y **enmascarada en la UI**
      (`nvapi-…abcd`). Local y sin autenticación: no es un secreto fuerte, pero al menos no
      se muestra en pantalla.
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
- Broker: simulado / Alpaca paper / Alpaca live
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
- [ ] **F7.3** Servicio `ingestor` en [docker-compose.yml](docker-compose.yml), con
      `restart: unless-stopped` y `stop_grace_period` corto.
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

- [ ] **F8.1** Borrar `data/trading.db` y el volumen de Docker.
- [ ] **F8.2** Borrar `web/index.html` y `web/server.py` cuando F3 y F4 estén verdes.
- [ ] **F8.3** Podar [.env.example](.env.example): solo infraestructura. Las ~35 variables de
      estrategia pasan a `agent_settings`.
- [ ] **F8.4** `.gitignore`: añadir `node_modules/`, `app/dist/`, `.vite/`.
- [ ] **F8.5** ⚠️ **Hay un `.env` con claves reales en el directorio.** Está en `.gitignore`,
      pero conviene confirmar que nunca llegó a subirse.
- [ ] **F8.6** Suite de tests entera en verde: `docker compose run --rm bot python -m pytest tests -q`.

### F9 — Futuro (no bloquea)

- [ ] **F9.1** Modelo premium (Claude, GPT) cuando el experimento dé señales. La fontanería la
      deja lista F6.6; solo hay que meter la clave y elegir el modelo.
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
                             ├─→ F6 (parámetros) ┼─→ F3 (API) ─→ F4 (React) ─→ F7 → F8
                             └─→ F5 (perfiles) ──┘
```

El spike va primero: es lo único que puede invalidar una decisión ya tomada. F1 bloquea todo
lo demás. F3 y F4 pueden solaparse en cuanto los endpoints estén definidos.

---

## 4. Riesgos y puntos a vigilar

- **R1 — Latencia real del dato.** "Cada minuto" solo vale si el dato es de hace un minuto.
  Hay que **medirlo** (F2.1), no asumirlo. Si Yahoo trae 15 minutos de retraso en 1m, el
  diseño no cambia pero la interpretación del experimento sí.
- **R2 — Yahoo puede limitar por IP.** Es una API no oficial, y el spike desmontó la
  mitigación que yo daba por buena: **son ~50 peticiones por minuto, no 1** (ver D3). En una
  sesión son ~19.500 peticiones al día desde la misma IP doméstica. No apareció ningún 429 en
  las pruebas, pero fueron pasadas sueltas con el mercado cerrado; el riesgo real solo se ve
  sosteniendo el ritmo una sesión entera (F2.1). Palancas si aparece: bajar el número de
  símbolos, espaciar las peticiones dentro del minuto en vez de lanzarlas de golpe, o pasar al
  plan B (Alpaca IEX, gratis y ya integrado).
- **R3 — Contención de escritura en SQLite.** Dos escritores (ingestor y ciclo) sobre el
  mismo fichero. WAL y `busy_timeout` ya lo cubren a este volumen, pero hay que medirlo
  (F2.9) antes de dar por hecho que escala a más perfiles.
- **R4 — Crecimiento del fichero.** ~50 MB al mes sin retención. Cómodo durante un año, no
  para siempre. Lo resuelve F1.9.
- **R5 — La API pasa a poder escribir.** Se pierde la garantía de solo lectura del dashboard
  actual. Acotado a las tablas de configuración y verificado con un test (F3.3).
- **R6 — Parámetros editables en caliente.** Cambiar el perfil de riesgo a mitad de un
  experimento invalida la comparación si no queda registrado. Lo resuelven F6.2 y F6.3.
- **R7 — Calidad del modelo gratuito.** Llama 3.3 70B puede no dar señal útil, y entonces el
  experimento mide el modelo, no la estrategia. Por eso F5.7 (perfil de control aleatorio):
  sin algo contra lo que comparar, no se sabe distinguir un caso del otro.

---

## 5. Decisiones pendientes

1. **Librería de gráficas**: Recharts (rápido de montar) o visx (más control).
2. **Frecuencia de los ciclos del agente**: ¿se mantiene 1 al día tras el cierre, o se
   aprovechan los datos de 1 minuto para varios ciclos intradía? Afecta al gasto de modelo.
3. **Tamaño del universo a seguir minuto a minuto**: 50 es el punto de partida; condiciona R2
   y R4.
4. ¿Se conserva el broker simulado como opción por defecto, o se pasa a Alpaca paper?
