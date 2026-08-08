-- ===========================================================================
-- financial-bot :: esquema SQLite
--
-- `src/db.py` ejecuta este fichero al abrir la conexion, en cada arranque. Es
-- idempotente (todo es CREATE ... IF NOT EXISTS), asi que hace de migracion
-- automatica: al anadir una tabla nueva aqui, aparece en el proximo ciclo.
--
-- Convenciones:
--   * Los ids de entidad son TEXT con un UUID generado en Python.
--   * Las fechas son TEXT en ISO-8601 UTC.
--   * Las columnas *_json son TEXT con JSON serializado; se consultan con las
--     funciones json_extract() / json_each() de SQLite.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- profiles: un experimento. Es el padre de todo lo demas.
--
-- Existe separada de `portfolios` a proposito, aunque la relacion sea 1:1:
-- `profiles` es la identidad del experimento (nombre, estado, parametros) y
-- `portfolios` es la entidad contable (presupuesto, modo). Mantenerlas aparte
-- deja intacto todo el codigo del ciclo, que trabaja con portfolio_id.
--
-- Borrar un perfil arrastra su cartera, y con ella ciclos, decisiones, ordenes,
-- posiciones y curva de capital: todas cuelgan de portfolios con cascade.
-- ---------------------------------------------------------------------------
create table if not exists profiles (
    id          text primary key,
    name        text not null unique,
    description text,
    status      text not null default 'draft'
                check (status in ('draft', 'active', 'paused', 'archived')),
    created_at  text not null,
    updated_at  text not null,
    archived_at text
);

create index if not exists profiles_status_idx on profiles (status);

-- ---------------------------------------------------------------------------
-- portfolios: la contabilidad de un experimento. El presupuesto vive aqui.
-- ---------------------------------------------------------------------------
create table if not exists portfolios (
    id             text primary key,
    profile_id     text references profiles (id) on delete cascade,
    name           text not null unique,
    mode           text not null check (mode in ('paper', 'live')),
    initial_budget real not null check (initial_budget > 0),
    is_active      integer not null default 1,
    notes          text,
    created_at     text not null
);

-- Una cartera por perfil. Es un indice y no una restriccion de columna para que
-- las carteras heredadas, sin perfil todavia, no choquen entre si por NULL.
create unique index if not exists portfolios_one_per_profile
    on portfolios (profile_id) where profile_id is not null;

-- ---------------------------------------------------------------------------
-- cycles: una fila por ejecucion del agente. Es la unidad de auditoria.
-- status='halted' significa que salto el kill switch de perdida diaria.
-- ---------------------------------------------------------------------------
create table if not exists cycles (
    id                   text primary key,
    portfolio_id         text not null references portfolios (id) on delete cascade,
    status               text not null default 'running'
                         check (status in ('running', 'completed', 'failed', 'halted')),
    started_at           text not null,
    finished_at          text,
    equity_start         real,
    equity_end           real,
    cash_start           real,
    market_open          integer,
    symbols_scanned_json text,
    llm_model            text,
    -- Copia de los parametros con los que corrio este ciclo. Sin esto, un
    -- experimento cuyos ajustes se editan a mitad deja de ser interpretable:
    -- no se sabria que configuracion produjo cada decision.
    settings_json        text,
    -- Cuantas veces se pregunto al modelo en este ciclo y cuantas se quedaron sin
    -- respuesta. El analista se traga los errores del LLM a proposito, asi que sin
    -- este par un ciclo con la cuota agotada se registra igual que una sesion sin
    -- oportunidades: 'completed' y cero propuestas (F6.9).
    analyst_calls        integer not null default 0,
    analyst_failures     integer not null default 0,
    error                text
);

create index if not exists cycles_portfolio_started_idx
    on cycles (portfolio_id, started_at desc);

