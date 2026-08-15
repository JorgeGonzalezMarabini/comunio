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

CREATE TABLE IF NOT EXISTS lineup_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    matchday        INTEGER NOT NULL,
    formation       TEXT NOT NULL,          -- formato humano "4-4-2"; convertir con to_api_tactic() al llamar a la API
    player_ids      TEXT NOT NULL,          -- JSON list
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


if __name__ == "__main__":
    init_db()
    print(f"Base de datos inicializada en {config.DATABASE_PATH}")
