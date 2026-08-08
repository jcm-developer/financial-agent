"""F6.5: de los dos deslizadores a los limites del Risk Manager.

Lo que se prueba aqui no es tanto que las cuentas salgan como que **el
deslizador sirva de algo**. Tres invariantes, y las tres pueden romperse sin que
nada falle a la vista:

  * los tres niveles de la tabla de TASKS.md son el contrato, y cambiarlos sin
    querer alteraria el significado de todos los experimentos anteriores,
  * moverse de 1 a 10 tiene que mover cada limite en una sola direccion: un
    tramo plano o un rebote hacen que el deslizador parezca roto,
  * apagar el modo avanzado tiene que devolver el mando a los deslizadores
    aunque las columnas conserven numeros viejos.
"""

from __future__ import annotations

import pytest

from src.config import ConfigError, RiskLimits
from src.risk_presets import (
    DERIVED_FIELDS,
    derive_limits,
    describe,
    is_derived,
    max_open_positions,
    resolve_limits,
    sector_cap,
)

# La tabla de F6.5, copiada a mano desde TASKS.md. Que este duplicada es
# deliberado: si alguien toca las anclas del modulo, este test tiene que
# quejarse, y no lo haria si leyera los numeros del propio modulo.
TABLA = {
    1:  {"risk_per_trade_pct": 0.25, "max_position_pct": 5.0,
         "max_total_exposure_pct": 30.0, "min_conviction": 85,
         "stop_atr_multiple": 3.0, "min_reward_risk": 2.5},
    5:  {"risk_per_trade_pct": 1.0, "max_position_pct": 20.0,
         "max_total_exposure_pct": 70.0, "min_conviction": 65,
         "stop_atr_multiple": 2.0, "min_reward_risk": 1.5},
    10: {"risk_per_trade_pct": 3.0, "max_position_pct": 40.0,
         "max_total_exposure_pct": 100.0, "min_conviction": 45,
         "stop_atr_multiple": 1.2, "min_reward_risk": 1.0},
}

# Direccion en la que cada limite debe moverse al subir el perfil de riesgo.
DIRECCION = {
    "risk_per_trade_pct": +1,
    "max_position_pct": +1,
    "max_total_exposure_pct": +1,
    "max_daily_loss_pct": +1,
    "min_conviction": -1,
    "stop_atr_multiple": -1,
    "min_reward_risk": -1,
}


# -- El contrato de la tabla -------------------------------------------------


@pytest.mark.parametrize("nivel", sorted(TABLA))
def test_los_niveles_de_la_tabla_salen_exactos(nivel):
    limites = derive_limits(nivel, 5)

    for campo, esperado in TABLA[nivel].items():
        assert limites[campo] == pytest.approx(esperado), campo


def test_diversificacion_marca_el_numero_de_posiciones():
    assert max_open_positions(1) == 3
    assert max_open_positions(10) == 25


def test_el_riesgo_no_toca_el_numero_de_posiciones():
    """Los dos deslizadores son independientes: si el de riesgo moviera tambien
    el numero de posiciones, no se podria aislar que causo un resultado."""
    posiciones = {derive_limits(nivel, 7)["max_open_positions"] for nivel in range(1, 11)}

    assert len(posiciones) == 1


# -- Monotonia ---------------------------------------------------------------


@pytest.mark.parametrize("campo", sorted(DIRECCION))
def test_cada_limite_se_mueve_siempre_en_la_misma_direccion(campo):
    signo = DIRECCION[campo]
    serie = [derive_limits(nivel, 5)[campo] for nivel in range(1, 11)]

    for anterior, siguiente in zip(serie, serie[1:]):
        delta = (siguiente - anterior) * signo
        assert delta > 0, f"{campo}: {serie} tiene un tramo plano o invertido"


def test_las_posiciones_crecen_con_la_diversificacion():
    serie = [max_open_positions(nivel) for nivel in range(1, 11)]

    assert serie == sorted(serie)
    assert len(set(serie)) == len(serie), f"hay niveles con el mismo tope: {serie}"


# -- Todo nivel produce limites validos --------------------------------------


@pytest.mark.parametrize("riesgo", range(1, 11))
@pytest.mark.parametrize("diversificacion", (1, 5, 10))
def test_cualquier_combinacion_da_un_risklimits_valido(riesgo, diversificacion):
    """`RiskLimits.__post_init__` rechaza combinaciones incoherentes.

    Si una casilla intermedia las produjera, el error saltaria al mover un
    deslizador en la interfaz y no habria forma de adivinar por que.
    """
    limites = RiskLimits(**derive_limits(riesgo, diversificacion))

    assert limites.risk_per_trade_pct <= limites.max_position_pct


