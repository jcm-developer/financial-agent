"""A conversation with the experiment's own model (F9.39).

It lets the person running the experiment ask the analyst why it decided what it
decided and how it reads the market now. Three things shape it, and each one is
a decision rather than a detail:

  * **Same model, same key, same provider as the profile.** Asking Sol about a
    decision Luna made would be asking a different analyst. The effort and the
    temperature are the profile's too, so the voice is the one that decided.

  * **The model does not remember.** Its hidden reasoning is not returned by the
    provider and was never stored, so every answer about a past decision is a
    reconstruction from the saved thesis and the data it saw — usually sound, but
    not memory. The prompt makes it say so, and makes it tell "what I decided
    then" from "what I think today", because with fresh data the two can differ
    and that is a new opinion, not a correction.

  * **Nothing flows back into the experiment.** The chat reads the history and
    writes only its own tables; the cycle never reads them. If a conversation
    reached the analyst's prompt, the head-to-head between profiles would stop
    comparing a single variable. It cannot trade or change settings either: its
    tools are read-only queries, and the connection that runs them is too.

The context is split in two on purpose. What is almost always relevant — the
profile, the book, the last cycle's decisions — travels in every call, rebuilt
from the database so it is never stale. Everything older is reached through
tools, which keeps the prompt at a few thousand tokens instead of the whole
history.

**Every answer is two calls, and the reason is the provider's, not ours.**
OpenAI's GPT-6 refuse function tools on `/chat/completions` while they reason
(measured 2026-09-29: «Function tools with reasoning_effort are not supported»,
unless the effort is `none`). Dropping the reasoning for the whole chat would
make the answers worse exactly where they matter —«how do you read the market»—
so the lookup runs with tools and effort `none`, and the answer with the
profile's effort and no tools, with the lookups folded into its context. The
other way out, OpenAI's `/v1/responses`, is a second API shape in `llm.py` for
one feature; it is left for when the cycle needs it too.

Discarded: streaming the answer to the browser. A reply takes 10-30 s with the
GPT-6 pair and the screen says so while it waits; streaming would mean a second
transport next to the SSE of `/api/stream` for a gain in perceived speed only.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import market_calendar
from .config import Settings
from .db import Database
from .llm import LLMClient, LLMError, ToolCall

log = logging.getLogger(__name__)

#: Output ceiling of one chat turn. Higher than the cycle's on purpose: the
#: GPT-6 models count their hidden reasoning in it, and a free-form answer is
#: longer than the cycle's JSON. The profile's value wins if it is higher.
CHAT_MAX_TOKENS = 4000

#: Rounds of tool calls before the model is made to answer with what it has. A
#: question rarely needs more than two; the ceiling is against a loop.
MAX_TOOL_ROUNDS = 4

#: Earlier messages of the thread sent back to the model. Older ones are left
#: out rather than summarised: a summary would be the model's words about its
#: own words, one step further from the data.
HISTORY_MESSAGES = 20

#: The lookup phase runs without reasoning (see `reply`) and only picks tools,
#: so its ceiling is small: a few calls' worth of arguments.
LOOKUP_EFFORT = "none"
LOOKUP_MAX_TOKENS = 800

LOOKUP_INSTRUCTION = """

FASE DE CONSULTA. Todavia no contestes a la pregunta. Decide solo si para \
contestarla bien necesitas datos que no estan arriba: decisiones de otros dias o \
de otros valores, los indicadores que viste en una decision, posiciones cerradas, \
cotizaciones de ahora o titulares. Si los necesitas, llama a las herramientas. Si \
con lo de arriba basta, responde solo: LISTO."""

#: Decisions a single tool call may return. Each carries a thesis of ~150 tokens.
MAX_TOOL_ROWS = 30

SYSTEM_PROMPT = """Eres el analista del experimento de inversion simulada «{profile}». \
Eres el mismo modelo ({model}) que propone las decisiones de este experimento en cada \
ciclo, y ahora conversas con la persona que lo dirige.

