from unittest.mock import patch

import jobs.sync_data as sync_data
from clients.futmondo_client import FutmondoClient
from db.models import get_connection, get_player_features

NOW = "2026-08-16T18:00:00+00:00"


def test_upsert_player_and_snapshot_roster_uses_own_market_field(tmp_db, roster_player_factory):
    roster_player = roster_player_factory(id=1001, name="Jugador Propio", role="defensa", on_market=False)
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, roster_player, NOW)

    features = get_player_features()
    assert features[0]["position"] == "DEF"  # normalizado desde "defensa"
    assert features[0]["on_market"] == 0


def test_upsert_player_and_snapshot_market_player_always_on_market_true(tmp_db, market_player_factory):
    """
    Regresión del bug real de Comunio del que se hereda la misma cautela:
    los items de get_market() NO traen el campo "market" (exclusivo de
    roster) -- sin el parámetro on_market=True explícito, todo jugador de
    mercado se guardaría con on_market=0 y run_market() nunca encontraría
    candidatos.
    """
    market_player = market_player_factory(id=2002, name="Jugador Mercado", role="delantero")
    assert "market" not in market_player  # confirma la forma real: sin ese campo
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, market_player, NOW, on_market=True)

    features = get_player_features(only_on_market=True)
    assert len(features) == 1
    assert features[0]["id"] == "2002"


def test_upsert_player_and_snapshot_parses_dash_as_null(tmp_db, roster_player_factory):
    """Futmondo, igual que Comunio, puede devolver "-" en pretemporada en vez de omitir el campo o dar 0."""
    player = roster_player_factory(id=1, name="J", role="defensa")
    player["points"] = "-"
    player["average"]["fitness"] = ["-"]
    player["average"]["average"] = "-"

    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, player, NOW)

    features = get_player_features()
    assert features[0]["points"] is None
    assert features[0]["last_points"] is None
    assert features[0]["average_points"] is None


def test_upsert_player_and_snapshot_uses_last_fitness_entry_as_last_points(tmp_db, roster_player_factory):
    player = roster_player_factory(id=1, name="J", role="defensa")
    player["average"]["fitness"] = [3, 7, 9]

    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, player, NOW)

    features = get_player_features()
    assert features[0]["last_points"] == 9  # último elemento de "fitness"


def test_reconcile_bids_all_three_cases(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)", ("1001", 500_000, "placed", NOW))  # ganada: en plantilla
        conn.execute("INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)", ("1002", 500_000, "placed", NOW))  # perdida: ni en plantilla ni en mercado
        conn.execute("INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)", ("1003", 500_000, "placed", NOW))  # sigue pendiente: en mercado, no en plantilla
        conn.execute("INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)", ("1004", 500_000, "failed", NOW))  # ya no está 'placed', no se toca

    result = sync_data._reconcile_bids(roster_player_ids={"1001"}, market_player_ids={"1003"})
    assert result == {"won": 1, "lost": 1}

    with get_connection() as conn:
        statuses = {r["player_id"]: r["status"] for r in conn.execute("SELECT player_id, status FROM bids")}
    assert statuses == {"1001": "won", "1002": "lost", "1003": "placed", "1004": "failed"}


def test_reconcile_sales_marks_sold_when_player_left_roster(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO sales (player_id, asking_price, status, created_at) VALUES (?,?,?,?)", ("2748", 1_100_000, "listed", NOW))

    sold = sync_data._reconcile_sales(roster_player_ids=set())
    assert sold == 1

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM sales WHERE player_id = ?", ("2748",)).fetchone()
    assert row["status"] == "sold"


def test_reconcile_sales_leaves_still_owned_players_listed(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO sales (player_id, asking_price, status, created_at) VALUES (?,?,?,?)", ("2748", 1_100_000, "listed", NOW))

    sold = sync_data._reconcile_sales(roster_player_ids={"2748"})
    assert sold == 0


def test_run_full_job_with_fake_client(tmp_db, roster_player_factory):
    roster_player = roster_player_factory(id=1001, name="Jugador Propio", role="defensa")

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    captured = []
    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=({"players": []}, "2025", True)):
        sync_data.run()

    assert len(captured) >= 1
    assert "1 en plantilla" in captured[-1]
    features = get_player_features()
    assert len(features) == 1


def test_run_crosses_with_understat_by_surname_and_reports_breakdown(tmp_db, roster_player_factory):
    """
    Caso real y habitual (ver README): Futmondo muestra solo el apellido de
    un jugador conocido -- el cruce con Understat (nombre completo) debe
    resolverlo por apellido+equipo, no solo por nombre exacto, y el resumen
    de la notificación debe reflejar qué estrategia se usó.
    """
    roster_player = roster_player_factory(id=1001, name="Cairney", role="delantero", team="Fulham")
    league_data = {"players": [{"player_name": "Tom Cairney", "team_title": "Fulham"}]}

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    captured = []
    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=(league_data, "2025", False)):
        sync_data.run()

    assert "1/1 cruzados con Understat (1 surname+team)" in captured[-1]
    features = get_player_features()
    assert features[0]["xg"] is None  # el fixture de player_name no trae xG -- solo se comprueba que cruzó, no las stats
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM external_stats WHERE player_id = ?", ("1001",)).fetchone()
    assert row["n"] == 1
