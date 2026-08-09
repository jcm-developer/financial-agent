# EXPERIMENT.md — Cómo corre un experimento, de punta a punta

Este documento describe **la lógica real** con la que opera el agente: qué pasa,
en qué orden, con qué datos y en qué momento. Es la referencia sobre la que se
construye lo que venga después.

Está escrito el 2026-08-10, con el primer experimento (`eu-05-muy-agresivo`)
arrancando ese mismo día. Cuando algo aquí deje de ser cierto, se corrige aquí
—no en un comentario suelto—, porque es el sitio donde se mira antes de tocar el
ciclo.

---

## 1. Qué es un experimento

Un **perfil** es un experimento. Lo define entero una fila de `agent_settings`
(41 columnas) más su universo, y lleva su propia cartera con su propio dinero
simulado. Dos experimentos con el mismo criterio en dos bolsas distintas son dos
perfiles, no dos ramas del código.

De un perfil salen: el mercado (y con él horario, calendario, divisa, benchmark y
suelo de liquidez), el capital, el universo, el modelo de lenguaje, el intervalo
de barras, las horas de ciclo y los dos deslizadores —**perfil de riesgo** y
**diversificación**— de los que se derivan los nueve límites duros.

`profiles.status` decide si corre: **solo los `active` se planifican y solo sus
símbolos se siguen en vivo.**

---

## 2. Los cuatro procesos, y por qué están separados

| Proceso | Qué hace | Cada cuánto |
|---|---|---|
| `api` | Sirve la interfaz y la API REST. **No puede escribir en el histórico** | continuo |
| `ingestor` | Descarga cotizaciones y las guarda | **cada minuto**, en ventana de mercado |
| `scheduler` | Lanza los ciclos de cada experimento activo a sus horas | relee el plan cada minuto |
| `bot` | Comandos puntuales (`check`, `report`, tests) | a mano |

Están separados a propósito: si Yahoo se cuelga, ni la interfaz ni el agente se
enteran. Y cada ciclo corre como **subproceso propio**, así que si una llamada al
modelo se queda colgada, el planificador sobrevive y el ciclo siguiente sigue en
pie.

---

## 3. ⚠️ Los dos relojes, que no se tocan

Esta es la confusión más fácil del proyecto y conviene tenerla clara antes que
nada.

```
  ingestor  ──cada minuto──►  quotes_live + bars_1m  ──►  PANTALLA
                                                     └──►  histórico para backtesting (F9.2)

  ciclo  ──a su hora──►  descarga propia de Yahoo  ──►  bar_cache (1h o 1d)  ──►  ANALISTA
```

**El agente no lee `bars_1m` ni `quotes_live`.** Ni `cycle.py`, ni
`universe_data.py`, ni `market_data.py` los tocan. La ingesta de cada minuto
alimenta la pantalla —precios en vivo, valoración de posiciones abiertas, la
antigüedad del dato— y construye el histórico de minuto que hoy no usa nadie y
que existe porque Yahoo solo sirve ~30 días hacia atrás: si no se guarda ahora, no
se recupera.

Los datos del analista los descarga **el propio ciclo**, en su `bar_interval`,
incrementalmente sobre lo que ya tuviera `bar_cache`.

**Consecuencias prácticas:**

- Si paras el ingestor, el agente opera igual.
- Si paras el planificador, el ingestor sigue acumulando histórico.
- Pausar el único experimento activo **deja el ingestor sin símbolos que seguir**,
  porque sigue la unión de los universos de los perfiles activos (más los símbolos
  con posición abierta, esos sí sin mirar el estado).

---

## 4. Anatomía de un ciclo, paso a paso

Lo orquesta [src/cycle.py](src/cycle.py). Dura ~20 minutos, casi todo esperando al
modelo.

```
1. Cerrojo         ¿hay otro ciclo corriendo sobre esta misma cartera? → se salta
2. Calendario      ¿toca operar ahora? (skip_when_market_closed)
3. Reconciliación  broker ↔ base: lo que el broker dice que hay manda
4. Snapshot        copia de los 41 parámetros → cycles.settings_json
5. Screener        89 símbolos → 20 candidatos       DETERMINISTA, sin LLM, segundos
6. Salidas obligatorias  stop y objetivo de cada posición abierta   SIN LLM
7. Revisión de salidas   1 llamada por posición abierta      sell | hold
8. Entradas             1 llamada por candidato              buy  | hold
9. Risk manager    aprueba, redimensiona o rechaza cada propuesta   DETERMINISTA
10. Broker         ejecuta lo aprobado
11. Cierre         equity_snapshot y estado final del ciclo
```

**El modelo nunca ejecuta.** Propone `{acción, convicción, tesis, riesgos,
horizonte, stop sugerido, objetivo sugerido}` y [src/risk.py](src/risk.py) decide
con reglas fijas. Ese corte es la premisa del proyecto.

**Los pasos 5, 6 y 9 no gastan modelo**, y eso importa: las salidas obligatorias
—stop perforado, objetivo alcanzado— se comprueban **en cada ciclo y gratis**. Ahí
está el valor de tener ocho ciclos al día en vez de uno: no en preguntarle ocho
veces al modelo, sino en mirar los stops ocho veces.

