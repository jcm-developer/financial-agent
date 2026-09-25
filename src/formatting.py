"""Numbers as the interface writes them, for text the backend puts on screen.

Risk verdicts, cycle summaries and the live log are read on screen next to
figures the frontend formats with `Intl.NumberFormat("es-ES")`. If the backend
writes `€3,949.20` and the table beside it `3949,20 €`, the same amount reads
two ways on one page — and the comma means opposite things in each. So these
functions copy the frontend's rules exactly (`app/src/lib/format.ts`):

  * decimal comma, and the thousands point **only from five digits up** —
    `1234,50` but `12.345,50`—, which is what `es-ES` does (CLDR's minimum
    grouping digits is 2);
  * the currency symbol **after** the amount, with a space;
  * the percent sign **glued** to the number, as the frontend writes it;
  * a real minus sign (`−`), not a hyphen, and no sign on a figure that prints
    as zero.

Only for screen text. Prompts to the model and machine-read values (JSON, SQL)
keep Python's own formatting: the model reads the prompt's numbers as data, and
changing their shape would change the experiment.
"""

from __future__ import annotations

MINUS = "−"


def number(value: float, decimals: int = 2) -> str:
    """`1234,5` → `1234,50`; `12345.5` → `12.345,50`."""
    rounded = round(float(value), decimals)
    if rounded == 0:
        rounded = 0.0  # no "−0,00"
    sign = MINUS if rounded < 0 else ""
    text = f"{abs(rounded):.{decimals}f}"
    whole, _, fraction = text.partition(".")
    if len(whole) >= 5:
        groups = []
        while len(whole) > 3:
            groups.insert(0, whole[-3:])
            whole = whole[:-3]
        groups.insert(0, whole)
        whole = ".".join(groups)
    return f"{sign}{whole},{fraction}" if fraction else f"{sign}{whole}"


def compact(value: float, decimals: int = 2) -> str:
    """Like `number`, without trailing zeros: `7,94`, `1,5`, `50`."""
    text = number(value, decimals)
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text


def money(value: float, symbol: str, decimals: int = 2) -> str:
    """`3949.2, "€"` → `3949,20 €`. An empty symbol writes the bare figure."""
    figure = number(value, decimals)
    return f"{figure} {symbol}" if symbol else figure


def signed_money(value: float, symbol: str, decimals: int = 2) -> str:
    """`+12,50 €`, `−3,00 €`, or `0,00 €` with no sign."""
    figure = money(abs(value), symbol, decimals)
    if round(value, decimals) == 0:
        return figure
    return f"{'+' if value > 0 else MINUS}{figure}"


def percent(value: float, decimals: int = 2, *, signed: bool = False) -> str:
    """`5` → `5%` (trailing zeros dropped); `signed=True` → `+1,25%`, `−0,40%`."""
    figure = compact(abs(value), decimals) if not signed else number(abs(value), decimals)
    if signed and round(value, decimals) != 0:
        return f"{'+' if value > 0 else MINUS}{figure}%"
    if not signed and round(value, decimals) < 0:
        return f"{MINUS}{figure}%"
    return f"{figure}%"


def utf8_console() -> None:
    """Makes stdout and stderr write UTF-8, whatever the console's code page.

    Screen text carries accents, `σ` and the minus sign `−`. The Windows console
    defaults to cp1252, which has the accents but neither of the other two, and a
    log line it cannot encode is printed as a "--- Logging error ---" traceback
    instead of the line. That is why the backend wrote ASCII for months; fixing
    the console once at each entry point is cheaper than writing badly
    everywhere. In Docker the console is already UTF-8 and this changes nothing.
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
