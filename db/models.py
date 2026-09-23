"""
Esquema de la base de datos (SQLite, archivo versionado en el repo).

Campos alineados con lo confirmado por fetch autenticado real (2026-08-17,
ver clients/futmondo_client.py para el detalle completo):
  - Futmondo (api.futmondo.com): roster/market devuelven `role` como
    palabra completa en ESPAÑOL ("portero"/"defensa"/"centrocampista"/
    "delantero") — se normaliza a POR/DEF/MED/DEL (FUTMONDO_POSITION_MAP)
    al escribir aquí, así que `players.position` YA está en la convención
    corta, no en la de Futmondo. `status` real visto en producción
    (2026-08-18, ver TODO.md #5): "" y "ok" (sano), "doubt" (duda) e
    "injuredN" (lesionado, tier numérico — ver
    clients.futmondo_client.is_injury_status(), ya confirmado con estos
    casos reales). No hay equivalente confirmado al "-" de puntos en
    pretemporada de Comunio, pero `_parse_int`/`_parse_float` en
    jobs/sync_data.py se dejan igual de defensivos por si acaso.
  - Understat (clients/laliga_stats_client.py): xG/xA/minutos/goles/tarjetas
    vía GET /getLeagueData/{league}/{season}, campos confirmados 1:1 contra
    la respuesta real (esto no cambia con la migración a Futmondo).

FBref queda descartado (bloquea con Cloudflare, ver README) — no hay
columnas específicas de esa fuente.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id              TEXT PRIMARY KEY,      -- id de Futmondo
    name            TEXT NOT NULL,
    team            TEXT,
    position        TEXT,                  -- POR | DEF | MED | DEL (convención interna)
    understat_id    TEXT,                  -- cruce por nombre normalizado, ver index_players_by_name()
    updated_at      TEXT NOT NULL
);

-- Snapshot de cada sync: precio/VM, puntos y estado tal como los reporta
-- Futmondo en ese momento (para poder ver evolución y auditar decisiones).
CREATE TABLE IF NOT EXISTS futmondo_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    price           INTEGER,                -- "value" (VM actual)
    buy_price       INTEGER,                -- "buyPrice" tal cual lo da Futmondo (ver
                                             -- clients/futmondo_client.py): NO distingue "comprado
                                             -- por el bot" de "asignado con la plantilla inicial",
                                             -- solo se guarda para referencia/auditoría; para eso
                                             -- se usa get_won_bid_prices() (tabla `bids`) en su lugar
    points          INTEGER,                -- "points" acumulados
    last_points     INTEGER,                -- último valor de "average.fitness" = puntos de la jornada más reciente
    average_points  REAL,                   -- "average.average"
    on_market       INTEGER,                 -- 0/1: roster.market (puesto en venta) o 1 fijo si viene del mercado de fichajes
    status          TEXT,                    -- valor real de Futmondo, ver clients.futmondo_client.is_injury_status()
    listing_price   INTEGER,                 -- "price" del listado (precio de SALIDA que pone quien vende -- otro
                                              -- manager o el "Computer" -- NO el VM); NULL fuera de mercado (roster)
    is_clause       INTEGER,                 -- 0/1: "isClause" del listado (solo tiene sentido si listing_price no es NULL)
    recent_points   TEXT,                    -- JSON de "average.fitness": puntos de las últimas 5 jornadas del
                                              -- EQUIPO, de la más antigua a la más reciente, 0 si no jugó
                                              -- (confirmado 2026-09-23, ver clients/futmondo_client.py)
    recorded_at     TEXT NOT NULL
);

-- Snapshot de stats externas (Understat), una fila por jugador y sync.
CREATE TABLE IF NOT EXISTS external_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    season          TEXT NOT NULL,
    games           INTEGER,
    minutes_played  INTEGER,                 -- Understat "time"
    goals           INTEGER,
    non_penalty_goals INTEGER,               -- Understat "npg"
    assists         INTEGER,
    xg              REAL,
    non_penalty_xg  REAL,                    -- Understat "npxG"
    xa              REAL,
    xg_chain        REAL,
    xg_buildup      REAL,
    yellow_cards    INTEGER,
    red_cards       INTEGER,
    understat_position TEXT,                 -- código Understat, ej. "F M S"
    team_games      INTEGER,                 -- partidos YA JUGADOS por el equipo esta temporada
                                              -- (len(teams[id]["history"]) de get_league_data(), NO
                                              -- "games" del jugador -- ver TODO.md #12), NULL si el
                                              -- equipo aún no tiene ese dato en Understat o si esta
                                              -- fila viene del fallback de temporada anterior (games/
                                              -- minutes_played de esa fila ya son de temporada
                                              -- completa, mezclarlos con el team_games de la
                                              -- temporada actual daría un ratio sin sentido)
    team_clean_sheets INTEGER,               -- de los `team_games` de arriba, en cuántos el equipo del
                                              -- jugador NO encajó (`missed`==0` en teams[id]["history"]) --
                                              -- Futmondo da puntos extra por portería a cero (sobre todo a
                                              -- POR/DEF), ver engine.evaluator.normalize_pool
                                              -- ("clean_sheet_rate") y jobs.sync_data._team_clean_sheets_by_title().
                                              -- Mismo NULL que team_games (y por el mismo motivo) si el
                                              -- equipo aún no tiene historial esta temporada o la fila es
                                              -- de fallback a temporada anterior.
    source          TEXT NOT NULL DEFAULT 'understat',
    recorded_at     TEXT NOT NULL
);

-- Sin id de oferta que guardar (a diferencia de Comunio): Futmondo no
-- devuelve ninguno al pujar (ver clients.futmondo_client.FutmondoClient.
-- place_bid) — la reconciliación en jobs/sync_data.py se hace por
-- player_id contra la plantilla y el mercado actuales, no por id de oferta.
CREATE TABLE IF NOT EXISTS bids (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    amount          INTEGER NOT NULL,
    status          TEXT NOT NULL,         -- 'placed' | 'won' | 'lost' | 'failed' | 'cancelled'
    score           REAL,                  -- score del evaluator que justificó la puja
    reason          TEXT,                  -- explicación legible de por qué se decidió pujar (auditoría del score)
    error           TEXT,                  -- mensaje de error real (str(excepción)) si status='failed', NULL en el resto de casos
                                            -- (2026-08-23: antes solo quedaba en memoria/Telegram, no en la BD --
                                            -- ver TODO.md #18, imposible diagnosticar un fallo real después de la pasada)
    created_at      TEXT NOT NULL
);

-- Registro de OFERTAS DE COMPRA recibidas de otros managers sobre
-- jugadores propios puestos en venta NORMAL (`isClause: false` --
-- clause.rosterbid tiene su propio mecanismo, no pasa por aquí; ver
-- clients.futmondo_client.get_my_players_in_market()/accept_sale_offer(),
-- TODO.md #15). Pensada puramente para ANÁLISIS de las dinámicas de
-- precio del mercado -- si el `asking_price` que calcula
-- engine/selling_strategy.py es realista frente a lo que otros managers
-- realmente ofrecen (¿nos quedamos cortos y vendemos por debajo de lo que
-- se podría haber pedido? ¿pedimos demasiado y nunca llega una oferta?) --
-- NO es la fuente de verdad operativa (para eso está `sales`, que ya
-- registra el precio pedido al listar, y `bids`, que son las pujas de
-- COMPRA que hace el propio bot, no las que recibe).
--
-- Una fila por oferta real de Futmondo (`futmondo_bid_id` UNIQUE, ver
-- record_received_offer() -- INSERT OR IGNORE): una oferta todavía
-- abierta que jobs/run_sales.py vuelve a ver en la siguiente pasada (dos
-- veces al día) sin haberse resuelto genera una sola fila, con el precio
-- de cuando se vio POR PRIMERA VEZ -- si Futmondo permitiera modificar el
-- importe de una oferta ya abierta (sin confirmar, ver
-- /5/market/modifybid en el docstring de clients/futmondo_client.py) esta
-- tabla no lo reflejaría.
CREATE TABLE IF NOT EXISTS received_sale_offers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id          TEXT NOT NULL REFERENCES players(id),
    futmondo_bid_id    TEXT NOT NULL UNIQUE,   -- "bids[].id" de get_my_players_in_market()
    listing_price      INTEGER NOT NULL,        -- nuestro precio pedido en ese momento ("price" del listado, no el VM)
    offer_price        INTEGER NOT NULL,        -- "bids[].price"
    bidder_name        TEXT,                    -- "bids[].userTeam.name"
    bidder_slug        TEXT,                    -- "bids[].userTeam.slug"
    accepted           INTEGER NOT NULL DEFAULT 0,  -- 0/1, ver mark_offer_accepted()
    seen_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    asking_price    INTEGER NOT NULL,       -- precio pedido ("value" en el momento de listar)
    purchase_price  INTEGER,                -- importe realmente pagado en la puja ganada, ver get_won_bid_prices()
    profit          INTEGER,                -- asking_price - purchase_price
    profit_pct      REAL,
    status          TEXT NOT NULL,          -- 'listed' | 'sold' | 'delisted' | 'failed'
    reason          TEXT,                   -- justificación de negocio (por qué decide_sales() eligió vender)
    error           TEXT,                   -- mensaje real del fallo (str(excepción)) si status='failed' -- ver
                                             -- _ensure_column más abajo y _persist_sale() en jobs/run_sales.py.
                                             -- Mismo fix que TODO.md #18 aplicó a `bids.error`: sin esto, un
                                             -- fallo de listado repetido (caso real: Galarreta, 14 intentos
                                             -- fallidos seguidos el 2026-09-01/02 antes de venderse) solo vivía
                                             -- en el mensaje de Telegram de esa pasada, no en la BD.
    created_at      TEXT NOT NULL
);

-- "Swap" (a petición del usuario, 2026-08-29, ver docstring de
-- jobs/run_sales.py y config.SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT): una
-- fila por venta que calificó ÚNICAMENTE por la vía "oportunidad de
-- mercado" de engine/selling_strategy.py -- guarda qué candidato de
-- mercado la motivó, para poder comprobar al ACEPTAR una oferta si ese
-- reemplazo (o uno equivalente, si hubo que retargetear -- ver
-- jobs.run_sales._resolve_swap_target()) sigue realmente disponible antes
-- de permitir quedarse temporalmente sin ningún suplente sano en esa
-- posición. Una fila por `sale_id` (PK, no autoincrement -- referencia
-- directa a la fila de `sales` que este swap justificó); `target_player_id`
-- es el candidato VIGENTE ahora mismo (se sobreescribe al retargetear,
-- ver retarget_swap_target()), mientras que `original_target_player_id`
-- nunca cambia, guardado solo para auditoría de qué motivó la venta el
-- día que se listó.
CREATE TABLE IF NOT EXISTS sale_swap_targets (
    sale_id                    INTEGER PRIMARY KEY REFERENCES sales(id),
    player_id                  TEXT NOT NULL REFERENCES players(id),  -- el propio, puesto en venta
    target_player_id           TEXT NOT NULL REFERENCES players(id),  -- candidato de mercado objetivo VIGENTE
    target_price               INTEGER,
    original_target_player_id  TEXT NOT NULL,
    retargeted                 INTEGER NOT NULL DEFAULT 0,  -- 0/1: si target_player_id ya no es el original
    updated_at                 TEXT NOT NULL
);

-- Venta de un TOP de su posición condicionada a fichar antes a su
-- sustituto (a petición del usuario, 2026-09-23, ver docstring de
-- engine/selling_strategy.py, "Los mejores solo se venden con sustituto"):
-- el top se lista ya, pero jobs/run_sales.py no acepta ninguna oferta
-- sobre él hasta que `target_player_id` esté en la plantilla propia
-- ('acquired'); jobs/run_market.py puja con prioridad por los 'pending'.
-- Si el sustituto sale del mercado sin ser nuestro, se retira la venta
-- ('lost') y el jugador vuelve a evaluarse.
CREATE TABLE IF NOT EXISTS sale_replacements (
    sale_id            INTEGER PRIMARY KEY REFERENCES sales(id),
    player_id          TEXT NOT NULL REFERENCES players(id),  -- el top propio, puesto en venta
    target_player_id   TEXT NOT NULL REFERENCES players(id),  -- sustituto a fichar ANTES de aceptar
    target_price       INTEGER,
    status             TEXT NOT NULL,          -- 'pending' | 'acquired' | 'lost'
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lineup_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    matchday        INTEGER,                -- NULL de momento: no hay endpoint de jornada actual implementado todavía
    formation       TEXT NOT NULL,          -- formato "4-4-2" (con guiones, igual que espera la API de Futmondo)
    player_ids      TEXT NOT NULL,          -- JSON list
    submitted_to_futmondo INTEGER NOT NULL DEFAULT 0,  -- 0/1, ver config.ENABLE_LINEUP_AUTO_SUBMIT
    reason          TEXT,
    created_at      TEXT NOT NULL
);

-- Una fila por sustitución decidida por jobs/manage_substitutes.py (titular
-- confirmado lesionado/en duda -> entra el suplente de su misma posición ya
-- asignado en el banquillo). Ver engine.lineup_optimizer.
-- build_substitution_changes() para el porqué de por qué esto es un job
-- aparte de lineup_decisions.
CREATE TABLE IF NOT EXISTS substitution_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    starter_id      TEXT NOT NULL REFERENCES players(id),      -- titular confirmado fuera
    substitute_id   TEXT NOT NULL REFERENCES players(id),      -- suplente que entra
    position        TEXT NOT NULL,          -- POR | DEF | MED | DEL
    submitted_to_futmondo INTEGER NOT NULL DEFAULT 0,  -- 0/1, ver config.ENABLE_SUBSTITUTE_AUTO_SUBMIT
    reason          TEXT,
    created_at      TEXT NOT NULL
);

-- Cache de un día de duración de la comprobación de alineación REAL
-- (clients/football_lineups_client.py, API-Football) por equipo: evita
-- repetir llamadas contra el límite de 100/día del plan gratuito en cada
-- pasada de jobs/manage_substitutes.py (varias veces al día, ver
-- manage_substitutes.yml). Una fila por (team, match_date); se sobreescribe
-- si se vuelve a consultar el mismo día (lineup_published pasa de 0 a 1
-- cuando la alineación real se publica, normalmente ~1h antes del partido).
CREATE TABLE IF NOT EXISTS real_lineup_checks (
    team                TEXT NOT NULL,      -- nombre de equipo tal cual en players.team
    match_date          TEXT NOT NULL,      -- fecha UTC YYYY-MM-DD del partido consultado
    fixture_id          INTEGER,            -- id de partido en API-Football, NULL si ese equipo no juega ese día
    lineup_published    INTEGER NOT NULL DEFAULT 0,  -- 0/1: si /fixtures/lineups ya devolvió el once real
    starting_player_ids TEXT,               -- JSON list de ids (Futmondo) de NUESTROS jugadores de ese equipo confirmados en el once real -- solo tiene sentido si lineup_published=1
    checked_at          TEXT NOT NULL,
    PRIMARY KEY (team, match_date)
);

-- Duración y resultado de cada ejecución de un job (jobs/*.py, ver
-- notifier.track_job_run()) -- antes no había ninguna forma de saber
-- cuánto tarda cada job sin abrir a mano cada ejecución de GitHub Actions.
-- Una fila por ejecución, tanto si termina bien como si revienta con una
-- excepción no controlada.
CREATE TABLE IF NOT EXISTS job_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name          TEXT NOT NULL,        -- 'run_market' | 'sync_data' | 'run_sales' | 'set_lineup' | 'manage_substitutes'
    status            TEXT NOT NULL,        -- 'ok' | 'error'
    duration_seconds  REAL NOT NULL,
    error             TEXT,                 -- "TipoExcepcion: mensaje" si status='error', NULL si 'ok'
    started_at        TEXT NOT NULL,
    finished_at       TEXT NOT NULL
);

-- Caché local de valores de configuración de LIGA que el usuario fija una
-- vez al crearla y Futmondo no cambia en la práctica (a petición del
-- usuario, 2026-08-23, ver TODO.md #18/#19) -- pensada como FALLBACK
-- cuando `client.get_information()` no informa un campo concreto en una
-- pasada puntual (glitch transitorio de la API: `configuration.
-- maxPlayersInRoster` ya vino ausente/confundido más de una vez el mismo
-- día que se empezó a usar, ver TODO.md #16). Antes, esa ausencia puntual
-- degradaba a "sin límite" (bug de las 11 pujas fallidas) o, tras el
-- primer arreglo, bloqueaba TODAS las pujas nuevas de esa pasada aunque ya
-- se supiera el valor real de una pasada anterior -- ninguna de las dos
-- reacciones tiene sentido si el dato ya lo conocíamos. Una fila por
-- clave (`key`), sobreescrita (INSERT OR REPLACE, ver
-- db.models.save_league_setting()) solo cuando la API real confirma un
-- valor -- nunca se reafirma a sí misma con el propio fallback.
CREATE TABLE IF NOT EXISTS league_settings (
    key         TEXT PRIMARY KEY,   -- 'max_players_in_roster'
    value       INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


@contextmanager
def get_connection():
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn, table: str, column: str, coltype: str) -> None:
    """
    Migración mínima para una columna nueva en una tabla que `CREATE TABLE
    IF NOT EXISTS` no toca si la tabla ya existía (caso real: `db/
    futmondo.db` está versionado en el repo con datos ya acumulados, ver
    docstring del módulo). Sin esto, añadir una columna al esquema de
    arriba no la crearía en la BD real, solo en una BD nueva desde cero.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "external_stats", "team_games", "INTEGER")
        _ensure_column(conn, "external_stats", "team_clean_sheets", "INTEGER")
        _ensure_column(conn, "futmondo_snapshots", "listing_price", "INTEGER")
        _ensure_column(conn, "futmondo_snapshots", "is_clause", "INTEGER")
        _ensure_column(conn, "futmondo_snapshots", "recent_points", "TEXT")
        _ensure_column(conn, "bids", "error", "TEXT")
        _ensure_column(conn, "sales", "error", "TEXT")


