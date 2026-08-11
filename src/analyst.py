"""The analyst: the only layer where the LLM takes part.

It produces `Proposal`, never orders. The prompt is explicit about that boundary
because models tend to write "buy 500 shares" unless they are told that sizing is
none of their business.

Every output of the model is validated and clamped to legal ranges before it
leaves this module: `risk.py` can assume it receives a well-formed `Proposal`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from . import fees
from .llm import LLMClient, LLMError
from .models import AccountState, BrokerPosition, MarketSnapshot, Proposal

log = logging.getLogger(__name__)


ENTRY_SYSTEM_PROMPT = """\
Eres un analista de renta variable en una mesa cuantitativa. Tu unica funcion es \
emitir un juicio razonado sobre si un activo es una compra atractiva a dias o \
semanas de horizonte.

Frontera de tu rol, no la cruces:
- NO decides cuantas acciones se compran ni mueves dinero. Un motor de riesgo \
determinista calcula la cantidad a partir de la volatilidad y de sus limites, y \
puede recortar o rechazar lo que propongas. Si mencionas numeros de acciones, se \
ignoran.
- SI propones el PESO que merece la idea, en `suggested_weight_pct`: que \
porcentaje del capital total te parece que deberia ocupar esta posicion. Es una \
peticion, no una orden -- el motor la recorta contra su tope y contra la caja \
disponible, nunca la amplia.
- NO ejecutas nada. Tu salida es una recomendacion que puede ser rechazada.

Sobre el peso, porque es la parte que se hace mal:
- El tope por posicion es un MAXIMO, no un objetivo. Pedir el maximo en cada \
idea es no haber decidido nada.
- Una idea que te gusta poco es un peso pequeño, no un "hold". Reserva los pesos \
altos para cuando varias señales independientes coincidan, igual que la \
conviccion.
- Piensa en la cartera entera: si pides pesos grandes en todo, las primeras dos \
o tres ideas agotan el capital y las demas no se ejecutan aunque sean mejores.

Operar cuesta dinero, y el objetivo tiene que tenerlo en cuenta:
- Cada orden paga una comision FIJA, en la compra y otra vez en la venta. El \
coste concreto de este activo te lo doy abajo.
- El motor de riesgo calcula el ratio beneficio/riesgo DESPUES de comisiones y \
rechaza la propuesta si no llega al minimo. Un objetivo pegado al precio de \
entrada hace que la operacion se descarte por buena que sea la tesis.
- Asi que no propongas objetivos de recorrido simbolico: si el movimiento que \
esperas no supera con holgura la comision de ida y vuelta, la respuesta correcta \
es "hold".

Reglas de honestidad intelectual, son lo que hace util este sistema:
- Solo puedes usar los datos numericos que te doy. No tienes acceso a noticias, \
resultados trimestrales ni precios posteriores a tu fecha de entrenamiento. NO \
inventes catalizadores, cifras de ingresos, upgrades de analistas ni titulares.
- Si tu conocimiento general del sector es relevante, puedes usarlo, pero \
marcalo como contexto cualitativo y no como hecho reciente.
- "hold" es la respuesta correcta la mayor parte del tiempo. Un analista que ve \
oportunidades en todo no aporta senal. No fuerces una compra.
- La conviccion debe estar calibrada y es una probabilidad, no una nota: X \
significa que en 10 casos parecidos acertarias unas X/10 veces. 50 es moneda al \
aire, 60 es una ventaja pequeña pero real, 85 es un setup excepcional donde \
varias senales independientes coinciden.
- USA EL RANGO. Si todas tus propuestas salen con la misma conviccion, no estas \
midiendo nada: estas repitiendo un numero. Dos activos distintos casi nunca \
merecen la misma cifra, y la diferencia entre ellos es la informacion util.
- La conviccion NO es el peso. Puedes estar muy seguro de un movimiento pequeño, \
o poco seguro de uno grande; son dos campos porque son dos preguntas.

