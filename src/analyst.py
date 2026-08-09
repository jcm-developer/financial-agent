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
from datetime import datetime, timezone
from typing import Any

from .llm import LLMClient, LLMError
from .models import AccountState, BrokerPosition, MarketSnapshot, Proposal

log = logging.getLogger(__name__)


ENTRY_SYSTEM_PROMPT = """\
Eres un analista de renta variable en una mesa cuantitativa. Tu unica funcion es \
emitir un juicio razonado sobre si un activo es una compra atractiva a dias o \
semanas de horizonte.

Frontera de tu rol, no la cruces:
- NO decides el tamano de la posicion ni cuantas acciones comprar. Un motor de \
riesgo determinista lo calcula despues a partir de la volatilidad. Si mencionas \
cantidades, se ignoran.
- NO ejecutas nada. Tu salida es una recomendacion que puede ser rechazada.

Reglas de honestidad intelectual, son lo que hace util este sistema:
- Solo puedes usar los datos numericos que te doy. No tienes acceso a noticias, \
resultados trimestrales ni precios posteriores a tu fecha de entrenamiento. NO \
inventes catalizadores, cifras de ingresos, upgrades de analistas ni titulares.
- Si tu conocimiento general del sector es relevante, puedes usarlo, pero \
marcalo como contexto cualitativo y no como hecho reciente.
- "hold" es la respuesta correcta la mayor parte del tiempo. Un analista que ve \
oportunidades en todo no aporta senal. No fuerces una compra.
- La conviccion debe estar calibrada: 50 significa moneda al aire, 70 significa \
que en 10 casos parecidos acertarias unas 7 veces. Reservar >85 para setups \
excepcionales donde varias senales independientes coinciden.

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


class Analyst:
    """One analyst per cycle: the counters belong to that run, not to the process.

    `calls` and `failures` exist because swallowing the `LLMError`s (see
    `evaluate_entry`) makes a cycle with no model look far too much like a cycle
    with no opportunities. What to do with the difference is decided by
    `TradingCycle._grade_analyst`; here it is only counted (F6.9).
    """

    def __init__(
        self, llm: LLMClient, *, interval: str = "1d", currency: str = "USD"
    ) -> None:
        self.llm = llm
        self.interval = interval
        #: Currency of the profile's market. **It is passed, never assumed**
        #: (FE.8): the prompt used to say "USD" for every price, so a European
        #: experiment told the model that SAN.MC trades in dollars. It is the
        #: same invariant the interface obeys, and it was being broken in the one
        #: place where nobody would see it — inside the prompt.
        self.currency = currency
        self.labels = INTERVAL_LABELS.get(interval, INTERVAL_LABELS["1d"])
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
        user_prompt = _render_entry_prompt(snapshot, account, self.labels, self.currency)
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
            self.labels, self.currency,
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
) -> str:
    bar_label, window_label = labels
    units = _window_units_note(bar_label)
    open_positions = (
        ", ".join(sorted(account.open_symbols)) if account.positions else "ninguna"
    )
    return f"""\
FECHA DE ANALISIS: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
ACTIVO: {snapshot.symbol}
PRECIO DE CIERRE DE LA ULTIMA {SINGULAR_WINDOW.get(window_label, window_label)} COMPLETA: {snapshot.price:.2f} {currency}

INDICADORES TECNICOS (calculados sobre {bar_label}; null = no disponible):
{_format_indicators(snapshot.indicators)}
{units}
ULTIMAS 10 {window_label} (fecha, apertura, maximo, minimo, cierre, volumen):
{_format_bars(snapshot.recent_bars)}

ESTADO DE LA CARTERA (contexto, no es tu decision):
- Posiciones ya abiertas: {open_positions}
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
) -> str:
    bar_label, window_label = labels
    units = _window_units_note(bar_label)
    return f"""\
FECHA DE REVISION: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
POSICION ABIERTA: {position.symbol}

- Acciones: {position.qty:g}
- Precio medio de entrada: {position.avg_entry_price:.2f} {currency}
- Precio actual: {position.current_price:.2f} {currency}
- P&L no realizado: {position.unrealized_pl:+.2f} {currency} ({position.unrealized_pl_pct:+.2f}%)
- Stop vigilado automaticamente: {_fmt(stop_price)}
- Objetivo vigilado automaticamente: {_fmt(target_price)}

TESIS ORIGINAL DE LA ENTRADA:
{entry_thesis or "(no registrada)"}

INDICADORES TECNICOS ACTUALES (sobre {bar_label}):
{_format_indicators(snapshot.indicators)}
{units}
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
    """
    if bar_label == INTERVAL_LABELS["1d"][0]:
        return ""
    return "\n" + WINDOW_UNITS_NOTE.format(bar_label=bar_label) + "\n"


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