# Última fila de futmondo_snapshots/external_stats por jugador (usa
# ROW_NUMBER() de SQLite 3.25+; el sqlite3 embebido en Python 3.11+ ya lo trae).
_PLAYER_FEATURES_SQL = """
WITH latest_snapshot AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY recorded_at DESC) AS rn
    FROM futmondo_snapshots
),
latest_external AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY recorded_at DESC) AS rn
    FROM external_stats
)
SELECT
    p.id, p.name, p.team, p.position,
    s.price, s.buy_price, s.points, s.last_points, s.average_points,
    s.on_market, s.status, s.listing_price, s.is_clause, s.recent_points,
    e.xg, e.xa, e.minutes_played, e.games, e.team_games, e.team_clean_sheets, e.non_penalty_goals, e.assists, e.understat_position,
    -- Minutos y partidos del equipo al cierre de la jornada (team_games) de
    -- hace al menos {recent_window} partidos, misma temporada: permite a
    -- engine.evaluator calcular el ratio de minutos de las jornadas
    -- RECIENTES (ver normalize_pool, "minutes_played_ratio"). NULL si no
    -- hay histórico local tan atrás para ese jugador.
    (
        SELECT e2.minutes_played FROM external_stats e2
        WHERE e2.player_id = p.id AND e2.season = e.season AND e2.team_games IS NOT NULL
          AND e2.team_games <= e.team_games - {recent_window}
        ORDER BY e2.team_games DESC, e2.recorded_at DESC LIMIT 1
    ) AS minutes_played_ref,
    (
        SELECT e2.team_games FROM external_stats e2
        WHERE e2.player_id = p.id AND e2.season = e.season AND e2.team_games IS NOT NULL
          AND e2.team_games <= e.team_games - {recent_window}
        ORDER BY e2.team_games DESC, e2.recorded_at DESC LIMIT 1
    ) AS team_games_ref
FROM players p
LEFT JOIN latest_snapshot s ON s.player_id = p.id AND s.rn = 1
LEFT JOIN latest_external e ON e.player_id = p.id AND e.rn = 1
{where}
"""