def test_los_campos_derivados_son_exactamente_los_de_risklimits():
    """Guardia contra deriva: anadir un limite en `RiskLimits` y olvidarlo aqui
    dejaria un limite que los deslizadores no controlan."""
    assert set(DERIVED_FIELDS) == set(RiskLimits().__dataclass_fields__)


@pytest.mark.parametrize("nivel", (0, 11, -3, "cinco", None))
def test_nivel_fuera_de_rango_se_rechaza(nivel):
    with pytest.raises(ConfigError):
        derive_limits(nivel, 5)


def test_el_redondeo_no_es_bancario():
    """`round()` de Python redondea 12,5 a 12 y 13,5 a 14 segun la paridad.

    Estos numeros se ensenan en pantalla y se guardan en el historial: que el
    resultado dependa de la paridad seria imposible de explicar.
    """
    # diversificacion 5 cae en 12,77 -> 13; con el bancario sobre 12,5 daria 12.
    assert max_open_positions(5) == 13


# -- Tope por sector ---------------------------------------------------------


def test_diversificacion_minima_permite_concentrar():
    """Nivel 1 es "concentracion permitida": un tope aqui no rechazaria nada."""
    assert sector_cap(1) is None


def test_diversificacion_alta_pone_tope():
    cap = sector_cap(10)

    assert cap is not None and cap < max_open_positions(10)


def test_el_tope_por_sector_respeta_el_maximo_real():
    """En modo avanzado el maximo de posiciones puede no ser el derivado.

    Un tope por sector mayor que el maximo global seria un numero sin sentido
    en la pantalla de ajustes.
    """
    cap = sector_cap(5, max_open=4)

    assert cap is not None and cap < 4


# -- Modo avanzado -----------------------------------------------------------


def test_sin_modo_avanzado_mandan_los_deslizadores():
    limites = resolve_limits({"risk_profile": 1, "diversification": 1,
                              "advanced_overrides": 0})

    assert limites.risk_per_trade_pct == pytest.approx(0.25)
    assert limites.max_open_positions == 3


def test_apagar_el_modo_avanzado_descarta_los_numeros_viejos():
    """El interruptor es el que manda, no la presencia de valores.

    Si los numeros de una sesion anterior siguieran ganando, apagar el modo
    avanzado no haria nada visible: el usuario concluiria que el interruptor
    esta roto y, peor, seguiria operando con limites que cree descartados.
    """
    fila = {"risk_profile": 1, "diversification": 1, "advanced_overrides": 0,
            "risk_per_trade_pct": 99.0, "max_open_positions": 42}

    limites = resolve_limits(fila)

    assert limites.risk_per_trade_pct == pytest.approx(0.25)
    assert limites.max_open_positions == 3


def test_con_modo_avanzado_pisa_solo_lo_que_no_es_nulo():
    """NULL sigue significando "derivalo": el modo avanzado es campo a campo."""
    fila = {"risk_profile": 5, "diversification": 5, "advanced_overrides": 1,
            "risk_per_trade_pct": 2.5, "max_position_pct": None}

    limites = resolve_limits(fila)

    assert limites.risk_per_trade_pct == pytest.approx(2.5)
    assert limites.max_position_pct == pytest.approx(20.0)  # el derivado de 5


def test_un_limite_entero_no_admite_decimales():
    fila = {"risk_profile": 5, "diversification": 5, "advanced_overrides": 1,
            "max_open_positions": 4.5}

    with pytest.raises(ConfigError, match="entero"):
        resolve_limits(fila)


def test_is_derived_distingue_lo_tocado_a_mano():
    fila = {"risk_profile": 5, "diversification": 5, "advanced_overrides": 1,
            "risk_per_trade_pct": 2.5, "max_position_pct": None}

    assert not is_derived(fila, "risk_per_trade_pct")
    assert is_derived(fila, "max_position_pct")


def test_is_derived_rechaza_un_campo_que_no_es_limite():
    with pytest.raises(ConfigError):
        is_derived({"advanced_overrides": 0}, "llm_model")


# -- Texto para la interfaz --------------------------------------------------


def test_describe_nombra_los_valores_efectivos():
    """Es el texto de F6.8: mover un deslizador sin ver la consecuencia en
    numeros concretos es adivinar."""
    texto = describe({"risk_profile": 10, "diversification": 10,
                      "advanced_overrides": 0})

    assert "25 posiciones" in texto
    assert "3% de riesgo" in texto
    assert "deslizadores" in texto


def test_describe_avisa_de_que_los_limites_son_manuales():
    texto = describe({"risk_profile": 5, "diversification": 5,
                      "advanced_overrides": 1, "max_open_positions": 2})

    assert "a mano" in texto
    assert "2 posiciones" in texto
