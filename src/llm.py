"""Model client, multi-provider (F6.6).

Two providers behind the same interface: **NVIDIA NIM** (the default, free tier)
and **OpenAI**. Both expose `/chat/completions` with the same request and
response format, so the difference between them is literally the base URL and the
key: there are not two implementations, there is one with a table of providers.
That is why httpx is enough and the project still drags in no SDK.

**Anthropic is deliberately not here.** Its API has another shape —`/v1/messages`,
different headers, the system prompt outside `messages`,
`input_tokens`/`output_tokens` instead of `prompt_tokens`/`completion_tokens`— and
its documentation asks you to use the official SDK rather than speak HTTP by
hand. That is a new dependency and a genuine second implementation, not a row in
a table; it is left for F9.1, when there is a reason to pay for a premium model.

**The response is read as a stream, and it is worth being clear about what that
does and does not buy** (F9.22), because it was adopted chasing a failure it
turned out not to fix. The «Server disconnected without sending a response» of
the 2026-08-12 was NVIDIA's `llama-3.3-70b` endpoint taking requests and never
dispatching them —measured: 61 s to the drop with 16 tokens and with 1.600,
streaming and not, while `llama-3.1-70b` answered in 4,7 s on the same key— so
no client could have saved it.

What streaming is kept for is the other half. With `stream: false` the whole
call has to fit in the timeout, and the sample of the 2026-08-11 has 8 of 54
generations over 60 s with a ceiling of 120. Reading the answer as it is
produced turns the timeout into something better shaped:

⚠️ it stops being «how long the whole call may take» and becomes «how long the
server may stay silent». A slow generation no longer trips it; a hung one still
does, which is what it was for.

The chunks are reassembled here and the rest of the module does not know the
difference.

The module assumes the worst of the model and tolerates it:

  * Reasoning models (deepseek-r1, nemotron) write their chain of thought in
    `<think>...</think>` before the JSON.
  * Almost any model wraps the JSON in ```json ... ``` at some point.
  * `response_format` and `stream_options` are extras that not every
    OpenAI-compatible deployment takes, so they are attempted and switched off
    for the rest of the session if the server rejects them.
  * The free tier returns 429 often: retries with exponential backoff, honouring
    `Retry-After` when it comes.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)
_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LLMError(RuntimeError):
    """La llamada al modelo fallo o devolvio algo inutilizable."""


@dataclass(frozen=True)
class Provider:
    """What tells one provider from another. Nothing more."""

    name: str
    label: str            # for the error messages, which a person reads
    default_base_url: str


PROVIDERS: dict[str, Provider] = {
    "nvidia": Provider(
        "nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1"
    ),
    "openai": Provider("openai", "OpenAI", "https://api.openai.com/v1"),
}

# Providers the `agent_settings.llm_provider` column admits but that are not
# implemented yet. They are named so the failure says "not yet", which is the
# truth, instead of "unknown provider", which confuses.
PLANNED_PROVIDERS = {
    "anthropic": (
        "El proveedor 'anthropic' no esta implementado todavia (queda en F9.1). "
        "Su API tiene otra forma que la de NIM y OpenAI y necesita su SDK "
        "oficial, que hoy no es una dependencia del proyecto. "
        f"Proveedores disponibles: {', '.join(sorted(PROVIDERS))}."
    ),
}


def resolve_provider(name: str) -> Provider:
    """Returns the provider, or fails with a message that tells the cases apart."""
    key = (name or "nvidia").strip().lower()
    provider = PROVIDERS.get(key)
    if provider is not None:
        return provider
    if key in PLANNED_PROVIDERS:
        raise LLMError(PLANNED_PROVIDERS[key])
    raise LLMError(
        f"Proveedor de modelo desconocido: {name!r}. "
        f"Validos: {', '.join(sorted(PROVIDERS))}."
    )


@dataclass
class LLMResponse:
    content: str
    parsed: dict[str, Any] | None
    model: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        provider: str = "nvidia",
        base_url: str = "",
        temperature: float = 0.2,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        """An empty `base_url` = the provider's. It can be set to point at a proxy
        or at an OpenAI-compatible deployment of your own."""
        self.provider = resolve_provider(provider)
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self._supports_json_mode = True
        self._supports_usage_in_stream = True
        if not api_key:
            raise LLMError(
                f"Falta la clave de API de {self.provider.label}. "
                "Ponla en el perfil (llm_api_key) o, para NVIDIA NIM, en "
                "NVIDIA_API_KEY."
            )
        self._client = httpx.Client(
            base_url=(base_url or self.provider.default_base_url).rstrip("/"),
            timeout=httpx.Timeout(timeout),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LLMClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1600,
    ) -> LLMResponse:
        """Asks for a response and demands that it contain a JSON object.

        Raises `LLMError` if no parsable JSON is obtained after the retries: we
        would rather skip the symbol than trade on a guess.
        """
        response = self._post_chat(system=system, user=user, max_tokens=max_tokens)
        if response.parsed is None:
            raise LLMError(
                f"El modelo {self.model} no devolvio JSON valido. "
                f"Respuesta (primeros 400 caracteres): {response.content[:400]!r}"
            )
        return response

    def _post_chat(self, *, system: str, user: str, max_tokens: int) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        last_error: Exception | None = None
        # The counter is advanced by hand because negotiating the optional
        # fields —see `_disable_rejected_option`— resends at once and must NOT
        # spend an attempt: with two negotiable extras it could otherwise eat
        # two of the three tries before the model is even asked anything.
        attempt = 0
        while attempt < self.max_retries:
            body = dict(payload)
            if self._supports_json_mode:
                body["response_format"] = {"type": "json_object"}
            if self._supports_usage_in_stream:
                # Without this the final chunk brings no `usage` and the tokens
                # of every decision would be written as zero.
                body["stream_options"] = {"include_usage": True}

            started = time.monotonic()
            status = 0
            error_body = ""
            retry_after: float | None = None
            stream = _Stream()
            try:
                with self._client.stream(
                    "POST", "/chat/completions", json=body
                ) as http_response:
                    if http_response.status_code >= 400:
                        # An error is not sent as SSE: it is a whole JSON body,
                        # and with `stream()` it has to be read before it can be
                        # looked at.
                        http_response.read()
                        status = http_response.status_code
                        error_body = http_response.text
                        retry_after = _parse_retry_after(
                            http_response.headers.get("Retry-After")
                        )
                    else:
                        stream = _read_sse(http_response.iter_lines())
            except httpx.HTTPError as exc:
                attempt += 1
                last_error = exc
                log.warning("Error de red hablando con %s (intento %d/%d): %s",
                            self.provider.label, attempt, self.max_retries, exc)
                self._sleep_backoff(attempt)
                continue

            latency_ms = int((time.monotonic() - started) * 1000)

            if status in (400, 422):
                disabled = self._disable_rejected_option(error_body)
                if disabled is not None:
                    log.info(
                        "El modelo %s rechazo %s (%d); se desactiva y se reintenta al momento.",
                        self.model, disabled, status,
                    )
                    continue

            if status:
                if status == 429 or status >= 500:
                    attempt += 1
                    last_error = LLMError(
                        f"{self.provider.label} devolvio {status}: {error_body[:200]}"
                    )
                    log.warning(
                        "%s devolvio %d (intento %d/%d).",
                        self.provider.label, status, attempt, self.max_retries,
                    )
                    self._sleep_backoff(attempt, override=retry_after)
                    continue

                # 401/403/404 no se arreglan reintentando.
                raise LLMError(
                    f"{self.provider.label} devolvio {status}: {error_body[:400]}"
                )

            # A stream that breaks halfway —an `error` event, or a 200 that
            # carries nothing— is a transport failure, not an answer, so it is
            # retried instead of being handed upstairs as an empty response.
            if stream.error or not stream.text:
                attempt += 1
                last_error = LLMError(
                    f"{self.provider.label} corto la respuesta a media generacion: "
                    f"{stream.error or 'no llego ningun fragmento'}"
                )
                log.warning(
                    "%s corto el stream tras %d ms (intento %d/%d): %s",
                    self.provider.label, latency_ms, attempt, self.max_retries,
                    stream.error or "sin fragmentos",
                )
                self._sleep_backoff(attempt)
                continue

            return LLMResponse(
                content=stream.text,
                parsed=extract_json_object(stream.text),
                model=stream.model or self.model,
                latency_ms=latency_ms,
                prompt_tokens=int(stream.usage.get("prompt_tokens") or 0),
                completion_tokens=int(stream.usage.get("completion_tokens") or 0),
                # There is no single server payload to keep any more: what goes
                # here is what was reassembled, which is what one would want to
                # look at while debugging.
                raw={"model": stream.model, "usage": stream.usage},
            )

        raise LLMError(
            f"No se pudo completar la llamada a {self.provider.label} tras "
            f"{self.max_retries} intentos: {last_error}"
        )

    def _disable_rejected_option(self, error_body: str) -> str | None:
        """Switches off, for the rest of the session, the optional field the
        server has just rejected. Returns its name, or None if there is nothing
        left to turn off —and then the 400 is a real error, not a negotiation.

        `response_format` and `stream_options` are extras on top of
        `/chat/completions` and not every deployment takes them. The error body
        usually names the offending one; when it does not, they are dropped in
        order, which costs a round trip and no attempt.
        """
        lowered = error_body.lower()
        named = [n for n in ("response_format", "stream_options") if n in lowered]
        for name in named or ["response_format", "stream_options"]:
            if name == "response_format" and self._supports_json_mode:
                self._supports_json_mode = False
                return name
            if name == "stream_options" and self._supports_usage_in_stream:
                self._supports_usage_in_stream = False
                return name
        return None

    def _sleep_backoff(self, attempt: int, *, override: float | None = None) -> None:
        delay = override if override is not None else min(2.0 ** attempt, 30.0)
        log.debug("Esperando %.1fs antes de reintentar.", delay)
        time.sleep(delay)


# ----------------------------------------------------------------------
# Lectura del stream
# ----------------------------------------------------------------------

@dataclass
class _Stream:
    """An SSE response, already reassembled. `error` empty = it arrived whole."""

    content: str = ""
    reasoning: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def text(self) -> str:
        # Reasoning models sometimes fill only `reasoning_content`, and then
        # that is the whole answer, JSON included.
        return self.content or self.reasoning


def _read_sse(lines: Iterable[str]) -> _Stream:
    """Reassembles the `data:` events of a `/chat/completions` in streaming.

    Tolerant on purpose, because this runs against a free tier: a malformed
    chunk is skipped rather than losing the response that was already
    accumulated, and the ending is not required to be the canonical `[DONE]` —
    the stream running out is ending enough.
    """
    stream = _Stream()
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("data:"):
            # Comments (`: keep-alive`) and the blank lines separating events.
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue

        error = chunk.get("error")
        if error:
            # Some servers report an overload mid-generation instead of at the
            # start. Whatever was accumulated is kept: the caller decides.
            stream.error = str(error)[:200]
            break

        stream.model = stream.model or str(chunk.get("model") or "")
        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage:
            # It comes in the last chunk, the one with an empty `choices`.
            stream.usage = usage

        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or choice.get("message") or {}
            if not isinstance(delta, dict):
                continue
            stream.content += _delta_text(delta.get("content"))
            stream.reasoning += _delta_text(delta.get("reasoning_content"))
    return stream


def _delta_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    # Algunos modelos mandan una lista de bloques tipo {"type":"text",...}.
    if isinstance(value, list):
        return "".join(
            block.get("text", "")
            for block in value
            if isinstance(block, dict) and block.get("type") in (None, "text")
        )
    return ""


# ----------------------------------------------------------------------
# Parseo defensivo
# ----------------------------------------------------------------------


def strip_reasoning(text: str) -> str:
    """Strips the <think> blocks, including the unclosed case (which happens when
    the response is cut off by max_tokens)."""
    cleaned = _THINK_BLOCK.sub("", text)
    if "<think>" in cleaned.lower():
        cleaned = _UNCLOSED_THINK.sub("", cleaned)
    return cleaned.strip()


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Pulls the first JSON object out of a response that may arrive dirty.

    A cascading strategy: clean text -> code block -> first object with balanced
    braces. Returns None if nothing parses.
    """
    if not text:
        return None

    candidates: list[str] = []
    cleaned = strip_reasoning(text)
    candidates.append(cleaned)

    for match in _CODE_FENCE.finditer(cleaned):
        candidates.append(match.group(1))

    balanced = _first_balanced_object(cleaned)
    if balanced:
        candidates.append(balanced)

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _first_balanced_object(text: str) -> str | None:
    """First `{...}` with balanced braces, ignoring the ones inside JSON strings
    (and honouring escapes)."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, min(float(value), 60.0))
    except ValueError:
        return None
