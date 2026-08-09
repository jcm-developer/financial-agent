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

The module assumes the worst of the model and tolerates it:

  * Reasoning models (deepseek-r1, nemotron) write their chain of thought in
    `<think>...</think>` before the JSON.
  * Almost any model wraps the JSON in ```json ... ``` at some point.
  * `response_format` is not supported by every model, so it is attempted and
    switched off for the rest of the session if the server rejects it.
  * The free tier returns 429 often: retries with exponential backoff, honouring
    `Retry-After` when it comes.
"""

from __future__ import annotations

import json
import logging
import re
import time
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
            "stream": False,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            body = dict(payload)
            if self._supports_json_mode:
                body["response_format"] = {"type": "json_object"}

            started = time.monotonic()
            try:
                http_response = self._client.post("/chat/completions", json=body)
            except httpx.HTTPError as exc:
                last_error = exc
                log.warning("Error de red hablando con %s (intento %d/%d): %s",
                            self.provider.label, attempt, self.max_retries, exc)
                self._sleep_backoff(attempt)
                continue

            latency_ms = int((time.monotonic() - started) * 1000)

            if http_response.status_code in (400, 422) and self._supports_json_mode:
                # The model probably does not accept response_format: it is
                # dropped and retried at once without spending an attempt.
                log.info(
                    "El modelo %s rechazo response_format (%d); se desactiva el modo JSON.",
                    self.model, http_response.status_code,
                )
                self._supports_json_mode = False
                continue

            if http_response.status_code == 429 or http_response.status_code >= 500:
                last_error = LLMError(
                    f"{self.provider.label} devolvio {http_response.status_code}: "
                    f"{http_response.text[:200]}"
                )
                retry_after = _parse_retry_after(http_response.headers.get("Retry-After"))
                log.warning(
                    "%s devolvio %d (intento %d/%d).",
                    self.provider.label, http_response.status_code,
                    attempt, self.max_retries,
                )
                self._sleep_backoff(attempt, override=retry_after)
                continue

            if http_response.status_code >= 400:
                # 401/403/404 no se arreglan reintentando.
                raise LLMError(
                    f"{self.provider.label} devolvio {http_response.status_code}: "
                    f"{http_response.text[:400]}"
                )

            try:
                data = http_response.json()
            except ValueError as exc:
                raise LLMError(
                    f"{self.provider.label} devolvio una respuesta no-JSON: {exc}"
                ) from exc

            content = _extract_message_content(data)
            usage = data.get("usage") or {}
            return LLMResponse(
                content=content,
                parsed=extract_json_object(content),
                model=data.get("model") or self.model,
                latency_ms=latency_ms,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                raw=data,
            )

        raise LLMError(
            f"No se pudo completar la llamada a {self.provider.label} tras "
            f"{self.max_retries} intentos: {last_error}"
        )

    def _sleep_backoff(self, attempt: int, *, override: float | None = None) -> None:
        delay = override if override is not None else min(2.0 ** attempt, 30.0)
        log.debug("Esperando %.1fs antes de reintentar.", delay)
        time.sleep(delay)


# ----------------------------------------------------------------------
# Parseo defensivo
# ----------------------------------------------------------------------

def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    # Algunos modelos devuelven una lista de bloques tipo {"type":"text",...}.
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") in (None, "text")
        ]
        return "".join(parts)
    # Last resort: reasoning models sometimes only fill reasoning_content.
    return str(message.get("reasoning_content") or "")


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