-- ---------------------------------------------------------------------------
-- market_snapshots: los datos exactos que vio el LLM. Sin esto no se puede
-- reproducir una decision a posteriori.
-- ---------------------------------------------------------------------------
create table if not exists market_snapshots (
    id              integer primary key autoincrement,
    cycle_id        text not null references cycles (id) on delete cascade,
    symbol          text not null,
    as_of           text not null,
    price           real not null,
    indicators_json text not null default '{}',
    created_at      text not null
);

create index if not exists market_snapshots_cycle_symbol_idx
    on market_snapshots (cycle_id, symbol);

-- ---------------------------------------------------------------------------
-- decisions: la salida cruda del LLM. La tabla mas valiosa del experimento:
-- permite medir despues si el razonamiento del modelo tenia algun valor.
-- ---------------------------------------------------------------------------
create table if not exists decisions (
    id                text primary key,
    cycle_id          text not null references cycles (id) on delete cascade,
    portfolio_id      text not null references portfolios (id) on delete cascade,
    snapshot_id       integer references market_snapshots (id) on delete set null,
    symbol            text not null,
    kind              text not null check (kind in ('entry', 'exit')),
    action            text not null check (action in ('buy', 'sell', 'hold')),
    conviction        integer not null check (conviction between 0 and 100),
    thesis            text,
    risks             text,
    horizon_days      integer,
    suggested_stop    real,
    suggested_target  real,
    reference_price   real,
    llm_model         text,
    latency_ms        integer,
    prompt_tokens     integer,
    completion_tokens integer,
    raw_response_json text,
    created_at        text not null
);

create index if not exists decisions_portfolio_created_idx
    on decisions (portfolio_id, created_at desc);
create index if not exists decisions_symbol_idx on decisions (symbol);

-- ---------------------------------------------------------------------------
-- risk_events: el veredicto del Risk Manager para cada propuesta, incluidos
-- los rechazos. Los rechazos son la evidencia de que la barrera funciona.
-- ---------------------------------------------------------------------------
create table if not exists risk_events (
    id                text primary key,
    cycle_id          text not null references cycles (id) on delete cascade,
    portfolio_id      text not null references portfolios (id) on delete cascade,
    decision_id       text references decisions (id) on delete set null,
    symbol            text,
    verdict           text not null check (verdict in ('approved', 'rejected')),
    rule              text,
    reason            text not null,
    approved_qty      real,
    approved_notional real,
    stop_price        real,
    target_price      real,
    details_json      text default '{}',
    created_at        text not null
);

create index if not exists risk_events_cycle_idx on risk_events (cycle_id);
create index if not exists risk_events_verdict_idx on risk_events (verdict, created_at desc);

-- ---------------------------------------------------------------------------
-- orders: todo intento de orden, incluidos los fallidos y los no ejecutados.
-- ---------------------------------------------------------------------------
create table if not exists orders (
    id               text primary key,
    cycle_id         text references cycles (id) on delete set null,
    portfolio_id     text not null references portfolios (id) on delete cascade,
    decision_id      text references decisions (id) on delete set null,
    risk_event_id    text references risk_events (id) on delete set null,
    symbol           text not null,
    side             text not null check (side in ('buy', 'sell')),
    qty              real not null check (qty > 0),
    order_type       text not null default 'market',
    limit_price      real,
    stop_price       real,
    target_price     real,
    status           text not null default 'submitted'
                     check (status in ('submitted', 'accepted', 'filled',
                                       'partially_filled', 'rejected',
                                       'canceled', 'failed', 'dry_run')),
    broker_order_id  text,
    filled_qty       real,
    filled_avg_price real,
    error            text,
    submitted_at     text not null,
    updated_at       text not null
);

create index if not exists orders_portfolio_submitted_idx
    on orders (portfolio_id, submitted_at desc);
create unique index if not exists orders_broker_order_id_key
    on orders (broker_order_id) where broker_order_id is not null;

