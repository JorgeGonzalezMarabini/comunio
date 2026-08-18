import runpy
from pathlib import Path
from unittest.mock import patch

import config
import jobs.manage_substitutes as manage_substitutes
from clients.futmondo_client import FutmondoClient
from db.models import get_connection

NOW = "2026-08-17T12:00:00+00:00"
JOB_PATH = str(Path(__file__).resolve().parent.parent / "jobs" / "manage_substitutes.py")


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
    assert len(changes) == 4
    # Orden fijo confirmado con una prueba real (2026-08-18, ver docstring
    # de build_substitution_changes): vaciar titular (campo), vaciar
    # suplente (banquillo), rellenar campo con el suplente, rellenar
    # banquillo con el titular -- Futmondo nunca acepta "to"+"from" juntos.
    assert changes[0]["from"] == "def_titular" and changes[0]["isBench"] is False and "to" not in changes[0]
    assert changes[1]["from"] == "def_suplente" and changes[1]["isBench"] is True and "to" not in changes[1]
    assert changes[2]["to"] == "def_suplente" and changes[2]["isBench"] is False and "from" not in changes[2]
    assert changes[3]["to"] == "def_titular" and changes[3]["isBench"] is True and "from" not in changes[3]

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
            # El tercer change (rellenar el campo con el suplente) falla --
            # basta con que UNO de los 4 falle para que la sustitución
            # entera no cuente como aplicada (ver manage_substitutes.run()).
            results = [{"change": c, "ok": True, "answer_or_error": {"code": "api.general.ok"}} for c in changes]
            results[2] = {"change": changes[2], "ok": False, "answer_or_error": "Futmondo rechazó la operación: api.error.not_allowed"}
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


def test_manage_substitutes_uses_real_lineup_check_when_enabled(tmp_db, monkeypatch):
    """
    Con ENABLE_REAL_LINEUP_CHECK activo, un titular SANO (status vacío)
    marcado como "confirmado fuera" por la fuente de alineación real debe
    sustituirse igual que uno lesionado -- ver
    engine.lineup_optimizer.build_substitution_changes().
    """
    monkeypatch.setattr(config, "ENABLE_SUBSTITUTE_AUTO_SUBMIT", False)
    monkeypatch.setattr(config, "ENABLE_REAL_LINEUP_CHECK", True)
    with get_connection() as conn:
        _seed_player(conn, "def_titular", "DEF", status="")  # sano según Futmondo
        _seed_player(conn, "def_suplente", "DEF", status="")

    class FakeClient(FutmondoClient):
        def get_lineup(self):
            return _lineup_answer([(6, "def_titular")], [(3, "def_suplente")])

    captured = []
    with patch("jobs.manage_substitutes.FutmondoClient", FakeClient), \
         patch("clients.football_lineups_client.find_players_confirmed_out_of_real_lineup", return_value={"def_titular"}), \
         patch("jobs.manage_substitutes.notify", side_effect=lambda m: captured.append(m)):
        manage_substitutes.run()

    assert "1 sustitución(es) decidida(s)" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT starter_id, substitute_id FROM substitution_decisions").fetchone()
    assert row["starter_id"] == "def_titular"
    assert row["substitute_id"] == "def_suplente"


def test_manage_substitutes_real_lineup_check_failure_falls_back_without_crashing(tmp_db, monkeypatch):
    """Un fallo consultando API-Football no debe tumbar la comprobación por lesión -- solo se pierde esa señal extra."""
    monkeypatch.setattr(config, "ENABLE_SUBSTITUTE_AUTO_SUBMIT", False)
    monkeypatch.setattr(config, "ENABLE_REAL_LINEUP_CHECK", True)
    with get_connection() as conn:
        _seed_player(conn, "def_titular", "DEF", status="lesionado")  # sigue detectable por is_injury_status
        _seed_player(conn, "def_suplente", "DEF", status="")

    class FakeClient(FutmondoClient):
        def get_lineup(self):
            return _lineup_answer([(6, "def_titular")], [(3, "def_suplente")])

    captured = []
    with patch("jobs.manage_substitutes.FutmondoClient", FakeClient), \
         patch("clients.football_lineups_client.find_players_confirmed_out_of_real_lineup", side_effect=RuntimeError("timeout")), \
         patch("jobs.manage_substitutes.notify", side_effect=lambda m: captured.append(m)):
        manage_substitutes.run()  # no debe lanzar

    assert any("fallo consultando alineaciones reales" in m for m in captured)
    assert any("1 sustitución(es) decidida(s)" in m for m in captured)


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


def test_manage_substitutes_skips_entirely_when_bot_disabled(tmp_db, monkeypatch, capsys):
    """ENABLE_BOT=false -- ni siquiera debe construirse el cliente, no digamos llamar a la red."""
    monkeypatch.setattr(config, "ENABLE_BOT", False)

    class BoomClient(FutmondoClient):
        def __init__(self, *args, **kwargs):
            raise AssertionError("no debería construirse FutmondoClient con ENABLE_BOT=false")

    captured = []
    with patch("jobs.manage_substitutes.FutmondoClient", BoomClient), patch("jobs.manage_substitutes.notify", side_effect=lambda m: captured.append(m)):
        manage_substitutes.run()

    assert captured == []
    assert "ENABLE_BOT=false" in capsys.readouterr().out


def test_manage_substitutes_main_does_not_record_job_run_when_bot_disabled(tmp_db, monkeypatch, capsys):
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
