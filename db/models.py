"""
Esquema de la base de datos (SQLite, archivo versionado en el repo).

Campos alineados con lo confirmado por fetch autenticado real (2026-08-15,
ver clients/comunio_client.py para el detalle completo):
  - Comunio (api.comunio.es): squad/market devuelven position como palabra
    completa en inglés ("keeper"/"defender"/"midfielder"/"striker") — se
    normaliza a POR/DEF/MED/DEL (COMUNIO_POSITION_MAP) al escribir aquí, así
    que `players.position` YA está en la convención corta, no en la de
    Comunio. `status` real visto: "ACTIVE" | "WEAKENED" | "INJURED" (con
    `statusInfo` en texto libre); no se ha visto un valor de sanción en esta
    muestra. `points` puede venir como "-" (string) en pretemporada sin
    puntos todavía — el job debe manejarlo (NULL, no 0 ni crash).
  - Understat (clients/laliga_stats_client.py): xG/xA/minutos/goles/tarjetas
    vía GET /getLeagueData/{league}/{season}, campos confirmados 1:1 contra
    la respuesta real.

FBref queda descartado (bloquea con Cloudflare, ver README) — no hay
columnas específicas de esa fuente.
"""
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id              TEXT PRIMARY KEY,      -- id de Comunio
    name            TEXT NOT NULL,
    team            TEXT,
    position        TEXT,                  -- POR | DEF | MED | DEL (convención Comunio)
    understat_id    TEXT,                  -- cruce por nombre normalizado, ver index_players_by_name()
    updated_at      TEXT NOT NULL
);

-- Snapshot de cada sync: precio/VM, puntos y estado tal como los reporta
-- Comunio en ese momento (para poder ver evolución y auditar decisiones).
-- price/points en NULL si Comunio devuelve "-" (normal en pretemporada).
CREATE TABLE IF NOT EXISTS comunio_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    price           INTEGER,                -- quotedprice/quotedPrice (VM actual)
    recommended_price INTEGER,               -- recommendedprice/recommendedPrice
    points          INTEGER,                -- points acumulados (NULL si Comunio da "-")
    last_points     INTEGER,
    average_points  REAL,
    on_market       INTEGER,                 -- 0/1, si está en el mercado de fichajes
    status          TEXT,                    -- ACTIVE | WEAKENED | INJURED | ... (valor real de Comunio)
    status_info     TEXT,                    -- texto libre tal cual lo da Comunio ("Lesión muscular"...)
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

