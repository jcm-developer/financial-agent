"""Cliente para los endpoints de NVIDIA NIM (build.nvidia.com).

NIM expone una API compatible con OpenAI, asi que basta con httpx: no hace
falta arrastrar un SDK entero para una sola llamada POST.

El modulo asume lo peor del modelo y lo tolera:

  * Los modelos de razonamiento (deepseek-r1, nemotron) escriben su cadena de
    pensamiento en `<think>...</think>` antes del JSON.
  * Casi cualquier modelo envuelve el JSON en ```json ... ``` alguna vez.
  * `response_format` no esta soportado por todos los modelos de NIM, asi que
    se intenta y se desactiva para el resto de la sesion si el servidor lo rechaza.
  * El nivel gratuito devuelve 429 con frecuencia: reintentos con espera
    exponencial, respetando `Retry-After` cuando viene.
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
        base_url: str,
        model: str,
        temperature: float = 0.2,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self._supports_json_mode = True
        self._client = httpx.Client(
            base_url=base_url,
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
        """Pide una respuesta y exige que contenga un objeto JSON.

        Lanza `LLMError` si tras los reintentos no se obtiene JSON parseable:
        preferimos saltarnos el simbolo antes que operar sobre una adivinanza.
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
                log.warning("Error de red hablando con NIM (intento %d/%d): %s",
                            attempt, self.max_retries, exc)
                self._sleep_backoff(attempt)
                continue

            latency_ms = int((time.monotonic() - started) * 1000)

            if http_response.status_code in (400, 422) and self._supports_json_mode:
                # Probablemente el modelo no acepta response_format: lo quitamos
                # y reintentamos inmediatamente sin gastar un intento.
                log.info(
                    "El modelo %s rechazo response_format (%d); se desactiva el modo JSON.",
                    self.model, http_response.status_code,
                )
                self._supports_json_mode = False
                continue

            if http_response.status_code == 429 or http_response.status_code >= 500:
                last_error = LLMError(
                    f"NIM devolvio {http_response.status_code}: {http_response.text[:200]}"
                )
                retry_after = _parse_retry_after(http_response.headers.get("Retry-After"))
                log.warning(
                    "NIM devolvio %d (intento %d/%d).",
                    http_response.status_code, attempt, self.max_retries,
                )
                self._sleep_backoff(attempt, override=retry_after)
                continue

            if http_response.status_code >= 400:
                # 401/403/404 no se arreglan reintentando.
                raise LLMError(
                    f"NIM devolvio {http_response.status_code}: {http_response.text[:400]}"
                )

            try:
                data = http_response.json()
            except ValueError as exc:
                raise LLMError(f"NIM devolvio una respuesta no-JSON: {exc}") from exc

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
            f"No se pudo completar la llamada a NIM tras {self.max_retries} intentos: {last_error}"
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
    # Ultimo recurso: los modelos de razonamiento a veces solo llenan reasoning_content.
    return str(message.get("reasoning_content") or "")


def strip_reasoning(text: str) -> str:
    """Elimina los bloques <think>, incluido el caso de bloque sin cerrar
    (ocurre cuando la respuesta se corta por max_tokens)."""
    cleaned = _THINK_BLOCK.sub("", text)
    if "<think>" in cleaned.lower():
        cleaned = _UNCLOSED_THINK.sub("", cleaned)
    return cleaned.strip()


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Saca el primer objeto JSON de una respuesta que puede venir sucia.

    Estrategia en cascada: texto limpio -> bloque de codigo -> primer objeto
    con llaves balanceadas. Devuelve None si nada parsea.
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
    """Primer `{...}` con llaves balanceadas, ignorando las que van dentro de
    cadenas JSON (y respetando escapes)."""
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
