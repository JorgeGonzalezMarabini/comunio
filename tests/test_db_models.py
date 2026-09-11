import sqlite3
from datetime import datetime, timedelta, timezone

import config
from db.models import (
    get_bids_risked_today,
    get_connection,
    get_open_bids,
    get_open_sales,
    get_pending_bid_amount,
    get_player_features,
    get_purchase_baselines,
    get_real_lineup_check,
    get_recent_price_history,
    get_won_bid_prices,
    init_db,
    save_real_lineup_check,
    update_bid_status,
    update_sale_status,
)

NOW = "2026-08-16T18:00:00+00:00"


def test_get_player_features_joins_latest_snapshot_and_external_stats(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", ("1", "Jugador", "Equipo", "DEF", NOW))
        # Dos snapshots -- debe quedarse con el más reciente
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?)",
            ("1", 1_000_000, 10, 0, "", "2026-08-15T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?)",
            ("1", 1_200_000, 15, 1, "", NOW),
        )
        conn.execute(
            "INSERT INTO external_stats (player_id, season, xg, minutes_played, recorded_at) VALUES (?,?,?,?,?)",
            ("1", "2025", 5.0, 900, NOW),
        )

    features = get_player_features()
    assert len(features) == 1
    assert features[0]["price"] == 1_200_000  # el snapshot más reciente, no el primero
    assert features[0]["on_market"] == 1
    assert features[0]["xg"] == 5.0


def test_get_player_features_includes_team_games(tmp_db):
    """team_games (TODO.md #12) debe salir en las features igual que el resto de columnas de external_stats."""
    with get_connection() as conn:
        conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", ("1", "Jugador", "Equipo", "DEF", NOW))
        conn.execute(
            "INSERT INTO external_stats (player_id, season, minutes_played, games, team_games, recorded_at) VALUES (?,?,?,?,?,?)",
            ("1", "2025", 270, 3, 5, NOW),
        )

    features = get_player_features()
    assert features[0]["team_games"] == 5


def test_init_db_migrates_existing_db_missing_team_games_column(tmp_path, monkeypatch):
    """
    Regresión: `db/futmondo.db` está versionado en el repo con datos ya
    acumulados de antes de que existiera `team_games` -- `CREATE TABLE IF
    NOT EXISTS` por sí solo NO añade la columna a una tabla que ya existía,
    así que init_db() necesita una migración explícita (`_ensure_column`).
    """
    db_path = tmp_path / "old.db"
    monkeypatch.setattr(config, "DATABASE_PATH", str(db_path))

    # Esquema "viejo", sin team_games, con una fila ya guardada.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE external_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id TEXT NOT NULL,
            season TEXT NOT NULL,
            games INTEGER,
            minutes_played INTEGER,
            source TEXT NOT NULL DEFAULT 'understat',
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO external_stats (player_id, season, games, minutes_played, recorded_at) VALUES (?,?,?,?,?)",
        ("1", "2025", 5, 450, NOW),
    )
    conn.commit()
    conn.close()

    init_db()  # no debe reventar, y debe dejar la columna nueva usable

    with get_connection() as db_conn:
        db_conn.execute("INSERT INTO external_stats (player_id, season, team_games, recorded_at) VALUES (?,?,?,?)", ("2", "2026", 5, NOW))
        rows = {r["player_id"]: r["team_games"] for r in db_conn.execute("SELECT player_id, team_games FROM external_stats")}
    assert rows == {"1": None, "2": 5}  # la fila anterior a la migración sigue ahí, con team_games NULL


def test_get_player_features_only_on_market_filter(tmp_db):
    with get_connection() as conn:
        for pid, on_market in [("1", 1), ("2", 0)]:
            conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", (pid, "J", "E", "DEF", NOW))
            conn.execute(
                "INSERT INTO futmondo_snapshots (player_id, price, on_market, recorded_at) VALUES (?,?,?,?)",
                (pid, 1_000_000, on_market, NOW),
            )

    on_market_only = get_player_features(only_on_market=True)
    assert {p["id"] for p in on_market_only} == {"1"}
    all_players = get_player_features(only_on_market=False)
    assert {p["id"] for p in all_players} == {"1", "2"}


