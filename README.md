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
                          Broker simulado  (o Alpaca)
                                        │
                                        ▼
                                   SQLite local
```

**El modelo aporta dirección y convicción. El dinero lo decide el código.**
El LLM nunca elige cuántas acciones comprar; eso sale de la volatilidad del
activo y de los límites que tú fijas en `.env`.

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
`SCREENER_MODE=random`, que selecciona candidatos arbitrarios respetando solo los
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

### Broker simulado vs. Alpaca

Por defecto (`BROKER=sim`) la ejecución es local: sin cuenta, sin MFA. El
simulador aplica deslizamiento y comisión configurables, no permite comprar sin
efectivo ni vender lo que no se tiene, y no hay apalancamiento.

Lo que **no** simula: liquidez (una orden grande movería el precio real), huecos
intradía, órdenes parciales, horarios de mercado ni reglas de patrón day trader.
A frecuencia diaria y en valores muy líquidos importa poco; en ilíquidos los
resultados serían optimistas.

Alpaca (`BROKER=alpaca`) solo hace falta el día que quieras dinero real. Como
`cycle.py` habla con el broker a través de una interfaz estrecha, el cambio es
una variable de entorno y el código que se ejecuta es el mismo.

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
→ busca `llama-3.3-70b-instruct` → **Get API Key**. Empieza por `nvapi-`.
Los valores por defecto (`BROKER=sim`, `DATA_PROVIDER=yahoo`) no piden nada más.

**2. Comprueba que conecta:**

```powershell
docker compose run --rm bot python run.py check
```

Cuatro bloques: datos de mercado, broker simulado, NVIDIA NIM y base de datos. El
de datos te muestra los precios de decisión y de ejecución uno al lado del otro,
que es la forma rápida de ver que la separación funciona.

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

**El planificador no pide confirmación en modo real.** La confirmación
interactiva (`CONFIRMO`) solo existe al ejecutar `run.py cycle` a mano; un
contenedor no tiene con quién hablar. Si pones `ALPACA_PAPER=false`, el
planificador operará con dinero real sin preguntar — lo avisa en el log al
arrancar.

**Si falta el `.env`, el servicio `scheduler` se para** con un mensaje
explicando qué falta, en lugar de entrar en bucle de reinicios. El `dashboard`
sigue funcionando.

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

Solo si vas a usar Alpaca: `pip install -r requirements-alpaca.txt`.

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

**Alpaca** solo si pones `BROKER=alpaca`: [app.alpaca.markets](https://app.alpaca.markets)
→ arriba a la izquierda cambia a **Paper Trading** → sidebar *API* →
*Generate New Key*. Exige activar antes la verificación en dos pasos con una app
autenticadora.

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

Verifica las cuatro piezas por separado (Alpaca, datos de mercado, NVIDIA NIM,
SQLite) y dice exactamente cuál falla. No opera.

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

Al **broker**: ninguna. Con `BROKER=sim` la ejecución y la contabilidad son
locales, así que no hay red ni cuotas por ese lado.

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

Los valores por defecto de `.env.example` son conservadores. Los importantes:

| Variable | Def. | Qué controla |
|---|---|---|
| `RISK_PER_TRADE_PCT` | 1.0 | % del equity que se arriesga hasta el stop en cada operación |
| `MAX_POSITION_PCT` | 20.0 | % máximo del equity en una sola posición |
| `MAX_TOTAL_EXPOSURE_PCT` | 80.0 | % máximo invertido en total |
| `MAX_OPEN_POSITIONS` | 5 | Posiciones simultáneas |
| `MAX_DAILY_LOSS_PCT` | 5.0 | Pérdida diaria que activa el kill switch |
| `MIN_CONVICTION` | 65 | Convicción mínima que debe declarar el LLM |
| `STOP_ATR_MULTIPLE` | 2.0 | Distancia del stop en múltiplos de ATR(14) |
| `MIN_REWARD_RISK` | 1.5 | Ratio beneficio/riesgo mínimo exigido |
| `SIM_SLIPPAGE_BPS` | 5 | Deslizamiento del simulador, siempre en contra |
| `SIM_COMMISSION` | 0 | Comisión por orden en USD |
| `SCREENER_TOP_N` | 20 | Candidatos que pasan al modelo |
| `SCREENER_MODE` | score | `random` es el grupo de control |
| `BAR_INTERVAL` | 1d | `1h` para varios ciclos por sesión |
| `SKIP_WHEN_MARKET_CLOSED` | true | No gastar cuota con el mercado cerrado |

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
requirements-alpaca.txt Extra opcional para operar contra Alpaca
Dockerfile              Imagen unica para los tres servicios
docker-compose.yml      dashboard + scheduler + comandos puntuales
run.py                  CLI: check / cycle / status / report / serve
schema.sql              Esquema SQLite; se aplica solo en cada arranque
src/
  config.py             Configuración validada desde .env
  models.py             Tipos de dominio compartidos
  indicators.py         RSI, ATR, MACD… funciones puras, sin numpy
  market_data.py        Barras: Yahoo (por defecto) o Alpaca
  bar_cache.py          Cache de barras con refresco incremental
  screener.py           Filtro determinista: universo -> candidatos
  universe_data.py      El embudo: cache + screener + snapshots
  market_calendar.py    Sesiones, festivos y medias sesiones de NYSE
  sim_broker.py         Broker simulado sobre SQLite (por defecto)
  llm.py                Cliente NVIDIA NIM y parseo defensivo de JSON
  analyst.py            Prompts y validación de la salida del modelo
  risk.py               Risk Manager  ← la pieza crítica
  broker.py             Alpaca; el único módulo que mueve dinero real
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

## 11. Pasar a dinero real

Cuando tengas suficientes ciclos para juzgar (mira la calibración, el profit
factor y la caída máxima, no el P&L de una semana):

1. Instala el extra y da de alta la cuenta: `pip install -r requirements-alpaca.txt`,
   luego `BROKER=alpaca`, `DATA_PROVIDER=alpaca` y `ALPACA_PAPER=false`.
   Alpaca exige activar la verificación en dos pasos antes de darte claves.
   **Pasa primero por `ALPACA_PAPER=true`**: es la única forma de comprobar que
   los fills reales se parecen a los del simulador.
2. **Cambia también `PORTFOLIO_NAME`.** El sistema se niega a reutilizar una
   cartera de paper en modo real: mezclar ambos históricos los haría
   incomparables.
3. Baja `INITIAL_BUDGET` a una cantidad que puedas perder por completo.

`run.py cycle` pedirá que escribas `CONFIRMO` antes de enviar la primera orden
real.