Como funciona el experimento, para que lo expliques bien:
- En cada ciclo, un screener elige candidatos del universo por liquidez. Tu analizas \
cada uno con sus indicadores diarios y sus titulares, y propones buy, sell o hold con \
una conviccion, una tesis, un stop, un objetivo y un peso.
- Un motor de riesgo DETERMINISTA aprueba, dimensiona o rechaza cada propuesta con \
reglas fijas. Tu nunca ejecutas nada: propones. Si una compra no se hizo, mira si fue \
tu hold o un rechazo del motor, y di cual de las dos.
- El dinero es simulado y en {currency}. Horizonte de cada idea: {horizon} dias.

Reglas de esta conversacion:
- NO RECUERDAS por que decidiste. Tu razonamiento de entonces no se guardo: solo \
quedan la tesis, los riesgos y los datos que viste. Cuando expliques una decision \
pasada, dilo asi —«segun la tesis que escribi…»— y no inventes motivos que no esten en ella.
- Distingue siempre lo que decidiste entonces de lo que opinas ahora. Si con los datos \
de hoy decidirias otra cosa, dilo claramente: es una opinion nueva, no una rectificacion.
- Solo sabes lo que hay en los datos de abajo y en lo que consultes con las \
herramientas. No tienes acceso a internet. Si te preguntan por algo que no esta ahi \
—un dato macro, una noticia que no aparece—, di que no lo tienes en vez de suponerlo.
- Cuando te apoyes en una decision concreta, citala como [SIMBOLO AAAA-MM-DD], por \
ejemplo [SAP.DE 2026-09-29], para que se pueda comprobar.
- No puedes operar ni cambiar ajustes. Si te lo piden, explica que eso no esta a tu \
alcance y que las decisiones solo salen de los ciclos.
- Responde en español, con claridad y sin relleno. Usa listas o tablas en Markdown \
cuando ayuden a leer, y cifras con coma decimal.

DATOS DEL EXPERIMENTO (a {now}):
{context}"""


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------

def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties},
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "search_decisions",
        "Decisiones del experimento, de la mas reciente a la mas antigua, con su tesis, "
        "sus riesgos y el veredicto del motor de riesgo. Para preguntas sobre dias o "
        "valores que no estan en el ultimo ciclo.",
        {
            "symbol": {"type": "string", "description": "Simbolo exacto, p. ej. SAP.DE."},
            "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
            "date": {"type": "string", "description": "Dia AAAA-MM-DD."},
            "limit": {"type": "integer", "description": f"Maximo {MAX_TOOL_ROWS}."},
        },
    ),
    _tool(
        "decision_data",
        "Los datos exactos que viste al tomar una decision: indicadores, precio y "
        "titulares de ese ciclo, y el veredicto del motor de riesgo.",
        {"decision_id": {"type": "string"}},
    ),
    _tool(
        "positions",
        "Posiciones de la cartera: abiertas (con precio actual) o cerradas (con su resultado).",
        {"status": {"type": "string", "enum": ["open", "closed"]}},
    ),
    _tool(
        "quotes",
        "Cotizaciones en vivo del universo del experimento: precio, variacion del dia y "
        "hora del dato. Sin simbolos, todas.",
        {"symbols": {"type": "array", "items": {"type": "string"}}},
    ),
    _tool(
        "cycles",
        "Los ultimos ciclos: fecha, capital, si el mercado estaba abierto, cuantas "
        "propuestas hubo y cuantas aprobo o rechazo el motor.",
        {"limit": {"type": "integer"}},
    ),
    _tool(
        "news",
        "Titulares que viste en un ciclo: los de mercado si no se da simbolo, o los de "
        "un valor. Sin ciclo, el ultimo.",
        {"symbol": {"type": "string"}, "cycle_id": {"type": "string"}},
    ),
]


# ----------------------------------------------------------------------
# The reply
# ----------------------------------------------------------------------

@dataclass
class ChatReply:
    content: str
    model: str
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: What the model looked up, in order: [{"name", "arguments"}].
    tools: list[dict[str, Any]] = field(default_factory=list)


def build_client(settings: Settings) -> LLMClient:
    """The profile's client, with a larger ceiling (see `CHAT_MAX_TOKENS`)."""
    return LLMClient(
        api_key=settings.model_api_key,
        provider=settings.llm_provider,
        base_url=settings.model_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_tokens=max(settings.llm_max_tokens, CHAT_MAX_TOKENS),
        reasoning_effort=settings.llm_reasoning_effort,
    )


