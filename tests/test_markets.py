"""El mercado como parametro del perfil: calendario europeo, registro y universo.

Lo que se prueba aqui son los fallos silenciosos, que en esta parte del proyecto
son casi todos. Un calendario europeo mal puesto no revienta: hace que el
ingestor pida barras con la bolsa cerrada y guarde el cierre anterior una y otra
vez, y a los tres dias hay un historico lleno de datos que parecen buenos. Lo
mismo con un simbolo americano dentro de un perfil europeo.

Todas las fechas son fijas, nunca el reloj.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src import market_calendar as mc
from src.config import ConfigError, Infra
from src.profile_settings import import_env_profile, resolve_settings

MADRID = ZoneInfo("Europe/Madrid")

INFRA = Infra(
    db_path="data/no-se-usa.db",
    log_level="CRITICAL",
    model_api_key="clave-de-prueba",
    model_base_url="http://stub",
)


def eu(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=MADRID)


# -- Registro de mercados ----------------------------------------------------


def test_el_registro_trae_los_dos_mercados():
    assert set(mc.MARKETS) == {"us", "eu"}
    assert mc.get_market("eu") is mc.EU
    assert mc.get_market("US") is mc.US, "el codigo no distingue mayusculas"
    assert mc.get_market() is mc.US, "sin argumento manda DEFAULT_MARKET"


def test_un_mercado_desconocido_dice_cuales_hay():
    """El mensaje importa: 'eur' en vez de 'eu' es la errata probable."""
    with pytest.raises(mc.UnknownMarket) as exc:
        mc.get_market("eur")

    assert "eur" in str(exc.value)
    assert "'eu'" in str(exc.value)


def test_las_divisas_no_se_mezclan():
    """Cada mercado lleva su moneda porque el proyecto no convierte divisa."""
    assert mc.US.currency == "USD"
    assert mc.EU.currency == "EUR"


def test_la_sesion_europea_es_mas_larga_que_la_americana():
    """510 minutos frente a 390: es lo que dimensiona el volumen de bars_1m."""
    assert mc.EU.session_minutes == 510
    assert mc.US.session_minutes == 390


# -- Horario europeo ---------------------------------------------------------


def test_la_sesion_europea_abre_a_las_nueve():
    assert not mc.is_session_open(eu(2026, 8, 5, 8, 59), market="eu")
    assert mc.is_session_open(eu(2026, 8, 5, 9, 0), market="eu")


def test_la_sesion_europea_cierra_a_las_cinco_y_media():
    assert mc.is_session_open(eu(2026, 8, 5, 17, 29), market="eu")
    assert not mc.is_session_open(eu(2026, 8, 5, 17, 30), market="eu")


def test_los_dos_mercados_se_solapan_por_la_tarde():
    """15:30-17:30 CET es la unica franja con las dos bolsas abiertas. Es el
    caso que el ingestor tiene que saber servir a la vez."""
    solape = eu(2026, 8, 5, 16, 0)

    assert mc.is_session_open(solape, market="eu")
    assert mc.is_session_open(solape, market="us")


def test_a_las_diez_de_la_manana_solo_esta_abierta_europa():
    """El motivo por el que el usuario pidio esto: el ordenador esta encendido."""
    manana = eu(2026, 8, 5, 10, 0)

    assert mc.is_session_open(manana, market="eu")
    assert not mc.is_session_open(manana, market="us")


def test_a_las_nueve_de_la_noche_solo_esta_abierto_nueva_york():
    noche = eu(2026, 8, 5, 21, 0)

    assert not mc.is_session_open(noche, market="eu")
    assert mc.is_session_open(noche, market="us")


def test_un_datetime_sin_zona_se_lee_en_hora_del_mercado():
    """No se interpreta como UTC sino como hora local de la bolsa preguntada.

    Las 17:00 lo demuestran: en Madrid quedan 30 minutos de sesion y en Nueva
    York la sesion cerro hace una hora.
    """
    assert mc.is_session_open(datetime(2026, 8, 5, 17, 0), market="eu")
    assert not mc.is_session_open(datetime(2026, 8, 5, 17, 0), market="us")


# -- Ventana operativa -------------------------------------------------------
#
# El punto de toda esta seccion: la ventana NO es la sesion, y confundirlas es
# el fallo que estos tests existen para impedir. `is_session_open` es un dato de
# mercado que se guarda en el historico; `is_operating` es "trabaja el sistema".


def test_la_ventana_europea_va_de_las_915_a_las_1745():
    assert mc.EU.operating_open == time(9, 15)
    assert mc.EU.operating_close == time(17, 45)


def test_los_primeros_quince_minutos_son_sesion_pero_no_ventana():
    """La bolsa esta abierta y el sistema no trabaja: es el calentamiento, para
    no decidir sobre la resaca de la subasta de apertura."""
    apertura = eu(2026, 8, 5, 9, 5)

    assert mc.is_session_open(apertura, market="eu")
    assert not mc.is_operating(apertura, market="eu")


def test_a_las_915_arranca_la_ventana():
    assert not mc.is_operating(eu(2026, 8, 5, 9, 14), market="eu")
    assert mc.is_operating(eu(2026, 8, 5, 9, 15), market="eu")


def test_los_quince_minutos_tras_el_cierre_son_ventana_pero_no_sesion():
    """Al reves que el calentamiento: la bolsa ya cerro y el sistema sigue,
    porque es cuando termina de llegar la ultima barra."""
    cola = eu(2026, 8, 5, 17, 40)

    assert not mc.is_session_open(cola, market="eu")
    assert mc.is_operating(cola, market="eu")


def test_a_las_1745_se_acaba_la_ventana():
    assert mc.is_operating(eu(2026, 8, 5, 17, 44), market="eu")
    assert not mc.is_operating(eu(2026, 8, 5, 17, 45), market="eu")


def test_la_ventana_no_abre_en_dia_sin_sesion():
    """Un festivo no tiene cola ni calentamiento: no hay nada que capturar."""
    assert not mc.is_operating(eu(2026, 5, 1, 12, 0), market="eu")
    assert not mc.is_operating(eu(2026, 8, 8, 12, 0), market="eu")


def test_la_ventana_americana_sigue_siendo_la_sesion():
    """Nadie ha pedido cambiar el comportamiento americano, y hacerlo de rebote
    alteraria un experimento en marcha."""
    assert mc.US.warmup_minutes == 0
    assert mc.US.drain_minutes == 0
    assert mc.US.operating_open == mc.US.open_time
    assert mc.US.operating_close == mc.US.close_time


def test_la_media_sesion_arrastra_su_ventana():
    """La razon de guardar desplazamientos y no horas absolutas. El 24 de
    diciembre Nueva York cierra a las 13:00; con 17:45 grabado a fuego, el
    sistema esperaria barras tres horas de una sesion terminada."""
    nochebuena = date(2026, 12, 24)

    assert mc.US.operating_close_for(nochebuena) == time(13, 0)
    assert mc.US.operating_close_for(date(2026, 12, 23)) == time(16, 0)


def test_next_operating_open_apunta_a_las_915_no_a_las_900():
    """Es lo que el ingestor usa para dormir: despertarse a las 09:00 seria
    pasarse quince minutos pidiendo barras de la subasta."""
    upcoming = mc.next_operating_open(eu(2026, 8, 7, 20, 0), market="eu")

    assert upcoming.date() == date(2026, 8, 10)   # el finde por medio
    assert upcoming.timetz().hour == 9
    assert upcoming.timetz().minute == 15


def test_la_ventana_dura_lo_mismo_que_la_sesion():
    """Calentamiento y cola son iguales, asi que el numero de barras por dia no
    cambia: 510 en Europa. Importa para el volumen estimado de bars_1m."""
    assert mc.EU.operating_minutes == mc.EU.session_minutes == 510
    assert mc.US.operating_minutes == mc.US.session_minutes == 390


def test_operating_bounds_devuelve_la_ventana_del_dia():
    inicio, fin = mc.operating_bounds(date(2026, 8, 10), market="eu")

    assert (inicio.hour, inicio.minute) == (9, 15)
    assert (fin.hour, fin.minute) == (17, 45)
    assert mc.operating_bounds(date(2026, 8, 8), market="eu") is None


def test_el_registro_se_valida_al_importar():
    """`_check_markets` corre al importar el modulo. Una ventana vacia o al
    reves no revienta sola: hace que el sistema trabaje en horas que nadie
    eligio, y eso no da sintoma hasta que se miran los datos."""
    roto = dataclasses.replace(mc.EU, warmup_minutes=600)

    assert roto.operating_open >= roto.operating_close
    with pytest.raises(ValueError, match="ventana operativa"):
        mc._check_markets([roto])


# -- Festivos europeos -------------------------------------------------------


@pytest.mark.parametrize("day", [
    date(2026, 1, 1),    # Ano Nuevo
    date(2026, 4, 3),    # Viernes Santo
    date(2026, 4, 6),    # Lunes de Pascua
    date(2026, 5, 1),    # Dia del Trabajo
    date(2026, 12, 24),  # Nochebuena
    date(2026, 12, 25),  # Navidad
    date(2026, 12, 31),  # Nochevieja
])
def test_los_festivos_europeos_no_son_dias_de_mercado(day):
    assert not mc.is_trading_day(day, market="eu")


def test_los_festivos_americanos_no_cierran_europa():
    """Accion de Gracias es el ejemplo obvio: Wall Street cierra y Madrid opera
    con normalidad. Compartir tabla habria costado una sesion entera."""
    accion_de_gracias = date(2026, 11, 26)

    assert not mc.is_trading_day(accion_de_gracias, market="us")
    assert mc.is_trading_day(accion_de_gracias, market="eu")


def test_los_festivos_europeos_no_cierran_nueva_york():
    """El 1 de mayo de 2026 (viernes) es festivo en toda la zona euro y dia
    normal en Estados Unidos."""
    assert not mc.is_trading_day(date(2026, 5, 1), market="eu")
    assert mc.is_trading_day(date(2026, 5, 1), market="us")


@pytest.mark.parametrize("day, motivo", [
    (date(2026, 5, 25), "Lunes de Pentecostes: cierra Xetra, no las demas"),
    (date(2026, 1, 6), "Epifania: cierra Milan, no las demas"),
])
def test_los_cierres_de_una_sola_bolsa_siguen_siendo_dia_de_mercado(day, motivo):
    """La tabla europea solo lleva los cierres COMUNES. Marcar estos dias como
    festivo costaria la sesion a los otros sesenta y tantos simbolos; asi, los
    afectados aparecen como simbolos vacios, que el ingestor ya sabe tratar."""
    assert mc.is_trading_day(day, market="eu"), motivo


def test_europa_no_traslada_los_festivos_de_fin_de_semana():
    """NYSE mueve el 4 de julio al viernes; Europa no mueve nada. El 26 de
    diciembre de 2026 cae en sabado y no genera ningun cierre extra."""
    assert date(2026, 12, 26).weekday() == 5
    assert mc.is_trading_day(date(2026, 12, 28), market="eu")


def test_la_tabla_europea_no_tiene_festivos_en_fin_de_semana():
    """Una fecha de fin de semana en la tabla seria inofensiva pero delataria
    que se copio el festivo nominal sin comprobar en que dia cae."""
    assert [d for d in mc.EU.holidays if d.weekday() >= 5] == []


def test_europa_no_tiene_medias_sesiones():
    """Nochebuena y Nochevieja se tratan como cierre completo, no como media
    sesion: ver el comentario de _EU_EARLY_CLOSES."""
    assert dict(mc.EU.early_closes) == {}
    assert not mc.is_trading_day(date(2026, 12, 24), market="eu")


# -- should_run y describe con mercado ---------------------------------------


def test_el_ciclo_diario_europeo_corre_despues_del_cierre():
    """Las 18:00 de Madrid son el equivalente al 16:15 ET del perfil americano:
    sesion terminada y barra del dia completa."""
    allowed, reason = mc.should_run("1d", eu(2026, 8, 10, 18, 0), market="eu")

    assert allowed
    assert "dia de mercado" in reason


def test_el_ciclo_horario_europeo_no_corre_de_noche():
    allowed, reason = mc.should_run("1h", eu(2026, 8, 10, 21, 0), market="eu")

    assert not allowed
    assert "sesion viva" in reason


def test_el_mismo_instante_da_respuestas_distintas_segun_el_mercado():
    """Es la razon de ser de todo esto: a las 10:00 CET un ciclo horario europeo
    tiene datos frescos y uno americano no."""
    momento = eu(2026, 8, 10, 10, 0)

    assert mc.should_run("1h", momento, market="eu")[0]
    assert not mc.should_run("1h", momento, market="us")[0]


def test_describe_europeo_dice_la_hora_en_su_zona():
    texto = mc.describe(eu(2026, 8, 5, 16, 30), market="eu")

    assert "abierto" in texto
    assert "60 min" in texto
    assert "16:30" in texto
    assert "CEST" in texto, "en agosto Madrid va en horario de verano"


def test_describe_americano_sigue_hablando_de_nueva_york():
    texto = mc.describe(datetime(2026, 8, 5, 15, 0, tzinfo=mc.EASTERN))

    assert "60 min" in texto
    assert "EDT" in texto


# -- Pertenencia de simbolos -------------------------------------------------


@pytest.mark.parametrize("symbol", ["SAN.MC", "ASML.AS", "SAP.DE", "MC.PA",
                                    "ISP.MI", "ABI.BR", "NDA-FI.HE"])
def test_los_simbolos_con_sufijo_son_europeos(symbol):
    assert mc.EU.owns_symbol(symbol)
    assert not mc.US.owns_symbol(symbol)


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "BRK-B"])
def test_los_simbolos_sin_sufijo_son_americanos(symbol):
    """BRK-B lleva guion, no punto: Yahoo usa el guion para las clases de
    accion americanas, asi que no puede confundirse con un sufijo de bolsa."""
    assert mc.US.owns_symbol(symbol)
    assert not mc.EU.owns_symbol(symbol)


@pytest.mark.parametrize("symbol", ["VOD.L", "NESN.SW", "VOLV-B.ST"])
def test_las_bolsas_que_no_son_del_euro_no_pertenecen_a_eu(symbol):
    """Londres cotiza en peniques, Zurich en francos y Estocolmo en coronas.
    Colarlos en el universo europeo romperia min_order_notional en silencio."""
    assert not mc.EU.owns_symbol(symbol)
    assert not mc.US.owns_symbol(symbol)


def test_foreign_symbols_conserva_el_orden_para_poder_nombrarlos():
    assert mc.EU.foreign_symbols(["SAN.MC", "AAPL", "SAP.DE", "MSFT"]) == [
        "AAPL", "MSFT"
    ]


# -- El fichero de universo --------------------------------------------------


def _cargar_universo() -> list[str]:
    ruta = Path(mc.EU.universe_file)
    if not ruta.is_absolute():
        ruta = Path(__file__).resolve().parent.parent / mc.EU.universe_file
    return [
        line.strip().upper()
        for line in ruta.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_el_universo_europeo_existe_y_tiene_los_89_simbolos():
    universo = _cargar_universo()

    assert len(universo) == 89
    assert len(set(universo)) == 89, "hay simbolos repetidos"


def test_todos_los_simbolos_del_universo_son_del_mercado_europeo():
    """Es la comprobacion que evita el fallo mas caro y mas silencioso: un
    ticker mal escrito no da error, se queda sin datos y desaparece del
    analisis."""
    assert mc.EU.foreign_symbols(_cargar_universo()) == []


def test_el_universo_americano_sigue_siendo_americano():
    ruta = Path(__file__).resolve().parent.parent / mc.US.universe_file
    universo = [
        line.strip().upper()
        for line in ruta.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert mc.US.foreign_symbols(universo) == []


# -- Resolucion del perfil ---------------------------------------------------


@pytest.fixture
def perfil_eu(db):
    profile_id = db.create_profile(name="europa-01", description="IBEX + ESTX")
    db.update_settings(profile_id, {"market": "eu", "benchmark": mc.EU.benchmark})
    db.set_profile_universe(profile_id, ["SAN.MC", "ASML.AS", "SAP.DE"])
    db.set_profile_status(profile_id, "active")
    return profile_id


def test_un_perfil_nace_en_el_mercado_americano(db):
    """El default es 'us' por las bases que ya existen, no por preferencia."""
    profile_id = db.create_profile(name="p")

    assert db.get_settings(profile_id)["market"] == "us"


def test_el_mercado_del_perfil_llega_a_los_settings(db, perfil_eu):
    settings = resolve_settings(db, perfil_eu, infra=INFRA)

    assert settings.market == "eu"


def test_el_mercado_entra_en_el_snapshot_del_ciclo(db, perfil_eu):
    """Sin el mercado en `cycles.settings_json`, un historico no se puede
    interpretar: las mismas horas significan cosas distintas segun la bolsa."""
    settings = resolve_settings(db, perfil_eu, infra=INFRA)

    assert settings.snapshot()["market"] == "eu"


def test_un_simbolo_americano_en_un_perfil_europeo_no_arranca(db, perfil_eu):
    """Falla al resolver, con el simbolo delante. Dejarlo pasar significaria que
    el analista decide sobre el cierre de ayer creyendo que es de hoy."""
    db.set_profile_universe(perfil_eu, ["SAN.MC", "AAPL", "SAP.DE"])

    with pytest.raises(ConfigError) as exc:
        resolve_settings(db, perfil_eu, infra=INFRA)

    assert "AAPL" in str(exc.value)
    assert "eu" in str(exc.value)


def test_un_simbolo_europeo_en_un_perfil_americano_tampoco(db):
    profile_id = db.create_profile(name="us-01")
    db.set_profile_universe(profile_id, ["AAPL", "SAN.MC"])
    db.set_profile_status(profile_id, "active")

    with pytest.raises(ConfigError) as exc:
        resolve_settings(db, profile_id, infra=INFRA)

    assert "SAN.MC" in str(exc.value)


def test_la_base_rechaza_un_mercado_que_no_esta_en_el_registro(db, perfil_eu):
    """Primera barrera: el CHECK del esquema. Es la que evita que un `market`
    invalido llegue siquiera a guardarse."""
    from src.db import DatabaseError

    with pytest.raises(DatabaseError, match="CHECK constraint failed"):
        db._execute(
            "update agent_settings set market = 'jp' where profile_id = ?",
            (perfil_eu,),
        )


def test_un_mercado_invalido_falla_con_el_nombre_del_perfil():
    """Segunda barrera, por si la fila llega de otro sitio (una base editada a
    mano, una importacion). El mensaje tiene que decir de que perfil se trata:
    con varios experimentos abiertos, 'mercado desconocido' a secas no orienta."""
    from src.profile_settings import _resolve_market

    with pytest.raises(ConfigError) as exc:
        _resolve_market({"market": "jp"}, label="europa-01")

    assert "europa-01" in str(exc.value)
    assert "jp" in str(exc.value)


def test_el_ciclo_europeo_se_salta_el_fin_de_semana_igual(db, perfil_eu):
    """La semantica de should_run no cambia con el mercado, solo el calendario."""
    settings = resolve_settings(db, perfil_eu, infra=INFRA)
    allowed, reason = mc.should_run("1d", eu(2026, 8, 8, 12), market=settings.market)

    assert not allowed
    assert "sin sesion" in reason


# -- Importacion de un .env heredado -----------------------------------------


def test_importar_un_env_con_valores_europeos_deduce_el_mercado(db):
    """Darlo por 'us' haria que el perfil recien creado fallara al resolverlo,
    que es el peor momento posible para descubrirlo."""
    from src.config import Settings

    env = Settings(
        sim_slippage_bps=5.0, sim_commission=0.0, model_api_key="k",
        model_base_url="http://stub", llm_model="m", llm_temperature=0.2,
        llm_timeout_seconds=120.0, llm_max_retries=3, db_path="x.db",
        portfolio_name="heredado", initial_budget=10_000.0,
        watchlist=("SAN.MC", "ITX.MC"), lookback_days=200, dry_run=False,
        log_level="CRITICAL",
    )

    profile_id = import_env_profile(db, env, name="heredado")

    assert db.get_settings(profile_id)["market"] == "eu"
    assert db.get_settings(profile_id)["benchmark"] == mc.EU.benchmark
    # Y el perfil resultante resuelve sin quejarse.
    assert resolve_settings(db, profile_id, infra=INFRA).market == "eu"


def test_importar_un_env_americano_sigue_dando_us(db):
    from src.config import Settings

    env = Settings(
        sim_slippage_bps=5.0, sim_commission=0.0, model_api_key="k",
        model_base_url="http://stub", llm_model="m", llm_temperature=0.2,
        llm_timeout_seconds=120.0, llm_max_retries=3, db_path="x.db",
        portfolio_name="heredado", initial_budget=10_000.0,
        watchlist=("AAPL", "MSFT"), lookback_days=200, dry_run=False,
        log_level="CRITICAL",
    )

    profile_id = import_env_profile(db, env, name="heredado")

    assert db.get_settings(profile_id)["market"] == "us"
    assert db.get_settings(profile_id)["benchmark"] == "SPY"


# -- Universo del ingestor por mercado ---------------------------------------


def test_el_universo_del_ingestor_viene_repartido_por_bolsa(db, perfil_eu):
    otro = db.create_profile(name="us-01")
    db.set_profile_universe(otro, ["AAPL", "MSFT"])
    db.set_profile_status(otro, "active")

    universos = db.active_universe_by_market()

    assert universos == {
        "eu": ["ASML.AS", "SAN.MC", "SAP.DE"],
        "us": ["AAPL", "MSFT"],
    }


def test_los_perfiles_pausados_no_entran(db, perfil_eu):
    db.set_profile_status(perfil_eu, "paused")

    assert db.active_universe_by_market() == {}


def test_un_perfil_con_solo_fichero_de_universo_no_llega_al_ingestor(db):
    """Trampa heredada de F2.4 que motiva que `new-profile` rellene las dos
    cosas: `universe_file` es lo que criba el screener para el ciclo, y
    `profile_universe` es lo que el ingestor sigue minuto a minuto. Un perfil con
    solo lo primero se queda sin precios en vivo sin que nada lo diga."""
    profile_id = db.create_profile(
        name="solo-fichero", settings={"universe_file": mc.EU.universe_file}
    )
    db.set_profile_status(profile_id, "active")

    assert db.active_universe_by_market() == {}


# -- El comando new-profile --------------------------------------------------


@pytest.fixture
def infra_tmp(tmp_path):
    return Infra(db_path=str(tmp_path / "cli.db"), log_level="CRITICAL")


def test_new_profile_rellena_universo_en_vivo_y_fichero(infra_tmp):
    from run import command_new_profile
    from src.db import Database

    assert command_new_profile(
        infra_tmp, name="europa-01", market="eu", watch=0, budget=10_000.0
    ) == 0

    with Database(path=infra_tmp.db_path) as database:
        profile_id = database.get_profile_by_name("europa-01")["id"]
        settings = database.get_settings(profile_id)

        assert settings["market"] == "eu"
        assert settings["benchmark"] == mc.EU.benchmark
        assert settings["universe_file"] == mc.EU.universe_file
        assert len(database.get_profile_universe(profile_id)) == 89


def test_new_profile_nace_en_borrador(infra_tmp):
    """Activarlo es un paso aparte: un perfil que naciera activo empezaria a
    consumir ingesta antes de que nadie revisara sus parametros."""
    from run import command_new_profile
    from src.db import Database

    command_new_profile(
        infra_tmp, name="europa-01", market="eu", watch=0, budget=10_000.0
    )

    with Database(path=infra_tmp.db_path) as database:
        assert database.get_profile_by_name("europa-01")["status"] == "draft"
        # Y por tanto todavia no pesa en el ingestor.
        assert database.active_universe_by_market() == {}


def test_new_profile_se_niega_a_seguir_el_sp500_entero(infra_tmp):
    """503 simbolos por minuto contra Yahoo desde una IP domestica es R2. El
    comando obliga a elegir en lugar de hacerlo por su cuenta."""
    from run import command_new_profile

    assert command_new_profile(
        infra_tmp, name="us-01", market="us", watch=0, budget=10_000.0
    ) == 2


def test_new_profile_acepta_el_sp500_con_un_tope_explicito(infra_tmp):
    from run import command_new_profile
    from src.db import Database

    assert command_new_profile(
        infra_tmp, name="us-01", market="us", watch=50, budget=10_000.0
    ) == 0

    with Database(path=infra_tmp.db_path) as database:
        profile_id = database.get_profile_by_name("us-01")["id"]
        assert len(database.get_profile_universe(profile_id)) == 50


def test_new_profile_rechaza_un_mercado_desconocido(infra_tmp):
    from run import command_new_profile

    assert command_new_profile(
        infra_tmp, name="x", market="eur", watch=0, budget=10_000.0
    ) == 2


def test_new_profile_exige_nombre(infra_tmp):
    from run import command_new_profile

    assert command_new_profile(
        infra_tmp, name="", market="eu", watch=0, budget=10_000.0
    ) == 2


def test_el_perfil_creado_por_el_comando_resuelve_sin_quejarse(infra_tmp):
    """La prueba que ata las dos mitades: lo que crea el comando es exactamente
    lo que `resolve_settings` acepta."""
    from run import command_new_profile
    from src.db import Database

    command_new_profile(
        infra_tmp, name="europa-01", market="eu", watch=0, budget=7_500.0
    )

    with Database(path=infra_tmp.db_path) as database:
        profile_id = database.get_profile_by_name("europa-01")["id"]
        settings = resolve_settings(database, profile_id, infra=INFRA)

    assert settings.market == "eu"
    assert settings.initial_budget == 7_500.0
    assert len(settings.watchlist) == 89


# -- Liquidez minima por mercado (FE.11) -------------------------------------


def test_el_suelo_de_liquidez_europeo_es_mas_bajo_que_el_americano():
    """Medido el 2026-08-08: con los 20 M del S&P 500, el screener europeo
    descarta 15 de los 89 —las medianas del IBEX por las que se anadio el
    indice— y lo hace en silencio, porque un valor filtrado no es un error."""
    assert mc.EU.min_turnover == 5_000_000.0
    assert mc.US.min_turnover == 20_000_000.0


def test_un_suelo_de_liquidez_no_positivo_se_rechaza_al_validar():
    """Un 0 no revienta: apaga el filtro sin decirlo."""
    roto = dataclasses.replace(mc.EU, min_turnover=0.0)

    with pytest.raises(ValueError, match="min_turnover"):
        mc._check_markets([roto])


@pytest.mark.parametrize("market, watch, esperado", [
    ("eu", 0, 5_000_000.0),
    ("us", 50, 20_000_000.0),
])
def test_new_profile_pone_el_suelo_de_liquidez_del_mercado(
    infra_tmp, market, watch, esperado
):
    """FE.11: antes esto era un aviso impreso que habia que aplicar a mano
    abriendo la base. Un aviso que exige trabajo manual acaba sin aplicarse, y
    el sintoma es un universo mas pequeno del que se cree."""
    from run import command_new_profile
    from src.db import Database

    command_new_profile(
        infra_tmp, name=f"perfil-{market}", market=market, watch=watch,
        budget=10_000.0,
    )

    with Database(path=infra_tmp.db_path) as database:
        profile_id = database.get_profile_by_name(f"perfil-{market}")["id"]
        settings = database.get_settings(profile_id)
        resuelto = resolve_settings(database, profile_id, infra=INFRA)

    assert settings["screener_min_dollar_volume"] == esperado
    # Y llega hasta el screener, que es el unico sitio donde el numero hace algo.
    assert resuelto.screener.min_dollar_volume == esperado


def test_el_reparto_cuadra_con_el_universo_plano(db, perfil_eu):
    """`active_universe` y `active_universe_by_market` no pueden discrepar: la
    segunda es la primera con la bolsa puesta."""
    otro = db.create_profile(name="us-01")
    db.set_profile_universe(otro, ["AAPL"])
    db.set_profile_status(otro, "active")

    plano = set(db.active_universe())
    repartido = {s for symbols in db.active_universe_by_market().values()
                 for s in symbols}

    assert plano == repartido
