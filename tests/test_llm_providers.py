"""F6.6 and F6.7: model provider and API key per profile.

None of this talks to the network: what gets tested is the provider table, the
resolution of credentials and the masking. What matters here are three things
that would fail silently or at the most inopportune moment:

  * an unimplemented provider has to say "not yet", not "unknown",
  * the NIM key must **not** be used against OpenAI: that does not fail at
    resolution, it fails halfway through the cycle with a 401 nobody relates to
    the profile,
  * the key must not appear whole in any output.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.config import ConfigError, Infra
from src.llm import PROVIDERS, LLMClient, LLMError, resolve_provider
from src.profile_settings import mask_secret, resolve_settings

INFRA = Infra(
    db_path="data/no-se-usa.db",
    log_level="CRITICAL",
    model_api_key="nvapi-del-entorno",
    model_base_url="http://nim-stub",
)


@pytest.fixture
def perfil(db):
    profile_id = db.create_profile(name="experimento-01")
    db.set_profile_universe(profile_id, ["AAPL"])
    db.set_profile_status(profile_id, "active")
    return profile_id


# -- Tabla de proveedores ----------------------------------------------------


def test_the_two_implemented_providers():
    assert set(PROVIDERS) == {"nvidia", "openai"}


def test_nvidia_is_the_default():
    assert resolve_provider("").name == "nvidia"


def test_the_name_is_normalised():
    assert resolve_provider("  OpenAI  ").name == "openai"


def test_anthropic_says_not_yet():
    """The schema has admitted 'anthropic' since F1.2, but it is not implemented.

    Telling "not yet" from "does not exist" matters: the first is a pending task
    (F9.1), the second would be a typo.
    """
    with pytest.raises(LLMError, match="no esta implementado todavia"):
        resolve_provider("anthropic")


def test_a_made_up_provider_is_refused():
    with pytest.raises(LLMError, match="desconocido"):
        resolve_provider("gemini")


def test_each_provider_brings_its_own_url():
    assert "nvidia.com" in PROVIDERS["nvidia"].default_base_url
    assert "openai.com" in PROVIDERS["openai"].default_base_url


# -- Cliente -----------------------------------------------------------------


def test_the_client_uses_the_providers_url():
    with LLMClient(api_key="k", provider="openai", model="gpt-x") as client:
        assert str(client._client.base_url).startswith("https://api.openai.com")


def test_an_explicit_url_overrides_the_providers():
    """Needed to point at a proxy or at a deployment of your own."""
    with LLMClient(
        api_key="k", provider="openai", base_url="http://localhost:8080/v1",
        model="m",
    ) as client:
        assert "localhost:8080" in str(client._client.base_url)


def test_with_no_key_the_client_is_not_built():
    """Better here than on the first call, with the cycle already open."""
    with pytest.raises(LLMError, match="Falta la clave"):
        LLMClient(api_key="", provider="openai", model="m")


def test_the_error_names_the_provider():
    with pytest.raises(LLMError, match="OpenAI"):
        LLMClient(api_key="", provider="openai", model="m")


# -- La llamada, en streaming (F9.22) ----------------------------------------
#
# Nothing here reaches the network either: `httpx.MockTransport` answers, which
# is what lets the responses that used to be impossible to provoke be written
# down —a stream that stops halfway, a server that refuses an optional field—
# and which are exactly the ones that were breaking the cycle.

SSE_CONTENT_TYPE = {"content-type": "text/event-stream"}


def sse(*events: str) -> bytes:
    return ("\n\n".join(events) + "\n\n").encode()


def ok_stream(text: str = '{"action": "hold"}', **usage: int) -> httpx.Response:
    events = [
        '{"model":"m","choices":[{"delta":{"content":%s}}]}' % json.dumps(text)
    ]
    if usage:
        events.append('{"choices":[],"usage":%s}' % json.dumps(usage))
    events.append("[DONE]")
    return httpx.Response(
        200,
        content=sse(*[f"data: {e}" for e in events]),
        headers=SSE_CONTENT_TYPE,
    )


@pytest.fixture
def no_espera(monkeypatch):
    """The backoff is 2 s at the first retry, and here nothing is being waited
    for."""
    monkeypatch.setattr(LLMClient, "_sleep_backoff", lambda *a, **k: None)


def stub(*responses: httpx.Response, **kwargs) -> tuple[LLMClient, list[httpx.Request]]:
    """A client that answers with `responses`, in order, and keeps the requests
    so the body that was sent can be looked at."""
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0) if queue else responses[-1]

    client = LLMClient(api_key="k", model="m", **kwargs)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://stub"
    )
    return client, seen


def sent_body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def test_the_answer_is_asked_for_as_a_stream():
    """The reason is in the module's docstring: a request with `stream: false`
    is a socket saying nothing for 40 s, and something in the middle hangs up."""
    client, seen = stub(ok_stream())

    client.complete_json(system="s", user="u")

    assert sent_body(seen[0])["stream"] is True


def test_the_fragments_come_back_as_one_answer():
    client, _ = stub(ok_stream('{"action": "sell"}'))

    response = client.complete_json(system="s", user="u")

    assert response.parsed == {"action": "sell"}


def test_the_tokens_survive_the_streaming():
    """`stream_options` is asked for on purpose: without it the last chunk
    brings no `usage` and every decision would be written with zero tokens."""
    client, seen = stub(ok_stream(prompt_tokens=120, completion_tokens=34))

    response = client.complete_json(system="s", user="u")

    assert sent_body(seen[0])["stream_options"] == {"include_usage": True}
    assert (response.prompt_tokens, response.completion_tokens) == (120, 34)


def test_a_refused_option_is_dropped_and_spends_no_attempt():
    """With a single attempt allowed, if the negotiation spent one there would
    be nothing left to ask the model with."""
    client, seen = stub(
        httpx.Response(422, json={"detail": "stream_options is not supported"}),
        ok_stream(),
        max_retries=1,
    )

    response = client.complete_json(system="s", user="u")

    assert response.parsed == {"action": "hold"}
    assert "stream_options" not in sent_body(seen[1])
    assert "response_format" in sent_body(seen[1])  # the other one is not touched


def test_a_refused_response_format_is_still_dropped():
    """It is the case that F6.6 already handled, and it must keep working now
    that there are two negotiable fields."""
    client, seen = stub(
        httpx.Response(400, json={"detail": "response_format not supported"}),
        ok_stream(),
        max_retries=1,
    )

    client.complete_json(system="s", user="u")

    assert "response_format" not in sent_body(seen[1])
    assert "stream_options" in sent_body(seen[1])


def test_a_400_that_names_nothing_ends_up_failing():
    """The negotiation is bounded: once both extras are off, a 400 is a real
    error and is not retried for ever."""
    client, seen = stub(httpx.Response(400, json={"detail": "modelo inexistente"}))

    with pytest.raises(LLMError, match="400"):
        client.complete_json(system="s", user="u")

    assert len(seen) == 3  # two negotiations and the hard failure


def test_a_401_is_not_retried():
    client, seen = stub(httpx.Response(401, text="clave invalida"))

    with pytest.raises(LLMError, match="401"):
        client.complete_json(system="s", user="u")

    assert len(seen) == 1


def test_a_stream_that_brings_nothing_is_retried(no_espera):
    """A 200 with an empty body is a broken transport, not an answer from the
    model: it is worth asking again instead of skipping the symbol."""
    client, seen = stub(
        httpx.Response(200, content=b"", headers=SSE_CONTENT_TYPE),
        ok_stream(),
    )

    response = client.complete_json(system="s", user="u")

    assert response.parsed == {"action": "hold"}
    assert len(seen) == 2


def test_an_error_halfway_through_the_stream_is_retried(no_espera):
    client, _ = stub(
        httpx.Response(
            200,
            content=sse(
                'data: {"choices":[{"delta":{"content":"{\\"act"}}]}',
                'data: {"error":{"message":"overloaded"}}',
            ),
            headers=SSE_CONTENT_TYPE,
        ),
        ok_stream(),
    )

    assert client.complete_json(system="s", user="u").parsed == {"action": "hold"}


def test_a_dropped_connection_gives_up_saying_how_many_times(no_espera):
    """The failure of the 2026-08-12: three attempts, all of them with the
    server hanging up without sending anything."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("Server disconnected without sending a response.")

    client = LLMClient(api_key="k", model="m", max_retries=3)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://stub"
    )

    with pytest.raises(LLMError, match="tras 3 intentos"):
        client.complete_json(system="s", user="u")


