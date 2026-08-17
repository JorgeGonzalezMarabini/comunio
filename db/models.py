"""
Esquema de la base de datos (SQLite, archivo versionado en el repo).

Campos alineados con lo confirmado por fetch autenticado real (2026-08-17,
ver clients/futmondo_client.py para el detalle completo):
  - Futmondo (api.futmondo.com): roster/market devuelven `role` como
    palabra completa en ESPAÑOL ("portero"/"defensa"/"centrocampista"/
    "delantero") — se normaliza a POR/DEF/MED/DEL (FUTMONDO_POSITION_MAP)
    al escribir aquí, así que `players.position` YA está en la convención
    corta, no en la de Futmondo. `status` real visto en esta liga de
    prueba (pretemporada, sin lesionados): siempre "" — no hay todavía un
    valor de lesión/sanción confirmado (ver
    clients.futmondo_client.is_injury_status(), una aproximación sin
    confirmar). No hay equivalente confirmado al "-" de puntos en
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
    buy_price       INTEGER,                -- "buyPrice" — ver TODO en clients/futmondo_client.py:
                                             -- no distingue con certeza "comprado por el bot" de
                                             -- "asignado con la plantilla inicial"
    points          INTEGER,                -- "points" acumulados
    last_points     INTEGER,                -- último valor de "average.fitness" (orden cronológico sin confirmar)
    average_points  REAL,                   -- "average.average"
    on_market       INTEGER,                 -- 0/1: roster.market (puesto en venta) o 1 fijo si viene del mercado de fichajes
    status          TEXT,                    -- valor real de Futmondo, ver clients.futmondo_client.is_injury_status()
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
    status          TEXT NOT NULL,         -- 'placed' | 'won' | 'lost' | 'failed'
    score           REAL,                  -- score del evaluator que justificó la puja
    reason          TEXT,                  -- explicación legible para auditoría
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    asking_price    INTEGER NOT NULL,       -- precio pedido ("value" en el momento de listar)
    purchase_price  INTEGER,                -- precio de referencia ("buyPrice" de roster, ver TODO arriba)
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


def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA)


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
    s.on_market, s.status,
    e.xg, e.xa, e.minutes_played, e.games, e.non_penalty_goals, e.assists, e.understat_position
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
    hace falta `get_pending_bid_amount()` (ver también
    clients.futmondo_client.total_pending_bid_amount y
    jobs/run_market.py). Esta función solo mira las pujas colocadas HOY;
    una puja de AYER que Futmondo todavía no haya resuelto no aparece
    aquí.
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

    A diferencia de Comunio (que exponía `GET .../offers?current` con la
    lista real de ofertas pendientes, fuente de verdad externa), Futmondo
    no tiene un endpoint equivalente confirmado (ver
    clients.futmondo_client.total_pending_bid_amount) — esta es nuestra
    única aproximación al "dinero ya comprometido pero no descontado del
    saldo todavía", y depende por completo de que jobs/sync_data.py
    reconcilie el estado ('placed' -> 'won'/'lost') en cada ejecución. Si
    la reconciliación se retrasa o falla, esto puede sobreestimar el
    compromiso real (nunca subestimarlo, que sería el caso peligroso).
    """
    with get_connection() as conn:
        row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM bids WHERE status = 'placed'").fetchone()
        return row["total"]


def get_open_bids() -> list[dict]:
    """
    Pujas que seguimos creyendo pendientes (status='placed'). Pensado para
    jobs.sync_data._reconcile_bids(): comparar cada `player_id` contra la
    plantilla y el mercado actuales (client.get_roster()/get_market()) para
    saber si ya se resolvió (ganada/perdida) desde la última vez — sin id
    de oferta que consultar (Futmondo no devuelve uno, ver
    clients/futmondo_client.py), el criterio es indirecto por diseño.
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT id, player_id FROM bids WHERE status = 'placed'").fetchall()
        return [dict(r) for r in rows]


def update_bid_status(bid_id: int, status: str) -> None:
    """Actualiza el status de una puja ya persistida (ver get_open_bids/reconciliación)."""
    with get_connection() as conn:
        conn.execute("UPDATE bids SET status = ? WHERE id = ?", (status, bid_id))


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


if __name__ == "__main__":
    init_db()
    print(f"Base de datos inicializada en {config.DATABASE_PATH}")
