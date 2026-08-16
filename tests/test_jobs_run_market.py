from unittest.mock import patch

import jobs.run_market as run_market
from clients.comunio_client import ComunioClient, ComunioOfferError
from db.models import get_connection

NOW = "2026-08-16T18:00:00+00:00"


def _seed_player(pid, position, price, points=10, on_market=1, status="ACTIVE"):
    with get_connection() as conn:
        conn.execute("INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)", (pid, f"J{pid}", "E", position, NOW))
        conn.execute(
            "INSERT INTO comunio_snapshots (player_id, price, points, last_points, average_points, on_market, status, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, price, points, points, float(points), on_market, status, NOW),
        )


def test_run_market_no_candidates_notifies_and_returns(tmp_db):
    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

    captured = []
    with patch("jobs.run_market.ComunioClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "no hay candidatos" in captured[0]


def test_run_market_places_bid_and_persists_with_offerid(tmp_db):
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": []}

        def get_offers(self):
            return {"credit": 20_000_000, "items": []}

        def place_bid(self, player_id, amount):
            return {"offerid": 999888777, "status": "OK", "price": amount, "tradableid": player_id}

    captured = []
    with patch("jobs.run_market.ComunioClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "1 puja(s) realizada(s)" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT status, comunio_offer_id FROM bids").fetchone()
    assert row["status"] == "placed"
    assert row["comunio_offer_id"] == 999888777


def test_run_market_business_rejection_is_audited_as_failed_without_crashing(tmp_db):
    """Regresión: la API puede devolver HTTP 200 pero rechazar la oferta (ComunioOfferError) -- no debe tumbar el job."""
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": []}

        def get_offers(self):
            return {"credit": 20_000_000, "items": []}

        def place_bid(self, player_id, amount):
            raise ComunioOfferError("player is not on exchangemarket")

    captured = []
    with patch("jobs.run_market.ComunioClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()  # no debe lanzar

    with get_connection() as conn:
        row = conn.execute("SELECT status, comunio_offer_id FROM bids").fetchone()
    assert row["status"] == "failed"
    assert row["comunio_offer_id"] is None


def test_run_market_respects_pending_committed_from_real_offers(tmp_db):
    """
    Regresión del bug de saldo negativo: si ya hay mucho comprometido en
    ofertas pendientes sin resolver (según get_offers(), no la BD local),
    el bot no debe pujar más de lo seguro.
    """
    _seed_player("4069", "DEF", price=1_000_000)

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": []}

        def get_offers(self):
            return {"credit": 20_000_000, "items": [{"id": 1, "type": "PURCHASE", "price": 17_500_000, "state": "PENDING"}]}

    captured = []
    with patch("jobs.run_market.ComunioClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "sin pujas esta ejecución" in captured[0]
    assert "comprometido en ofertas pendientes=17500000" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM bids").fetchone()["n"] == 0


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
    # (get_player_features), no del get_squad() en crudo -- hay que
    # sincronizar también estos jugadores, no solo declararlos en la
    # respuesta simulada. Delantera EN RIESGO (2 sanos para 2 titulares,
    # sin margen); el resto de posiciones con margen de sobra, para que el
    # boost de riesgo solo se aplique a DEL en este test.
    squad_ids = list(range(9001, 9016))
    squad_positions = ["DEL", "DEL", "POR", "POR"] + ["DEF"] * 5 + ["MED"] * 6
    for pid, position in zip(squad_ids, squad_positions):
        _seed_player(str(pid), position, price=500_000, on_market=0)

    class FakeClient(ComunioClient):
        def login(self):
            self.token = "fake"; self.user_id = "21161679"; return self.token

        def get_squad(self):
            return {"items": [{"id": pid, "status": "ACTIVE"} for pid in squad_ids]}

        def get_offers(self):
            return {"credit": 20_000_000, "items": []}

        def place_bid(self, player_id, amount):
            return {"offerid": 1, "status": "OK", "price": amount, "tradableid": player_id}

    captured = []
    with patch("jobs.run_market.ComunioClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    with get_connection() as conn:
        rows = {r["player_id"]: r["reason"] for r in conn.execute("SELECT player_id, reason FROM bids")}
    assert "prioridad" in rows.get("del1", "")
