from unittest.mock import patch

import config
import jobs.manage_substitutes as manage_substitutes
from clients.futmondo_client import FutmondoClient
from db.models import get_connection

NOW = "2026-08-17T12:00:00+00:00"


def _seed_player(conn, pid, position, status=""):
    conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", (pid, f"J{pid}", "Equipo", position, NOW))
    conn.execute(
        "INSERT INTO futmondo_snapshots (player_id, price, points, last_points, average_points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
        (pid, 500_000, 10, 2, 2.0, 0, status, NOW),
    )


def _lineup_answer(field_players, bench_players):
    """field_players/bench_players: [(slot, id), ...] -> shape de get_lineup()["answer"]."""
    return {
        "answer": {
            "strategy": "4-4-2",
            "players": [{"id": pid, "position": slot} for slot, pid in field_players],
            "bench": {"players": [{"id": pid, "position": slot} for slot, pid in bench_players]},
        }
    }


def test_manage_substitutes_empty_lineup_notifies_without_crashing(tmp_db):
    class FakeClient(FutmondoClient):
        def get_lineup(self):
            return _lineup_answer([], [])

    captured = []
    with patch("jobs.manage_substitutes.FutmondoClient", FakeClient), \
         patch("jobs.manage_substitutes.notify", side_effect=lambda m: captured.append(m)):
        manage_substitutes.run()

    assert "vino vacía" in captured[0]


def test_manage_substitutes_stays_silent_when_nobody_injured(tmp_db):
    """A diferencia del resto de jobs, sin sustituciones que decidir no debe notificar nada -- ver docstring del módulo."""
    with get_connection() as conn:
        _seed_player(conn, "def_titular", "DEF", status="")
        _seed_player(conn, "def_suplente", "DEF", status="")

    class FakeClient(FutmondoClient):
        def get_lineup(self):
            return _lineup_answer([(6, "def_titular")], [(3, "def_suplente")])

    captured = []
    with patch("jobs.manage_substitutes.FutmondoClient", FakeClient), \
         patch("jobs.manage_substitutes.notify", side_effect=lambda m: captured.append(m)):
        manage_substitutes.run()

    assert captured == []
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM substitution_decisions").fetchone()["n"] == 0


