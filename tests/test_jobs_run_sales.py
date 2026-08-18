import runpy
from pathlib import Path
from unittest.mock import patch

import config
import jobs.run_sales as run_sales
from clients.futmondo_client import FutmondoClient, FutmondoOfferError
from db.models import get_connection

JOB_PATH = str(Path(__file__).resolve().parent.parent / "jobs" / "run_sales.py")

ROSTER_442_BASE = (
    [{"id": 1, "role": "portero", "status": "", "value": 500_000}]
    + [{"id": 10 + i, "role": "defensa", "status": "", "value": 500_000} for i in range(4)]
    + [{"id": 20 + i, "role": "centrocampista", "status": "", "value": 500_000} for i in range(4)]
    + [{"id": 30 + i, "role": "delantero", "status": "", "value": 500_000} for i in range(2)]
)


def _mark_won(player_id, amount):
    """Registra una puja ya ganada por el bot (ver db.models.get_won_bid_prices) -- la
    fuente que ahora usa run_sales/decide_sales en vez de `buyPrice` de Futmondo."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            (str(player_id), amount, "won", "2026-08-01T00:00:00+00:00"),
        )


def test_run_sales_no_profitable_candidates_notifies_and_returns(tmp_db):
    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [dict(p) for p in ROSTER_442_BASE]}  # nadie comprado por el bot -> nada que vender

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "ningún jugador supera el umbral" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"] == 0


def test_run_sales_lists_profitable_player_and_persists(tmp_db):
    roster = [dict(p) for p in ROSTER_442_BASE]
    roster.append({"id": 25, "role": "centrocampista", "status": "", "value": 900_000})
    _mark_won(25, 700_000)

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
    _mark_won(1, 300_000)  # portero, +66% de plusvalía

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
    roster.append({"id": 25, "role": "centrocampista", "status": "", "value": 900_000})
    _mark_won(25, 700_000)

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


def test_run_sales_skips_entirely_when_bot_disabled(tmp_db, monkeypatch, capsys):
    """ENABLE_BOT=false -- ni siquiera debe construirse el cliente, no digamos llamar a la red."""
    monkeypatch.setattr(config, "ENABLE_BOT", False)

    class BoomClient(FutmondoClient):
        def __init__(self, *args, **kwargs):
            raise AssertionError("no debería construirse FutmondoClient con ENABLE_BOT=false")

    captured = []
    with patch("jobs.run_sales.FutmondoClient", BoomClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert captured == []
    assert "ENABLE_BOT=false" in capsys.readouterr().out


def test_run_sales_main_does_not_record_job_run_when_bot_disabled(tmp_db, monkeypatch, capsys):
    """
    Regresión (2026-08-18): con ENABLE_BOT=false, el bloque `__main__` no
    debe entrar en `track_job_run()` -- si lo hiciera, aunque run() no haga
    nada, se registraría igualmente una fila en `job_runs`, ensuciando
    db/futmondo.db y provocando que el workflow comitee/pushee pese a que
    el bot está pausado (ver README, "Pausar el bot sin tocar los 5
    workflows").
    """
    monkeypatch.setattr(config, "ENABLE_BOT", False)

    runpy.run_path(JOB_PATH, run_name="__main__")

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0]
    assert count == 0
    assert "ENABLE_BOT=false" in capsys.readouterr().out
