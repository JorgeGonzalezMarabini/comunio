from unittest.mock import patch

import config
import jobs.run_market as run_market
from clients.futmondo_client import FutmondoClient, FutmondoOfferError
from db.models import get_connection

NOW = "2026-08-16T18:00:00+00:00"


def _seed_player(pid, position, price, points=10, on_market=1, status="", last_points=None, average_points=None):
    """
    `last_points`/`average_points`: por defecto ambos = `points` (sin
    "trend", ver engine/evaluator.py) -- pásalos explícitamente para
    simular forma reciente distinta de la media de temporada.
    """
    last_points = points if last_points is None else last_points
    average_points = float(points) if average_points is None else average_points
    with get_connection() as conn:
        conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", (pid, f"J{pid}", "E", position, NOW))
        conn.execute(
            "INSERT INTO futmondo_snapshots (player_id, price, points, last_points, average_points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, price, points, last_points, average_points, on_market, status, NOW),
        )


def test_run_market_no_candidates_notifies_and_returns(tmp_db):
    captured = []
    with patch("jobs.run_market.FutmondoClient", FutmondoClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "no hay candidatos" in captured[0]


def test_run_market_places_bid_and_persists(tmp_db):
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 350_000}]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            return {"code": "api.general.ok"}

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "1 puja(s) realizada(s)" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT status, player_id, amount FROM bids").fetchone()
    assert row["status"] == "placed"
    assert row["player_id"] == "4069"


def test_run_market_business_rejection_is_audited_as_failed_without_crashing(tmp_db):
    """Regresión: la API puede devolver HTTP 200 pero rechazar la oferta (FutmondoOfferError) -- no debe tumbar el job."""
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 350_000}]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise FutmondoOfferError("api.market.max_number_players_in_roster")

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()  # no debe lanzar

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM bids").fetchone()
    assert row["status"] == "failed"


def test_run_market_player_no_longer_in_market_is_audited_as_failed(tmp_db):
    """El mercado cambió entre evaluar y pujar -- el candidato ya no aparece en get_market()."""
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {"answer": []}  # ya no está

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM bids").fetchone()
    assert row["status"] == "failed"


def test_run_market_respects_pending_committed_from_local_db(tmp_db):
    """
    Regresión del bug de saldo negativo: si ya hay mucho comprometido en
    pujas locales pendientes sin resolver (get_pending_bid_amount(), no un
    endpoint externo -- Futmondo no expone uno, ver
    clients/futmondo_client.py), el bot no debe pujar más de lo seguro.
    """
    _seed_player("4069", "DEF", price=1_000_000)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("9999", 17_500_000, "placed", NOW),
        )

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "sin pujas esta ejecución" in captured[0]
    assert "comprometido en pujas pendientes=17500000" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM bids WHERE status != 'placed' OR player_id != '9999'").fetchone()["n"] == 0


def test_run_market_prioritizes_at_risk_position_over_higher_score(tmp_db):
    """
    Verifica que run_market usa engine.squad_risk sobre la plantilla propia
    para priorizar posiciones en riesgo -- no solo evaluator.evaluate_players
    a secas.
    """
    # Mercado: un delantero y un medio con precio/puntos que hacen que el
    # medio tenga mejor score base con los pesos de puja.
    _seed_player("del1", "DEL", price=1_000_000, points=8)
    _seed_player("med1", "MED", price=1_000_000, points=12)

    # Plantilla propia: run_market calcula el riesgo a partir de la BD
    # (get_player_features), no del get_roster() en crudo -- hay que
    # sincronizar también estos jugadores, no solo declararlos en la
    # respuesta simulada. Delantera EN RIESGO (2 sanos para 2 titulares,
    # sin margen); el resto de posiciones con margen de sobra, para que el
    # boost de riesgo solo se aplique a DEL en este test.
    squad_ids = list(range(9001, 9016))
    squad_positions = ["DEL", "DEL", "POR", "POR"] + ["DEF"] * 5 + ["MED"] * 6
    for pid, position in zip(squad_ids, squad_positions):
        _seed_player(str(pid), position, price=500_000, on_market=0)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": str(pid), "status": ""} for pid in squad_ids]}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {"answer": [
                {"id": "del1", "slug": "jugador-del1", "value": 1_000_000},
                {"id": "med1", "slug": "jugador-med1", "value": 1_000_000},
            ]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            return {"code": "api.general.ok"}

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    with get_connection() as conn:
        rows = {r["player_id"]: r["reason"] for r in conn.execute("SELECT player_id, reason FROM bids")}
    assert "prioridad" in rows.get("del1", "")