def get_player_features(only_on_market: bool = False) -> list[dict]:
    """
    Devuelve una fila por jugador combinando `players` con su snapshot más
    reciente de `futmondo_snapshots` y de `external_stats` (LEFT JOIN: un
    jugador sin stats de Understat todavía cruzadas sale con esos campos en
    NULL, no se descarta). Pensado como entrada directa de
    engine/evaluator.py (ver `normalize_pool`/`evaluate_players`).

    `only_on_market=True` filtra a solo jugadores marcados `on_market` en
    el snapshot más reciente — para evaluar candidatos de puja en vez de
    toda la plantilla propia.
    """
    where = "WHERE s.on_market = 1" if only_on_market else ""
    with get_connection() as conn:
        sql = _PLAYER_FEATURES_SQL.format(where=where, recent_window=int(config.EVALUATOR_RECENT_MINUTES_WINDOW_GAMES))
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]


def get_bids_risked_today() -> int:
    """
    Suma el importe de las pujas ya colocadas hoy (status='placed'),
    aproximando "jornada" como "día natural" — el mercado de Futmondo
    tampoco parece abrir/cerrar siempre en un único día (los listados de
    mercado capturados traían `expirationDate` a ~2 días vista), así que
    esto es una aproximación razonable, no un cálculo exacto de jornada.
    Pensado para pasarlo como `already_risked_this_matchday` a
    engine.bidding_strategy.decide_bids_for_market en cada ejecución del
    cron, para que varias ejecuciones el mismo día no acumulen más riesgo
    del permitido entre todas.

    OJO: esto es solo el RITMO de gasto por jornada (un límite
    autoimpuesto, conservador), NO la protección real de saldo — para eso
    hace falta `get_pending_bid_amount()` cruzado con
    `clients.futmondo_client.real_pending_bid_amount()` (ver TODO.md #3,
    resuelto, y jobs/run_market.py). Esta función solo mira las pujas
    colocadas HOY; una puja de AYER que Futmondo todavía no haya resuelto
    no aparece aquí.
    """
    from datetime import datetime, timezone

    today_prefix = datetime.now(timezone.utc).date().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM bids WHERE status = 'placed' AND created_at LIKE ?",
            (f"{today_prefix}%",),
        ).fetchone()
        return row["total"]


