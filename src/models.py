"""Tipos de dominio compartidos entre las capas del agente.

Viven en su propio modulo para que `risk` no tenga que importar `broker` ni
`analyst`: el Risk Manager debe ser testeable sin red ni credenciales.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Action = Literal["buy", "sell", "hold"]
Kind = Literal["entry", "exit"]


@dataclass(frozen=True)
class BrokerPosition:
    """Posicion tal como la reporta el broker. Es la fuente de verdad."""

    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float


@dataclass(frozen=True)
class AccountState:
    """Foto de la cuenta al inicio del ciclo."""

    equity: float
    cash: float
    buying_power: float
    last_equity: float
    positions: tuple[BrokerPosition, ...] = ()

    @property
    def positions_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def open_symbols(self) -> set[str]:
        return {p.symbol for p in self.positions}

    @property
    def day_pnl(self) -> float:
        """P&L de la sesion. `last_equity` es el equity al cierre anterior."""
        return self.equity - self.last_equity

    @property
    def day_pnl_pct(self) -> float:
        if self.last_equity <= 0:
            return 0.0
        return self.day_pnl / self.last_equity * 100.0

    def position_for(self, symbol: str) -> BrokerPosition | None:
        for position in self.positions:
            if position.symbol == symbol:
                return position
        return None


@dataclass(frozen=True)
class Proposal:
    """Propuesta del analista LLM. Todavia NO es una orden.

    Ningun campo de aqui llega al broker sin pasar por el Risk Manager.
    """

    symbol: str
    kind: Kind
    action: Action
    conviction: int
    thesis: str
    risks: str = ""
    horizon_days: int | None = None
    suggested_stop: float | None = None
    suggested_target: float | None = None
    reference_price: float = 0.0
    # Metadatos de la llamada al modelo, para auditoria.
    model: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskVerdict:
    """Resultado del Risk Manager. `approved=False` significa que no se envia
    nada al broker, y el motivo queda registrado en `risk_events`."""

    approved: bool
    reason: str
    rule: str | None = None
    qty: float = 0.0
    notional: float = 0.0
    stop_price: float | None = None
    target_price: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExitSignal:
    """Orden de cierre. `forced=True` cuando la dispara una regla determinista
    (stop o target alcanzado) y por tanto el LLM no puede vetarla."""

    symbol: str
    qty: float
    reason: str
    rule: str
    forced: bool
    price: float


@dataclass(frozen=True)
class MarketSnapshot:
    """Datos de mercado de un simbolo en el momento del analisis.

    Los tres precios son distintos a proposito, y la distincion es lo que evita
    el sesgo de anticipacion:

      * `price`  -> cierre de la ultima sesion COMPLETA. Es lo unico que ven el
                    analista y el Risk Manager, y sobre lo que se dimensiona.
      * `fill_price` -> apertura de la sesion siguiente, donde se ejecuta. Un
                    sistema real que decide con el cierre de ayer se ejecuta con
                    la apertura de hoy; usar el mismo cierre para decidir y para
                    ejecutar regalaria el hueco de la noche y falsearia todo.
      * `mark_price` -> ultimo precio conocido, solo para valorar la cartera.
    """

    symbol: str
    as_of: datetime
    price: float
    indicators: dict[str, Any]
    recent_bars: list[dict[str, Any]] = field(default_factory=list)
    snapshot_id: int | None = None
    fill_price: float | None = None
    mark_price: float | None = None
    fill_basis: str = "close"
    session: str | None = None

    @property
    def execution_price(self) -> float:
        """Precio al que se ejecutaria una orden ahora mismo."""
        return self.fill_price if self.fill_price else self.price

    @property
    def valuation_price(self) -> float:
        """Precio para valorar una posicion abierta."""
        return self.mark_price if self.mark_price else self.price