CREATE TABLE IF NOT EXISTS bids (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    comunio_offer_id INTEGER,               -- id real de la oferta en Comunio, para poder retirarla (withdraw_bid)
    amount          INTEGER NOT NULL,
    status          TEXT NOT NULL,         -- 'placed' | 'won' | 'lost' | 'withdrawn' | 'failed'
    score           REAL,                  -- score del evaluator que justificó la puja
    reason          TEXT,                  -- explicación legible para auditoría
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    asking_price    INTEGER NOT NULL,       -- precio pedido (quotedprice en el momento de listar)
    purchase_price  INTEGER,                -- lo que pagamos por él (purchaseInfo.price de squad)
    profit          INTEGER,                -- asking_price - purchase_price
    profit_pct      REAL,
    status          TEXT NOT NULL,          -- 'listed' | 'sold' | 'delisted' | 'failed'
    reason          TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lineup_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    matchday        INTEGER,                -- NULL de momento: no hay endpoint de matchday actual implementado todavía
    formation       TEXT NOT NULL,          -- formato humano "4-4-2"; convertir con to_api_tactic() al llamar a la API
    player_ids      TEXT NOT NULL,          -- JSON list
    submitted_to_comunio INTEGER NOT NULL DEFAULT 0,  -- 0/1, ver config.ENABLE_LINEUP_AUTO_SUBMIT
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


# Última fila de comunio_snapshots/external_stats por jugador (usa
# ROW_NUMBER() de SQLite 3.25+; el sqlite3 embebido en Python 3.11+ ya lo trae).
_PLAYER_FEATURES_SQL = """
WITH latest_snapshot AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY recorded_at DESC) AS rn
    FROM comunio_snapshots
),
latest_external AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY recorded_at DESC) AS rn
    FROM external_stats
)
SELECT
    p.id, p.name, p.team, p.position,
    s.price, s.recommended_price, s.points, s.last_points, s.average_points,
    s.on_market, s.status, s.status_info,
    e.xg, e.xa, e.minutes_played, e.games, e.non_penalty_goals, e.assists, e.understat_position
FROM players p
LEFT JOIN latest_snapshot s ON s.player_id = p.id AND s.rn = 1
LEFT JOIN latest_external e ON e.player_id = p.id AND e.rn = 1
{where}
"""


def get_player_features(only_on_market: bool = False) -> list[dict]:
    """
    Devuelve una fila por jugador combinando `players` con su snapshot más
    reciente de `comunio_snapshots` y de `external_stats` (LEFT JOIN: un
    jugador sin stats de Understat todavía cruzadas sale con esos campos en
    NULL, no se descarta). Pensado como entrada directa de
    engine/evaluator.py (ver `normalize_pool`/`evaluate_players`).

    `only_on_market=True` filtra a solo jugadores marcados `onMarket` en el
    snapshot más reciente — para evaluar candidatos de puja en vez de toda
    la plantilla propia.
    """
    where = "WHERE s.on_market = 1" if only_on_market else ""
    with get_connection() as conn:
        rows = conn.execute(_PLAYER_FEATURES_SQL.format(where=where)).fetchall()
        return [dict(r) for r in rows]


def get_bids_risked_today() -> int:
    """
    Suma el importe de las pujas ya colocadas hoy (status='placed'),
    aproximando "jornada" como "día natural" — el mercado no siempre abre y
    cierra en un único día (la UI mostraba "Desde 15.08 - Hasta 16.08"), así
    que esto es una aproximación razonable, no un cálculo exacto de
    jornada. Pensado para pasarlo como `already_risked_this_matchday` a
    engine.bidding_strategy.decide_bids_for_market en cada ejecución del
    cron, para que varias ejecuciones el mismo día no acumulen más riesgo
    del permitido entre todas.

    OJO: esto es solo el RITMO de gasto por jornada (un límite
    autoimpuesto, conservador), NO la protección real de saldo — para eso
    hace falta `pending_committed` (ver clients.comunio_client.
    total_pending_purchase_amount y jobs/run_market.py), que suma TODAS
    las ofertas pendientes sin resolver en Comunio, no solo las de hoy.
    Esta función solo mira la tabla `bids` local (nuestra propia auditoría
    del día), así que una oferta pendiente de AYER que Comunio todavía no
    haya resuelto no aparece aquí — por eso no basta sola para evitar
    saldo negativo si el mercado dura más de un día.
    """
    from datetime import datetime, timezone

    today_prefix = datetime.now(timezone.utc).date().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM bids WHERE status = 'placed' AND created_at LIKE ?",
            (f"{today_prefix}%",),
        ).fetchone()
        return row["total"]


def get_open_bids() -> list[dict]:
    """
    Pujas que seguimos creyendo pendientes (status='placed') y de las que
    sabemos el id real de la oferta en Comunio (comunio_offer_id — las que
    fallaron al colocarse nunca tuvieron uno y no hay nada que reconciliar
    en ellas). Pensado para jobs.sync_data._reconcile_bids(): comparar cada
    una contra las ofertas pendientes reales (client.get_offers()) para
    saber si ya se resolvió (ganada/perdida) desde la última vez.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, player_id, comunio_offer_id FROM bids WHERE status = 'placed' AND comunio_offer_id IS NOT NULL"
        ).fetchall()
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
