"""Headlines for the analyst (F9.4): per company and for the market as a whole.

**Why this exists, and why it is built the way it is.** Until 2026-09-25 the
system was 100 % technical and the prompt forbade the model to mention news at
all. That rule is what made the experiment readable: a catalyst in a thesis was a
hallucination, and it showed. Letting the model read news takes that guarantee
away unless something replaces it, and what replaces it is here:

  * **The system fetches the news, not the model.** The model does not search:
    it is handed a fixed list, the same one that gets recorded. A model that
    searches reads different things every cycle and chooses what to read, which
    invites it to look for what agrees with it.
  * **Every headline has an id** (`N1` for the company, `M1` for the market) and
    the model cites by id. An id that was not in its prompt is the one
    hallucination this system can still see.
  * **What was shown is stored** (`news_items`), with the ref it was shown by, so
    a thesis can be checked against its sources months later.

**Sources, measured on the 89 European symbols on 2026-09-25 (F9.7).** Yahoo's
`get_news` left 32 of them with nothing in a week —23 of the 35 in Madrid— and
what it did bring was mostly American aggregators. Google News RSS, searched by
company name in the language of its exchange, covered all 89 with local press.
So Google is the source and Yahoo is the fallback, used only when Google fails
or the symbol has no curated name — never when Google simply has nothing: an
empty week is information, and filling it with Zacks would be the bias F9.7
measured.

**Only headlines, never the article.** The prompt says so: the model knows the
title and the outlet, not whether the body supports it.

Nothing here decides anything. It fetches, filters and numbers.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

log = logging.getLogger(__name__)

#: Curated search names, one per symbol. See the file's header for the format.
NEWS_NAMES_FILE = "universe/news_names.txt"

GOOGLE_NEWS_SEARCH = "https://news.google.com/rss/search"

#: Language and country of each exchange's press, as Google News takes them.
#: Local press is the whole point: in Spanish, Acciona Energía had 46 headlines
#: in a week; Yahoo had none.
LOCALE_BY_SUFFIX: Mapping[str, tuple[str, str]] = {
    ".MC": ("es", "ES"),
    ".PA": ("fr", "FR"),
    ".DE": ("de", "DE"),
    ".AS": ("nl", "NL"),
    ".MI": ("it", "IT"),
    ".BR": ("fr", "BE"),
    ".HE": ("fi", "FI"),
}
#: For symbols with no suffix, which on Yahoo are the American ones.
DEFAULT_LOCALE = ("en", "US")

#: The searches each market's context is read from: language, country and query.
#:
#: **Searches and not the business front page**, measured on 2026-09-25: the
#: Spanish front page brought a bank buying a building and the British one a
#: haulage firm going under — true, and useless to judge a European stock. These
#: ask for what moves the market as a whole: the indices, the central bank, the
#: sovereign spread, and in English the wires' daily "European shares" story,
#: which is where a war or an oil shock shows up with its effect on prices.
#: English first because the wires cover the whole area; Spanish because Madrid
#: is 35 of the 89 symbols.
MARKET_QUERIES: Mapping[str, tuple[tuple[str, str, str], ...]] = {
    "eu": (
        ("en", "GB", '("European shares" OR "STOXX 600" OR "euro zone" OR ECB)'),
        ("es", "ES", '(Ibex OR "bolsas europeas" OR BCE OR "prima de riesgo")'),
    ),
    "us": (
        ("en", "US", '("Wall Street" OR "S&P 500" OR "Federal Reserve" OR Treasury yields)'),
    ),
}

#: How old market context may be, in days, at most. The company window is the
#: profile's (`news_max_age_days`, a week by default); the market's is shorter
#: because "what is happening" goes stale in a couple of sessions, and a week of
#: index stories would crowd out the last two days with the five before.
MARKET_MAX_AGE_DAYS = 2

#: Titles that are not news: quote pages, forums and price histories. Measured on
#: 2026-09-25 among the Google results: «VEOLIA Cours Action VIE, Cotation Bourse
#: Euronext Paris - Boursorama», «Foro Logista | Comentarios sobre LOG». Matched
#: on the normalised title (lower case, no accents). Deliberately narrow: «la
#: cotización se dispara» is news, «cotización e historial» is a page.
NOISE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"precio de (las )?acciones",
        r"cotizacion e historial",
        r"cotizacion en tiempo real",
        r"\bcours (de l'?)?action\b",
        r"\bcotation bourse\b",
        r"\baktienkurs\b",
        r"stock price, news",
        r"\bstock quote\b",
        r"^foro\b",
        r"\bforo (de|sobre)\b",
        r"\bcomentarios sobre\b",
        r"\bforum\b",
        r"\bhistorial de precios\b",
        # finanzen.net writes one of these per stock and per session: «Deutsche
        # Börse Aktie News: Deutsche Börse zieht am Mittag an». A price move the
        # indicators already carry, said in words.
        r"\baktie news:",
        # Warrant and turbo listings: «Achat du Turbo infini CALL VINCI»,
        # «Vinci DWRT 90C - Warrant». A product page, not news about the company.
        r"\bwarrant\b",
        r"\bturbo infini\b",
    )
)

#: Pause after each request to Google, in seconds. There is no published quota,
#: and a domestic IP asking for twenty feeds in a burst is the pattern that gets
#: throttled. Twenty candidates cost ten seconds, against a cycle of minutes.
REQUEST_PAUSE = 0.5

REQUEST_TIMEOUT = 15.0


# ----------------------------------------------------------------------
# Datos
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class SearchName:
    """How the press names one company."""

    query: str
    aliases: tuple[str, ...] = ()
    #: Language and country override, for a company whose press is not its
    #: exchange's. None = the exchange's.
    locale: tuple[str, str] | None = None

    @property
    def names(self) -> tuple[str, ...]:
        """What a title has to mention. A raw query (with quotes or operators) is
        not a name, so then only the aliases count."""
        if '"' in self.query or "(" in self.query:
            return self.aliases
        return (self.query, *self.aliases)


@dataclass(frozen=True)
class Headline:
    title: str
    source: str
    url: str
    #: None when the feed gave no date. Kept and shown as "fecha desconocida"
    #: rather than dropped: the model is told, and it is rare.
    published_at: datetime | None
    #: "google" or "yahoo", so the record says which source each came from.
    provider: str


@dataclass(frozen=True)
class NumberedHeadline:
    """A headline with the id the model sees and cites it by."""

    ref: str
    headline: Headline

    def as_row(self) -> dict[str, Any]:
        """What `Database.save_news_items` stores."""
        h = self.headline
        return {
            "ref": self.ref,
            "title": h.title,
            "source": h.source,
            "url": h.url,
            "published_at": h.published_at.isoformat() if h.published_at else None,
            "provider": h.provider,
        }


@dataclass(frozen=True)
class NewsResult:
    headlines: tuple[Headline, ...] = ()
    #: Why nothing could be fetched. **Not the same as no headlines**, and the
    #: prompt says which one it is: "there was no news" and "we could not look"
    #: lead to different judgements.
    error: str | None = None


@dataclass(frozen=True)
class NewsContext:
    """Everything one prompt is told about the news, already numbered."""

    company: tuple[NumberedHeadline, ...] = ()
    company_error: str | None = None
    market: tuple[NumberedHeadline, ...] = ()
    market_error: str | None = None
    max_age_days: int = 7

    @property
    def refs(self) -> frozenset[str]:
        return frozenset(item.ref for item in (*self.company, *self.market))


def number(headlines: Iterable[Headline], prefix: str) -> tuple[NumberedHeadline, ...]:
    """`N1`, `N2`... in the order given, which is newest first."""
    return tuple(
        NumberedHeadline(f"{prefix}{index}", headline)
        for index, headline in enumerate(headlines, start=1)
    )


# ----------------------------------------------------------------------
# Nombres curados
# ----------------------------------------------------------------------

def load_search_names(path: str | Path = NEWS_NAMES_FILE) -> dict[str, SearchName]:
    """Reads the curated names file. A missing file is an empty mapping.

    Missing and not an error because a profile of another market may have no
    curated names at all: its symbols fall back to Yahoo, and the cycle says so.
    """
    file = Path(path)
    if not file.is_file():
        log.warning("No hay fichero de nombres de noticias en %s.", file)
        return {}
    names: dict[str, SearchName] = {}
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError(f"{file}: linea sin simbolo o sin consulta: {raw!r}")
        aliases = tuple(a.strip() for a in parts[2].split(",") if a.strip()) if len(parts) > 2 else ()
        locale = None
        if len(parts) > 3 and parts[3]:
            language, _, country = parts[3].partition("-")
            locale = (language.lower(), (country or language).upper())
        names[parts[0].upper()] = SearchName(parts[1], aliases, locale)
    return names


# ----------------------------------------------------------------------
# Proveedor
# ----------------------------------------------------------------------

class NewsProvider:
    """Fetches, filters and orders headlines. Never raises for a network failure:
    it reports it in `NewsResult.error`, because a cycle must not die because a
    news feed did."""

    def __init__(
        self,
        *,
        market: str,
        names: Mapping[str, SearchName],
        max_items: int = 8,
        max_age_days: int = 7,
        fetch_text: Callable[[str], str] | None = None,
        fetch_yahoo: Callable[[str], list[dict[str, Any]]] | None = None,
        now: Callable[[], datetime] | None = None,
        pause: float = REQUEST_PAUSE,
    ) -> None:
        """`fetch_text`, `fetch_yahoo` and `now` are injectable so the tests can
        run without the network and on a fixed clock."""
        self.market = market
        self.names = dict(names)
        self.max_items = max_items
        self.max_age_days = max_age_days
        self._fetch_text = fetch_text or _http_get_text
        self._fetch_yahoo = fetch_yahoo or _yahoo_news
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pause = pause

    # -- Por empresa -------------------------------------------------------

    def company(self, symbol: str) -> NewsResult:
        name = self.names.get(symbol.upper())
        if name is None:
            return self._from_yahoo(symbol, reason="sin nombre curado")

        locale = name.locale or locale_for(symbol)
        url = google_search_url(name.query, locale, self.max_age_days)
        try:
            items = parse_google_rss(self._fetch_text(url))
        except Exception as exc:  # noqa: BLE001 - any failure of the feed is the same failure
            log.warning("Google News fallo para %s (%s); se prueba Yahoo.", symbol, exc)
            return self._from_yahoo(symbol, reason=f"Google News fallo: {exc}")
        finally:
            self._sleep()

        relevant = [h for h in items if mentions(h.title, name.names)]
        return NewsResult(self._keep(relevant))

    def _from_yahoo(self, symbol: str, *, reason: str) -> NewsResult:
        try:
            items = parse_yahoo_items(self._fetch_yahoo(symbol))
        except Exception as exc:  # noqa: BLE001
            log.warning("Tampoco Yahoo dio noticias de %s: %s", symbol, exc)
            return NewsResult(error=f"{reason}; Yahoo tambien fallo: {exc}")
        log.info("Noticias de %s desde Yahoo (%s).", symbol, reason)
        return NewsResult(self._keep(items))

    # -- De mercado --------------------------------------------------------

    def market_context(self) -> NewsResult:
        searches = MARKET_QUERIES.get(self.market, ())
        max_age = min(self.max_age_days, MARKET_MAX_AGE_DAYS)
        collected: list[Headline] = []
        errors: list[str] = []
        # Each search gets its share of the slots before they are merged. Sorted
        # by date alone, the Spanish close-of-session stories —six of them within
        # half an hour— pushed every wire story out of the list.
        share = max(1, -(-self.max_items // max(1, len(searches))))
        for language, country, query in searches:
            url = google_search_url(query, (language, country), max_age)
            try:
                found = parse_google_rss(self._fetch_text(url))
                collected += self._keep(found, max_age_days=max_age)[:share]
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{language}-{country}: {exc}")
                log.warning("Contexto de mercado %s-%s no disponible: %s", language, country, exc)
            finally:
                self._sleep()
        if errors and not collected:
            return NewsResult(error="; ".join(errors))
        return NewsResult(self._keep(collected, max_age_days=max_age))

    # -- Filtro comun ------------------------------------------------------

    def _keep(
        self, items: Iterable[Headline], *, max_age_days: int | None = None
    ) -> tuple[Headline, ...]:
        """Drops old, noisy and repeated headlines and keeps the newest N.

        A headline with no date survives the age filter —there is no telling—
        and sorts last.
        """
        cutoff = self._now() - timedelta(days=max_age_days or self.max_age_days)
        seen: set[str] = set()
        kept: list[Headline] = []
        for headline in items:
            if headline.published_at is not None and headline.published_at < cutoff:
                continue
            key = normalise(headline.title)
            if not key or key in seen or is_noise(key):
                continue
            seen.add(key)
            kept.append(headline)
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        kept.sort(key=lambda h: h.published_at or epoch, reverse=True)
        return tuple(kept[: self.max_items])

    def _sleep(self) -> None:
        if self._pause > 0:
            time.sleep(self._pause)


def build_news_provider(settings) -> NewsProvider | None:
    """The provider a profile's settings ask for, or None with news off."""
    if not getattr(settings, "news_enabled", False):
        return None
    return NewsProvider(
        market=settings.market,
        names=load_search_names(),
        max_items=settings.news_max_items,
        max_age_days=settings.news_max_age_days,
    )


