# financial-bot

Agente experimental de trading. Un LLM gratuito de NVIDIA NIM analiza activos y
propone operaciones; un motor de riesgo determinista decide si esas propuestas se
convierten en órdenes y de qué tamaño; un broker simulado las ejecuta. Todo queda
registrado en SQLite para poder auditar después si el razonamiento del modelo
tenía algún valor.

**Arranca con una sola clave de API** (la del modelo). Sin cuenta de broker, sin
MFA, sin datos personales.

> **Qué esperar, sin adornos.** Un LLM no tiene ventaja informativa sobre el
> mercado y sus datos de entrenamiento tienen fecha de corte. Como forma de ganar
> dinero, la expectativa realista está entre cero y negativa. Como experimento
> medible sobre el razonamiento de un modelo —¿la convicción que declara predice
> algo?— es genuinamente interesante, y eso es lo que este proyecto mide.

---

## 1. La idea central

El error habitual al montar esto es dejar que el LLM ejecute órdenes. Aquí no
puede:

```
Barras diarias (Yahoo Finance)
        │
        ▼
  Indicadores  ──────────►  Analista (LLM)
  RSI, ATR, SMA,            propone {acción, convicción, tesis, stop, objetivo}
  MACD, volatilidad…                    │
                                        ▼
                          ┌─────────────────────────────┐
                          │  RISK MANAGER               │  ← cero IA, solo aritmética
                          │  · tamaño = f(equity, ATR)  │
                          │  · stop obligatorio por ATR │
                          │  · máx. % por posición      │
                          │  · máx. exposición total    │
                          │  · máx. posiciones abiertas │
                          │  · kill switch de pérdida   │
                          │  · ratio beneficio/riesgo   │
                          └─────────────────────────────┘
                                        │
                              aprobada  │  rechazada
                                        ▼         └──► se registra el motivo
                             Broker simulado
                                        │
                                        ▼
                                   SQLite local
```

**El modelo aporta dirección y convicción. El dinero lo decide el código.**
El LLM nunca elige cuántas acciones comprar; eso sale de la volatilidad del
activo y de los límites del perfil de experimento.

Dos defensas concretas, ambas con test:

**El modelo puede ampliar el stop pero nunca estrecharlo.** Un stop pegado al
precio permitiría justificar una posición gigantesca con el mismo presupuesto de
riesgo — es el vector de ataque obvio, y está cerrado
([`src/risk.py`](src/risk.py), `test_model_cannot_tighten_the_stop_to_inflate_the_position`).

**Se decide con el cierre de ayer y se ejecuta con la apertura de hoy.** Ejecutar
al mismo precio con el que se decide regala el hueco de la noche y convierte
cualquier resultado en basura; es el error clásico que invalida los backtests.
Los tres precios viven separados en [`MarketSnapshot`](src/models.py) y la
propiedad se comprueba en `test_decision_and_fill_prices_are_different` y
`test_a_crash_in_the_execution_bar_is_not_visible_yet`.

Consecuencia de esto último que conviene entender: **el agente reacciona con un
ciclo de retardo.** Un desplome que solo aparece en la barra de ejecución todavía
no lo ha visto; el stop salta en el ciclo siguiente. Es el precio de no hacer
trampas.

### El embudo: 500 activos, 20 análisis

A 45 segundos por llamada al modelo, analizar el S&P 500 completo costaría **seis
horas por ciclo**. El cuello de botella es el LLM, no Yahoo. La solución es un
embudo de dos etapas:

```
503 activos  →  Caché de barras  →  Screener (Python, cero IA)  →  20  →  LLM
                 SQLite, refresco     tendencia, momento, volumen,
                 incremental          liquidez, RSI no extremo
```

Medido de verdad con los 503 componentes del índice:

| | Peticiones a Yahoo | Tiempo | Tamaño |
|---|---|---|---|
| Primer arranque | 9 (lotes de 60) | 4,5 min | 103.000 barras, 18 MB |
| Ciclos siguientes | 9, solo barras nuevas | 90 s | +0 filas |

Los 90 segundos son ruido al lado de los ~15 minutos que tarda el modelo en
analizar veinte candidatos. La caché es idempotente, así que reescribe la última
barra en cada ciclo mientras el mercado está abierto sin acumular duplicados.