def get_pending_bid_amount() -> int:
    """
    Suma el importe de TODAS las pujas que seguimos creyendo pendientes
    (status='placed'), sin importar el día en que se colocaron.

    Resuelve TODO.md #3 (junto con `clients.futmondo_client.
    real_pending_bid_amount()`): Futmondo NO tiene un endpoint dedicado
    como el `GET .../offers?current` de Comunio, pero cada item de
    `get_market()` en el que tenemos puja pendiente sí trae el importe real
    en un campo `"bid"` (confirmado en vivo 2026-08-18) — esa es ahora la
    fuente PRIMARIA en `jobs/run_market.py` (más fuerte: viene del propio
    Futmondo, no depende de reconciliación). Esta función local se sigue
    usando como colchón adicional (`max()` con la fuente real) por si la
    consulta al mercado fallara — sigue dependiendo de que
    `jobs/sync_data.py` reconcilie el estado ('placed' -> 'won'/'lost') a
    tiempo para no arrastrar pujas ya resueltas indefinidamente.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM bids WHERE status = 'placed'").fetchone()
        return row["total"]


def get_open_bids() -> list[dict]:
    """
    Pujas que seguimos creyendo pendientes (status='placed'): `id` (fila
    local, PK autoincrement -- NO es el id de oferta de Futmondo),
    `player_id`, `score` y `amount`.

    Pensado para dos consumidores:
      - jobs.sync_data._reconcile_bids(): comparar cada `player_id` contra
        la plantilla y el mercado actuales (client.get_roster()/get_market())
        para saber si ya se resolvió (ganada/perdida) desde la última vez —
        criterio indirecto por diseño, `id`/`score`/`amount` no le hacen
        falta.
      - engine.bidding_strategy.find_cancel_swap_candidates() (vía
        jobs/run_market.py): necesita `score` para decidir qué puja abierta
        sacrificar (la más floja) y `player_id` para cruzar con
        `get_market()` y sacar el id REAL de oferta de Futmondo (campo
        `"bid": {"id": ...}` de cada item -- ver clients.futmondo_client.
        cancel_bid/real_pending_bid_amount) que esta tabla local no guarda.
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT id, player_id, score, amount FROM bids WHERE status = 'placed'").fetchall()
        return [dict(r) for r in rows]