-- ---------------------------------------------------------------------------
-- positions: estado de las posiciones gestionadas por el bot. Se reconcilia
-- con el broker en cada ciclo (el broker es la fuente de verdad de qty/precio).
-- ---------------------------------------------------------------------------
create table if not exists positions (
    id             text primary key,
    portfolio_id   text not null references portfolios (id) on delete cascade,
    symbol         text not null,
    status         text not null default 'open' check (status in ('open', 'closed')),
    qty            real not null,
    entry_price    real not null,
    stop_price     real,
    target_price   real,
    thesis         text,
    horizon_days   integer,
    entry_order_id text references orders (id) on delete set null,
    exit_order_id  text references orders (id) on delete set null,
    exit_price     real,
    realized_pnl   real,
    exit_reason    text,
    opened_at      text not null,
    closed_at      text
);

-- Una sola posicion abierta por simbolo y cartera.
create unique index if not exists positions_one_open_per_symbol
    on positions (portfolio_id, symbol) where status = 'open';
create index if not exists positions_portfolio_status_idx
    on positions (portfolio_id, status);

-- ---------------------------------------------------------------------------
-- equity_snapshots: curva de capital, una fila por ciclo.
-- ---------------------------------------------------------------------------
create table if not exists equity_snapshots (
    id              integer primary key autoincrement,
    portfolio_id    text not null references portfolios (id) on delete cascade,
    cycle_id        text references cycles (id) on delete set null,
    as_of           text not null,
    equity          real not null,
    cash            real not null,
    positions_value real not null default 0,
    open_positions  integer not null default 0,
    day_pnl         real,
    day_pnl_pct     real
);

create index if not exists equity_snapshots_portfolio_as_of_idx
    on equity_snapshots (portfolio_id, as_of desc);

-- ===========================================================================
-- Cache de barras
--
-- Con un universo de 500 activos, volver a descargar 275 barras de cada uno en
-- cada ciclo son 138.000 filas por ciclo: Yahoo acabaria devolviendo 429. Con la
-- cache, el primer arranque baja el historico completo y los siguientes solo
-- piden las barras nuevas.
--
-- La clave primaria hace el refresco idempotente: un `insert or replace` sobre la
-- misma sesion actualiza la barra en lugar de duplicarla, lo que importa porque
-- la ultima barra del dia cambia mientras el mercado sigue abierto.
-- ===========================================================================

create table if not exists bar_cache (
    symbol   text not null,
    interval text not null,          -- 1d | 1h
    ts       text not null,          -- ISO-8601 UTC del inicio de la barra
    open     real not null,
    high     real not null,
    low      real not null,
    close    real not null,
    volume   real not null default 0,
    primary key (symbol, interval, ts)
);

create index if not exists bar_cache_symbol_interval_ts
    on bar_cache (symbol, interval, ts desc);

-- Estado del refresco por simbolo: permite saber a quien hay que pedir datos sin
-- recorrer millones de filas, y detectar simbolos que Yahoo ya no reconoce.
create table if not exists bar_cache_state (
    symbol        text not null,
    interval      text not null,
    last_ts       text,
    last_refresh  text,
    bars          integer not null default 0,
    failures      integer not null default 0,
    last_error    text,
    primary key (symbol, interval)
);

-- ===========================================================================
-- Broker simulado (BROKER=sim)
--
-- Estas tablas son el libro contable del broker, deliberadamente separado de
-- `positions`, que es el registro de *por que* se abrio cada posicion. Podrian
-- fusionarse, pero entonces la reconciliacion del ciclo compararia una tabla
-- consigo misma y dejaria de detectar nada. Manteniendolas aparte, el ciclo
-- reconcilia contra una fuente externa de verdad igual que lo haria con un
-- broker real.
-- ===========================================================================

