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

        def get_lineup(self):
            return {"answer": {"strategy": "4-4-2", "players": []}}  # sin alineación previa -- todos los slots vacíos

        def change_lineup(self, changes):
            calls.append(changes)
            return [{"change": c, "ok": True, "answer_or_error": {"code": "api.general.ok"}} for c in changes]

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    assert "Enviada a Futmondo (11/11 cambios aplicados)" in captured[0]
    assert len(calls) == 1
    changes = calls[0]
    assert len(changes) == 11
    by_id = {c["to"]: c for c in changes}
    assert by_id["1001"]["position"] == 10  # portero siempre el último índice

    with get_connection() as conn:
        row = conn.execute("SELECT submitted_to_futmondo FROM lineup_decisions").fetchone()
    assert row["submitted_to_futmondo"] == 1


def test_set_lineup_partial_failure_is_not_reported_as_fully_sent(tmp_db, monkeypatch):
    """
    Regresión del bug real de producción (2026-08-17, ver docstring de
    clients.futmondo_client.FutmondoClient.change_lineup): si Futmondo
    rechaza colocar a alguno de los 11, el job NO debe decir "Enviada a
    Futmondo" sin más -- tiene que dejar claro que se envió a medias y qué
    jugador(es) fallaron, y `submitted_to_futmondo` debe quedar en 0 (no se
    puede confiar en que la alineación mostrada coincida con la decidida).
    """
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", True)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in squad_ids]}

        def get_lineup(self):
            return {"answer": {"strategy": "4-4-2", "players": []}}  # sin alineación previa -- todos los slots vacíos

        def change_lineup(self, changes):
            results = [{"change": c, "ok": True, "answer_or_error": {"code": "api.general.ok"}} for c in changes]
            results[0]["ok"] = False
            results[0]["answer_or_error"] = "Futmondo rechazó la operación: api.market.some_rejection"
            return results

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    assert "Enviada A MEDIAS" in captured[0]
    assert "1/11" in captured[0]
    assert "Enviada a Futmondo (11/11" not in captured[0]

    with get_connection() as conn:
        row = conn.execute("SELECT submitted_to_futmondo FROM lineup_decisions").fetchone()
    assert row["submitted_to_futmondo"] == 0  # a medias NO cuenta como enviada


def _add_bench_candidates(conn):
    """
    Añade 4 jugadores más (uno por posición) con peor `average_points` que
    los titulares de _seed_squad_11 -- garantiza que queden en el
    banquillo, no como titulares, para poder probar pick_substitutes()/
    build_bench_changes() end-to-end en jobs/set_lineup.py.
    """
    extras = [("3001", "POR"), ("3002", "DEF"), ("3010", "MED"), ("3020", "DEL")]
    for pid, position in extras:
        conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", (pid, f"J{pid}", "Equipo", position, NOW))
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, last_points, average_points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, 500_000, 5, 1, 1.0, 0, "", NOW),  # average_points=1.0 < 2.0 de los titulares -- se van al banquillo
        )
    return [pid for pid, _ in extras]


def test_set_lineup_picks_and_reports_substitutes(tmp_db, monkeypatch):
    """
    Con más de 11 jugadores disponibles, debe elegir un suplente por
    posición (ver engine.lineup_optimizer.pick_substitutes) y reportarlo
    en la notificación -- CONFIRMADO al 100% que Futmondo solo tiene sitio
    para uno por posición (ver README/docstring del módulo).
    """
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", False)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)
        bench_ids = _add_bench_candidates(conn)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in squad_ids + bench_ids]}

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    assert "Suplentes: MED: J3010, DEL: J3020, POR: J3001, DEF: J3002" in captured[0]


def test_set_lineup_submits_bench_changes_with_isbench_true(tmp_db, monkeypatch):
    """Con ENABLE_LINEUP_AUTO_SUBMIT=True, los suplentes se mandan con isBench=true y el slot fijo confirmado."""
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", True)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)
        bench_ids = _add_bench_candidates(conn)

    calls = []

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in squad_ids + bench_ids]}

        def get_lineup(self):
            return {"answer": {"strategy": "4-4-2", "players": [], "bench": {"players": []}}}

        def change_lineup(self, changes):
            calls.append(changes)
            return [{"change": c, "ok": True, "answer_or_error": {"code": "api.general.ok"}} for c in changes]

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()

    changes = calls[0]
    assert len(changes) == 15  # 11 titulares + 4 suplentes
    bench_changes = [c for c in changes if c["isBench"]]
    assert len(bench_changes) == 4
    by_id = {c["to"]: c for c in bench_changes}
    assert by_id["3001"]["position"] == 2  # POR
    assert by_id["3002"]["position"] == 3  # DEF
    assert by_id["3010"]["position"] == 0  # MED
    assert by_id["3020"]["position"] == 1  # DEL
    assert "Enviada a Futmondo (15/15 cambios aplicados)" in captured[0]


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