def test_a_429_is_retried_honouring_retry_after(no_espera):
    client, seen = stub(
        httpx.Response(429, text="rate limit", headers={"Retry-After": "1"}),
        ok_stream(),
    )

    client.complete_json(system="s", user="u")

    assert len(seen) == 2


def test_a_stream_without_json_says_what_came_back():
    """The message carries the beginning of the response because that is the
    only trace left of what the model answered."""
    client, _ = stub(ok_stream("No puedo ayudarte con eso."))

    with pytest.raises(LLMError, match="No puedo ayudarte"):
        client.complete_json(system="s", user="u")


# -- Clave por perfil (F6.7) -------------------------------------------------


def test_nvidia_falls_back_to_the_environment_when_the_profile_has_no_key(db, perfil):
    """Compatibilidad: quien ya tenia NVIDIA_API_KEY sigue funcionando."""
    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.llm_provider == "nvidia"
    assert settings.model_api_key == "nvapi-del-entorno"


def test_the_profiles_key_wins_over_the_environments(db, perfil):
    db.update_settings(perfil, {"llm_api_key": "nvapi-del-perfil"})

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.model_api_key == "nvapi-del-perfil"


def test_openai_without_a_profile_key_is_refused(db, perfil):
    """The NIM key is no good for OpenAI.

    If the environment fallback were accepted, the failure would arrive as a 401
    inside the cycle's first analysis, with rows already written and no clue that
    the problem was the profile.
    """
    db.update_settings(perfil, {"llm_provider": "openai"})

    with pytest.raises(ConfigError, match="no tiene clave de API"):
        resolve_settings(db, perfil, infra=INFRA)