**Sobre la puntuación del screener, honestamente:** los pesos son una heurística
razonable, no una ventaja demostrada. Si el experimento acaba mostrando algo, será
imposible saber cuánto viene del filtro y cuánto del modelo. Por eso existe
`screener_mode=random`, que selecciona candidatos arbitrarios respetando solo los
descartes duros: es el grupo de control. Si el agente rinde igual con `random`, el
filtro no aporta nada.

Los descartes duros sí son defendibles. El de liquidez sobre todo: en un valor
ilíquido el simulador mentiría, porque supone que compras al precio de apertura sin
mover el mercado.

### Barras diarias u horarias

`BAR_INTERVAL=1h` permite 2-3 ciclos por sesión. La razón para hacerlo **no es que
el intradía sea más rentable** — es donde un LLM está más en desventaja. La razón
es estadística: con un ciclo diario y una o dos entradas aprobadas necesitas meses
para acumular 30 operaciones cerradas; con barras horarias llegas en semanas, y sin
30 operaciones no se puede decir nada sobre la calibración.

Lo que cambia al activarlo:

- `SMA200` pasa a ser 200 **horas** (~28 sesiones), no 200 días. El prompt se
  adapta y le dice al modelo que los datos son horarios, porque llamarlas
  "sesiones" le haría razonar sobre un horizonte equivocado.
- Yahoo solo sirve ~700 días de histórico intradía.
- **El cribado sigue usando barras diarias.** Solo los 20 seleccionados se bajan en
  horario. Sin esa separación, bajar 503 activos en horario serían ~2,5 millones de
  barras y varios cientos de MB; así son 28.600 barras en una única petición.

Un aviso sobre los datos: el endpoint gratuito de Yahoo sirve precios intradía con
unos 15 minutos de retraso en muchos símbolos. Para el simulador da igual, porque
lo que importa es la consistencia entre decisión y ejecución, pero te engañarías si
pensaras que los resultados se trasladan tal cual a dinero real.

### Calendario de mercado

`SKIP_WHEN_MARKET_CLOSED=true` hace que un ciclo con el mercado cerrado termine sin
analizar nada. Antes de existir, el agente ejecutaba los fines de semana volviendo
a decidir sobre las barras del viernes: ~26 llamadas al modelo tiradas cada fin de
semana.

El calendario ([`src/market_calendar.py`](src/market_calendar.py)) lleva los
festivos de NYSE en una tabla, con las fechas **observadas** (cuando el 4 de julio
cae en sábado, cierra el viernes 3) y los días de media sesión que cierran a las
13:00 ET. La tabla llega hasta 2027 y avisa por log en lugar de mentir cuando se
pasa de ahí.

### Broker simulado

La ejecución es local: sin cuenta de broker, sin MFA, sin dinero real. El
simulador aplica deslizamiento y comisión configurables, no permite comprar sin
efectivo ni vender lo que no se tiene, y no hay apalancamiento.

Lo que **no** simula: liquidez (una orden grande movería el precio real), huecos
intradía, órdenes parciales, horarios de mercado ni reglas de patrón day trader.
A frecuencia diaria y en valores muy líquidos importa poco; en ilíquidos los
resultados serían optimistas.

`cycle.py` habla con el broker a través del protocolo estrecho de
[`src/broker.py`](src/broker.py), así que el día que se añada un broker real el
ciclo no cambia.

---

## 2. Puesta en marcha con Docker

Es la vía recomendada: el planificador va incluido, así que no hay que tocar el
Programador de tareas de Windows.

### 2.1 Requisito

**Docker Desktop arrancado.** Compruébalo con `docker info`; si da
`failed to connect to the docker API`, abre Docker Desktop y espera a que el
icono deje de moverse.

### 2.2 Ver el dashboard sin credenciales (2 minutos)

```powershell
cd c:\Users\jaume\Desktop\financial-bot
docker compose up -d dashboard
docker compose run --rm bot python tools/seed_demo.py
```

Abre http://127.0.0.1:8000 y elige `demo` en el selector de cartera. No hace
falta `.env` ni ninguna clave: el dashboard solo lee la base de datos.

### 2.3 Ponerlo a operar

**1. Crea el `.env` y pon la única clave necesaria:**

```powershell
copy .env.example .env
notepad .env
```