def update_bid_status(bid_id: int, status: str) -> None:
    """
    Actualiza el status de una puja ya persistida. Usado tanto por la
    reconciliación (jobs.sync_data._reconcile_bids, -> 'won'/'lost') como
    por engine.bidding_strategy.find_cancel_swap_candidates vía
    jobs.run_market.run() (-> 'cancelled', ver TODO.md #13 y
    clients.futmondo_client.cancel_bid).
    """
    with get_connection() as conn:
        conn.execute("UPDATE bids SET status = ? WHERE id = ?", (status, bid_id))


def get_won_bid_prices() -> dict[str, int]:
    """
    Fuente local fiable de "qué jugadores ha comprado el bot y a qué precio
    real" (resuelve TODO.md #4): a diferencia de `buyPrice` de Futmondo
    (aparece también en jugadores de la plantilla inicial, a veces >0 —
    ver clients/futmondo_client.py), esta tabla `bids` solo tiene filas de
    pujas que el propio bot colocó, y jobs.sync_data._reconcile_bids() ya
    las marca 'won' comparándolas contra la plantilla real. Si un jugador
    tiene más de una puja ganada en su historial (vendido y recomprado más
    adelante), se queda con la más reciente (mayor `id`).

    Devuelve {player_id: amount} solo para jugadores con al menos una puja
    'won' — exactamente el conjunto que engine.selling_strategy.
    decide_sales() debe tratar como "comprado por el bot", igual que
    `purchaseInfo != null` hacía en Comunio.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            WITH latest_won AS (
                SELECT player_id, amount, ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY id DESC) AS rn
                FROM bids
                WHERE status = 'won'
            )
            SELECT player_id, amount FROM latest_won WHERE rn = 1
            """
        ).fetchall()
        return {row["player_id"]: row["amount"] for row in rows}


