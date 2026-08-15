"""
Esquema de la base de datos (SQLite, archivo versionado en el repo).

El esquema es provisional: se afinará en cuanto se sepa con certeza qué
campos exactos exponen Comunio y las fuentes externas (Understat/FBref).
Por ahora cubre lo mínimo para poder empezar a persistir algo real:
jugadores, precios históricos, stats externas, pujas y decisiones (auditoría).
"""
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id              TEXT PRIMARY KEY,      -- id de Comunio
    name            TEXT NOT NULL,
    team            TEXT,
    position        TEXT,
    understat_id    TEXT,
    fbref_url       TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    price           INTEGER NOT NULL,
    points          INTEGER,
    matchday        INTEGER,
    recorded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    xg              REAL,
    minutes_played  INTEGER,
    injury_status   TEXT,
    source          TEXT NOT NULL,         -- 'understat' | 'fbref'
    recorded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bids (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       TEXT NOT NULL REFERENCES players(id),
    amount          INTEGER NOT NULL,
    status          TEXT NOT NULL,         -- 'placed' | 'won' | 'lost' | 'failed'
    score           REAL,                  -- score del evaluator que justificó la puja
    reason          TEXT,                  -- explicación legible para auditoría
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lineup_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    matchday        INTEGER NOT NULL,
    formation       TEXT NOT NULL,
    player_ids      TEXT NOT NULL,         -- JSON list
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
