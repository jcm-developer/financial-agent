"""F6.6 y F6.7: proveedor de modelo y clave de API por perfil.

Nada de esto habla con la red: se prueban la tabla de proveedores, la resolucion
de credenciales y el enmascarado. Lo que importa aqui son tres cosas que fallarian
en silencio o a la hora mas inoportuna:

  * un proveedor sin implementar tiene que decir "aun no", no "desconocido",
  * la clave de NIM **no** debe usarse contra OpenAI: eso no falla al resolver,
    falla a mitad del ciclo con un 401 que nadie relaciona con el perfil,
  * la clave no debe aparecer entera en ninguna salida.
"""

from __future__ import annotations

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


def test_los_dos_proveedores_implementados():
    assert set(PROVIDERS) == {"nvidia", "openai"}


def test_nvidia_es_el_valor_por_defecto():
    assert resolve_provider("").name == "nvidia"


def test_el_nombre_se_normaliza():
    assert resolve_provider("  OpenAI  ").name == "openai"


def test_anthropic_dice_que_todavia_no():
    """El esquema admite 'anthropic' desde F1.2, pero no esta implementado.

    Distinguir "aun no" de "no existe" importa: el primero es una tarea
    pendiente (F9.1), el segundo seria una errata.
    """
    with pytest.raises(LLMError, match="no esta implementado todavia"):
        resolve_provider("anthropic")


def test_un_proveedor_inventado_se_rechaza():
    with pytest.raises(LLMError, match="desconocido"):
        resolve_provider("gemini")


def test_cada_proveedor_trae_su_url():
    assert "nvidia.com" in PROVIDERS["nvidia"].default_base_url
    assert "openai.com" in PROVIDERS["openai"].default_base_url


# -- Cliente -----------------------------------------------------------------


def test_el_cliente_usa_la_url_del_proveedor():
    with LLMClient(api_key="k", provider="openai", model="gpt-x") as client:
        assert str(client._client.base_url).startswith("https://api.openai.com")


def test_una_url_explicita_pisa_la_del_proveedor():
    """Hace falta para apuntar a un proxy o a un despliegue propio."""
    with LLMClient(
        api_key="k", provider="openai", base_url="http://localhost:8080/v1",
        model="m",
    ) as client:
        assert "localhost:8080" in str(client._client.base_url)


def test_sin_clave_el_cliente_no_se_construye():
    """Mejor aqui que en la primera llamada, con el ciclo ya abierto."""
    with pytest.raises(LLMError, match="Falta la clave"):
        LLMClient(api_key="", provider="openai", model="m")


def test_el_error_nombra_el_proveedor():
    with pytest.raises(LLMError, match="OpenAI"):
        LLMClient(api_key="", provider="openai", model="m")


# -- Clave por perfil (F6.7) -------------------------------------------------


def test_nvidia_cae_al_entorno_si_el_perfil_no_trae_clave(db, perfil):
    """Compatibilidad: quien ya tenia NVIDIA_API_KEY sigue funcionando."""
    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.llm_provider == "nvidia"
    assert settings.model_api_key == "nvapi-del-entorno"


def test_la_clave_del_perfil_manda_sobre_la_del_entorno(db, perfil):
    db.update_settings(perfil, {"llm_api_key": "nvapi-del-perfil"})

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.model_api_key == "nvapi-del-perfil"


def test_openai_sin_clave_de_perfil_se_rechaza(db, perfil):
    """La clave de NIM no vale para OpenAI.

    Si se aceptara el respaldo del entorno, el fallo llegaria como un 401 dentro
    del primer analisis del ciclo, con filas ya escritas y sin pista de que el
    problema era el perfil.
    """
    db.update_settings(perfil, {"llm_provider": "openai"})

    with pytest.raises(ConfigError, match="no tiene clave de API"):
        resolve_settings(db, perfil, infra=INFRA)


def test_openai_con_clave_de_perfil_resuelve(db, perfil):
    db.update_settings(
        perfil, {"llm_provider": "openai", "llm_api_key": "sk-propia"}
    )

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert (settings.llm_provider, settings.model_api_key) == ("openai", "sk-propia")


def test_la_url_del_entorno_solo_aplica_a_nvidia(db, perfil):
    """`NVIDIA_BASE_URL` apuntando a OpenAI seria un fallo dificil de ver."""
    db.update_settings(
        perfil, {"llm_provider": "openai", "llm_api_key": "sk-propia"}
    )

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.model_base_url == ""  # el cliente pone la de OpenAI


def test_la_url_del_entorno_si_aplica_a_nvidia(db, perfil):
    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.model_base_url == "http://nim-stub"


def test_un_proveedor_sin_implementar_falla_al_resolver_el_perfil(db, perfil):
    db.update_settings(perfil, {"llm_provider": "anthropic"})

    with pytest.raises(ConfigError, match="experimento-01"):
        resolve_settings(db, perfil, infra=INFRA)


# -- Enmascarado -------------------------------------------------------------


def test_la_clave_enmascarada_conserva_prefijo_y_cola():
    assert mask_secret("nvapi-abcdefgh1234") == "nvapi-...1234"


def test_el_enmascarado_es_ascii():
    """Se imprime en la consola de Windows, que destroza el caracter de elipsis."""
    assert mask_secret("nvapi-abcdefgh1234").isascii()


def test_se_puede_cambiar_el_texto_de_clave_vacia():
    """Con NVIDIA, columna vacia = clave del entorno, no ausencia de clave."""
    assert mask_secret("", empty="(del entorno)") == "(del entorno)"


def test_el_enmascarado_no_deja_ver_el_cuerpo():
    enmascarada = mask_secret("sk-supersecretovalor9999")
    assert "supersecreto" not in enmascarada


def test_sin_clave_se_dice_explicitamente():
    """Vacio se veria como un fallo de pintado; "(sin clave)" es informacion."""
    assert mask_secret(None) == "(sin clave)"
    assert mask_secret("   ") == "(sin clave)"


def test_una_clave_corta_no_se_filtra_entera():
    assert "abc" not in mask_secret("abc")


def test_el_snapshot_del_ciclo_no_lleva_la_clave_del_perfil(db, perfil):
    """F6.3 + F6.7: la clave por perfil tampoco puede acabar en el historico."""
    db.update_settings(perfil, {"llm_api_key": "nvapi-secreto-del-perfil"})
    settings = resolve_settings(db, perfil, infra=INFRA)

    datos = settings.snapshot()

    assert "nvapi-secreto-del-perfil" not in str(datos)
    assert datos["llm_provider"] == "nvidia"