Responde UNICAMENTE con un objeto JSON, sin texto antes ni despues, con \
exactamente este esquema:

{
  "action": "buy" | "hold",
  "conviction": <entero 0-100>,
  "thesis": "<2-4 frases: por que ahora, apoyandote en los datos concretos>",
  "risks": "<1-3 frases: que invalidaria la tesis>",
  "horizon_days": <entero, dias que esperas mantener la posicion>,
  "suggested_stop": <precio de invalidacion tecnica, o null>,
  "suggested_target": <precio objetivo realista, o null>,
  "suggested_weight_pct": <porcentaje del capital que merece esta idea, o null>,
  "key_signals": ["<senal 1>", "<senal 2>"]
}
"""

EXIT_SYSTEM_PROMPT = """\
Eres el gestor de riesgo discrecional de una mesa cuantitativa. Revisas una \
posicion ABIERTA y decides si la tesis sigue viva.

Contexto importante: el stop y el objetivo ya se vigilan automaticamente y se \
ejecutan sin ti. Tu trabajo es distinto: detectar que la tesis se ha degradado \
antes de que el precio llegue al stop, o que sigue intacta y hay que aguantar.

Reglas:
- Solo puedes usar los datos numericos que te doy. No inventes noticias.
- Cerrar cuesta una comision fija, que te doy abajo y que se resta del \
resultado. Salir de una posicion plana es perder esa comision sin mas, asi que \
no cierres por ruido: hace falta deterioro de la tesis, no un dia malo.
- No cortes ganadoras por nerviosismo ni mantengas perdedoras por esperanza. \
Justifica con los datos.
- "hold" es una respuesta legitima y frecuente.
- Usa "sell" cuando el deterioro tecnico contradice la razon original de la entrada.

Responde UNICAMENTE con un objeto JSON, sin texto antes ni despues:

{
  "action": "sell" | "hold",
  "conviction": <entero 0-100, tu conviccion en la accion propuesta>,
  "thesis": "<2-3 frases justificando>",
  "risks": "<que podria salir mal si haces esto>",
  "suggested_stop": <nuevo stop si conviene ajustarlo al alza, o null>,
  "suggested_target": <objetivo revisado, o null>
}
"""


# How the interval is named in the prompts. Saying "sessions" when they are
# really hours would make the model reason about the wrong horizon.
INTERVAL_LABELS = {
    "1d": ("barras diarias", "SESIONES"),
    "1h": ("barras horarias", "HORAS DE COTIZACION"),
}

#: Singular of each bar label, for the line that names the reference price. Same
#: reason as `SINGULAR_WINDOW`: the model reads it.
SINGULAR_BAR = {
    "barras diarias": "sesion",
    "barras horarias": "hora de cotizacion",
}

#: Singular of each window label. It used to be `window_label.rstrip("S")`, which
#: gave "SESIONE" and "HORAS DE COTIZACION" — the model reads that line, and a
#: prompt that writes badly is a prompt that is being read carelessly.
SINGULAR_WINDOW = {
    "SESIONES": "SESION",
    "HORAS DE COTIZACION": "HORA DE COTIZACION",
}

#: Warning handed to the model when the bars are not daily.
#:
#: ⚠️ The indicator keys carry a unit **in the name** —`return_60d_pct`,
#: `high_52w`, `volatility_20d_pct`— and those names are inherited from the daily
#: design. The windows are counted in **bars**, so with hourly data
#: `pct_from_52w_high` is the distance to the high of 252 *hours*, about six
#: weeks, and not to the 52-week high. Saying "computed on hourly bars" was not
#: enough: the key name invites reading it the other way, and the model builds its
#: thesis on exactly these figures.
#:
#: The keys are not renamed because they are serialised into
#: `market_snapshots.indicators` and queried from SQL later; the note costs four
#: lines of prompt and lies to nobody.
WINDOW_UNITS_NOTE = """ATENCION A LAS UNIDADES: los nombres de los indicadores dicen "d" y "w" por
herencia del diseño con barras diarias, pero las ventanas se cuentan en BARRAS.
Aqui, con {bar_label}: `return_20d_pct` y `volatility_20d_pct` son 20 barras,
`return_60d_pct` son 60, `sma_200` son 200, y `high_52w`, `low_52w` y
`pct_from_52w_high` son 252 barras. NO son dias ni semanas."""

#: Note handed to the model when the price clock is faster than the indicators
#: (F9.14), which is the normal case now: `bar_interval=1h` with daily indicators.
#:
#: It exists because the bundle carries its own `price` —the daily close every
#: band and distance in it refers to— while the reference price is the current
#: intraday one. Two figures called price and no explanation would be the model's
#: problem to reconcile, and it would reconcile it by guessing. So the gap is
#: **precomputed**, which is the same rule the derived boolean signals follow:
#: arithmetic is where this model goes wrong most often.
PRICE_CONTEXT_NOTE = """CONTEXTO: los indicadores estan calculados sobre {bar_label} y se refieren al
ultimo cierre diario completo, {context_price:.2f} {currency}. El precio de arriba
es el actual, {gap:+.2f}% respecto de ese cierre: usa los indicadores para juzgar
la situacion tecnica y el precio actual para situar stop y objetivo."""


class Analyst:
    """One analyst per cycle: the counters belong to that run, not to the process.

    `calls` and `failures` exist because swallowing the `LLMError`s (see
    `evaluate_entry`) makes a cycle with no model look far too much like a cycle
    with no opportunities. What to do with the difference is decided by
    `TradingCycle._grade_analyst`; here it is only counted (F6.9).
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        price_interval: str = "1d",
        indicator_interval: str = "1d",
        currency: str = "USD",
        commission_for: Callable[[str], float] | None = None,
        max_position_pct: float | None = None,
    ) -> None:
        """Two intervals and not one, since F9.14.

        `price_interval` is the profile's `bar_interval` and names the reference
        price; `indicator_interval` is what the technical bundle was computed on
        and is `market_data.INDICATOR_INTERVAL` — daily — for everything the cycle
        builds today.

        **The parameter is kept rather than hardcoded** because the units note
        below is only correct while it is told the truth: the day someone reasons
        on another interval again, the prompt says so instead of the code saying
        one thing and the constant another. It is also what makes `WINDOW_UNITS_NOTE`
        live code rather than a comment about the past.
        """
        self.llm = llm
        self.price_interval = price_interval
        self.indicator_interval = indicator_interval
        #: What one leg costs, so the prompt can state it (F9.9). Injected for the
        #: same reason as in `RiskManager`: `cycle.py` passes the broker's own,
        #: which adds the profile's surcharge. Default is the bank's tariff and
        #: never zero, which would tell the model that trading is free.
        self._commission_for = commission_for or fees.standard_commission
        #: The profile's per-position ceiling, so `suggested_weight_pct` has a
        #: scale. Without it the model cannot tell whether 10 % is timid or bold,
        #: and the field would be noise.
        #:
        #: ⚠️ It **anchors**, and that is the price paid: told the ceiling, a model
        #: tends to ask for it. The prompt answers that head on —"el tope es un
        #: MAXIMO, no un objetivo"— and `risk.py` caps regardless. The alternative
        #: was a number with no units, which is worse: it would look like a
        #: decision and be a guess.
        self.max_position_pct = max_position_pct
        #: Currency of the profile's market. **It is passed, never assumed**
        #: (FE.8): the prompt used to say "USD" for every price, so a European
        #: experiment told the model that SAN.MC trades in dollars. It is the
        #: same invariant the interface obeys, and it was being broken in the one
        #: place where nobody would see it — inside the prompt.
        self.currency = currency
        self.price_labels = INTERVAL_LABELS.get(price_interval, INTERVAL_LABELS["1d"])
        self.labels = INTERVAL_LABELS.get(
            indicator_interval, INTERVAL_LABELS["1d"]
        )
        #: Times the model has been asked, including the calls that failed.
        self.calls = 0
        #: Of those, how many got no usable answer.
        self.failures = 0

    # -- Entradas ----------------------------------------------------------

    def evaluate_entry(
        self, snapshot: MarketSnapshot, account: AccountState
    ) -> Proposal | None:
        """Analyses one candidate. Returns None if the model fails: a symbol with
        no analysis is skipped, not traded blind."""
        user_prompt = _render_entry_prompt(
            snapshot, account, self.labels, self.currency,
            self._commission_for(snapshot.symbol), self.max_position_pct,
            self.price_labels,
        )
        self.calls += 1
        try:
            response = self.llm.complete_json(
                system=ENTRY_SYSTEM_PROMPT, user=user_prompt
            )
        except LLMError as exc:
            self.failures += 1
            log.warning("El analisis de entrada de %s fallo: %s", snapshot.symbol, exc)
            return None

        data = response.parsed or {}
        action = _coerce_action(data.get("action"), allowed={"buy", "hold"})
        proposal = Proposal(
            symbol=snapshot.symbol,
            kind="entry",
            action=action,
            conviction=_coerce_conviction(data.get("conviction")),
            thesis=_coerce_text(data.get("thesis"), limit=2000),
            risks=_coerce_text(data.get("risks"), limit=2000),
            horizon_days=_coerce_int(data.get("horizon_days"), minimum=1, maximum=365),
            suggested_stop=_coerce_price(data.get("suggested_stop")),
            suggested_target=_coerce_price(data.get("suggested_target")),
            suggested_weight_pct=_coerce_weight(data.get("suggested_weight_pct")),
            reference_price=snapshot.price,
            model=response.model,
            latency_ms=response.latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            raw_response=_audit_payload(response.content, data),
        )
        log.info(
            "%s -> %s (conviccion %d) %s",
            snapshot.symbol, proposal.action, proposal.conviction,
            _truncate(proposal.thesis, 110),
        )
        return proposal

    # -- Salidas -----------------------------------------------------------

    def evaluate_exit(
        self,
        position: BrokerPosition,
        snapshot: MarketSnapshot,
        entry_thesis: str | None,
        stop_price: float | None,
        target_price: float | None,
    ) -> Proposal | None:
        user_prompt = _render_exit_prompt(
            position, snapshot, entry_thesis, stop_price, target_price,
            self.labels, self.currency, self._commission_for(position.symbol),
            self.price_labels,
        )
        self.calls += 1
        try:
            response = self.llm.complete_json(
                system=EXIT_SYSTEM_PROMPT, user=user_prompt
            )
        except LLMError as exc:
            self.failures += 1
            log.warning("La revision de salida de %s fallo: %s", position.symbol, exc)
            return None

        data = response.parsed or {}
        return Proposal(
            symbol=position.symbol,
            kind="exit",
            action=_coerce_action(data.get("action"), allowed={"sell", "hold"}),
            conviction=_coerce_conviction(data.get("conviction")),
            thesis=_coerce_text(data.get("thesis"), limit=2000),
            risks=_coerce_text(data.get("risks"), limit=2000),
            horizon_days=None,
            suggested_stop=_coerce_price(data.get("suggested_stop")),
            suggested_target=_coerce_price(data.get("suggested_target")),
            reference_price=snapshot.price,
            model=response.model,
            latency_ms=response.latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            raw_response=_audit_payload(response.content, data),
        )