# ----------------------------------------------------------------------
# URLs, parseo y filtros (puros, con tests)
# ----------------------------------------------------------------------

def locale_for(symbol: str) -> tuple[str, str]:
    for suffix, locale in LOCALE_BY_SUFFIX.items():
        if symbol.upper().endswith(suffix):
            return locale
    return DEFAULT_LOCALE


def google_search_url(query: str, locale: tuple[str, str], max_age_days: int) -> str:
    """The search, restricted to the last N days by Google itself.

    A plain name is quoted, so Google looks for the phrase. A query that already
    carries quotes or parentheses is taken as written: that is how an ambiguous
    name gets its context —«"Puig" (Ibex OR bolsa...)»— without the file needing
    a second field.
    """
    language, country = locale
    phrase = query if ('"' in query or "(" in query) else f'"{query}"'
    q = quote(f"{phrase} when:{max_age_days}d")
    return (
        f"{GOOGLE_NEWS_SEARCH}?q={q}&hl={language}&gl={country}"
        f"&ceid={country}:{language}"
    )


def parse_google_rss(xml_text: str) -> list[Headline]:
    """The items of a Google News RSS feed.

    Google writes the title as «Headline - Outlet» and also sends the outlet in
    `<source>`; the suffix is cut so the prompt does not name the outlet twice.
    """
    root = ElementTree.fromstring(xml_text)
    headlines: list[Headline] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        source = (item.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        headlines.append(
            Headline(
                title=title,
                source=source,
                url=(item.findtext("link") or "").strip(),
                published_at=_parse_rfc822(item.findtext("pubDate")),
                provider="google",
            )
        )
    return headlines


def parse_yahoo_items(items: list[dict[str, Any]]) -> list[Headline]:
    """`yfinance`'s `get_news()`, in the two shapes it has used.

    Since 0.2.5x the item is `{"content": {...}}` with an ISO `pubDate`; before,
    the fields sat at the top level with an epoch `providerPublishTime`. Both are
    read so a yfinance upgrade does not silently empty the fallback.
    """
    headlines: list[Headline] = []
    for raw in items or []:
        content = raw.get("content") if isinstance(raw.get("content"), dict) else raw
        title = str(content.get("title") or "").strip()
        if not title:
            continue
        provider = content.get("provider")
        source = (
            provider.get("displayName") if isinstance(provider, dict) else None
        ) or content.get("publisher") or ""
        link = content.get("canonicalUrl")
        url = (link.get("url") if isinstance(link, dict) else None) or content.get("link") or ""
        headlines.append(
            Headline(
                title=title,
                source=str(source),
                url=str(url),
                published_at=_parse_any_date(
                    content.get("pubDate") or content.get("providerPublishTime")
                ),
                provider="yahoo",
            )
        )
    return headlines


def normalise(text: str) -> str:
    """Lower case, no accents, single spaces: what the filters compare."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().split())


def is_noise(normalised_title: str) -> bool:
    return any(pattern.search(normalised_title) for pattern in NOISE_PATTERNS)


def mentions(title: str, names: Iterable[str]) -> bool:
    """Whether the title names the company by any of its names, as a whole word.

    A short all-caps name (ING, ACS, AXA) is compared case-sensitively: lower-
    cased, «ING» would match inside half the words of a Dutch headline.
    """
    folded = normalise(title)
    for name in names:
        if len(name) <= 4 and name.isupper():
            if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", title):
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(normalise(name))}(?![a-z0-9])", folded):
            return True
    return False


# ----------------------------------------------------------------------

def _http_get_text(url: str) -> str:
    response = httpx.get(
        url,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (financial-agent; news)"},
    )
    response.raise_for_status()
    return response.text


def _yahoo_news(symbol: str) -> list[dict[str, Any]]:
    import yfinance as yf

    return list(yf.Ticker(symbol).get_news(count=20) or [])


def _parse_rfc822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_any_date(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return _parse_rfc822(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


__all__ = [
    "Headline", "NewsContext", "NewsProvider", "NewsResult", "NumberedHeadline",
    "SearchName", "build_news_provider", "load_search_names", "number",
]