def test_get_player_features_left_join_keeps_players_without_external_stats(tmp_db):
    """Un jugador sin cruce con Understat debe salir igual, con xg/etc en NULL, no descartado."""
    with get_connection() as conn:
        conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", ("1", "J", "E", "DEF", NOW))
        conn.execute("INSERT INTO futmondo_snapshots (player_id, price, recorded_at) VALUES (?,?,?)", ("1", 1_000_000, NOW))

    features = get_player_features()
    assert len(features) == 1
    assert features[0]["xg"] is None


def test_get_bids_risked_today_sums_only_todays_placed_bids(tmp_db):
    today = datetime.now(timezone.utc).date().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 500_000, "placed", f"{today}T10:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("2", 300_000, "placed", f"{today}T12:00:00+00:00"),
        )
        conn.execute(  # de ayer -- no debe contar
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("3", 1_000_000, "placed", "2020-01-01T00:00:00+00:00"),
        )
        conn.execute(  # de hoy pero ya resuelta -- no debe contar
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("4", 999_999, "won", f"{today}T09:00:00+00:00"),
        )

    assert get_bids_risked_today() == 800_000


def test_get_pending_bid_amount_sums_regardless_of_date(tmp_db):
    """
    A diferencia de get_bids_risked_today() (solo hoy), get_pending_bid_amount()
    suma TODAS las pujas 'placed' sin importar la fecha -- es la protección
    de saldo real (ver clients.futmondo_client.total_pending_bid_amount).
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 500_000, "placed", "2020-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("2", 300_000, "placed", NOW),
        )
        conn.execute(  # ya resuelta -- no debe contar
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("3", 999_999, "won", NOW),
        )

    assert get_pending_bid_amount() == 800_000


def test_get_open_bids_and_update_status_roundtrip(tmp_db):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 500_000, "placed", NOW),
        )
        conn.execute(  # ya resuelta -- no debe salir en get_open_bids
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("2", 500_000, "failed", NOW),
        )

    open_bids = get_open_bids()
    assert len(open_bids) == 1
    assert open_bids[0]["player_id"] == "1"

    update_bid_status(open_bids[0]["id"], "won")
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM bids WHERE id = ?", (open_bids[0]["id"],)).fetchone()
    assert row["status"] == "won"
    assert get_open_bids() == []  # ya no está 'placed', no vuelve a salir


def test_get_open_sales_and_update_status_roundtrip(tmp_db):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sales (player_id, asking_price, status, created_at) VALUES (?,?,?,?)",
            ("1", 1_000_000, "listed", NOW),
        )

    open_sales = get_open_sales()
    assert len(open_sales) == 1

    update_sale_status(open_sales[0]["id"], "sold")
    assert get_open_sales() == []


def test_get_won_bid_prices_only_includes_won_bids(tmp_db):
    """Resuelve TODO.md #4: `bought_by_bot` para decide_sales() sale solo de pujas 'won', no de 'placed'/'lost'/'failed'."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 500_000, "won", NOW),
        )
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("2", 300_000, "placed", NOW),
        )
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("3", 200_000, "lost", NOW),
        )

    assert get_won_bid_prices() == {"1": 500_000}