# ----------------------------------------------------------------------
# Construccion de prompts
# ----------------------------------------------------------------------

def _render_entry_prompt(
    snapshot: MarketSnapshot,
    account: AccountState,
    labels: tuple[str, str],
    currency: str = "USD",
    commission: float = 0.0,
    max_position_pct: float | None = None,
    price_labels: tuple[str, str] | None = None,
) -> str:
    bar_label, window_label = labels
    _, price_window_label = price_labels or labels
    units = _window_units_note(bar_label)
    context = _price_context_note(snapshot, bar_label, price_labels, currency)
    techo = (
        f"- Peso maximo permitido por posicion: {max_position_pct:.0f}% del capital. "
        f"Es un TOPE, no un objetivo."
        if max_position_pct is not None
        else "- Peso maximo por posicion: lo decide el motor de riesgo."
    )
    open_positions = (
        ", ".join(sorted(account.open_symbols)) if account.positions else "ninguna"
    )
    return f"""\
FECHA DE ANALISIS: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
ACTIVO: {snapshot.symbol}
PRECIO DE CIERRE DE LA ULTIMA {SINGULAR_WINDOW.get(price_window_label, price_window_label)} COMPLETA: {snapshot.price:.2f} {currency}

INDICADORES TECNICOS (calculados sobre {bar_label}; null = no disponible):
{_format_indicators(snapshot.indicators)}
{units}{context}
ULTIMAS 10 {window_label} (fecha, apertura, maximo, minimo, cierre, volumen):
{_format_bars(snapshot.recent_bars)}

COSTE DE OPERAR {snapshot.symbol}: {commission:.2f} {currency} por orden, o sea
{commission * 2:.2f} {currency} de ida y vuelta. Es un importe fijo, no un
porcentaje, y se descuenta del resultado.

ESTADO DE LA CARTERA:
- Posiciones ya abiertas: {open_positions}
{techo}
- Diversificacion: evita concentrar en activos muy correlacionados con los ya abiertos.

NOTA: `horizon_days` se expresa siempre en dias naturales, tambien si los datos
vienen en {bar_label}.

Emite tu juicio sobre {snapshot.symbol} en el JSON especificado."""