def reply(
    llm: LLMClient,
    db: Database,
    *,
    settings: Settings,
    portfolio_id: str,
    history: list[dict[str, Any]],
    question: str,
    focus_ids: list[str] | None = None,
) -> ChatReply:
    """Answers `question` given the thread's earlier messages.

    @param db: Any connection; only reads go through it.
    @param history: The thread's messages before this one, oldest first, as
        `Database.chat_messages` returns them. Failed answers are skipped.
    @param focus_ids: Decisions the conversation is about. Each is described in
        full in the context, not only the one asked about in this turn, so a
        follow-up question keeps it in view.
    @raise LLMError: if the model does not answer.
    """
    market = market_calendar.get_market(settings.market)
    context = build_context(db, portfolio_id, settings, focus_ids or [])
    system = SYSTEM_PROMPT.format(
        profile=settings.portfolio_name,
        model=settings.llm_model,
        currency=market.currency,
        horizon=settings.horizon_days,
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        context=context,
    )
    conversation: list[dict[str, Any]] = [
        {"role": message["role"], "content": message["content"]}
        for message in history[-HISTORY_MESSAGES:]
        if message.get("content") and not message.get("error")
    ]
    conversation.append({"role": "user", "content": question})

    result = ChatReply(content="", model=llm.model)

    def call(messages: list[dict[str, Any]], **options: Any):
        started = time.monotonic()
        response = llm.complete_chat(messages=messages, **options)
        result.latency_ms += int((time.monotonic() - started) * 1000)
        result.prompt_tokens += response.prompt_tokens
        result.completion_tokens += response.completion_tokens
        result.model = response.model or result.model
        return response

    # Phase one, the lookup: with tools and without reasoning, because GPT-6
    # refuses the two together on /chat/completions (see `complete_chat`). What
    # it writes instead of a tool call is thrown away: it was produced without
    # reasoning, and the answer is phase two's job.
    lookup = [
        {"role": "system", "content": system + LOOKUP_INSTRUCTION},
        *conversation,
    ]
    lookups: list[dict[str, Any]] = []
    for _ in range(MAX_TOOL_ROUNDS):
        response = call(
            lookup, tools=TOOLS, reasoning_effort=LOOKUP_EFFORT,
            max_tokens=LOOKUP_MAX_TOKENS,
        )
        if not response.tool_calls:
            break
        lookup.append({
            "role": "assistant",
            "content": response.content or None,
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {"name": tool_call.name, "arguments": tool_call.arguments},
                }
                for tool_call in response.tool_calls
            ],
        })
        for tool_call in response.tool_calls:
            output = run_tool(db, portfolio_id, settings, tool_call)
            arguments = _arguments(tool_call)
            result.tools.append({"name": tool_call.name, "arguments": arguments})
            lookups.append({"herramienta": tool_call.name, "argumentos": arguments,
                            "resultado": output})
            lookup.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(output, ensure_ascii=False, default=str),
            })

    # Phase two, the answer: the profile's own reasoning, no tools, and what was
    # looked up folded into the context as data. Folded rather than replayed as
    # tool messages, so the call is plain text any provider takes.
    if lookups:
        system += "\n\n## consultas_hechas_para_esta_pregunta\n" + json.dumps(
            lookups, ensure_ascii=False, default=str
        )
    response = call([{"role": "system", "content": system}, *conversation])
    if not response.content:
        raise LLMError(f"El modelo {llm.model} no llegó a contestar.")
    result.content = response.content
    return result