create table if not exists sim_accounts (
    id           text primary key,   -- = portfolio_id
    cash         real not null,
    initial_cash real not null,
    -- Equity al cierre de la sesion anterior: referencia del P&L del dia y del
    -- kill switch. `last_session` evita recalcularlo varias veces al dia.
    last_equity  real not null,
    last_session text,
    created_at   text not null,
    updated_at   text not null
);

create table if not exists sim_positions (
    id               text primary key,
    account_id       text not null references sim_accounts (id) on delete cascade,
    symbol           text not null,
    qty              real not null check (qty > 0),
    avg_entry_price  real not null,
    opened_at        text not null
);

create unique index if not exists sim_positions_account_symbol
    on sim_positions (account_id, symbol);

-- Registro de ejecuciones simuladas: precio, base de calculo y costes. Permite
-- comprobar despues a que precio se ejecuto cada orden y por que.
create table if not exists sim_fills (
    id           integer primary key autoincrement,
    account_id   text not null references sim_accounts (id) on delete cascade,
    symbol       text not null,
    side         text not null check (side in ('buy', 'sell')),
    qty          real not null check (qty > 0),
    price        real not null,
    basis        text,               -- next_open | close
    slippage_bps real not null default 0,
    commission   real not null default 0,
    realized_pnl real,
    filled_at    text not null
);

create index if not exists sim_fills_account_idx on sim_fills (account_id, filled_at desc);

-- ===========================================================================
-- Parametros del agente, por experimento
--
-- Sustituyen a las variables de entorno: cada perfil lleva los suyos y son
-- editables en caliente desde la interfaz. `src/config.py` queda solo para
-- infraestructura (rutas, nivel de log).
--
-- Los limites duros del risk manager son NULL a proposito: NULL significa
-- "derivalo de risk_profile y diversification". Solo se rellenan cuando el
-- usuario activa el modo avanzado y los fija a mano. Asi mover un slider sigue
-- surtiendo efecto sin tener que recalcular y reescribir nueve columnas.
-- ===========================================================================

create table if not exists agent_settings (
    profile_id             text primary key references profiles (id) on delete cascade,

    -- Modelo
    llm_provider           text not null default 'nvidia'
                           check (llm_provider in ('nvidia', 'anthropic', 'openai')),
    llm_model              text not null default 'meta/llama-3.3-70b-instruct',
    llm_api_key            text,
    llm_temperature        real not null default 0.2
                           check (llm_temperature between 0 and 2),
    llm_timeout_seconds    real not null default 120 check (llm_timeout_seconds >= 5),
    llm_max_retries        integer not null default 3
                           check (llm_max_retries between 1 and 10),
    analyst_persona        text,

    -- Estrategia. Estos dos mandan sobre los limites de abajo.
    risk_profile           integer not null default 5
                           check (risk_profile between 1 and 10),
    diversification        integer not null default 5
                           check (diversification between 1 and 10),
    horizon_days           integer not null default 10 check (horizon_days > 0),

    -- Bolsa contra la que opera el perfil. Decide el horario, el calendario de
    -- festivos y **la divisa de la cartera**: no hay conversion de divisa en
    -- ninguna parte del proyecto, asi que un perfil 'eu' esta integramente en
    -- euros. Mezclar mercados dentro de un mismo perfil no esta soportado y por
    -- eso es una columna del perfil y no del simbolo. Ver src/market_calendar.py.
    -- El default es 'us' por las bases que ya existen, no por preferencia.
    market                 text not null default 'us'
                           check (market in ('us', 'eu')),

    -- Universo. `universe_file` vacio o NULL = no hay embudo: se analiza la
    -- watchlist de `profile_universe` tal cual. Con fichero, el screener criba
    -- ese universo y `profile_universe` deja de mandar.
    universe_file          text,
    screener_mode          text not null default 'score'
                           check (screener_mode in ('score', 'random')),
    screener_top_n         integer not null default 20
                           check (screener_top_n between 1 and 200),
    screener_min_dollar_volume real not null default 20000000,
    screener_min_price     real not null default 5,
    screener_max_volatility_pct real not null default 120,

    allow_shorts           integer not null default 0,
    excluded_sectors_json  text not null default '[]',
    cash_reserve_pct       real not null default 0
                           check (cash_reserve_pct between 0 and 100),
    -- Indice de referencia. El default vale para 'us'; un perfil 'eu' quiere
    -- EXW1.DE (iShares EURO STOXX 50), que es el equivalente en euros y con el
    -- mismo horario. `Market.benchmark` lleva el que corresponde a cada uno.
    benchmark              text not null default 'SPY',

    -- Ejecucion. No hay columna de broker: la unica implementacion es el
    -- simulador de `sim_broker.py`.
    initial_budget         real not null default 10000 check (initial_budget > 0),
    bar_interval           text not null default '1d'
                           check (bar_interval in ('1m', '1h', '1d')),
    lookback_days          integer not null default 200,
    cycle_times            text not null default '22:15',
    cycle_tz               text not null default 'Europe/Madrid',
    sim_slippage_bps       real not null default 5 check (sim_slippage_bps >= 0),
    sim_commission         real not null default 0 check (sim_commission >= 0),
    dry_run                integer not null default 0,
    skip_when_market_closed integer not null default 1,

    -- Limites duros. NULL = derivado de los sliders (ver comentario de arriba).
    advanced_overrides     integer not null default 0,
    risk_per_trade_pct     real,
    max_position_pct       real,
    max_total_exposure_pct real,
    max_open_positions     integer,
    max_daily_loss_pct     real,
    min_conviction         integer,
    stop_atr_multiple      real,
    min_reward_risk        real,
    min_order_notional     real,

    extra_json             text not null default '{}',
    updated_at             text not null
);

