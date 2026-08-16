from unittest.mock import patch

import config
import jobs.set_lineup as set_lineup
from clients.comunio_client import ComunioClient
from db.models import get_connection

NOW = "2026-08-16T18:00:00+00:00"


def _seed_squad_11(conn):
    """Plantilla justa 4-4-2 (11 jugadores exactos, todos ACTIVE) ya sincronizada en la BD."""
    players = (
        [("1001", "POR")]
        + [(f"100{2+i}", "DEF") for i in range(4)]
        + [(f"10{10+i}", "MED") for i in range(4)]
        + [(f"10{20+i}", "DEL") for i in range(2)]
    )
    for pid, position in players:
        conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", (pid, f"J{pid}", "Equipo", position, NOW))
        conn.execute(
            "INSERT INTO comunio_snapshots (player_id, price, points, last_points, average_points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, 500_000, 10, 2, 2.0, 0, "ACTIVE", NOW),
        )
    return [pid for pid, _ in players]


def test_set_lineup_picks_valid_lineup_and_always_audits_decision(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", False)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": [{"id": pid} for pid in squad_ids]}

    captured = []
    with patch("jobs.set_lineup.ComunioClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    assert "once decidido" in captured[0]
    assert "NO enviada a Comunio" in captured[0]

    with get_connection() as conn:
        row = conn.execute("SELECT formation, player_ids, submitted_to_comunio FROM lineup_decisions").fetchone()
    assert row["formation"] == "4-4-2"
    assert row["submitted_to_comunio"] == 0
    import json

    assert len(json.loads(row["player_ids"])) == 11


def test_set_lineup_empty_squad_notifies_without_crashing(tmp_db):
    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": []}

    captured = []
    with patch("jobs.set_lineup.ComunioClient", FakeClient), patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)):
        set_lineup.run()

    assert "plantilla vino vacía" in captured[0]


def test_set_lineup_insufficient_players_notifies_without_crashing(tmp_db, monkeypatch):
    """Menos de 11 disponibles -- pick_lineup lanza ValueError, el job no debe romperse."""
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", False)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)
    squad_ids = squad_ids[:-1]  # falta un jugador

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": [{"id": pid} for pid in squad_ids]}

    captured = []
    with patch("jobs.set_lineup.ComunioClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    assert "no se pudo formar el once" in captured[0]


def test_set_lineup_submits_to_comunio_when_enabled(tmp_db, monkeypatch):
    """Con ENABLE_LINEUP_AUTO_SUBMIT=True, debe llamar a set_lineup() con tactic/slots/substitutes reales."""
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", True)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)

    calls = []

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": [{"id": pid} for pid in squad_ids]}

        def set_lineup(self, tactic, lineup_by_slot, substitutes=None):
            calls.append({"tactic": tactic, "lineup_by_slot": lineup_by_slot, "substitutes": substitutes})
            return {"status": "OK"}

    captured = []
    with patch("jobs.set_lineup.ComunioClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    assert "Enviada a Comunio" in captured[0]
    assert len(calls) == 1
    assert calls[0]["tactic"] == "442"  # to_api_tactic() ya aplicado
    assert calls[0]["lineup_by_slot"]["11"] == "1001"  # portero siempre el slot 11
    assert len(calls[0]["lineup_by_slot"]) == 11

    with get_connection() as conn:
        row = conn.execute("SELECT submitted_to_comunio FROM lineup_decisions").fetchone()
    assert row["submitted_to_comunio"] == 1