def test_get_won_bid_prices_keeps_most_recent_when_bought_more_than_once(tmp_db):
    """Vendido y recomprado más tarde -> se queda con la puja 'won' más reciente, no la primera."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 500_000, "won", "2026-08-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 700_000, "won", NOW),
        )

    assert get_won_bid_prices() == {"1": 700_000}


# --- get_purchase_baselines / get_recent_price_history (evaluación del trigger de venta, 2026-09-11) ---


def test_get_purchase_baselines_peak_price_and_points_at_purchase(tmp_db):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 1_000_000, "won", "2026-08-01T00:00:00+00:00"),
        )
        # Snapshot ANTES de la compra -- no debe contar para el peak ni para el baseline de puntos.
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, recorded_at) VALUES (?,?,?,?)",
            ("1", 5_000_000, 999, "2026-07-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, recorded_at) VALUES (?,?,?,?)",
            ("1", 1_000_000, 10, "2026-08-02T00:00:00+00:00"),  # primero DESPUÉS de la compra
        )
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, recorded_at) VALUES (?,?,?,?)",
            ("1", 1_500_000, 20, "2026-08-10T00:00:00+00:00"),  # pico
        )
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, recorded_at) VALUES (?,?,?,?)",
            ("1", 1_200_000, 30, "2026-08-15T00:00:00+00:00"),
        )

    baselines = get_purchase_baselines()
    assert baselines["1"] == {"peak_price": 1_500_000, "points_at_purchase": 10}


def test_get_purchase_baselines_uses_most_recent_won_bid(tmp_db):
    """Vendido y recomprado más tarde -> el peak/baseline se cuentan desde la puja 'won' más reciente."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 500_000, "won", "2026-07-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 700_000, "won", "2026-08-01T00:00:00+00:00"),
        )
        conn.execute(  # de la primera compra -- NO debe contar
            "INSERT INTO futmondo_snapshots (player_id, price, points, recorded_at) VALUES (?,?,?,?)",
            ("1", 9_000_000, 999, "2026-07-15T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, recorded_at) VALUES (?,?,?,?)",
            ("1", 800_000, 5, "2026-08-02T00:00:00+00:00"),
        )

    baselines = get_purchase_baselines()
    assert baselines["1"] == {"peak_price": 800_000, "points_at_purchase": 5}


def test_get_purchase_baselines_omits_players_without_snapshot_since_purchase(tmp_db):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("1", 500_000, "won", NOW),
        )

    assert get_purchase_baselines() == {}


def test_get_recent_price_history_filters_by_window_and_player_ids(tmp_db):
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=30)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, recorded_at) VALUES (?,?,?)",
            ("1", 1_000_000, old),  # fuera de la ventana de 3 días
        )
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, recorded_at) VALUES (?,?,?)",
            ("1", 900_000, recent),
        )
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, recorded_at) VALUES (?,?,?)",
            ("2", 2_000_000, recent),  # otro jugador, no pedido
        )

    history = get_recent_price_history(["1"], since_days=3)
    assert history == {"1": [{"recorded_at": recent, "price": 900_000}]}


def test_get_recent_price_history_returns_empty_dict_for_no_player_ids(tmp_db):
    assert get_recent_price_history([], since_days=3) == {}


def test_get_real_lineup_check_returns_none_when_never_checked(tmp_db):
    assert get_real_lineup_check("Athletic Club", "2026-08-17") is None


def test_save_and_get_real_lineup_check_roundtrip(tmp_db):
    save_real_lineup_check("Athletic Club", "2026-08-17", 555, True, ["p1", "p2"], NOW)
    check = get_real_lineup_check("Athletic Club", "2026-08-17")

    assert check["fixture_id"] == 555
    assert check["lineup_published"] == 1
    assert check["starting_player_ids"] == '["p1", "p2"]'


def test_save_real_lineup_check_overwrites_same_team_and_date(tmp_db):
    """Clave (team, match_date): una segunda consulta el mismo día sobreescribe, no duplica."""
    save_real_lineup_check("Athletic Club", "2026-08-17", None, False, [], NOW)
    save_real_lineup_check("Athletic Club", "2026-08-17", 555, True, ["p1"], NOW)

    check = get_real_lineup_check("Athletic Club", "2026-08-17")
    assert check["lineup_published"] == 1
    assert check["fixture_id"] == 555

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM real_lineup_checks WHERE team = ? AND match_date = ?", ("Athletic Club", "2026-08-17")
        ).fetchone()[0]
    assert count == 1