def get_purchase_baselines() -> dict[str, dict]:
    """
    Para cada jugador con al menos una puja 'won' (mismo conjunto y mismo
    criterio de "más reciente" -- mayor `id`, ver get_won_bid_prices() --
    para el caso de recompra tras una venta anterior), agrega DESDE el
    `created_at` de esa puja hasta ahora sobre `futmondo_snapshots` (SIN
    llamada de red, reutilizando el histórico local que ya recoge cada
    pasada de jobs/sync_data.py):
      - "peak_price": MAX(price) visto desde la compra -- para el
        trailing-stop de engine.selling_strategy.decide_sales() ("corte
        por reversión desde máximo", ver su docstring).
      - "points_at_purchase": "points" (acumulado de TEMPORADA, NO se
        resetea al fichar) del snapshot MÁS CERCANO a esa fecha --
        baseline para que decide_sales() calcule cuántos puntos sumó el
        jugador MIENTRAS fue del bot (points_now del roster en vivo, menos
        este valor). Puramente informativo en el `reason`: no participa en
        ninguna condición de venta (ver docstring de decide_sales sobre
        por qué no debe hacerlo).
      - "value_at_purchase": valor de mercado del ÚLTIMO snapshot ANTERIOR
        (o simultáneo) a la puja -- referencia del corte de pérdidas de
        decide_sales() en lugar del importe pagado (a petición del usuario,
        2026-09-23: la puja se gana de media ~11% por ENCIMA del VM, así
        que medir la pérdida contra lo pagado disparaba el corte casi solo
        con esa prima, ver config.SELLING_MAX_LOSS_PCT). None si no hay
        ningún snapshot previo a la compra.
      - "purchased_at": `created_at` de esa puja (ISO) -- antigüedad del
        fichaje, para el multiplicador por tiempo del corte de pérdidas y
        la antigüedad mínima de la vía "oportunidad de mercado".

    Devuelve {player_id: {"peak_price": int, "points_at_purchase": int|None,
    "value_at_purchase": int|None, "purchased_at": str}}
    -- solo para jugadores con al menos un snapshot de precio desde la
    compra; sin eso ambas señales quedan sin dato para ese jugador esta
    pasada (decide_sales() lo trata como "vía/nota desactivada", nunca
    asume 0). "points_at_purchase" puede ser None dentro de una fila
    presente si ese primer snapshot no tenía "points" parseable.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            WITH latest_won AS (
                SELECT player_id, created_at, ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY id DESC) AS rn
                FROM bids
                WHERE status = 'won'
            ),
            since_purchase AS (
                SELECT s.player_id, s.price, s.points,
                       ROW_NUMBER() OVER (PARTITION BY s.player_id ORDER BY s.recorded_at ASC) AS rn_asc
                FROM futmondo_snapshots s
                JOIN latest_won w ON w.player_id = s.player_id AND w.rn = 1
                WHERE s.recorded_at >= w.created_at
            )
            SELECT
                sp.player_id,
                MAX(CASE WHEN sp.price > 0 THEN sp.price END) AS peak_price,
                MAX(CASE WHEN sp.rn_asc = 1 THEN sp.points END) AS points_at_purchase,
                w.created_at AS purchased_at,
                (
                    SELECT s2.price FROM futmondo_snapshots s2
                    WHERE s2.player_id = sp.player_id AND s2.recorded_at <= w.created_at AND s2.price > 0
                    ORDER BY s2.recorded_at DESC LIMIT 1
                ) AS value_at_purchase
            FROM since_purchase sp
            JOIN latest_won w ON w.player_id = sp.player_id AND w.rn = 1
            GROUP BY sp.player_id
            """
        ).fetchall()
        return {
            row["player_id"]: {
                "peak_price": row["peak_price"],
                "points_at_purchase": row["points_at_purchase"],
                "value_at_purchase": row["value_at_purchase"],
                "purchased_at": row["purchased_at"],
            }
            for row in rows
            if row["peak_price"] is not None
        }


def get_recent_price_history(player_ids: list, since_days: float) -> dict[str, list[dict]]:
    """
    Histórico local RECIENTE de precio ({"recorded_at", "price"} por fila,
    orden cronológico ascendente) de `futmondo_snapshots` para cada
    jugador en `player_ids`, desde hace `since_days` hasta ahora -- SIN
    llamada de red (a diferencia de `FutmondoClient.get_player_summary()`,
    que cubre una ventana corta y cuesta una llamada por jugador). Pensado
    para `engine.selling_strategy.confirm_loss_is_sustained()` vía
    `decide_sales()` ("recent_price_history", ver su docstring): confirma
    que un corte de pérdidas no se dispara por un único dato de ruido
    reciente antes de vender.

    `player_ids` acotado explícitamente (a diferencia de
    get_purchase_baselines(), ya acotado por `bids`) porque
    `futmondo_snapshots` cubre TODO jugador visto alguna vez en roster o
    mercado, no solo los comprados por el bot -- sin este filtro se
    traería histórico de cientos de jugadores irrelevantes para esta
    llamada.

    Devuelve {player_id: [{"recorded_at", "price"}, ...]} -- SOLO
    jugadores con al menos una fila en la ventana; ausencia = "sin
    historial reciente todavía", decide_sales()/confirm_loss_is_sustained()
    lo tratan igual que "sin datos suficientes" (falla abierto, ver
    docstring de confirm_loss_is_sustained).
    """
    if not player_ids:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in player_ids)
        rows = conn.execute(
            f"""
            SELECT player_id, price, recorded_at
            FROM futmondo_snapshots
            WHERE player_id IN ({placeholders}) AND recorded_at >= ? AND price > 0
            ORDER BY player_id, recorded_at ASC
            """,
            [str(pid) for pid in player_ids] + [cutoff],
        ).fetchall()
    history: dict[str, list[dict]] = {}
    for row in rows:
        history.setdefault(row["player_id"], []).append({"recorded_at": row["recorded_at"], "price": row["price"]})
    return history