def test_openai_with_a_profile_key_resolves(db, perfil):
    db.update_settings(
        perfil, {"llm_provider": "openai", "llm_api_key": "sk-propia"}
    )

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert (settings.llm_provider, settings.model_api_key) == ("openai", "sk-propia")


def test_the_environments_url_only_applies_to_nvidia(db, perfil):
    """`NVIDIA_BASE_URL` apuntando a OpenAI seria un fallo dificil de ver."""
    db.update_settings(
        perfil, {"llm_provider": "openai", "llm_api_key": "sk-propia"}
    )

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.model_base_url == ""  # the client supplies OpenAI's


def test_the_environments_url_does_apply_to_nvidia(db, perfil):
    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.model_base_url == "http://nim-stub"


def test_an_unimplemented_provider_fails_while_resolving_the_profile(db, perfil):
    db.update_settings(perfil, {"llm_provider": "anthropic"})

    with pytest.raises(ConfigError, match="experimento-01"):
        resolve_settings(db, perfil, infra=INFRA)


# -- Enmascarado -------------------------------------------------------------


def test_the_masked_key_keeps_prefix_and_tail():
    assert mask_secret("nvapi-abcdefgh1234") == "nvapi-...1234"


def test_the_masking_is_ascii():
    """It is printed to the Windows console, which mangles the ellipsis character."""
    assert mask_secret("nvapi-abcdefgh1234").isascii()


def test_the_empty_key_text_can_be_changed():
    """With NVIDIA, an empty column = key from the environment, not no key."""
    assert mask_secret("", empty="(del entorno)") == "(del entorno)"


def test_the_masking_does_not_reveal_the_body():
    enmascarada = mask_secret("sk-supersecretovalor9999")
    assert "supersecreto" not in enmascarada


def test_with_no_key_it_is_said_explicitly():
    """Empty would look like a rendering fault; "(sin clave)" is information."""
    assert mask_secret(None) == "(sin clave)"
    assert mask_secret("   ") == "(sin clave)"


def test_a_short_key_is_not_leaked_whole():
    assert "abc" not in mask_secret("abc")


def test_the_cycle_snapshot_does_not_carry_the_profiles_key(db, perfil):
    """F6.3 + F6.7: the per-profile key must not end up in the history either."""
    db.update_settings(perfil, {"llm_api_key": "nvapi-secreto-del-perfil"})
    settings = resolve_settings(db, perfil, infra=INFRA)

    datos = settings.snapshot()

    assert "nvapi-secreto-del-perfil" not in str(datos)
    assert datos["llm_provider"] == "nvidia"
