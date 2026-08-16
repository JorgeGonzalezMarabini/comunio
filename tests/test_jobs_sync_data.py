from unittest.mock import patch

import jobs.sync_data as sync_data
from clients.comunio_client import ComunioClient
from db.models import get_connection, get_player_features

NOW = "2026-08-16T18:00:00+00:00"


def test_upsert_player_and_snapshot_squad_uses_own_onmarket_field(tmp_db):
    squad_player = {
        "id": 1001, "name": "Jugador Propio", "club": {"name": "MiEquipo"},
        "position": "defender", "status": "ACTIVE", "statusInfo": "",
        "points": 10, "lastPoints": 2, "averagePoints": 2.0,
        "quotedprice": 500_000, "recommendedprice": 500_000, "onMarket": False,
    }
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, squad_player, NOW)

    features = get_player_features()
    assert features[0]["position"] == "DEF"  # normalizado desde "defender"
    assert features[0]["on_market"] == 0


def test_upsert_player_and_snapshot_market_player_always_on_market_true(tmp_db):
    """
    Regresión del bug real detectado en producción: los items de
    get_market() (_embedded.player) NUNCA traen "onMarket" en su JSON --
    sin el parámetro on_market=True explícito, todo jugador de mercado se
    guardaba con on_market=0 y run_market() nunca encontraba candidatos.
    """
    market_player = {
        "id": 2002, "name": "Jugador Mercado", "club": {"name": "OtroEquipo"},
        "position": "striker", "status": "ACTIVE", "statusInfo": "",
        "points": 20, "quotedPrice": 800_000, "recommendedPrice": 800_000,
        # sin "onMarket" -- así viene de verdad
    }
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, market_player, NOW, on_market=True)

    features = get_player_features(only_on_market=True)
    assert len(features) == 1
    assert features[0]["id"] == "2002"


def test_upsert_player_and_snapshot_parses_dash_as_null(tmp_db):
    """Comunio devuelve "-" en pretemporada en vez de omitir el campo o dar 0."""
    player = {
        "id": 1, "name": "J", "club": {"name": "E"}, "position": "defender",
        "status": "ACTIVE", "statusInfo": "", "points": "-", "lastPoints": "-",
        "averagePoints": "-", "quotedprice": 500_000, "onMarket": False,
    }
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, player, NOW)

    features = get_player_features()
    assert features[0]["points"] is None
    assert features[0]["average_points"] is None


def test_reconcile_bids_all_four_cases(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO bids (player_id, comunio_offer_id, amount, status, created_at) VALUES (?,?,?,?,?)", ("1001", 5001, 500_000, "placed", NOW))  # ganada
        conn.execute("INSERT INTO bids (player_id, comunio_offer_id, amount, status, created_at) VALUES (?,?,?,?,?)", ("1002", 5002, 500_000, "placed", NOW))  # perdida
        conn.execute("INSERT INTO bids (player_id, comunio_offer_id, amount, status, created_at) VALUES (?,?,?,?,?)", ("1003", 5003, 500_000, "placed", NOW))  # sigue pendiente
        conn.execute("INSERT INTO bids (player_id, comunio_offer_id, amount, status, created_at) VALUES (?,?,?,?,?)", ("1004", None, 500_000, "failed", NOW))  # nunca tuvo offer_id

    result = sync_data._reconcile_bids(squad_player_ids={"1001"}, pending_offer_ids={5003})
    assert result == {"won": 1, "lost": 1}

    with get_connection() as conn:
        statuses = {r["player_id"]: r["status"] for r in conn.execute("SELECT player_id, status FROM bids")}
    assert statuses == {"1001": "won", "1002": "lost", "1003": "placed", "1004": "failed"}


def test_reconcile_sales_marks_sold_when_player_left_squad(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO sales (player_id, asking_price, status, created_at) VALUES (?,?,?,?)", ("2748", 1_100_000, "listed", NOW))

    sold = sync_data._reconcile_sales(squad_player_ids=set())
    assert sold == 1

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM sales WHERE player_id = ?", ("2748",)).fetchone()
    assert row["status"] == "sold"


def test_reconcile_sales_leaves_still_owned_players_listed(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO sales (player_id, asking_price, status, created_at) VALUES (?,?,?,?)", ("2748", 1_100_000, "listed", NOW))

    sold = sync_data._reconcile_sales(squad_player_ids={"2748"})
    assert sold == 0


def test_run_full_job_with_fake_client(tmp_db):
    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"
            self.user_id = "21161679"
            return self.token

        def get_squad(self):
            return {"items": [
                {"id": 1001, "name": "Jugador Propio", "club": {"name": "E"}, "position": "defender",
                 "status": "ACTIVE", "statusInfo": "", "points": 10, "lastPoints": 2, "averagePoints": 2.0,
                 "quotedprice": 500_000, "onMarket": False},
            ]}

        def get_market(self):
            return {"items": []}

        def get_offers(self):
            return {"credit": 20_000_000, "items": []}

    captured = []
    with patch("jobs.sync_data.ComunioClient", FakeClient), \
         patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=({"players": []}, "2025", True)):
        sync_data.run()

    assert len(captured) >= 1
    assert "1 en plantilla" in captured[-1]
    features = get_player_features()
    assert len(features) == 1
