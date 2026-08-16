from unittest.mock import patch

import jobs.run_sales as run_sales
from clients.comunio_client import ComunioClient, ComunioOfferError
from db.models import get_connection

SQUAD_442_BASE = (
    [{"id": 1, "position": "keeper", "status": "ACTIVE", "quotedprice": 500_000, "purchaseInfo": None}]
    + [{"id": 10 + i, "position": "defender", "status": "ACTIVE", "quotedprice": 500_000, "purchaseInfo": None} for i in range(4)]
    + [{"id": 20 + i, "position": "midfielder", "status": "ACTIVE", "quotedprice": 500_000, "purchaseInfo": None} for i in range(4)]
    + [{"id": 30 + i, "position": "striker", "status": "ACTIVE", "quotedprice": 500_000, "purchaseInfo": None} for i in range(2)]
)


def test_run_sales_no_profitable_candidates_notifies_and_returns(tmp_db):
    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": [dict(p) for p in SQUAD_442_BASE]}  # nadie con purchaseInfo -> nada que vender

    captured = []
    with patch("jobs.run_sales.ComunioClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "ningún jugador supera el umbral" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"] == 0


def test_run_sales_lists_profitable_player_and_persists(tmp_db):
    squad = [dict(p) for p in SQUAD_442_BASE]
    squad.append({"id": 25, "position": "midfielder", "status": "ACTIVE", "quotedprice": 900_000, "purchaseInfo": {"price": 700_000}})

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": squad}

        def list_for_sale(self, player_id, price):
            return {"status": "OK", "notPlaced": [], "purchasePrices": {}, "remaining": 30}

    captured = []
    with patch("jobs.run_sales.ComunioClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "1 jugador(es) puesto(s) en venta" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT status, player_id, purchase_price FROM sales").fetchone()
    assert row["status"] == "listed"
    assert row["player_id"] == "25"
    assert row["purchase_price"] == 700_000


def test_run_sales_never_lists_player_that_would_leave_position_uncovered(tmp_db):
    """Regresión de nivel job: el único portero, aunque rentable, no debe listarse jamás."""
    squad = [dict(p) for p in SQUAD_442_BASE]
    squad[0]["purchaseInfo"] = {"price": 300_000}  # portero, +66% de plusvalía

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": squad}

    captured = []
    with patch("jobs.run_sales.ComunioClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "ningún jugador supera el umbral" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"] == 0


def test_run_sales_business_rejection_is_audited_as_failed_without_crashing(tmp_db):
    squad = [dict(p) for p in SQUAD_442_BASE]
    squad.append({"id": 25, "position": "midfielder", "status": "ACTIVE", "quotedprice": 900_000, "purchaseInfo": {"price": 700_000}})

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": squad}

        def list_for_sale(self, player_id, price):
            raise ComunioOfferError("algo salió mal")

    captured = []
    with patch("jobs.run_sales.ComunioClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()  # no debe lanzar

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM sales").fetchone()
    assert row["status"] == "failed"