-- Quien cambio que y cuando. Se pide poder editar los parametros en cualquier
-- momento; sin este registro, comparar dos experimentos deja de tener sentido
-- porque no se sabe con que ajustes corrio cada tramo.
create table if not exists agent_settings_history (
    id         integer primary key autoincrement,
    profile_id text not null references profiles (id) on delete cascade,
    field      text not null,
    old_value  text,
    new_value  text,
    source     text,            -- ui | api | cli
    changed_at text not null
);

create index if not exists agent_settings_history_profile_idx
    on agent_settings_history (profile_id, changed_at desc);

-- Universo a vigilar por perfil. La union de todos los perfiles activos es lo
-- que el ingestor pide cada minuto.
create table if not exists profile_universe (
    profile_id text not null references profiles (id) on delete cascade,
    symbol     text not null,
    added_at   text not null,
    primary key (profile_id, symbol)
);

create index if not exists profile_universe_symbol_idx on profile_universe (symbol);

-- ===========================================================================
-- Datos de mercado en vivo (ingestor, cada minuto)
--
-- Separadas de `bar_cache` a proposito: `bar_cache` es la despensa del agente
-- para calcular indicadores, se llena a demanda y puede podarse sin perder
-- nada. Estas dos son el registro de lo que el mercado hizo minuto a minuto, y
-- son la materia prima del backtesting futuro.
-- ===========================================================================

-- Ultimo precio conocido de cada simbolo. Una fila por simbolo: el ingestor
-- hace `insert or replace`, asi que la tabla no crece.
create table if not exists quotes_live (
    symbol     text primary key,
    price      real not null,
    prev_close real,
    change_pct real,
    volume     real,
    as_of      text not null,   -- inicio de la barra, ISO-8601 UTC
    updated_at text not null    -- cuando lo escribimos nosotros
);

-- Historico minuto a minuto. La clave primaria hace el refresco idempotente,
-- que importa porque la barra del minuto en curso cambia mientras se consulta.
create table if not exists bars_1m (
    symbol text not null,
    ts     text not null,       -- inicio de la barra, ISO-8601 UTC
    open   real not null,
    high   real not null,
    low    real not null,
    close  real not null,
    volume real not null default 0,
    primary key (symbol, ts)
);