def _arguments(call: ToolCall) -> dict[str, Any]:
    try:
        parsed = json.loads(call.arguments or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def run_tool(
    db: Database, portfolio_id: str, settings: Settings, call: ToolCall
) -> dict[str, Any]:
    """Runs one tool. A bad call comes back as an error the model can read and
    correct, instead of aborting the answer."""
    args = _arguments(call)
    try:
        if call.name == "search_decisions":
            return {"decisions": _search_decisions(db, portfolio_id, **_only(
                args, "symbol", "action", "date", "limit"))}
        if call.name == "decision_data":
            detail = _decision_data(db, portfolio_id, str(args.get("decision_id") or ""))
            return detail or {"error": "No existe esa decisión en este experimento."}
        if call.name == "positions":
            return {"positions": _positions(db, portfolio_id, str(args.get("status") or "open"))}
        if call.name == "quotes":
            symbols = [str(s).upper() for s in args.get("symbols") or []]
            market = market_calendar.get_market(settings.market)
            return {"quotes": [
                row for row in _quotes(db, symbols or list(settings.watchlist))
                # With no watchlist every live quote comes back, other markets'
                # included; a profile covers one exchange (D8).
                if market.owns_symbol(row["symbol"])
            ]}
        if call.name == "cycles":
            return {"cycles": _cycles(db, portfolio_id, _limit(args.get("limit"), 10))}
        if call.name == "news":
            return {"news": _news(db, portfolio_id, **_only(args, "symbol", "cycle_id"))}
    except (TypeError, ValueError) as exc:
        return {"error": f"Argumentos no válidos: {exc}"}
    return {"error": f"No existe la herramienta «{call.name}»."}


def _only(args: dict[str, Any], *names: str) -> dict[str, Any]:
    return {name: args[name] for name in names if args.get(name) not in (None, "")}


def _limit(value: Any, default: int) -> int:
    try:
        return max(1, min(int(value), MAX_TOOL_ROWS))
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------
# Context
# ----------------------------------------------------------------------

def build_context(
    db: Database, portfolio_id: str, settings: Settings, focus_ids: list[str]
) -> str:
    """The part of the experiment that travels in every call, as JSON blocks.

    JSON and not prose: the model reads it well, and every figure keeps the
    exact value the screens show instead of a rounded paraphrase.
    """
    market = market_calendar.get_market(settings.market)
    last_cycle = _latest_cycle(db, portfolio_id)
    blocks: dict[str, Any] = {
        "experimento": {
            "nombre": settings.portfolio_name,
            "modelo": settings.llm_model,
            "mercado": market.label,
            "divisa": market.currency,
            "presupuesto_inicial": settings.initial_budget,
            "horizonte_dias": settings.horizon_days,
            "reglas_de_riesgo": settings.risk_summary,
        },
        "cartera": _book(db, portfolio_id),
        "posiciones_abiertas": _positions(db, portfolio_id, "open"),
    }
    if last_cycle is None:
        blocks["ultimo_ciclo"] = "Todavía no ha corrido ningún ciclo."
    else:
        blocks["ultimo_ciclo"] = {
            **last_cycle,
            "decisiones": _search_decisions(
                db, portfolio_id, cycle_id=last_cycle["id"], limit=100
            ),
            "titulares_de_mercado": _news(db, portfolio_id, cycle_id=last_cycle["id"]),
        }
    focus = [
        detail
        for detail in (_decision_data(db, portfolio_id, d) for d in focus_ids[-3:])
        if detail
    ]
    if focus:
        blocks["decisiones_por_las_que_se_pregunta"] = focus
    return "\n\n".join(
        f"## {name}\n{json.dumps(value, ensure_ascii=False, default=str)}"
        for name, value in blocks.items()
    )


def _latest_cycle(db: Database, portfolio_id: str) -> dict[str, Any] | None:
    rows = _cycles(db, portfolio_id, 1)
    return rows[0] if rows else None


def _cycles(db: Database, portfolio_id: str, limit: int) -> list[dict[str, Any]]:
    rows = db.query(
        "select c.id, c.started_at, c.status, c.market_open, c.equity_start, "
        "       c.equity_end, c.analyst_calls, c.analyst_failures, "
        "       (select count(1) from decisions d where d.cycle_id = c.id) as decisions, "
        "       (select count(1) from risk_events r where r.cycle_id = c.id "
        "               and r.verdict = 'approved') as approved, "
        "       (select count(1) from risk_events r where r.cycle_id = c.id "
        "               and r.verdict = 'rejected') as rejected "
        "from cycles c where c.portfolio_id = ? order by c.started_at desc limit ?",
        (portfolio_id, limit),
    )
    for row in rows:
        row["market_open"] = None if row["market_open"] is None else bool(row["market_open"])
    return rows


def _book(db: Database, portfolio_id: str) -> dict[str, Any] | None:
    rows = db.query(
        "select as_of, equity, cash, positions_value, open_positions, day_pnl_pct "
        "from equity_snapshots where portfolio_id = ? order by as_of desc limit 1",
        (portfolio_id,),
    )
    return rows[0] if rows else None


_DECISION_COLUMNS = (
    "select d.id, substr(d.created_at, 1, 10) as date, d.cycle_id, d.symbol, d.kind, "
    "       d.action, d.conviction, d.reference_price, d.suggested_stop, "
    "       d.suggested_target, d.suggested_weight_pct, d.thesis, d.risks, "
    "       r.verdict, r.rule, r.reason as risk_reason "
    "from decisions d left join risk_events r on r.decision_id = d.id "
)


def _search_decisions(
    db: Database, portfolio_id: str, *, symbol: str = "", action: str = "",
    date: str = "", cycle_id: str = "", limit: Any = 10,
) -> list[dict[str, Any]]:
    where = ["d.portfolio_id = ?"]
    params: list[Any] = [portfolio_id]
    if symbol:
        where.append("d.symbol = ?")
        params.append(str(symbol).upper())
    if action:
        where.append("d.action = ?")
        params.append(str(action))
    if date:
        where.append("substr(d.created_at, 1, 10) = ?")
        params.append(str(date)[:10])
    if cycle_id:
        where.append("d.cycle_id = ?")
        params.append(str(cycle_id))
    ceiling = 100 if cycle_id else _limit(limit, 10)
    return db.query(
        f"{_DECISION_COLUMNS} where {' and '.join(where)} "
        "order by d.created_at desc limit ?",
        (*params, ceiling),
    )


def _decision_data(db: Database, portfolio_id: str, decision_id: str) -> dict[str, Any] | None:
    if not decision_id:
        return None
    rows = db.query(
        f"{_DECISION_COLUMNS} where d.portfolio_id = ? and d.id = ?",
        (portfolio_id, decision_id),
    )
    if not rows:
        return None
    detail = rows[0]
    snapshot = db.query(
        "select s.price, s.as_of, s.indicators_json from decisions d "
        "join market_snapshots s on s.id = d.snapshot_id where d.id = ?",
        (decision_id,),
    )
    if snapshot:
        detail["price_seen"] = snapshot[0]["price"]
        detail["price_as_of"] = snapshot[0]["as_of"]
        detail["indicators"] = json.loads(snapshot[0]["indicators_json"] or "{}")
    detail["news_seen"] = _news(
        db, portfolio_id, symbol=detail["symbol"], cycle_id=detail["cycle_id"]
    )
    return detail


def _positions(db: Database, portfolio_id: str, status: str) -> list[dict[str, Any]]:
    status = "closed" if status == "closed" else "open"
    rows = db.query(
        "select symbol, qty, entry_price, stop_price, target_price, opened_at, "
        "       closed_at, exit_price, realized_pnl, exit_reason, thesis "
        "from positions where portfolio_id = ? and status = ? "
        "order by coalesce(closed_at, opened_at) desc limit ?",
        (portfolio_id, status, MAX_TOOL_ROWS),
    )
    if status == "open" and rows:
        live = db.latest_quotes([row["symbol"] for row in rows])
        for row in rows:
            quote = live.get(row["symbol"])
            row["last_price"] = quote["price"] if quote else None
            row["last_price_as_of"] = quote["as_of"] if quote else None
    return rows


def _quotes(db: Database, symbols: list[str]) -> list[dict[str, Any]]:
    return [
        {key: row[key] for key in ("symbol", "price", "prev_close", "change_pct", "as_of")}
        for row in sorted(db.latest_quotes(symbols or None).values(), key=lambda r: r["symbol"])
    ]


def _news(
    db: Database, portfolio_id: str, *, symbol: str = "", cycle_id: str = ""
) -> list[dict[str, Any]]:
    if not cycle_id:
        latest = _latest_cycle(db, portfolio_id)
        if latest is None:
            return []
        cycle_id = latest["id"]
    # The cycle has to be this experiment's: a cycle id from another profile
    # would otherwise read that profile's headlines.
    owned = db.query(
        "select 1 from cycles where id = ? and portfolio_id = ?", (cycle_id, portfolio_id)
    )
    if not owned:
        return []
    if symbol:
        clause, params = "symbol = ?", (cycle_id, str(symbol).upper())
    else:
        clause, params = "symbol is null", (cycle_id,)
    return db.query(
        "select ref, title, source, published_at from news_items "
        f"where cycle_id = ? and {clause} order by ref limit ?",
        (*params, MAX_TOOL_ROWS),
    )
