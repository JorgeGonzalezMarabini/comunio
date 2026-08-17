from unittest.mock import patch

import jobs.run_sales as run_sales
from clients.futmondo_client import FutmondoClient, FutmondoOfferError
from db.models import get_connection

ROSTER_442_BASE = (
    [{"id": 1, "role": "portero", "status": "", "value": 500_000, "buyPrice": 0}]
    + [{"id": 10 + i, "role": "defensa", "status": "", "value": 500_000, "buyPrice": 0} for i in range(4)]
    + [{"id": 20 + i, "role": "centrocampista", "status": "", "value": 500_000, "buyPrice": 0} for i in range(4)]
    + [{"id": 30 + i, "role": "delantero", "status": "", "value": 500_000, "buyPrice": 0} for i in range(2)]
)


def test_run_sales_no_profitable_candidates_notifies_and_returns(tmp_db):
    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [dict(p) for p in ROSTER_442_BASE]}  # nadie con buyPrice > 0 -> nada que vender

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "ningún jugador supera el umbral" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"] == 0


def test_run_sales_lists_profitable_player_and_persists(tmp_db):
    roster = [dict(p) for p in ROSTER_442_BASE]
    roster.append({"id": 25, "role": "centrocampista", "status": "", "value": 900_000, "buyPrice": 700_000})

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": roster}

        def list_for_sale(self, player_id, price):
            return {"code": "api.general.ok"}

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "1 jugador(es) puesto(s) en venta" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT status, player_id, purchase_price FROM sales").fetchone()
    assert row["status"] == "listed"
    assert row["player_id"] == "25"
    assert row["purchase_price"] == 700_000


def test_run_sales_never_lists_player_that_would_leave_position_uncovered(tmp_db):
    """Regresión de nivel job: el único portero, aunque rentable, no debe listarse jamás."""
    roster = [dict(p) for p in ROSTER_442_BASE]
    roster[0]["buyPrice"] = 300_000  # portero, +66% de plusvalía

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": roster}

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "ningún jugador supera el umbral" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"] == 0


def test_run_sales_business_rejection_is_audited_as_failed_without_crashing(tmp_db):
    roster = [dict(p) for p in ROSTER_442_BASE]
    roster.append({"id": 25, "role": "centrocampista", "status": "", "value": 900_000, "buyPrice": 700_000})

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": roster}

        def list_for_sale(self, player_id, price):
            raise FutmondoOfferError("algo salió mal")

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()  # no debe lanzar

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM sales").fetchone()
    assert row["status"] == "failed"


def test_run_sales_empty_roster_notifies_without_crashing(tmp_db):
    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "plantilla vino vacía" in captured[0]