create index if not exists bars_1m_symbol_ts_idx on bars_1m (symbol, ts desc);
create index if not exists bars_1m_ts_idx on bars_1m (ts);

-- Una fila por tick del ingestor. Es lo que permite ver en la interfaz si la
-- ingesta esta sana, y lo que responde a "por que falta el precio de las 15:42".
create table if not exists ingest_runs (
    id                integer primary key autoincrement,
    started_at        text not null,
    finished_at       text,
    -- 'tick' es la pasada de cada minuto; 'backfill' es el relleno de huecos que
    -- corre una vez al dia fuera de ventana (F2.10). Se distinguen porque un
    -- backfill descarga varios dias de golpe: mezclado con los ticks, una sola de
    -- sus filas desplaza cualquier media de latencia y el panel de salud pasa a
    -- medir otra cosa.
    kind              text not null default 'tick'
                      check (kind in ('tick', 'backfill')),
    symbols_requested integer not null default 0,
    symbols_ok        integer not null default 0,
    symbols_failed    integer not null default 0,
    latency_ms        integer,
    rate_limited      integer not null default 0,
    error             text
);

create index if not exists ingest_runs_started_idx on ingest_runs (started_at desc);

-- ===========================================================================
-- Vistas de analisis. `python run.py report` las consulta.
-- Se usa sum(case when ...) en lugar de FILTER para no depender de la version
-- de SQLite que traiga el interprete.
-- ===========================================================================

-- Rendimiento por simbolo sobre posiciones cerradas.
drop view if exists v_performance_by_symbol;
create view v_performance_by_symbol as
select
    p.portfolio_id,
    p.symbol,
    count(*)                                                     as trades,
    sum(case when p.realized_pnl > 0 then 1 else 0 end)          as wins,
    round(100.0 * sum(case when p.realized_pnl > 0 then 1 else 0 end)
          / count(*), 2)                                         as win_rate_pct,
    round(sum(p.realized_pnl), 2)                                as total_pnl,
    round(avg(p.realized_pnl), 2)                                as avg_pnl,
    round(avg(julianday(p.closed_at) - julianday(p.opened_at)), 1) as avg_holding_days
from positions p
where p.status = 'closed'
group by p.portfolio_id, p.symbol;

-- Calibracion del modelo: la conviccion declarada predice el resultado?
-- Si el win rate no crece con el bucket, el modelo no esta aportando senal.
drop view if exists v_conviction_calibration;
create view v_conviction_calibration as
select
    d.portfolio_id,
    (cast(d.conviction / 10 as integer) * 10)                    as conviction_bucket,
    count(*)                                                     as trades,
    round(avg(p.realized_pnl), 2)                                as avg_pnl,
    round(100.0 * sum(case when p.realized_pnl > 0 then 1 else 0 end)
          / count(*), 2)                                         as win_rate_pct
from decisions d
join orders o    on o.decision_id = d.id
join positions p on p.entry_order_id = o.id and p.status = 'closed'
where d.kind = 'entry'
group by d.portfolio_id, conviction_bucket
order by conviction_bucket;

-- Contra que limite choca el modelo mas a menudo.
drop view if exists v_risk_rejections;
create view v_risk_rejections as
select
    portfolio_id,
    rule,
    count(*)        as rejections,
    max(created_at) as last_seen
from risk_events
where verdict = 'rejected'
group by portfolio_id, rule
order by rejections desc;

-- Reparto de acciones propuestas por el analista, por simbolo.
drop view if exists v_decision_mix;
create view v_decision_mix as
select
    portfolio_id,
    symbol,
    kind,
    action,
    count(*)              as n,
    round(avg(conviction), 1) as avg_conviction
from decisions
group by portfolio_id, symbol, kind, action;