def get_open_sales() -> list[dict]:
    """
    Ventas que seguimos creyendo listadas (status='listed'). Pensado para
    jobs.sync_data._reconcile_sales(): si el jugador ya no aparece en la
    plantilla, es que alguien completó la compra -> 'sold'. No hay forma
    de confirmar con los datos de la API si de verdad se vendió o si se
    quitó manualmente del mercado sin vender (misma limitación que con las
    pujas, ver get_open_bids) — mientras el jugador siga en la plantilla,
    se asume que la venta sigue listada tal cual.
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT id, player_id, created_at FROM sales WHERE status = 'listed'").fetchall()
        return [dict(r) for r in rows]


def update_sale_status(sale_id: int, status: str) -> None:
    """Actualiza el status de una venta ya persistida (ver get_open_sales/reconciliación)."""
    with get_connection() as conn:
        conn.execute("UPDATE sales SET status = ? WHERE id = ?", (status, sale_id))


def record_received_offer(
    player_id,
    futmondo_bid_id,
    listing_price: int,
    offer_price: int,
    bidder_name: str | None,
    bidder_slug: str | None,
    seen_at: str,
) -> bool:
    """
    Registra una oferta de compra recibida sobre un jugador propio puesto
    en venta (ver docstring de `received_sale_offers` arriba y
    `jobs.run_sales._process_received_offers()`, TODO.md #15) -- pensado
    para llamarse sobre CADA oferta vista en cada pasada, aceptada o no.

    `accepted` se guarda siempre en 0 aquí -- usar `mark_offer_accepted()`
    aparte, DESPUÉS de que `FutmondoClient.accept_sale_offer()` confirme
    éxito, para no marcar una oferta como aceptada si la llamada real
    falla (ver ese job para el porqué de separarlo en dos pasos).

    Devuelve True si la oferta era nueva (se insertó), False si ya estaba
    registrada de una pasada anterior (INSERT OR IGNORE por
    `futmondo_bid_id`, UNIQUE) -- pensado para poder distinguir en la
    notificación cuántas ofertas son nuevas desde la última ejecución.
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO received_sale_offers
                (player_id, futmondo_bid_id, listing_price, offer_price, bidder_name, bidder_slug, accepted, seen_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (str(player_id), str(futmondo_bid_id), listing_price, offer_price, bidder_name, bidder_slug, seen_at),
        )
        return cur.rowcount > 0


def mark_offer_accepted(futmondo_bid_id) -> None:
    """
    Marca como aceptada una oferta ya registrada con `record_received_offer()`
    -- llamar solo tras confirmar éxito real de
    `FutmondoClient.accept_sale_offer()` (ver jobs/run_sales.py).
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE received_sale_offers SET accepted = 1 WHERE futmondo_bid_id = ?",
            (str(futmondo_bid_id),),
        )


def save_swap_target(sale_id: int, player_id, target_player_id, target_price: int, now: str) -> None:
    """
    Registra el candidato de mercado que motivó una venta por "oportunidad
    de mercado" (ver docstring de `sale_swap_targets` más arriba) -- llamar
    solo tras persistir la fila de `sales` correspondiente (necesita su
    `sale_id` real), y solo para decisiones con `swap_target_player_id`
    (ver engine.selling_strategy.decide_sales()). `original_target_player_id`
    se fija aquí, una única vez, al valor inicial de `target_player_id` --
    ver retarget_swap_target() para cuando cambia más adelante.

    INSERT OR IGNORE por `sale_id` (PK): no debería llamarse dos veces para
    el mismo `sale_id`, pero si ocurriera (reintento), no pisa la fila ya
    existente.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO sale_swap_targets
                (sale_id, player_id, target_player_id, target_price, original_target_player_id, retargeted, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (sale_id, str(player_id), str(target_player_id), target_price, str(target_player_id), now),
        )


def get_swap_target_for_player(player_id) -> dict | None:
    """
    Swap en marcha (si lo hay) para el listado ABIERTO ahora mismo de
    `player_id` -- cruza con `sales.status = 'listed'` para la fila MÁS
    RECIENTE (mayor `id`), por si el jugador se vendió/recompró/volvió a
    listar más de una vez en su historia. None si esa venta no calificó por
    "oportunidad de mercado" (nunca se guardó fila para ella) o si no hay
    ningún listado abierto para ese jugador.

    Pensado para `jobs.run_sales._resolve_swap_target()`, llamado justo
    antes de aceptar una oferta que dejaría su posición en bench=0 (ver
    config.SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT).
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT t.sale_id, t.player_id, t.target_player_id, t.target_price, t.original_target_player_id
            FROM sale_swap_targets t
            JOIN sales s ON s.id = t.sale_id
            WHERE t.player_id = ? AND s.status = 'listed'
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (str(player_id),),
        ).fetchone()
        return dict(row) if row else None


def retarget_swap_target(sale_id: int, new_target_player_id, new_target_price: int, now: str) -> None:
    """
    Actualiza el candidato VIGENTE de un swap ya registrado (el original ya
    no está disponible -- ver `jobs.run_sales._resolve_swap_target()`) a un
    equivalente encontrado ahora mismo en el mercado. `original_target_player_id`
    NUNCA se toca aquí -- sigue siendo el candidato que motivó la venta el
    día que se listó, solo de auditoría.
    """
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE sale_swap_targets
            SET target_player_id = ?, target_price = ?, retargeted = 1, updated_at = ?
            WHERE sale_id = ?
            """,
            (str(new_target_player_id), new_target_price, now, sale_id),
        )


