import json
import runpy
from pathlib import Path
from unittest.mock import patch

import config
import jobs.set_lineup as set_lineup
from clients.futmondo_client import FutmondoClient
from db.models import get_connection

NOW = "2026-08-16T18:00:00+00:00"
JOB_PATH = str(Path(__file__).resolve().parent.parent / "jobs" / "set_lineup.py")


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


def test_set_lineup_stays_silent_when_rerun_finds_nothing_new(tmp_db, monkeypatch):
    """
    Regresión (2026-08-18): con el cron ahora repetido varias veces antes
    del cierre (ver set_lineup.yml, mitiga un clausulazo de última hora),
    una pasada sin cambios NO debe notificar -- si no, serían horas de la
    misma notificación repetida cada 20 min, puro ruido en Telegram (ver
    docstring del módulo). Sí debe seguir auditando la decisión.
    """
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", True)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)
        bench_ids = _add_bench_candidates(conn)  # sin banquillo habría warnings de riesgo -- también deben notificar

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in squad_ids + bench_ids]}

        def get_lineup(self):
            return {"answer": {"strategy": "4-4-2", "players": []}}

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}), \
         patch("jobs.set_lineup.build_lineup_changes", return_value=[]), \
         patch("jobs.set_lineup.build_bench_changes", return_value=[]):
        set_lineup.run()

    assert captured == []

    with get_connection() as conn:
        row = conn.execute("SELECT submitted_to_futmondo FROM lineup_decisions").fetchone()
    assert row["submitted_to_futmondo"] == 1  # sigue auditando aunque no notifique


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


def test_set_lineup_cascades_when_a_starter_leaves_the_squad(tmp_db, monkeypatch):
    """
    Pregunta real del usuario (2026-08-18): si un titular cae por
    cláusula, ¿el suplente que sube a titular deja su propio hueco de
    banquillo sin cubrir? No hace falta código especial para esto --
    pick_lineup()/pick_substitutes() se recalculan desde cero contra la
    plantilla ACTUAL en cada pasada (nunca recuerdan la decisión
    anterior), así que la cascada completa (titular perdido -> el mejor
    suplente sube a titular -> el siguiente mejor disponible se convierte
    en el nuevo suplente) sale sola. Este test la ejercita de punta a
    punta con dos pasadas seguidas, simulando el titular perdido entre
    medias -- igual que haría el cron repetido de set_lineup.yml.
    """
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", False)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)
        # 2 DEF de más, mismo average_points que los titulares (2.0) pero
        # peor "trend" (last_points más bajo) para que evaluate_players()
        # los deje claramente por detrás de los 4 titulares sin dejar de
        # diferenciarlos entre sí -- 2001 (trend menos negativo) será el
        # suplente en la 1a pasada, y el titular promovido en la 2a; 2002
        # se queda sin usar hasta que hace falta, ver 2a pasada.
        for pid, last_points in [("2001", 1.9), ("2002", 1.0)]:
            conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", (pid, f"J{pid}", "Equipo", "DEF", NOW))
            conn.execute(
                "INSERT INTO futmondo_snapshots (player_id, price, points, last_points, average_points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                (pid, 500_000, 5, last_points, 2.0, 0, "", NOW),
            )

    current_roster = squad_ids + ["2001", "2002"]

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in current_roster]}

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()  # pasada 1: plantilla completa, sin clausulazo todavía

    assert "DEF: J2001" in captured[0]  # el mejor de los dos DEF de más ya es el suplente
    assert "J2002" not in captured[0]  # el otro se queda sin uso, de sobra

    # Clausulazo: un titular de DEF (1002) desaparece de la plantilla.
    current_roster = [pid for pid in current_roster if pid != "1002"]
    captured.clear()

    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()  # pasada 2: el cron repetido ya ve el clausulazo en get_roster()

    with get_connection() as conn:
        rows = conn.execute("SELECT player_ids FROM lineup_decisions ORDER BY id").fetchall()
    second_starters = json.loads(rows[-1]["player_ids"])
    assert "1002" not in second_starters  # el que se fue con la cláusula, obviamente, ya no juega
    assert "2001" in second_starters  # el que era suplente ahora es titular -- sube solo
    assert "DEF: J2002" in captured[0]  # y el que quedaba "de sobra" ahora es el nuevo suplente


def test_set_lineup_replaces_substitute_when_the_substitute_itself_leaves(tmp_db, monkeypatch):
    """
    Segunda mitad de la misma pregunta (2026-08-18): si la cláusula se
    paga sobre un SUPLENTE (no un titular), el once no cambia, pero el
    hueco de banquillo sí debe cubrirse con el siguiente disponible --
    igual que arriba, sale solo de recalcular pick_substitutes() desde
    cero cada pasada, sin código especial para distinguir "se fue un
    titular" de "se fue un suplente".
    """
    monkeypatch.setattr(config, "ENABLE_LINEUP_AUTO_SUBMIT", False)
    with get_connection() as conn:
        squad_ids = _seed_squad_11(conn)
        for pid, last_points in [("2001", 1.9), ("2002", 1.0)]:
            conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", (pid, f"J{pid}", "Equipo", "DEF", NOW))
            conn.execute(
                "INSERT INTO futmondo_snapshots (player_id, price, points, last_points, average_points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                (pid, 500_000, 5, last_points, 2.0, 0, "", NOW),
            )

    current_roster = squad_ids + ["2001", "2002"]

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid} for pid in current_roster]}

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()  # pasada 1: J2001 es el suplente de DEF

    assert "DEF: J2001" in captured[0]

    # Clausulazo sobre el SUPLENTE (2001), no sobre un titular.
    current_roster = [pid for pid in current_roster if pid != "2001"]
    captured.clear()

    with patch("jobs.set_lineup.FutmondoClient", FakeClient), \
         patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.set_lineup.get_league_data", return_value={"players": [], "teams": {}, "dates": []}):
        set_lineup.run()  # pasada 2

    with get_connection() as conn:
        rows = conn.execute("SELECT player_ids FROM lineup_decisions ORDER BY id").fetchall()
    second_starters = json.loads(rows[-1]["player_ids"])
    assert set(second_starters) == {"1001", "1002", "1003", "1004", "1005", "1010", "1011", "1012", "1013", "1020", "1021"}  # el once NO cambia
    assert "DEF: J2002" in captured[0]  # el hueco de banquillo se cubre con el siguiente disponible


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


def test_set_lineup_skips_entirely_when_bot_disabled(tmp_db, monkeypatch, capsys):
    """ENABLE_BOT=false -- ni siquiera debe construirse el cliente, no digamos llamar a la red."""
    monkeypatch.setattr(config, "ENABLE_BOT", False)

    class BoomClient(FutmondoClient):
        def __init__(self, *args, **kwargs):
            raise AssertionError("no debería construirse FutmondoClient con ENABLE_BOT=false")

    captured = []
    with patch("jobs.set_lineup.FutmondoClient", BoomClient), patch("jobs.set_lineup.notify", side_effect=lambda m: captured.append(m)):
        set_lineup.run()

    assert captured == []
    assert "ENABLE_BOT=false" in capsys.readouterr().out


def test_set_lineup_main_does_not_record_job_run_when_bot_disabled(tmp_db, monkeypatch, capsys):
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
