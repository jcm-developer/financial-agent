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
-- portfolios: una fila por experimento. El presupuesto vive aqui.
-- ---------------------------------------------------------------------------
create table if not exists portfolios (
    id             text primary key,
    name           text not null unique,
    mode           text not null check (mode in ('paper', 'live')),
    initial_budget real not null check (initial_budget > 0),
    is_active      integer not null default 1,
    notes          text,
    created_at     text not null
);

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
-- consigo misma y dejaria de detectar nada. Manteniendolas aparte, el codigo del
-- ciclo es identico con broker simulado y con Alpaca.
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