---

## 5. Los tres precios, y por qué son tres

Es la parte más sutil y la que evita engañarse solo.

| Precio | Qué es | Para qué |
|---|---|---|
| `price` | **Cierre de la última barra COMPLETA** | Lo único que ven el analista y el risk manager. Sobre esto se dimensiona |
| `fill_price` | **Apertura de la barra siguiente** | Donde se ejecuta, más deslizamiento en contra |
| `mark_price` | Último precio conocido | Solo para valorar la cartera |

**La última barra nunca se usa para decidir**, porque puede estar a medias si el
mercado sigue abierto. Decidir y ejecutar con el mismo cierre regalaría el hueco
de apertura y falsearía el resultado entero.

⚠️ **Con barras horarias esa protección casi desaparece, y hay un desfase nuevo.**
Con barras diarias, decidir con el cierre de ayer y ejecutar en la apertura de hoy
es realista. Con barras horarias las dos son casi el mismo instante —el cierre de
las 10:00 y la apertura de las 10:00—, así que:

- El ciclo de las **10:20** decide con la barra 09:00–10:00 y **ejecuta al precio
  de las 10:00**, que para cuando el modelo ha contestado tiene 20–40 minutos.
- No es sesgo de anticipación —no se ve el futuro— pero **tampoco es un precio al
  que se habría podido comprar**.

Está apuntado como **F9.3**: ejecutar al precio vivo del momento de la orden,
usando `quotes_live`, que es justo el dato que el ingestor ya guarda cada minuto y
que hoy no llega al circuito de operar.

---

## 6. Qué ve el modelo, y qué no

**Ve** ~30 indicadores calculados sobre las barras, más las últimas 10 barras
(fecha, apertura, máximo, mínimo, cierre, volumen), más el estado de la cartera
como contexto:

> SMA 20/50/200, RSI 14, ATR 14 y %, MACD y señal, Bollinger, retornos a 5/20/60
> barras, volatilidad, máximo y mínimo de 252 barras, distancia a esos extremos,
> volumen y ratio sobre su media, y una decena de señales booleanas precalculadas.

**No ve nada más.** No hay fundamentales, ni noticias, ni sentimiento, ni
resultados trimestrales. El sistema es **100 % técnico**, y el prompt se lo dice
explícitamente al modelo: *«No tienes acceso a noticias, resultados trimestrales ni
precios posteriores a tu fecha de entrenamiento. NO inventes catalizadores, cifras
de ingresos, upgrades de analistas ni titulares.»*

Esa regla es lo que hace **interpretable** el experimento: hoy, si el modelo cita
un catalizador, es una alucinación y se ve en la pantalla de Decisiones. Añadir
noticias obligaría a rediseñar esa garantía (ver **F9.7**).

⚠️ **Las ventanas están en BARRAS, no en días, y varios nombres lo contradicen.**
`return_60d_pct` son 60 barras (~7 sesiones con barras horarias);
`pct_from_52w_high` son 252 barras (~30 sesiones), no 52 semanas. Los nombres
vienen del diseño con barras diarias, donde sí eran ciertos.

**Resuelto el 2026-08-10 sin renombrar las claves**: cuando el intervalo no es
diario, el prompt lleva una nota que dice exactamente cuántas barras es cada
ventana. Las claves no se tocan porque se serializan en
`market_snapshots.indicators` y se consultan luego por SQL; la nota cuesta cuatro
líneas y no engaña a nadie. Decir solo «calculados sobre barras horarias» no
bastaba: el nombre de la clave invita a leerlo al revés, y la tesis se apoya justo
en esas cifras.

**Y la divisa se pasa, nunca se asume** (FE.8). El prompt escribía `USD` en los
cuatro precios que enseña, así que un experimento europeo le contaba al modelo que
SAN.MC cotiza en dólares. Era el mismo invariante que la interfaz respeta, roto en
el único sitio donde no se ve.

---

## 7. Por qué el screener criba a 20, y no analiza los 89

Hay **dos filtros distintos** dentro del screener y conviene no confundirlos.

**a) Descartes duros** — no son una preferencia, son una condición para que el
experimento signifique algo:

| Descarte | Por qué |
|---|---|
| Liquidez < 5.000.000 €/día | El simulador supone que se compra a la apertura **sin mover el mercado**. En un valor ilíquido eso es mentira, y contamina el resultado |
| Precio < 5 € | Chicharros: el ruido de tick domina el movimiento |
| Volatilidad anualizada > 120 % | El stop por ATR saldría tan ancho que la posición resultante sería irrelevante |
| Menos de 60 barras, o sin ATR | Los indicadores largos no significan nada, y el risk manager rechazaría la entrada igualmente |

**b) Recorte a `screener_top_n`** — este sí es una decisión de coste, y es la que
preguntas:

- **Cada candidato es una llamada al modelo, y van en serie.** ~30–60 s cada una.
- 20 candidatos ≈ **15–20 minutos de ciclo**. Con ocho ciclos al día son ~160 de
  los 510 minutos de la ventana europea: cabe.
- **89 candidatos serían ~45–90 minutos por ciclo.** Con ocho ciclos no cabe ni de
  lejos; daría para **uno solo al día**.

O sea: el recorte es lo que compra **frecuencia**. Es la misma moneda de cambio
que aparece en todo el proyecto —amplitud contra profundidad— y aquí se resuelve a
favor de la profundidad porque las salidas obligatorias se comprueban en cada
ciclo y no gastan modelo.

**Es un parámetro, no una constante:** `screener_top_n` va de 1 a 200 y se edita
en Ajustes. Subirlo cuesta tiempo de ciclo, y hay que bajar el número de ciclos.

⚠️ **Y sí, la puntuación es una decisión editorial que limita lo que el modelo
puede ver.** El proyecto lo sabe: por eso existe `screener_mode = random`
(**F5.7**), que reparte candidatos arbitrarios manteniendo los descartes duros. Si
el agente rinde igual con candidatos al azar, **el filtro no estaba aportando
nada**, y ese es exactamente el grupo de control que lo mide.

---

## 8. Qué queda registrado

Todo, y a propósito: el experimento se interpreta después.

| Tabla | Qué guarda |
|---|---|
| `cycles` | Cada ejecución, con **copia de los 41 parámetros** con los que corrió |
| `decisions` | Cada propuesta del modelo: acción, convicción, tesis, riesgos, horizonte |
| `risk_events` | Qué hizo el risk manager con cada propuesta y **bajo qué regla** |
| `orders` | Lo que se mandó al broker |
| `positions` | Abiertas y cerradas, con su tesis y su motivo de salida |
| `equity_snapshots` | La curva de capital, un punto por ciclo |
| `market_snapshots` | Los indicadores que vio el modelo, tal cual |
| `agent_settings_history` | Cada cambio real de parámetro, con valor viejo y nuevo |

Que `cycles.settings_json` lleve copia de los parámetros es lo que permite leer un
histórico meses después: sin eso, cambiar un deslizador haría ininterpretable todo
lo anterior.

---

## 9. Ciclo de vida: arrancar, mirar, cerrar

**Arrancar.** Se crea el perfil (pantalla de Experimentos), se le ponen los
parámetros (Ajustes) y se **activa**. El planificador lo recoge en menos de un
minuto, sin reiniciar nada.

**Mirar.** Resumen, Posiciones, Decisiones, Órdenes, Riesgo, Ciclos y Analítica.
La pantalla de Ciclos enseña el log en vivo del que esté corriendo.

**Cerrar.** ⚠️ **Aquí hay un hueco reconocido.** Hoy solo se puede **pausar**, y
pausar:

- **no cierra las posiciones abiertas**, así que el resultado que se lee es **no
  realizado**: la cartera valorada a mercado, no un resultado cerrado;
- **deja de comprobar stops y objetivos**, porque eso solo pasa dentro de un ciclo.
  Un experimento pausado con posiciones vivas está expuesto sin vigilancia;
- si era el único activo, **deja al ingestor sin símbolos** y los precios de
  valoración se congelan en el último conocido.

Falta un **«Cerrar experimento»** que venda todo por el broker —con sus órdenes,
sus fills y sus posiciones cerradas, no un `UPDATE` a mano— y deje el resultado
realizado. Está apuntado como **F5.8**.

---

## 10. Lo que hoy NO hace, y dónde está apuntado

Para no volver a preguntárselo:

| No hace | Tarea |
|---|---|
| Cerrar el experimento vendiendo las posiciones | **F5.8** |
| Ejecutar al precio del momento de la orden (usa la apertura de la barra) | **F9.3** |
| Leer noticias o fundamentales | **F9.7** (spike), luego **F9.4** |
| Aplicar el tope por sector (lo calcula y no lo hace cumplir) | **FE.12** / **F6.5** |
| Operar en corto | `allow_shorts` existe y está a 0 |
| Cerrar por horizonte cumplido (`horizon_days` se registra, no cierra) | — |
| Convertir divisa | **nunca**, es una restricción del diseño (D8) |
| Operar con dinero real | **nunca**, el único broker es el simulador |

---

## 11. Resumen en una página

1. Un perfil activo = un experimento con su cartera y sus parámetros.
2. El **ingestor** guarda precio cada minuto → pantalla e histórico. **No llega al
   agente.**
3. El **planificador** lanza un ciclo a cada hora configurada del perfil.
4. Cada ciclo: screener determinista criba 89 → 20; el modelo opina sobre esos 20
   y sobre cada posición abierta; el risk manager decide; el broker ejecuta.
5. Se decide con la **última barra completa** y se ejecuta en la **apertura de la
   siguiente**, con deslizamiento en contra.
6. Todo queda registrado, incluidos los parámetros de ese ciclo.
7. Pausar detiene los ciclos pero **no cierra nada**: el resultado es no realizado
   hasta que exista **F5.8**.
