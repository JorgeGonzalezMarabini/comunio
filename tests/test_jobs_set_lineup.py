import json
from unittest.mock import patch

import config
import jobs.set_lineup as set_lineup
from clients.futmondo_client import FutmondoClient
from db.models import get_connection

NOW = "2026-08-16T18:00:00+00:00"


def _seed_squad_11(conn):
    """Plantilla justa 4-4-2 (11 jugadores exactos, todos sanos) ya sincronizada en la BD."""
    players = (
        [("1001", "POR")]
        + [(f"100{2+i}", "DEF") for i in range(4)]
        + [(f"10{10+i}", "MED") for i in range(4)]
        + [(f"10{20+i}", "DEL") for i in range(2)]
    )
    for pid, position in players:
        conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", (pid, f"J{pid}", "Equipo", position, NOW))
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, last_points, average_points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, 500_000, 10, 2, 2.0, 0, "", NOW),
        )
    return [pid for pid, _ in players]


def test_set_lineup_picks_valid_lineup_and_always_audits_decision(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", False)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in squad_ids]}

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    assert "once decidido" in captured[0]
    assert "NO enviada a Futmondo" in captured[0]

    with get_connection() as conn:
        row = conn.execute("SELECT formation, player_ids, submitted_to_futmondo FROM lineup_decisions").fetchone()
    assert row["formation"] == "4-4-2"
    assert row["submitted_to_futmondo"] == 0
    assert len(json.loads(row["player_ids"])) == 11


def test_set_lineup_empty_squad_notifies_without_crashing(tmp_db):
    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)):
        set_lineup.run()

    assert "plantilla vino vacía" in captured[0]


def test_set_lineup_insufficient_players_notifies_without_crashing(tmp_db, monkeypatch):
    """Menos de 11 disponibles -- pick_lineup lanza ValueError, el job no debe romperse."""
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", False)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)
    squad_ids = squad_ids[:-1]  # falta un jugador

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in squad_ids]}

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    assert "no se pudo formar el once" in captured[0]


def test_set_lineup_submits_to_futmondo_when_enabled(tmp_db, monkeypatch):
    """Con ENABLE_LINEUP_AUTO_SUBMIT=True, debe llamar a change_lineup() con la lista de changes construida."""
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", True)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)

    calls = []

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in squad_ids]}

        def change_lineup(self, changes):
            calls.append(changes)
            return {"code": "api.general.ok"}

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    assert "Enviada a Futmondo" in captured[0]
    assert len(calls) == 1
    changes = calls[0]
    assert len(changes) == 11
    by_id = {c["to"]: c for c in changes}
    assert by_id["1001"]["position"] == 10  # portero siempre el último índice

    with get_connection() as conn:
        row = conn.execute("SELECT submitted_to_futmondo FROM lineup_decisions").fetchone()
    assert row["submitted_to_futmondo"] == 1


def test_set_lineup_submit_failure_is_audited_without_crashing(tmp_db, monkeypatch):
    """Un fallo al enviar (HTTP o rechazo de negocio) no debe tumbar la auditoría de la decisión."""
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", True)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in squad_ids]}

        def change_lineup(self, changes):
            raise RuntimeError("fallo de red simulado")

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()  # no debe lanzar

    assert "NO enviada a Futmondo" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT submitted_to_futmondo FROM lineup_decisions").fetchone()
    assert row["submitted_to_futmondo"] == 0