Solo `NVIDIA_API_KEY`. La consigues en [build.nvidia.com](https://build.nvidia.com)
→ busca `llama-3.3-70b-instruct` → **Get API Key**. Empieza por `nvapi-`. Es la
única clave del proyecto: los datos son de Yahoo y el broker es local.

**2. Crea el perfil de experimento.** Los parámetros del agente viven en la base
de datos, no en el `.env`. Para partir de los valores de la plantilla:

```powershell
docker compose run --rm bot python run.py import-profile --name experimento-01
```

Eso crea el perfil, su cartera y sus parámetros, y lo deja activo. Compruébalo
con `run.py profiles`.

**2. Comprueba que conecta:**

```powershell
docker compose run --rm bot python run.py check
```

Cinco bloques: configuración del perfil, calendario, datos de mercado, broker
simulado, NVIDIA NIM y base de datos. El de datos te muestra los precios de
decisión y de ejecución uno al lado del otro, que es la forma rápida de ver que
la separación funciona.

**3. Ciclo en seco**, que analiza y registra pero no ejecuta:

```powershell
docker compose run --rm bot python run.py cycle --dry-run
```

Revisa el resultado en el dashboard, pestaña *Decisiones del modelo*.

**4. Arranca todo**, dashboard y planificador:

```powershell
docker compose up -d
docker compose logs -f scheduler
```

### 2.4 Comandos del día a día

| Qué quieres | Comando |
|---|---|
| Arrancar todo | `docker compose up -d` |
| Ver los ciclos programados | `docker compose logs -f scheduler` |
| Lanzar un ciclo ya, sin esperar | `docker compose run --rm bot python run.py cycle` |
| Analítica en consola | `docker compose run --rm bot python run.py report` |
| Estado de la cuenta | `docker compose run --rm bot python run.py status` |
| Ejecutar los tests | `docker compose run --rm bot python -m pytest tests -q` |
| Parar todo | `docker compose down` |
| Aplicar cambios de código | `docker compose up -d --build` |
| Solo el dashboard | `docker compose up -d dashboard` |
| Parar el planificador | `docker compose stop scheduler` |

### 2.5 Cambiar la cadencia

Por defecto un ciclo diario a las **22:15 hora peninsular**, poco después del
cierre de Nueva York, cuando las barras diarias ya están completas. Para
cambiarlo, añade al `.env`:

```
CYCLE_TIMES=15:35,22:15      # dos ciclos: apertura y cierre de NY
CYCLE_TZ=Europe/Madrid
RUN_ON_START=false           # true = un ciclo al arrancar el contenedor
```

Y recarga: `docker compose up -d`.

### 2.6 Detalles que conviene saber

**La base de datos vive en un volumen de Docker**, no en `./data`. Es
deliberado: SQLite necesita bloqueo de fichero fiable y los *bind mounts* de
Docker Desktop en Windows no lo garantizan. Para sacar una copia:

```powershell
docker compose cp dashboard:/app/data/trading.db ./data/trading.db
```

Y para inspeccionarla dentro del contenedor:

```powershell
docker compose run --rm bot python run.py report
```

**El dashboard se publica solo en `127.0.0.1:8000`.** No tiene autenticación y
son datos de una cuenta de inversión. Si de verdad quieres abrirlo al resto de
tu red, cambia el mapeo a `"8000:8000"` en `docker-compose.yml` sabiendo lo que
implica.

**Si falta el `.env` o no hay perfil activo, el servicio `scheduler` se para**
con un mensaje explicando qué falta, en lugar de entrar en bucle de reinicios. El
perfil se resuelve al arrancar el planificador, no al lanzar el ciclo: descubrir
que no hay ninguno activo a las 22:15, tras ocho horas dormido, es la peor hora
posible para enterarse. El `dashboard` sigue funcionando.

---

## 3. Puesta en marcha sin Docker

### 3.1 Dependencias

```powershell
cd c:\Users\jaume\Desktop\financial-bot
python -m pip install -r requirements.txt
```

`yfinance` (que arrastra pandas), `httpx`, `python-dotenv`, `tzdata` y
`pytest`. La base de datos es SQLite de la biblioteca estándar y el dashboard no
usa ninguna librería de gráficos.


### 3.2 Ver el dashboard sin credenciales

Puedes probar toda la interfaz antes de dar de alta nada, con datos sintéticos:

```powershell
python tools/seed_demo.py          # crea la cartera 'demo' con 40 ciclos
python run.py serve                # http://127.0.0.1:8000
```

En el dashboard, elige `demo` en el selector de cartera. Para regenerarla:
`python tools/seed_demo.py --reset`.

### 3.3 Credenciales

Copia la plantilla y rellena **una** línea:

```powershell
copy .env.example .env
notepad .env
```

**NVIDIA NIM** es lo único obligatorio — [build.nvidia.com](https://build.nvidia.com)
→ elige un modelo → *Get API Key*. Da créditos gratuitos y solo pide un email.

```
NVIDIA_API_KEY=nvapi-...
LLM_MODEL=meta/llama-3.3-70b-instruct
```

Modelos que funcionan bien con este prompt:

| Modelo | Comentario |
|---|---|
| `meta/llama-3.3-70b-instruct` | Equilibrado, rápido, buen JSON. **Empieza por aquí.** |
| `qwen/qwen2.5-72b-instruct` | Alternativa muy sólida. |
| `deepseek-ai/deepseek-r1` | Razona mejor, pero lento y emite `<think>` (ya se filtra). |
| `nvidia/llama-3.3-nemotron-super-49b-v1` | Más ligero, algo peor calibrado. |

No hace falta configurar nada de base de datos: se crea sola en `data/trading.db`.

### 3.4 Comprobar que todo conecta

```powershell
python run.py check
```

Verifica cada pieza por separado (perfil, calendario, datos de mercado, broker
simulado, NVIDIA NIM, SQLite) y dice exactamente cuál falla. No opera.

### 3.5 Primer ciclo en seco

```powershell
python run.py cycle --dry-run
```

Analiza toda la watchlist, registra las decisiones del modelo y los veredictos
de riesgo en la base de datos, pero **no envía ninguna orden**. Es la forma de
ver qué haría el agente antes de dejarle operar.

### 3.6 Operar de verdad (en paper)

```powershell
python run.py cycle
```

Con el mercado cerrado analiza y registra igualmente, pero no ejecuta: las
órdenes quedan como `canceled` con el motivo. Esto es útil — puedes lanzarlo por
la noche y leer el análisis por la mañana.

---

## 4. Comandos

| Comando | Qué hace | Necesita credenciales |
|---|---|---|
| `python run.py check` | Diagnóstico de las cuatro piezas | solo la del modelo |
| `python run.py cycle` | Un ciclo completo de análisis y operativa | solo la del modelo |
| `python run.py cycle --dry-run` | Igual pero sin ejecutar órdenes | solo la del modelo |
| `python run.py status` | Estado de la cuenta y posiciones vivas | no |
| `python run.py report` | Analítica del histórico en consola | **no** |
| `python run.py serve` | Dashboard web | **no** |

`report` y `serve` solo leen la base de datos, y la abren en modo solo lectura.
Por eso no piden claves: puedes revisar la operativa desde cualquier sitio sin
exponer credenciales, y un `.env` a medio rellenar no te impide ver los datos.

---

## 5. El dashboard

```powershell
python run.py serve                  # http://127.0.0.1:8000
python run.py serve --port 8080      # otro puerto
```

Escucha **solo en 127.0.0.1**: son datos de una cuenta de inversión y no tienen
por qué estar accesibles desde la red local. Si de verdad quieres abrirlo al
resto de tu red, `--host 0.0.0.0`, sabiendo que no hay autenticación.

Cuatro pestañas:

**Resumen** — Equity, P&L realizado y abierto, acierto, profit factor y caída
máxima. Debajo, cinco gráficos:

- *Curva de capital*: un punto por ciclo, con la referencia del capital inicial.
- *Calibración de la convicción* — **el gráfico que decide el experimento**. Es
  el acierto real agrupado por la convicción que el modelo declaró al entrar. Si
  las barras no suben de izquierda a derecha, la convicción del modelo no
  informa de nada y estás operando con ruido caro.
- *P&L realizado por activo*: escala divergente con el signo en la etiqueta.
- *Rechazos del Risk Manager*: contra qué límite choca el modelo.
- *Convicción declarada*: si se concentra en un solo tramo, el modelo no
  discrimina entre oportunidades.

**Decisiones del modelo** — Cada llamada al LLM junto al veredicto de riesgo que
recibió. Filtrable por acción y activo; pulsa una fila para ver la tesis
completa. Es la tabla que da sentido a todo el ejercicio.

**Posiciones** — Abiertas (con margen hasta el stop) y cerradas. El precio
mostrado es el último que registró el bot, no una cotización en vivo, y así se
etiqueta.

**Ciclos y órdenes** — Cada ejecución y cada intento de orden, incluidos los
fallidos y los no enviados.

### Lanzar un ciclo desde el dashboard

El botón **Lanzar ciclo** de la cabecera arranca un ciclo sin tocar la terminal, y
**Simular** hace lo mismo con `--dry-run`. Aparece un panel con la etapa actual, el
tiempo transcurrido y el log en vivo; puedes cerrar la pestaña, porque el proceso
corre en el servidor, no en el navegador.

El dashboard sigue abriendo SQLite en **solo lectura**: el botón no escribe nada
por su cuenta, arranca `run.py cycle` como proceso aparte con su propia conexión.

Dos protecciones:

- **Un solo ciclo por cartera a la vez.** Si el planificador ya tiene uno en
  marcha, el botón lo rechaza con un mensaje en lugar de arrancar un segundo. Dos
  ciclos en paralelo se pisan el efectivo y las posiciones.
- **`DASHBOARD_CONTROLS=false`** quita los botones. Ponlo si publicas el dashboard
  fuera de localhost: no hay autenticación, así que quien alcance el puerto podría
  gastar tu cuota y mover la cartera. Con el mapeo por defecto
  (`127.0.0.1:8000`) no hace falta.

Cada gráfico tiene un botón *Tabla* que muestra los mismos datos en texto, y hay
tema claro y oscuro. La paleta está validada para daltonismo: la pareja
verde/rojo habitual en finanzas falla la separación para deuteranopía, así que
los gráficos usan azul/rojo y el signo `+`/`−` acompaña siempre al valor.

---

## 6. Cada cuánto se ejecuta y cuántas llamadas hace

**El agente no corre en bucle.** Cada `python run.py cycle` es una pasada
completa y termina. Lo programas tú.

### Llamadas al LLM por ciclo

Una por activo analizado, más una por posición abierta:

```
llamadas = activos con datos suficientes  +  posiciones abiertas
```

Con la watchlist por defecto (10 activos) y 3 posiciones abiertas: **~13
llamadas**. Cada una consume del orden de 1.500 tokens de entrada y 300 de
salida.

### Llamadas externas por ciclo

Al **modelo**: una por activo analizado más una por posición abierta.

A **Yahoo**: una sola petición para toda la watchlist, no una por activo.

Al **broker**: ninguna. La ejecución y la contabilidad son locales, así que no
hay red ni cuotas por ese lado.

### Cadencia recomendada

| Cadencia | Llamadas/día | Comentario |
|---|---|---|
| 1×/día, tras el cierre | ~13 | **Recomendado.** Los datos diarios ya están completos y no hay prisa. |
| 2×/día (apertura y cierre) | ~26 | Razonable. |
| Cada hora | ~90 | Innecesario: el agente usa barras diarias, no cambian intradía. |
| Cada minuto | — | No hagas esto. Agotarás la cuota y no hay señal nueva que leer. |

El horizonte del agente es de días o semanas (`horizon_days`), y los indicadores
se calculan sobre barras diarias. Ejecutarlo más de dos veces al día no aporta
información nueva, solo gasta cuota. El nivel gratuito de NIM tiene límites de
peticiones; el cliente ya reintenta con espera exponencial y respeta
`Retry-After`, pero no conviene tentarlo.

### Programarlo en Windows

Un ciclo diario a las 22:15 (poco después del cierre de Nueva York, hora
peninsular):

```powershell
$py  = (Get-Command python).Source
$dir = "c:\Users\jaume\Desktop\financial-bot"
$action  = New-ScheduledTaskAction -Execute $py -Argument "run.py cycle" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 22:15
Register-ScheduledTask -TaskName "financial-bot-ciclo" -Action $action -Trigger $trigger `
  -Description "Ciclo diario del agente de trading"
```

Para comprobar que la tarea funciona: `Start-ScheduledTask -TaskName "financial-bot-ciclo"`
y luego `python run.py report`.

El dashboard (`serve`) sí es un proceso continuo, pero no hace llamadas a nada:
solo lee el fichero SQLite cuando refrescas la página.

---

## 7. Qué hace un ciclo, en orden

1. **Reconciliar con el broker.** El broker es la fuente de verdad de lo que se
   posee; la base de datos, de *por qué* se posee. Si una orden se ejecutó y su
   registro falló, se detecta aquí: las posiciones huérfanas se adoptan y se les
   asigna un stop por ATR, y las que ya no existen se marcan cerradas.
2. **Descargar datos** de toda la watchlist en una sola petición y calcular
   indicadores.
3. **Kill switch.** Si la pérdida del día supera `MAX_DAILY_LOSS_PCT`, no se abre
   ninguna posición nueva por convincente que sea la tesis.
4. **Salidas obligatorias.** Stop o objetivo alcanzado. No se consulta al LLM: un
   stop tocado no se negocia.
5. **Revisión discrecional de salidas.** El LLM revisa cada posición abierta por
   si la tesis se ha degradado antes de llegar al stop. Puede subir el stop,
   nunca bajarlo.
6. **Entradas.** Análisis, filtro de riesgo, ejecución. Van después de las
   salidas a propósito: estas liberan efectivo y huecos de posición que las
   entradas del mismo ciclo pueden aprovechar.
7. **Curva de capital y cierre**, pase lo que pase, para no dejar ciclos
   colgados en estado `running`.

---

## 8. Configuración de riesgo

**Los parámetros viven en la base de datos, uno por perfil de experimento, no en
el `.env`.** Del entorno solo sale la infraestructura: `DB_PATH`,
`NVIDIA_API_KEY`, `LOG_LEVEL`. Cámbialos con `db.update_settings(...)`, o desde
la interfaz cuando exista; cada cambio real queda en `agent_settings_history`,
que es lo que permite explicar después por qué el agente cambió de conducta.

Los nueve límites duros no se editan de uno en uno: **salen de dos deslizadores**
([`src/risk_presets.py`](src/risk_presets.py)).

| `risk_profile` | Riesgo/op. | Máx. posición | Máx. exposición | Convicción mín. | Stop×ATR | R/R mín. | Kill switch |
|---|---|---|---|---|---|---|---|
| 1 muy conservador | 0,25 % | 5 % | 30 % | 85 | 3,0 | 2,5 | −2 % |
| 5 equilibrado | 1,0 % | 20 % | 70 % | 65 | 2,0 | 1,5 | −5 % |
| 10 muy agresivo | 3,0 % | 40 % | 100 % | 45 | 1,2 | 1,0 | −10 % |

Los niveles intermedios se interpolan, así que mover el deslizador un punto
siempre cambia algo. `diversification` (1–10) controla aparte el número máximo de
posiciones: 3 en el nivel 1, 25 en el 10.

Que sean dos deslizadores y no dieciséis casillas es el punto: el experimento
consiste en clonar un perfil, cambiar **un** parámetro y comparar. Para fijar un
límite a mano hay modo avanzado (`advanced_overrides`), campo a campo; apagarlo
devuelve el mando a los deslizadores.

**Proveedor de modelo** (F6.6): `llm_provider` acepta `nvidia` (NVIDIA NIM, por
defecto y gratis) u `openai`. Los dos hablan `/chat/completions`, así que cambiar de
uno a otro es un parámetro del perfil y no añade dependencias. La clave va en
`llm_api_key` del perfil (F6.7) y se muestra enmascarada; con NIM, si la dejas
vacía se usa `NVIDIA_API_KEY` del entorno. Anthropic todavía no está
implementado — su API no es compatible y necesitaría su SDK.

Otros parámetros del perfil: `llm_model`, `llm_temperature`, `initial_budget`,
`bar_interval` (`1h` para varios ciclos por sesión), `screener_top_n`,
`screener_mode` (`random` es el grupo de control), `sim_slippage_bps`,
`sim_commission`, `dry_run` y `skip_when_market_closed`. Los ves todos con:

```powershell
python run.py profiles
```

Hay una interacción de estos valores que conviene entender: un stop de 2×ATR
ronda el 4% del precio en una acción típica, así que arriesgar el 1% del equity
implicaría una posición del **25%**. El tope del 20% recorta antes, y eso es lo
buscado: manda la diversificación sobre el presupuesto de riesgo. Lo verás en el
dashboard como rechazos o recortes con la regla `max_position_pct`.

---

## 9. Consultar los datos a mano

```powershell
sqlite3 data/trading.db
```

Tablas: `portfolios`, `cycles`, `market_snapshots`, `decisions`, `risk_events`,
`orders`, `positions`, `equity_snapshots`. Vistas listas para usar:
`v_performance_by_symbol`, `v_conviction_calibration`, `v_risk_rejections`,
`v_decision_mix`.

La tabla más valiosa es `decisions`: guarda el prompt resuelto, la respuesta
cruda del modelo (incluida su cadena de razonamiento) y la tesis. Es lo que
permite responder, dentro de unos meses, si el modelo aportaba señal o solo
generaba texto convincente.

```sql
-- ¿Sube el acierto con la convicción declarada?
select * from v_conviction_calibration;

-- Las tesis de las operaciones que peor salieron
select p.symbol, p.realized_pnl, d.conviction, d.thesis
from positions p
join orders o    on o.id = p.entry_order_id
join decisions d on d.id = o.decision_id
where p.status = 'closed'
order by p.realized_pnl asc limit 10;
```

---

## 10. Estructura

```
Dockerfile              Imagen unica para los tres servicios
docker-compose.yml      dashboard + scheduler + comandos puntuales
run.py                  CLI: check / cycle / status / report / serve
                        + profiles / import-profile / activate
schema.sql              Esquema SQLite; se aplica solo en cada arranque
src/
  config.py             Infra desde .env (rutas, clave, log)
  profile_settings.py   agent_settings -> Settings del ciclo
  risk_presets.py       Deslizadores 1-10 -> límites del Risk Manager
  models.py             Tipos de dominio compartidos
  indicators.py         RSI, ATR, MACD… funciones puras, sin numpy
  market_data.py        Barras desde Yahoo (yfinance)
  bar_cache.py          Cache de barras con refresco incremental
  screener.py           Filtro determinista: universo -> candidatos
  universe_data.py      El embudo: cache + screener + snapshots
  market_calendar.py    Sesiones, festivos y medias sesiones de NYSE
  sim_broker.py         Broker simulado sobre SQLite
  llm.py                Cliente NVIDIA NIM y parseo defensivo de JSON
  analyst.py            Prompts y validación de la salida del modelo
  risk.py               Risk Manager  ← la pieza crítica
  broker.py             El contrato que el ciclo espera de un broker
  db.py                 Persistencia y reconciliación
  dashboard.py          Ensamblado de datos para report y web
  cycle.py              Orquestación del ciclo
web/
  server.py             Servidor del dashboard (biblioteca estándar)
  index.html            Dashboard autocontenido
tools/
  fetch_universe.py     Descarga la lista de componentes del S&P 500
  scheduler.py          Planificador de ciclos (proceso del contenedor)
  seed_demo.py          Datos sintéticos para probar la interfaz
universe/
  sp500.txt             503 símbolos, en notación de Yahoo
tests/                  284 tests, sin red ni credenciales
  helpers.py            Dobles del LLM y de los datos, compartidos
                        (incluye un ciclo completo de integración)
```

```powershell
python -m pytest tests -q
```

Los tests no tocan la red: el Risk Manager, los indicadores, el parseo de JSON
del LLM y la capa de datos son deterministas y se prueban en aislamiento.

---

## 11. Dinero real: hoy no se puede

**No hay integración con ningún broker real, y es deliberado.** La única
implementación es el simulador de [`src/sim_broker.py`](src/sim_broker.py), que
lleva la contabilidad en SQLite. El proyecto es un experimento para medir si el
criterio de un LLM aporta señal; hasta que esa pregunta tenga respuesta, poder
enviar órdenes reales solo añade formas de perder dinero.

La fontanería para añadirlo está hecha: `cycle.py` solo conoce el protocolo
`Broker` de [`src/broker.py`](src/broker.py), así que una implementación nueva no
toca el ciclo. Lo que haría falta antes es tener con qué juzgar: calibración de
la convicción, profit factor y caída máxima sobre suficientes operaciones
cerradas — no el P&L de una semana.