def save_sale_replacement(sale_id: int, player_id, target_player_id, target_price: int, now: str) -> None:
    """
    Registra el sustituto que hay que fichar ANTES de aceptar ofertas sobre
    el top `player_id` recién listado (ver `sale_replacements` arriba) --
    llamar tras persistir la fila de `sales` (necesita su `sale_id` real).
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO sale_replacements
                (sale_id, player_id, target_player_id, target_price, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (sale_id, str(player_id), str(target_player_id), target_price, now, now),
        )


def get_open_sale_replacements() -> list[dict]:
    """
    Sustitutos de ventas TODAVÍA listadas (`sales.status = 'listed'`), en
    estado 'pending' o 'acquired' -- para jobs/run_sales.py (bloquear o
    permitir aceptar ofertas, retirar la venta si se perdió la compra) y
    jobs/run_market.py (pujar por los 'pending').
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT r.sale_id, r.player_id, r.target_player_id, r.target_price, r.status
            FROM sale_replacements r
            JOIN sales s ON s.id = r.sale_id
            WHERE s.status = 'listed' AND r.status IN ('pending', 'acquired')
            ORDER BY r.sale_id
            """
        ).fetchall()
        return [dict(r) for r in rows]


def update_sale_replacement_status(sale_id: int, status: str, now: str) -> None:
    """Actualiza el estado ('pending' | 'acquired' | 'lost') de un sustituto registrado."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE sale_replacements SET status = ?, updated_at = ? WHERE sale_id = ?",
            (status, now, sale_id),
        )


def get_real_lineup_check(team: str, match_date: str) -> dict | None:
    """
    Lee la comprobación de alineación real ya cacheada para (team, match_date)
    -- ver save_real_lineup_check() y clients/football_lineups_client.py.
    None si nunca se consultó ese equipo ese día (no confundir con
    lineup_published=0, que significa "se consultó pero Futmondo/API-Football
    todavía no había publicado la alineación real").
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM real_lineup_checks WHERE team = ? AND match_date = ?", (team, match_date)
        ).fetchone()
        return dict(row) if row else None


def save_real_lineup_check(
    team: str, match_date: str, fixture_id: int | None, lineup_published: bool, starting_player_ids: list[str], checked_at: str
) -> None:
    """
    Guarda/sobreescribe (INSERT OR REPLACE, clave (team, match_date)) el
    resultado de consultar la alineación real de `team` para `match_date` --
    ver get_real_lineup_check(). `starting_player_ids` se serializa a JSON
    (lista de ids Futmondo de NUESTROS jugadores de ese equipo confirmados en
    el once real; vacía si lineup_published=False, todavía no hay nada que
    guardar).
    """
    import json

    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO real_lineup_checks
                (team, match_date, fixture_id, lineup_published, starting_player_ids, checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (team, match_date, fixture_id, 1 if lineup_published else 0, json.dumps(starting_player_ids), checked_at),
        )


def get_league_setting(key: str) -> int | None:
    """
    Lee un valor de configuración de LIGA cacheado localmente (ver
    save_league_setting()/docstring de la tabla `league_settings`) -- None
    si nunca se confirmó ese `key` en ninguna pasada anterior. Pensado
    SOLO como fallback cuando la API real no informa el campo en la pasada
    actual (ver jobs/run_market.py, TODO.md #18/#19) -- `client.
    get_information()` sigue siendo la fuente de verdad cuando sí responde
    con el campo.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM league_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def save_league_setting(key: str, value: int, now: str) -> None:
    """
    Guarda/sobreescribe (INSERT OR REPLACE, clave `key`) un valor de
    configuración de liga -- ver get_league_setting(). Llamar solo con un
    valor que de verdad vino de la API REAL en esta misma pasada, nunca
    con el propio valor de fallback (evitaría que un valor viejo se
    reafirmara a sí mismo sin ninguna confirmación nueva).
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO league_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )


def record_job_run(
    job_name: str,
    status: str,
    duration_seconds: float,
    started_at,
    error: str = None,
) -> None:
    """
    Persiste cuánto tardó una ejecución de un job (ver
    notifier.track_job_run(), que llama a esto al terminar `run()`, tanto
    en éxito como en fallo no controlado) -- antes no había ninguna forma
    de saber cuánto tarda cada job sin abrir a mano cada ejecución de
    GitHub Actions.

    `started_at`: datetime (con tz) de cuándo empezó la ejecución --
    `finished_at` se calcula aquí mismo como "ahora". `status`: 'ok' o
    'error'. `error`: "TipoExcepcion: mensaje" si status='error', None si
    'ok'.
    """
    from datetime import datetime, timezone

    finished_at = datetime.now(timezone.utc)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO job_runs (job_name, status, duration_seconds, error, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_name, status, duration_seconds, error, started_at.isoformat(), finished_at.isoformat()),
        )


if __name__ == "__main__":
    init_db()
    print(f"Base de datos inicializada en {config.DATABASE_PATH}")