def _render_exit_prompt(
    position: BrokerPosition,
    snapshot: MarketSnapshot,
    entry_thesis: str | None,
    stop_price: float | None,
    target_price: float | None,
    labels: tuple[str, str],
    currency: str = "USD",
    commission: float = 0.0,
    price_labels: tuple[str, str] | None = None,
) -> str:
    bar_label, window_label = labels
    units = _window_units_note(bar_label)
    context = _price_context_note(snapshot, bar_label, price_labels, currency)
    return f"""\
FECHA DE REVISION: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
POSICION ABIERTA: {position.symbol}

- Acciones: {position.qty:g}
- Precio medio de entrada: {position.avg_entry_price:.2f} {currency}
- Precio actual: {position.current_price:.2f} {currency}
- P&L no realizado: {position.unrealized_pl:+.2f} {currency} ({position.unrealized_pl_pct:+.2f}%)
- Stop vigilado automaticamente: {_fmt(stop_price)}
- Objetivo vigilado automaticamente: {_fmt(target_price)}
- Coste de cerrar: {commission:.2f} {currency} de comision, que se resta del resultado

TESIS ORIGINAL DE LA ENTRADA:
{entry_thesis or "(no registrada)"}

INDICADORES TECNICOS ACTUALES (sobre {bar_label}):
{_format_indicators(snapshot.indicators)}
{units}{context}
ULTIMAS 10 {window_label} (fecha, apertura, maximo, minimo, cierre, volumen):
{_format_bars(snapshot.recent_bars)}

Decide si la tesis sigue viva, en el JSON especificado."""


def _window_units_note(bar_label: str) -> str:
    """The units warning, only when the bars are not daily.

    With daily bars the names do not lie —`60d` really is 60 sessions— so adding
    the note would be noise in a prompt that is already long, and noise in a
    prompt costs attention on the figures that do matter.

    It comes back with a leading newline so the caller can drop it in without a
    blank line appearing when there is nothing to say.

    Since F9.14 the indicators are daily, so in practice this returns the empty
    string — and that is the point of the change, not a reason to delete the
    function: the note is what keeps the prompt honest if the indicator interval
    ever moves again.
    """
    if bar_label == INTERVAL_LABELS["1d"][0]:
        return ""
    return "\n" + WINDOW_UNITS_NOTE.format(bar_label=bar_label) + "\n"


