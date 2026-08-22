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
    last_points     INTEGER,                -- último valor de "average.fitness" (orden cronológico sin confirmar)
    average_points  REAL,                   -- "average.average"
    on_market       INTEGER,                 -- 0/1: roster.market (puesto en venta) o 1 fijo si viene del mercado de fichajes
    status          TEXT,                    -- valor real de Futmondo, ver clients.futmondo_client.is_injury_status()
    listing_price   INTEGER,                 -- "price" del listado (precio de SALIDA que pone quien vende -- otro
                                              -- manager o el "Computer" -- NO el VM); NULL fuera de mercado (roster)
    is_clause       INTEGER,                 -- 0/1: "isClause" del listado (solo tiene sentido si listing_price no es NULL)
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
    reason          TEXT,                  -- explicación legible para auditoría
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    asking_price    INTEGER NOT NULL,       -- precio pedido ("value" en el momento de listar)
    purchase_price  INTEGER,                -- importe realmente pagado en la puja ganada, ver get_won_bid_prices()
    profit          INTEGER,                -- asking_price - purchase_price
    profit_pct      REAL,
    status          TEXT NOT NULL,          -- 'listed' | 'sold' | 'delisted' | 'failed'
    reason          TEXT,
    created_at      TEXT NOT NULL
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
        _ensure_column(conn, "futmondo_snapshots", "listing_price", "INTEGER")
        _ensure_column(conn, "futmondo_snapshots", "is_clause", "INTEGER")


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
    s.on_market, s.status, s.listing_price, s.is_clause,
    e.xg, e.xa, e.minutes_played, e.games, e.team_games, e.non_penalty_goals, e.assists, e.understat_position
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
        rows = conn.execute(_PLAYER_FEATURES_SQL.format(where=where)).fetchall()
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
        rows = conn.execute("SELECT id, player_id FROM sales WHERE status = 'listed'").fetchall()
        return [dict(r) for r in rows]


def update_sale_status(sale_id: int, status: str) -> None:
    """Actualiza el status de una venta ya persistida (ver get_open_sales/reconciliación)."""
    with get_connection() as conn:
        conn.execute("UPDATE sales SET status = ? WHERE id = ?", (status, sale_id))


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