def test_run_market_prioritizes_candidate_that_would_upgrade_lineup(tmp_db):
    """
    Un candidato que en forma reciente (trend, LINEUP_EVALUATOR_WEIGHTS)
    supera al peor titular actual de su posición debe marcarse
    "mejora el once titular", aunque esa posición no tenga ningún riesgo
    de plantilla (banquillo de sobra) -- señal de CALIDAD, no de CANTIDAD.
    """
    # Plantilla: 5 MED con margen de sobra (4 titulares + 1 de banquillo,
    # bench=1 -> NO en riesgo) y sin "trend" (last_points == average_points,
    # todos empatan bajo LINEUP_EVALUATOR_WEIGHTS, que no pesa el precio) +
    # resto de posiciones con margen también, para que ninguna quede en
    # riesgo y así aislar la señal de "mejora del once".
    for i in range(5):
        _seed_player(f"med{i}", "MED", price=500_000, points=5, on_market=0)
    for i in range(2):
        _seed_player(f"por{i}", "POR", price=500_000, points=5, on_market=0)
    for i in range(5):
        _seed_player(f"def{i}", "DEF", price=500_000, points=5, on_market=0)
    for i in range(3):
        _seed_player(f"del{i}", "DEL", price=500_000, points=5, on_market=0)
    squad_ids = [f"med{i}" for i in range(5)] + [f"por{i}" for i in range(2)] + [f"def{i}" for i in range(5)] + [f"del{i}" for i in range(3)]

    # Mercado: un MED en gran forma reciente (last_points muy por encima de
    # su media -> trend alto bajo LINEUP_EVALUATOR_WEIGHTS, supera al
    # listón de los MED de plantilla, todos con trend=0) y un DEF con mejor
    # relación puntos/precio (EVALUATOR_WEIGHTS, el que de verdad decide
    # cuánto pujar) para comprobar que la prioridad viene del boost, no de
    # que ya tuviera mejor score de puja.
    _seed_player("med_upgrade", "MED", price=1_000_000, points=6, last_points=12, average_points=6.0)
    _seed_player("def_normal", "DEF", price=1_000_000, points=12)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": pid, "status": ""} for pid in squad_ids]}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {"answer": [
                {"id": "med_upgrade", "slug": "jugador-med_upgrade", "value": 1_000_000},
                {"id": "def_normal", "slug": "jugador-def_normal", "value": 1_000_000},
            ]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            return {"code": "api.general.ok"}

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    with get_connection() as conn:
        rows = {r["player_id"]: r["reason"] for r in conn.execute("SELECT player_id, reason FROM bids")}
    assert "mejora el once titular" in rows.get("med_upgrade", "")
    assert "mejora el once titular" not in rows.get("def_normal", "")
    # Ninguna posición estaba en riesgo de cantidad -- el boost es solo por calidad.
    assert "prioridad riesgo de plantilla" not in rows.get("med_upgrade", "")


def test_run_market_skips_entirely_when_bot_disabled(tmp_db, monkeypatch, capsys):
    """ENABLE_BOT=false -- ni siquiera debe construirse el cliente, no digamos llamar a la red."""
    monkeypatch.setattr(config, "ENABLE_BOT", False)

    class BoomClient(FutmondoClient):
        def __init__(self, *args, **kwargs):
            raise AssertionError("no debería construirse FutmondoClient con ENABLE_BOT=false")

    captured = []
    with patch("jobs.run_market.FutmondoClient", BoomClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert captured == []  # no debe notificar nada -- pausado a propósito, no un fallo
    assert "ENABLE_BOT=false" in capsys.readouterr().out