def _price_context_note(
    snapshot: MarketSnapshot,
    bar_label: str,
    price_labels: tuple[str, str] | None,
    currency: str,
) -> str:
    """The gap between the reference price and the close the indicators refer to.

    Only when the two clocks differ (F9.14). With one interval for both there is
    no gap to explain and the note would be a line saying that 0,00 % is zero.

    It is also skipped when the bundle carries no price of its own, which happens
    with an empty snapshot: there would be nothing to compare against, and writing
    a gap from a missing figure is worse than writing nothing.
    """
    if price_labels is None or price_labels[0] == bar_label:
        return ""
    context_price = snapshot.indicators.get("price")
    if not isinstance(context_price, (int, float)) or context_price <= 0:
        return ""
    return "\n" + PRICE_CONTEXT_NOTE.format(
        bar_label=bar_label,
        context_price=context_price,
        currency=currency,
        gap=(snapshot.price / context_price - 1.0) * 100.0,
    ) + "\n"


def _format_indicators(indicators: dict[str, Any]) -> str:
    if not indicators:
        return "  (sin datos)"
    lines = []
    for key in sorted(indicators):
        value = indicators[key]
        if isinstance(value, float):
            rendered = f"{value:,.4f}".rstrip("0").rstrip(".")
        else:
            rendered = "null" if value is None else str(value)
        lines.append(f"  {key}: {rendered}")
    return "\n".join(lines)


def _format_bars(bars: list[dict[str, Any]]) -> str:
    if not bars:
        return "  (sin datos)"
    lines = []
    for bar in bars[-10:]:
        lines.append(
            f"  {bar.get('date', '?')}  "
            f"o={_fmt(bar.get('open'))}  h={_fmt(bar.get('high'))}  "
            f"l={_fmt(bar.get('low'))}  c={_fmt(bar.get('close'))}  "
            f"v={_fmt_volume(bar.get('volume'))}"
        )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/d"
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return str(value)


def _fmt_volume(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/d"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ----------------------------------------------------------------------
# Coercing the model's output
# ----------------------------------------------------------------------

def _coerce_action(value: Any, *, allowed: set[str]) -> str:
    """Anything that is not an allowed action is degraded to 'hold'."""
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in allowed:
            return candidate
    log.debug("Accion no reconocida %r; se degrada a 'hold'.", value)
    return "hold"


def _coerce_conviction(value: Any) -> int:
    """Out of range or non-numeric is read as zero conviction, which the Risk
    Manager will reject for not reaching the minimum."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, number))


def _coerce_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))


def _coerce_price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number != number or number in (float("inf"), float("-inf")):
        return None
    return round(number, 4)


def _coerce_weight(value: Any) -> float | None:
    """The weight the analyst asks for, clamped to something a portfolio allows.

    Above 100 it is nonsense —there is no leverage— and at or below zero it is a
    "hold" written in the wrong field, so both come back None and the sizing falls
    back to the conviction factor. It is **not** clamped to `max_position_pct`
    here: that is a limit of the profile and belongs to `risk.py`, which is the
    only place allowed to know the limits (F6.5). Clamping it here would also hide
    a model that keeps asking for more than it may have, which is exactly what the
    verdict's `suggested_weight_pct` is recorded for.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0 or number > 100:
        return None
    return round(number, 2)


def _coerce_text(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.strip()
    return text if len(text) <= limit else text[:limit]


def _audit_payload(raw_content: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """What gets stored in `decisions.raw_response`. The trimmed raw text is
    included: if the model hallucinates, we want to be able to see it later."""
    return {
        "parsed": parsed,
        "raw_text": raw_content[:8000],
    }
