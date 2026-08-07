"""El analista: la unica capa donde interviene el LLM.

Produce `Proposal`, nunca ordenes. El prompt es explicito sobre esa frontera
porque los modelos tienden a escribir "compra 500 acciones" si no se les dice
que el dimensionado no es asunto suyo.

Toda salida del modelo se valida y se recorta a rangos legales antes de salir
de este modulo: `risk.py` puede asumir que recibe un `Proposal` bien formado.
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


# Como se nombra el intervalo en los prompts. Decirle "sesiones" cuando en
# realidad son horas haria que el modelo razonara sobre un horizonte equivocado.
INTERVAL_LABELS = {
    "1d": ("barras diarias", "SESIONES"),
    "1h": ("barras horarias", "HORAS DE COTIZACION"),
}


class Analyst:
    def __init__(self, llm: LLMClient, *, interval: str = "1d") -> None:
        self.llm = llm
        self.interval = interval
        self.labels = INTERVAL_LABELS.get(interval, INTERVAL_LABELS["1d"])

    # -- Entradas ----------------------------------------------------------

    def evaluate_entry(
        self, snapshot: MarketSnapshot, account: AccountState
    ) -> Proposal | None:
        """Analiza un candidato. Devuelve None si el modelo falla: un simbolo
        sin analisis se salta, no se opera a ciegas."""
        user_prompt = _render_entry_prompt(snapshot, account, self.labels)
        try:
            response = self.llm.complete_json(
                system=ENTRY_SYSTEM_PROMPT, user=user_prompt
            )
        except LLMError as exc:
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
            position, snapshot, entry_thesis, stop_price, target_price, self.labels
        )
        try:
            response = self.llm.complete_json(
                system=EXIT_SYSTEM_PROMPT, user=user_prompt
            )
        except LLMError as exc:
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
    snapshot: MarketSnapshot, account: AccountState, labels: tuple[str, str]
) -> str:
    bar_label, window_label = labels
    open_positions = (
        ", ".join(sorted(account.open_symbols)) if account.positions else "ninguna"
    )
    return f"""\
FECHA DE ANALISIS: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
ACTIVO: {snapshot.symbol}
PRECIO DE CIERRE DE LA ULTIMA {window_label.rstrip('S')} COMPLETA: {snapshot.price:.2f} USD

INDICADORES TECNICOS (calculados sobre {bar_label}; null = no disponible):
{_format_indicators(snapshot.indicators)}

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
) -> str:
    bar_label, window_label = labels
    return f"""\
FECHA DE REVISION: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
POSICION ABIERTA: {position.symbol}

- Acciones: {position.qty:g}
- Precio medio de entrada: {position.avg_entry_price:.2f} USD
- Precio actual: {position.current_price:.2f} USD
- P&L no realizado: {position.unrealized_pl:+.2f} USD ({position.unrealized_pl_pct:+.2f}%)
- Stop vigilado automaticamente: {_fmt(stop_price)}
- Objetivo vigilado automaticamente: {_fmt(target_price)}

TESIS ORIGINAL DE LA ENTRADA:
{entry_thesis or "(no registrada)"}

INDICADORES TECNICOS ACTUALES (sobre {bar_label}):
{_format_indicators(snapshot.indicators)}

ULTIMAS 10 {window_label} (fecha, apertura, maximo, minimo, cierre, volumen):
{_format_bars(snapshot.recent_bars)}

Decide si la tesis sigue viva, en el JSON especificado."""


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
# Coercion de la salida del modelo
# ----------------------------------------------------------------------

def _coerce_action(value: Any, *, allowed: set[str]) -> str:
    """Cualquier cosa que no sea una accion permitida se degrada a 'hold'."""
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in allowed:
            return candidate
    log.debug("Accion no reconocida %r; se degrada a 'hold'.", value)
    return "hold"


def _coerce_conviction(value: Any) -> int:
    """Fuera de rango o no numerico se interpreta como conviccion nula, que el
    Risk Manager rechazara por no alcanzar el minimo."""
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
    """Lo que se guarda en `decisions.raw_response`. Incluimos el texto crudo
    recortado: si el modelo alucina, queremos poder verlo despues."""
    return {
        "parsed": parsed,
        "raw_text": raw_content[:8000],
    }
