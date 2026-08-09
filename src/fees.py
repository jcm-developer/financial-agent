"""Broker commissions, by exchange.

A simulation is worth what its friction is worth. Until now the commission was a
single number per profile with a default of zero, and `sim_broker`'s docstring
said why: American brokers do not charge for shares. D8 moved the experiment to
Europe and that default quietly became a lie -- a very aggressive profile with
eight cycles a day would have been trading for free, and free trading flatters
exactly the behaviour the experiment is trying to measure.

The tariff below is the one the experiment actually runs against: a Spanish
retail bank, per order and per leg.

  * **Spanish shares (Madrid, `.MC`): 4,11 EUR.** It is the dearer of the two,
    which reads backwards until you remember it carries the BME fee on top of
    the flat rate.
  * **The rest of the European exchanges: 3,00 EUR.**
  * **The United States: nothing.** That is the old default, and for shares it
    is still true.

**Why this lives in code and not in the profile.** Everything that defines an
experiment lives in `agent_settings` (F6), but this does not define an
experiment: it is a fact about the bank, and the same fact for every profile
that trades that exchange. Putting it in the profile would invite five profiles
with five different tariffs, and then a comparison between them would be
measuring the tariff instead of the strategy. What stays in the profile is
`sim_commission`, which is now a **surcharge on top** of this tariff: zero means
"the bank's standard", and anything else is a deliberate deviation for a
what-if.

**The tariff is per leg, so a round trip costs double**, and that is the number
that matters when reading a result: 8,22 EUR on a Spanish name, 6,00 EUR on the
rest. Against `MIN_ORDER_NOTIONAL` of 100 EUR that is 6-8 % of friction, which
is deliberate and written down in TASKS.md -- not an oversight to be found later
in the P&L.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .market_calendar import Market, get_market

#: Standard tariff, per order and per leg, by the exchange suffix Yahoo uses.
#: Every suffix of every market in `MARKETS` must appear here; `test_fees.py`
#: checks it, so adding an exchange without deciding what it costs fails loudly
#: instead of trading it for free.
COMMISSION_BY_SUFFIX: Mapping[str, float] = MappingProxyType({
    ".MC": 4.11,   # Madrid (BME): flat rate plus the exchange fee
    ".PA": 3.00,   # Paris
    ".DE": 3.00,   # Xetra
    ".AS": 3.00,   # Amsterdam
    ".MI": 3.00,   # Milan
    ".BR": 3.00,   # Brussels
    ".HE": 3.00,   # Helsinki
})

#: What a symbol with no suffix costs. Yahoo leaves the American ones bare
#: (`AAPL`, `BRK-B`), and for shares that broker charges nothing.
NO_SUFFIX_COMMISSION = 0.0


def standard_commission(symbol: str) -> float:
    """The bank's tariff for one order on `symbol`, per leg.

    A symbol carrying a suffix nobody has priced raises instead of falling back
    to zero. It cannot happen through the normal route -- `resolve_settings`
    rejects a universe that mixes exchanges, so the universe file has already
    been validated by the time an order exists -- and that is the point: if it
    ever does happen, a loud failure is far cheaper than a histogram of trades
    that silently paid no commission.
    """
    clean = symbol.strip().upper()
    for suffix, amount in COMMISSION_BY_SUFFIX.items():
        if clean.endswith(suffix):
            return amount
    if "." in clean:
        raise KeyError(
            f"No hay tarifa de comision para {symbol}: su sufijo de bolsa no esta "
            f"en COMMISSION_BY_SUFFIX. Decide lo que cuesta antes de operarlo, "
            f"porque el valor por defecto seria operar gratis."
        )
    return NO_SUFFIX_COMMISSION


def tariffs_for_market(market: str | Market | None = None) -> dict[float, list[str]]:
    """The tariffs that apply to a market, grouped by amount.

    Grouped and not one entry per exchange because that is how it gets shown:
    `run.py check` prints "4,11 (.MC) / 3,00 (el resto)", and a market with a
    single tariff prints a single number.
    """
    mkt = get_market(market)
    if not mkt.symbol_suffixes:
        return {NO_SUFFIX_COMMISSION: []}
    grouped: dict[float, list[str]] = {}
    for suffix in sorted(mkt.symbol_suffixes):
        grouped.setdefault(COMMISSION_BY_SUFFIX[suffix], []).append(suffix)
    return grouped