def test_manage_substitutes_decides_and_audits_without_submitting_by_default(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_SUBSTITUTE_AUTO_SUBMIT", False)
    with get_connection() as conn:
        _seed_player(conn, "def_titular", "DEF", status="lesionado")
        _seed_player(conn, "def_suplente", "DEF", status="")

    class FakeClient(FutmondoClient):
        def get_lineup(self):
            return _lineup_answer([(6, "def_titular")], [(3, "def_suplente")])

        def change_lineup(self, changes):
            raise AssertionError("no debería llamarse con ENABLE_SUBSTITUTE_AUTO_SUBMIT=false")

    captured = []
    with patch("jobs.manage_substitutes.FutmondoClient", FakeClient), \
         patch("jobs.manage_substitutes.notify", side_effect=lambda m: captured.append(m)):
        manage_substitutes.run()

    assert "1 sustitución(es) decidida(s)" in captured[0]
    assert "Jdef_titular" in captured[0] and "Jdef_suplente" in captured[0]
    assert "NO enviada(s) a Futmondo (ENABLE_SUBSTITUTE_AUTO_SUBMIT=false)" in captured[0]

    with get_connection() as conn:
        row = conn.execute("SELECT starter_id, substitute_id, position, submitted_to_futmondo FROM substitution_decisions").fetchone()
    assert row["starter_id"] == "def_titular"
    assert row["substitute_id"] == "def_suplente"
    assert row["position"] == "DEF"
    assert row["submitted_to_futmondo"] == 0


def test_manage_substitutes_submits_both_changes_in_order_when_enabled(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_SUBSTITUTE_AUTO_SUBMIT", True)
    with get_connection() as conn:
        _seed_player(conn, "def_titular", "DEF", status="lesionado")
        _seed_player(conn, "def_suplente", "DEF", status="")

    calls = []

    class FakeClient(FutmondoClient):
        def get_lineup(self):
            return _lineup_answer([(6, "def_titular")], [(3, "def_suplente")])

        def change_lineup(self, changes):
            calls.append(changes)
            return [{"change": c, "ok": True, "answer_or_error": {"code": "api.general.ok"}} for c in changes]

    captured = []
    with patch("jobs.manage_substitutes.FutmondoClient", FakeClient), \
         patch("jobs.manage_substitutes.notify", side_effect=lambda m: captured.append(m)):
        manage_substitutes.run()

    assert len(calls) == 1
    changes = calls[0]
    assert len(changes) == 2
    # El suplente entra primero (desde el banquillo, caso confirmado);
    # el titular sale después, al slot de banquillo ya libre -- ver
    # docstring de build_substitution_changes sobre por qué el orden importa.
    assert changes[0]["to"] == "def_suplente" and changes[0]["isBench"] is False
    assert changes[1]["to"] == "def_titular" and changes[1]["isBench"] is True

    assert "Enviada(s) a Futmondo (1/1 sustitución(es) aplicada(s))" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT submitted_to_futmondo FROM substitution_decisions").fetchone()
    assert row["submitted_to_futmondo"] == 1


def test_manage_substitutes_partial_failure_is_not_reported_as_fully_sent(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_SUBSTITUTE_AUTO_SUBMIT", True)
    with get_connection() as conn:
        _seed_player(conn, "def_titular", "DEF", status="lesionado")
        _seed_player(conn, "def_suplente", "DEF", status="")

    class FakeClient(FutmondoClient):
        def get_lineup(self):
            return _lineup_answer([(6, "def_titular")], [(3, "def_suplente")])

        def change_lineup(self, changes):
            # El segundo change (mandar al titular al banquillo) falla --
            # el escenario sin confirmar que documenta build_substitution_changes().
            results = [{"change": c, "ok": True, "answer_or_error": {"code": "api.general.ok"}} for c in changes]
            results[1] = {"change": changes[1], "ok": False, "answer_or_error": "Futmondo rechazó la operación: api.error.not_allowed"}
            return results

    captured = []
    with patch("jobs.manage_substitutes.FutmondoClient", FakeClient), \
         patch("jobs.manage_substitutes.notify", side_effect=lambda m: captured.append(m)):
        manage_substitutes.run()

    assert "1/1 sustitución(es) NO se pudieron aplicar" in captured[0]
    assert "api.error.not_allowed" in captured[0]
    assert "Enviada(s) a Futmondo (1/1" not in captured[0]

    with get_connection() as conn:
        row = conn.execute("SELECT submitted_to_futmondo FROM substitution_decisions").fetchone()
    assert row["submitted_to_futmondo"] == 0  # a medias NO cuenta como enviada


def test_manage_substitutes_submit_error_is_audited_without_crashing(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_SUBSTITUTE_AUTO_SUBMIT", True)
    with get_connection() as conn:
        _seed_player(conn, "def_titular", "DEF", status="lesionado")
        _seed_player(conn, "def_suplente", "DEF", status="")

    class FakeClient(FutmondoClient):
        def get_lineup(self):
            return _lineup_answer([(6, "def_titular")], [(3, "def_suplente")])

        def change_lineup(self, changes):
            raise RuntimeError("fallo de red simulado")

    captured = []
    with patch("jobs.manage_substitutes.FutmondoClient", FakeClient), \
         patch("jobs.manage_substitutes.notify", side_effect=lambda m: captured.append(m)):
        manage_substitutes.run()  # no debe lanzar

    assert "NO enviada(s) a Futmondo (error: fallo de red simulado)" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT submitted_to_futmondo FROM substitution_decisions").fetchone()
    assert row["submitted_to_futmondo"] == 0
