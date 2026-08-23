import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

import config
import jobs.run_market as run_market
from clients.futmondo_client import FutmondoClient, FutmondoOfferError
from db.models import get_connection

NOW = "2026-08-16T18:00:00+00:00"
JOB_PATH = str(Path(__file__).resolve().parent.parent / "jobs" / "run_market.py")


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
    """
    `get_market()` se pide SIEMPRE ahora, incluso sin candidatos en la BD
    (ver docstring del módulo/TODO.md #15: la revisión de pujas sobre otro
    manager debe ejecutarse en cada pasada) -- de ahí el FakeClient en vez
    del FutmondoClient real tal cual.
    """
    captured = []

    class FakeClient(FutmondoClient):
        def get_market(self):
            return {"answer": []}

    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "no hay candidatos" in captured[0]


def test_run_market_places_bid_and_persists(tmp_db):
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 999}}}

        def get_market(self):
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 350_000, "computer": True}]}

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
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 999}}}

        def get_market(self):
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 350_000, "computer": True}]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise FutmondoOfferError("api.market.max_number_players_in_roster")

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()  # no debe lanzar

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM bids").fetchone()
    assert row["status"] == "failed"


def test_run_market_skips_bidding_when_roster_is_full(tmp_db):
    """
    Regresión (2026-08-22, 11 pujas fallidas en vivo): si la plantilla ya
    tiene `configuration.maxPlayersInRoster` jugadores (el máximo REAL de la
    liga -- NO `configuration.numberOfPlayers`, que es el número de
    jugadores INICIALES, ver docstring de clients/futmondo_client.py),
    Futmondo rechaza CUALQUIER puja con
    `api.market.max_number_players_in_roster` sin importar posición ni
    presupuesto -- el job debe cortar antes de evaluar candidatos, sin
    llamar a place_bid() ni auditar pujas 'failed'.
    """
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": str(i)} for i in range(15)]}

        def get_information(self):
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 15}}}

        def get_market(self):
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 350_000, "computer": True}]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise AssertionError("no debería intentar pujar con la plantilla llena")

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "plantilla completa" in captured[0]
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM bids").fetchone()["n"]
    assert count == 0


def test_run_market_ignores_numberOfPlayers_field_for_roster_limit(tmp_db):
    """
    Regresión (2026-08-22, a petición del usuario -- bug real en vivo):
    `configuration.numberOfPlayers` es el número de jugadores INICIALES de
    la liga (confirmado inspeccionando main.dart.js de la propia app,
    ver docstring de clients/futmondo_client.py.get_information()), NO el
    máximo real -- este job nunca debe leerlo como si fuera
    `maxPlayersInRoster`. Plantilla de 15 (== numberOfPlayers) pero SIN
    `maxPlayersInRoster` informado -> sin máximo confirmado, comportamiento
    conservador (ver test_run_market_treats_missing_max_roster_size_as_anomaly
    más abajo, TODO.md #18): no se puja ningún candidato nuevo, no se lee
    `numberOfPlayers` como fallback.
    """
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [{"id": str(i)} for i in range(15)]}

        def get_information(self):
            # Liga real con tope 18 -- pero numberOfPlayers=15 (jugadores
            # iniciales) NUNCA debe leerse como si fuera el máximo.
            return {"answer": {"budget": 20_000_000, "configuration": {"numberOfPlayers": 15}}}

        def get_market(self):
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 350_000, "computer": True}]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise AssertionError("no debería intentar pujar sin maxPlayersInRoster confirmado")

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "plantilla completa" not in captured[0]
    assert "maxPlayersInRoster no informado" in captured[0]
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM bids").fetchone()["n"]
    assert count == 0


def test_run_market_treats_missing_max_roster_size_as_anomaly(tmp_db):
    """
    Regresión (2026-08-23, a raíz de 2 pujas fallidas reales en vivo por
    api.market.max_number_players_in_roster -- ver TODO.md #18): si
    `configuration.maxPlayersInRoster` viene `None` (glitch transitorio de
    la API, o un cambio de forma de la respuesta), NO se debe degradar en
    silencio a "sin límite" -- eso reproduciría el bug original de las 11
    pujas fallidas. Comportamiento conservador esperado: no se puja NINGÚN
    candidato nuevo y se notifica la anomalía explícitamente.
    """
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            # Ni siquiera trae la clave "configuration" -- caso extremo del mismo bug.
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 350_000, "computer": True}]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise AssertionError("no debería intentar pujar sin maxPlayersInRoster confirmado")

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "maxPlayersInRoster no informado" in captured[0]
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM bids").fetchone()["n"]
    assert count == 0


def test_run_market_aborts_remaining_new_bids_after_live_roster_full_rejection(tmp_db):
    """
    Regresión (2026-08-23, 2 pujas fallidas reales en vivo con
    api.market.max_number_players_in_roster a pesar de que el snapshot
    inicial de roster decía que había hueco -- TOCTOU, ver TODO.md #18): en
    cuanto Futmondo rechaza EN VIVO un candidato con ese código exacto, el
    resto de candidatos NUEVOS de este mismo lote comparte el mismo hueco
    ya inexistente -- el job no debe seguir intentándolos uno a uno.
    """
    _seed_player("mejor", "MED", price=350_000, points=20)
    _seed_player("peor", "DEF", price=350_000, points=5)
    attempts = []

    class FakeClient(FutmondoClient):
        def get_roster(self):
            # Snapshot inicial: 0 en plantilla, tope 2 -- "hueco" para ambos candidatos.
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 2}}}

        def get_market(self):
            return {
                "answer": [
                    {"id": "mejor", "slug": "jugador-mejor", "value": 350_000, "computer": True},
                    {"id": "peor", "slug": "jugador-peor", "value": 350_000, "computer": True},
                ]
            }

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            attempts.append(player_id)
            # En vivo, Futmondo ya rechaza -- el hueco real cambió entre el
            # snapshot y este intento (otra oferta se resolvió en medio).
            raise FutmondoOfferError(
                "Futmondo rechazó la operación: api.market.max_number_players_in_roster",
                code="api.market.max_number_players_in_roster",
            )

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    # Solo se intentó el mejor candidato (primero del lote) -- "peor" nunca llegó a place_bid().
    assert attempts == ["mejor"]
    with get_connection() as conn:
        rows = {(r["player_id"], r["status"], r["error"]) for r in conn.execute("SELECT player_id, status, error FROM bids")}
    assert rows == {
        ("mejor", "failed", "Futmondo rechazó la operación: api.market.max_number_players_in_roster")
    }
    assert "plantilla llena detectada A MITAD" in captured[0]
    assert "peor" in captured[0]


def test_run_market_limits_bids_to_available_roster_slots_by_priority(tmp_db):
    """
    Regresión: con hueco PARCIAL en la plantilla (queda sitio, pero para
    menos candidatos de los que pasarían el resto de filtros), run_market
    debe pujar solo por los más importantes hasta llenar el hueco -- no
    por todos los que pasen score/precio/presupuesto.
    """
    _seed_player("mejor", "MED", price=350_000, points=20)
    _seed_player("peor", "DEF", price=350_000, points=5)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            # 1 jugador en plantilla, máximo de la liga 2 -- solo 1 plaza libre.
            return {"answer": [{"id": "999"}]}

        def get_information(self):
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 2}}}

        def get_market(self):
            return {
                "answer": [
                    {"id": "mejor", "slug": "jugador-mejor", "value": 350_000, "computer": True},
                    {"id": "peor", "slug": "jugador-peor", "value": 350_000, "computer": True},
                ]
            }

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            return {"code": "api.general.ok"}

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    with get_connection() as conn:
        rows = {r["player_id"] for r in conn.execute("SELECT player_id FROM bids")}
    # Solo el mejor candidato -- "peor" quedó bloqueado por la única plaza libre, no por score/precio.
    assert rows == {"mejor"}


def test_run_market_player_no_longer_in_market_is_audited_as_failed(tmp_db, monkeypatch):
    """
    El mercado cambió entre evaluar y pujar -- el candidato ya no aparece
    en get_market(). Necesita ENABLE_BIDS_ON_MANAGER_LISTINGS=true: con el
    default (false), un candidato ausente del mercado en vivo ya se
    descarta ANTES de evaluar (ver docstring del módulo/TODO.md #15, no
    hay forma de confirmar que sea del "Computer") -- este test simula en
    concreto el caso de que SÍ se pudo confirmar en su momento pero el
    mercado cambió justo antes de pujar.
    """
    monkeypatch.setattr(config, "ENABLE_BIDS_ON_MANAGER_LISTINGS", True)
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 999}}}

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

        def get_market(self):
            # Sin campo "bid" -- el mercado en vivo no aporta compromiso
            # extra aquí, la protección viene solo de la BD local.
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 1_000_000, "computer": True}]}

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "sin pujas esta ejecución" in captured[0]
    assert "comprometido en pujas pendientes=17.500.000" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM bids WHERE status != 'placed' OR player_id != '9999'").fetchone()["n"] == 0


def test_run_market_respects_pending_committed_from_live_market_bid_field(tmp_db):
    """
    Resuelve TODO.md #3: si la BD local no sabe nada (perdida/no
    reconciliada) pero el propio Futmondo SÍ reporta una puja pendiente
    real vía el campo "bid" de get_market(), la protección de presupuesto
    debe verla igualmente -- no depender solo de la auditoría local.
    """
    _seed_player("4069", "DEF", price=1_000_000)
    # BD local vacía a propósito -- nada en la tabla `bids`.

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {
                "answer": [
                    {"id": "4069", "slug": "jugador-4069", "value": 1_000_000, "computer": True},
                    # Puja pendiente real sobre OTRO jugador que la BD local desconoce.
                    {"id": "9999", "slug": "jugador-9999", "value": 1_000_000, "bid": {"id": "x", "price": 17_500_000}},
                ]
            }

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "sin pujas esta ejecución" in captured[0]
    assert "comprometido en pujas pendientes=17.500.000" in captured[0]


def test_run_market_skips_candidate_with_already_open_local_bid(tmp_db):
    """
    Regresión del hallazgo real (2026-08-18): pujar dos veces sobre el
    mismo jugador no actualiza el importe en Futmondo (ver
    clients.futmondo_client.real_pending_bid_amount) -- run_market no debe
    ni intentarlo. Un candidato con puja local 'placed' se excluye antes de
    evaluar, aunque siga en el mercado.
    """
    _seed_player("4069", "DEF", price=1_000_000)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            ("4069", 1_044_999, "placed", NOW),
        )

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            # "computer": True -- si no, la nueva revisión de pujas sobre
            # otro manager (TODO.md #15) intentaría cancelarla sin más,
            # que no es lo que este test quiere ejercitar.
            return {
                "answer": [
                    {
                        "id": "4069",
                        "slug": "jugador-4069",
                        "value": 1_000_000,
                        "computer": True,
                        "bid": {"id": "x", "price": 1_044_999},
                    }
                ]
            }

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise AssertionError("no debería intentar pujar de nuevo sobre un candidato ya con puja abierta")

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "puja local ya" in captured[0]
    with get_connection() as conn:
        # Sigue habiendo solo la puja original -- no se insertó una segunda fila.
        assert conn.execute("SELECT COUNT(*) AS n FROM bids").fetchone()["n"] == 1


# --- Ofertas sobre jugadores de OTRO MANAGER (a petición del usuario, 2026-08-22,
# TODO.md #15, config.ENABLE_BIDS_ON_MANAGER_LISTINGS) ---


def test_run_market_skips_candidate_on_another_managers_listing_by_default(tmp_db):
    """
    Mientras no se confirme que ganar una puja de compra a otro manager se
    resuelve sola (podría requerir que el otro manager acepte, igual que
    al vender -- ver TODO.md #15), el bot por defecto solo evalúa
    candidatos puestos en venta por el propio Futmondo ("computer": True).
    """
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 350_000, "computer": False}]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise AssertionError("no debería pujar por un jugador puesto en venta por otro manager")

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "otro manager" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM bids").fetchone()["n"] == 0


def test_run_market_bids_on_manager_listed_candidate_when_flag_enabled(tmp_db, monkeypatch):
    """Con config.ENABLE_BIDS_ON_MANAGER_LISTINGS=true, un candidato de otro manager vuelve a evaluarse con normalidad."""
    monkeypatch.setattr(config, "ENABLE_BIDS_ON_MANAGER_LISTINGS", True)
    _seed_player("4069", "DEF", price=350_000)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 999}}}

        def get_market(self):
            return {"answer": [{"id": "4069", "slug": "jugador-4069", "value": 350_000, "computer": False}]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            return {"code": "api.general.ok"}

    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify"):
        run_market.run()

    with get_connection() as conn:
        row = conn.execute("SELECT status, player_id FROM bids").fetchone()
    assert row["status"] == "placed"
    assert row["player_id"] == "4069"


def test_run_market_cancels_open_bid_on_manager_listed_player_by_default(tmp_db):
    """
    Regresión: una puja de compra YA ABIERTA sobre un jugador de otro
    manager se cancela por defecto -- no dejar presupuesto/plaza
    comprometidos indefinidamente en una oferta que no depende de
    nosotros. Se ejecuta aunque no haya ningún candidato nuevo que evaluar
    esta pasada (BD de candidatos vacía a propósito).
    """
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO bids (player_id, amount, status, score, created_at) VALUES (?,?,?,?,?)",
            ("otro", 1_000_000, "placed", 0.5, NOW),
        )
        row_id = cur.lastrowid

    cancel_calls = []

    class FakeClient(FutmondoClient):
        def get_market(self):
            return {
                "answer": [
                    {
                        "id": "otro",
                        "slug": "jugador-otro",
                        "value": 1_000_000,
                        "computer": False,
                        "bid": {"id": "futmondo-bid-otro", "price": 1_000_000},
                    }
                ]
            }

        def cancel_bid(self, bid_id):
            cancel_calls.append(bid_id)
            return {"code": "api.general.ok"}

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert cancel_calls == ["futmondo-bid-otro"]
    with get_connection() as conn:
        assert conn.execute("SELECT status FROM bids WHERE id = ?", (row_id,)).fetchone()["status"] == "cancelled"
    assert any("otro manager" in m and "cancelada" in m for m in captured)


def test_run_market_does_not_cancel_open_bid_on_computer_listed_player(tmp_db):
    """Una puja abierta sobre un jugador SÍ puesto en venta por el Computer nunca se toca."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, score, created_at) VALUES (?,?,?,?,?)",
            ("del_computer", 1_000_000, "placed", 0.5, NOW),
        )

    class FakeClient(FutmondoClient):
        def get_market(self):
            return {
                "answer": [
                    {
                        "id": "del_computer",
                        "slug": "jugador-del_computer",
                        "value": 1_000_000,
                        "computer": True,
                        "bid": {"id": "futmondo-bid-del_computer", "price": 1_000_000},
                    }
                ]
            }

        def cancel_bid(self, bid_id):
            raise AssertionError("no debería cancelar una puja sobre un jugador del Computer")

    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify"):
        run_market.run()

    with get_connection() as conn:
        assert conn.execute("SELECT status FROM bids WHERE player_id = 'del_computer'").fetchone()["status"] == "placed"


def test_run_market_reports_failed_cancellation_of_manager_listed_bid(tmp_db):
    """Si cancel_bid() falla al limpiar una puja sobre otro manager, se notifica y la puja local sigue 'placed' (no se marca 'cancelled' a ciegas)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, score, created_at) VALUES (?,?,?,?,?)",
            ("otro", 1_000_000, "placed", 0.5, NOW),
        )

    class FakeClient(FutmondoClient):
        def get_market(self):
            return {
                "answer": [
                    {
                        "id": "otro",
                        "slug": "jugador-otro",
                        "value": 1_000_000,
                        "computer": False,
                        "bid": {"id": "futmondo-bid-otro", "price": 1_000_000},
                    }
                ]
            }

        def cancel_bid(self, bid_id):
            raise FutmondoOfferError("api.market.bid_already_resolved")

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    with get_connection() as conn:
        assert conn.execute("SELECT status FROM bids WHERE player_id = 'otro'").fetchone()["status"] == "placed"
    assert any("fallida(s) al cancelar" in m for m in captured)


def test_run_market_discards_confirmed_injured_candidate_without_bidding(tmp_db):
    """
    A petición del usuario (2026-08-22): un candidato con lesión CONFIRMADA
    ("injured2") se descarta antes de evaluar, aunque tenga muy buenas
    stats -- nunca se puja por él, ni siquiera con score bajo (no llega a
    calcularse un score, sencillamente no entra en la evaluación).
    """
    _seed_player("lesionado", "DEL", price=350_000, points=20, status="injured2")  # stats excelentes

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {"answer": [{"id": "lesionado", "slug": "jugador-lesionado", "value": 350_000, "computer": True}]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise AssertionError("no debería pujar por un candidato con lesión confirmada")

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    assert "lesión confirmada" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM bids").fetchone()["n"] == 0


def test_run_market_still_bids_on_doubtful_candidate_with_reduced_score(tmp_db):
    """
    "doubt" (duda) NO se descarta -- sigue evaluándose y puede recibir
    puja, solo que con un score más bajo (config.EVALUATOR_WEIGHTS
    ["doubt_penalty"]) que un candidato idéntico pero sano.
    """
    _seed_player("en_duda", "DEL", price=350_000, points=10, status="doubt")
    _seed_player("sano", "MED", price=350_000, points=10, status="")

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 999}}}

        def get_market(self):
            return {"answer": [
                {"id": "en_duda", "slug": "jugador-en_duda", "value": 350_000, "computer": True},
                {"id": "sano", "slug": "jugador-sano", "value": 350_000, "computer": True},
            ]}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            return {"code": "api.general.ok"}

    captured = []
    with patch("jobs.run_market.FutmondoClient", FakeClient), patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)):
        run_market.run()

    with get_connection() as conn:
        rows = {r["player_id"]: r["score"] for r in conn.execute("SELECT player_id, score FROM bids")}
    # Ambos se pujaron (duda no descarta), pero el de duda con score menor.
    assert set(rows) == {"en_duda", "sano"}
    assert rows["en_duda"] < rows["sano"]
    assert rows["sano"] - rows["en_duda"] == pytest.approx(config.EVALUATOR_WEIGHTS["doubt_penalty"])


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
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 999}}}

        def get_market(self):
            return {"answer": [
                {"id": "del1", "slug": "jugador-del1", "value": 1_000_000, "computer": True},
                {"id": "med1", "slug": "jugador-med1", "value": 1_000_000, "computer": True},
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
            return {"answer": {"budget": 20_000_000, "configuration": {"maxPlayersInRoster": 999}}}

        def get_market(self):
            return {"answer": [
                {"id": "med_upgrade", "slug": "jugador-med_upgrade", "value": 1_000_000, "computer": True},
                {"id": "def_normal", "slug": "jugador-def_normal", "value": 1_000_000, "computer": True},
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


def test_run_market_main_does_not_record_job_run_when_bot_disabled(tmp_db, monkeypatch, capsys):
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


def test_run_market_scores_sacrificable_bids_freshly_not_frozen(tmp_db):
    """
    Regresión (2026-08-22, a petición del usuario): el score de una puja
    abierta que se pasa a find_cancel_swap_candidates() para decidir si
    merece la pena sacrificarla debe recalcularse con los datos de HOY
    (mismos pesos/boosts que el resto del mercado), no leerse tal cual de
    `bids.score` -- ese valor queda congelado desde el momento en que se
    pujó y puede llevar días/semanas desactualizado.
    """
    _seed_player("old", "DEF", price=1_000_000, points=10)  # jugador con la puja abierta
    _seed_player("bloqueado", "DEF", price=1_000_000, points=10)  # candidato nuevo, bloqueado por presupuesto=0

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, score, created_at) VALUES (?,?,?,?,?)",
            # Score congelado a un valor que evaluate_players() nunca podría producir hoy --
            # si el fix funciona, el que llega a sacrificable_bids no puede ser este.
            ("old", 1_044_999, "placed", 12345.0, NOW),
        )

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 0, "configuration": {"maxPlayersInRoster": 999}}}  # nada entra por la vía normal -- todo queda "blocked"

        def get_market(self):
            # "computer": True en ambos -- si no, la nueva revisión de
            # pujas sobre otro manager (TODO.md #15) cancelaría "old" sin
            # más, y "bloqueado" ni siquiera llegaría a evaluarse.
            return {
                "answer": [
                    {
                        "id": "old",
                        "slug": "jugador-old",
                        "value": 1_000_000,
                        "computer": True,
                        "bid": {"id": "futmondo-bid-old", "price": 1_044_999},
                    },
                    {"id": "bloqueado", "slug": "jugador-bloqueado", "value": 1_000_000, "computer": True},
                ]
            }

    with (
        patch("jobs.run_market.FutmondoClient", FakeClient),
        patch("jobs.run_market.find_cancel_swap_candidates", return_value=[]) as mock_swap,
        patch("jobs.run_market.notify"),
    ):
        run_market.run()

    assert mock_swap.called
    sacrificable_bids = mock_swap.call_args[0][1]
    assert len(sacrificable_bids) == 1
    assert sacrificable_bids[0]["player_id"] == "old"
    assert sacrificable_bids[0]["score"] != 12345.0


# --- Cancelar+pujar mejor (TODO.md #13) ---
#
# La lógica pura de qué cancelar/cuándo ya se prueba a fondo en
# test_bidding_strategy.py (find_cancel_swap_candidates) -- aquí solo se
# comprueba que run_market.py EJECUTA bien una propuesta ya decidida:
# cancela, audita 'cancelled', recalcula presupuesto con lo liberado, puja
# el candidato, y maneja los fallos a medias sin romper nada. Por eso se
# mockea find_cancel_swap_candidates en vez de forzar los scores reales del
# evaluator a un margen exacto.


def test_run_market_executes_cancel_swap_end_to_end(tmp_db):
    """
    Presupuesto tan ajustado que NADA entra por la vía normal (cap<=0 para
    todos, `decisions` vacío) -- justo el caso que motivó calcular el swap
    incluso sin pujas normales (ver docstring del módulo). Una puja local
    abierta de 4.500.000€ deja sin margen un presupuesto de 5.000.000€;
    cancelarla libera hueco de sobra para el candidato nuevo.
    """
    _seed_player("new", "DEL", price=500_000, points=10)

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO bids (player_id, amount, status, score, created_at) VALUES (?,?,?,?,?)",
            ("old", 4_500_000, "placed", 0.10, NOW),
        )
        old_row_id = cur.lastrowid

    fake_proposal = [
        {
            "candidate": {"id": "new", "score": 0.9, "price": 500_000},
            "sacrifice": {
                "local_row_id": old_row_id,
                "player_id": "old",
                "score": 0.10,
                "amount": 4_500_000,
                "bid_id": "futmondo-bid-old",
            },
            "reason": "test",
        }
    ]

    cancel_calls, place_calls = [], []

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 5_000_000, "configuration": {"maxPlayersInRoster": 999}}}

        def get_market(self):
            return {"answer": [{"id": "new", "slug": "jugador-new", "value": 500_000, "computer": True}]}

        def cancel_bid(self, bid_id):
            cancel_calls.append(bid_id)
            return {"code": "api.general.ok"}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            place_calls.append((player_id, amount))
            return {"code": "api.general.ok"}

    captured = []
    with (
        patch("jobs.run_market.FutmondoClient", FakeClient),
        patch("jobs.run_market.find_cancel_swap_candidates", return_value=fake_proposal),
        patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)),
    ):
        run_market.run()

    assert cancel_calls == ["futmondo-bid-old"]
    assert len(place_calls) == 1
    assert place_calls[0][0] == "new"
    assert place_calls[0][1] >= 500_000  # nunca por debajo del precio real

    with get_connection() as conn:
        rows = {r["player_id"]: r["status"] for r in conn.execute("SELECT player_id, status FROM bids")}
    assert rows["old"] == "cancelled"
    assert rows["new"] == "placed"
    assert any("swap" in m.lower() for m in captured)


def test_run_market_cancel_swap_aborts_cleanly_when_cancel_bid_fails(tmp_db):
    """
    Si cancel_bid() falla (HTTP o rechazo de negocio), el swap se aborta
    por completo -- nunca se llega a pujar por el candidato nuevo, y la
    puja vieja sigue 'placed' (nada cambia respecto a antes del intento).
    """
    _seed_player("new", "DEL", price=500_000, points=10)

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO bids (player_id, amount, status, score, created_at) VALUES (?,?,?,?,?)",
            ("old", 4_500_000, "placed", 0.10, NOW),
        )
        old_row_id = cur.lastrowid

    fake_proposal = [
        {
            "candidate": {"id": "new", "score": 0.9, "price": 500_000},
            "sacrifice": {
                "local_row_id": old_row_id,
                "player_id": "old",
                "score": 0.10,
                "amount": 4_500_000,
                "bid_id": "futmondo-bid-old",
            },
            "reason": "test",
        }
    ]

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 5_000_000, "configuration": {"maxPlayersInRoster": 999}}}

        def get_market(self):
            return {"answer": [{"id": "new", "slug": "jugador-new", "value": 500_000, "computer": True}]}

        def cancel_bid(self, bid_id):
            raise FutmondoOfferError("api.market.bid_already_resolved")

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise AssertionError("no debería llegar a pujar si cancel_bid() falló")

    captured = []
    with (
        patch("jobs.run_market.FutmondoClient", FakeClient),
        patch("jobs.run_market.find_cancel_swap_candidates", return_value=fake_proposal),
        patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)),
    ):
        run_market.run()  # no debe lanzar

    with get_connection() as conn:
        rows = {r["player_id"]: r["status"] for r in conn.execute("SELECT player_id, status FROM bids")}
    assert rows["old"] == "placed"  # sigue igual, nunca se marcó 'cancelled'
    assert "new" not in rows  # nunca se llegó a insertar una fila para el candidato
    assert any("cancelación fallida" in m for m in captured)


# --- Reajustar a la baja pujas abiertas cuyo VM ha caído (a petición del
# usuario, 2026-08-22) ---
#
# La lógica pura de cuándo reajustar ya se prueba a fondo en
# test_bidding_strategy.py (find_reprice_down_candidates) -- aquí solo se
# comprueba que run_market.py EJECUTA bien una propuesta ya decidida
# (cancela, audita 'cancelled', puja el importe recalculado más barato) y
# que esta fase NO depende de que haya candidatos nuevos ni swaps en la
# misma pasada (mismo mecanismo cancel_bid()+place_bid() que el swap, así
# que se mockea find_reprice_down_candidates en vez de forzar el score
# real del evaluator a una caída de VM exacta).


def test_run_market_executes_reprice_down_end_to_end(tmp_db):
    """
    Sin candidatos nuevos en el mercado (solo el jugador con la puja ya
    abierta) -- justo el caso que el corte "sin pujas esta ejecución" de
    antes se habría comido sin llegar a revisar el reajuste a la baja.
    """
    _seed_player("old", "DEF", price=700_000, points=10)

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO bids (player_id, amount, status, score, created_at) VALUES (?,?,?,?,?)",
            ("old", 1_500_000, "placed", 0.50, NOW),
        )
        old_row_id = cur.lastrowid

    fake_proposal = [
        {
            "player_id": "old",
            "local_row_id": old_row_id,
            "bid_id": "futmondo-bid-old",
            "old_amount": 1_500_000,
            "new_decision": {"player_id": "old", "amount": 900_000, "score": 0.50, "reason": "test"},
            "reason": "test",
        }
    ]

    cancel_calls, place_calls = [], []

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {
                "answer": [
                    {
                        "id": "old",
                        "slug": "jugador-old",
                        "value": 700_000,
                        "computer": True,
                        "bid": {"id": "futmondo-bid-old", "price": 1_500_000},
                    }
                ]
            }

        def cancel_bid(self, bid_id):
            cancel_calls.append(bid_id)
            return {"code": "api.general.ok"}

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            place_calls.append((player_id, amount))
            return {"code": "api.general.ok"}

    captured = []
    with (
        patch("jobs.run_market.FutmondoClient", FakeClient),
        patch("jobs.run_market.find_reprice_down_candidates", return_value=fake_proposal),
        patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)),
    ):
        run_market.run()

    assert cancel_calls == ["futmondo-bid-old"]
    assert place_calls == [("old", 900_000)]

    with get_connection() as conn:
        rows = conn.execute("SELECT status, amount FROM bids WHERE player_id = 'old' ORDER BY id").fetchall()
    assert [r["status"] for r in rows] == ["cancelled", "placed"]
    assert rows[1]["amount"] == 900_000
    assert any("reajustada" in m.lower() for m in captured)


def test_run_market_reprice_down_aborts_cleanly_when_cancel_bid_fails(tmp_db):
    """Si cancel_bid() falla, el reajuste se aborta -- la puja vieja sigue 'placed' tal cual."""
    _seed_player("old", "DEF", price=700_000, points=10)

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO bids (player_id, amount, status, score, created_at) VALUES (?,?,?,?,?)",
            ("old", 1_500_000, "placed", 0.50, NOW),
        )
        old_row_id = cur.lastrowid

    fake_proposal = [
        {
            "player_id": "old",
            "local_row_id": old_row_id,
            "bid_id": "futmondo-bid-old",
            "old_amount": 1_500_000,
            "new_decision": {"player_id": "old", "amount": 900_000, "score": 0.50, "reason": "test"},
            "reason": "test",
        }
    ]

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": 20_000_000}}

        def get_market(self):
            return {
                "answer": [
                    {
                        "id": "old",
                        "slug": "jugador-old",
                        "value": 700_000,
                        "computer": True,
                        "bid": {"id": "futmondo-bid-old", "price": 1_500_000},
                    }
                ]
            }

        def cancel_bid(self, bid_id):
            raise FutmondoOfferError("api.market.bid_already_resolved")

        def place_bid(self, player_id, player_slug, amount, is_clause=False):
            raise AssertionError("no debería llegar a pujar si cancel_bid() falló")

    captured = []
    with (
        patch("jobs.run_market.FutmondoClient", FakeClient),
        patch("jobs.run_market.find_reprice_down_candidates", return_value=fake_proposal),
        patch("jobs.run_market.notify", side_effect=lambda m: captured.append(m)),
    ):
        run_market.run()  # no debe lanzar

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM bids WHERE player_id = 'old'").fetchone()
    assert row["status"] == "placed"  # sigue igual, nunca se marcó 'cancelled'
    assert any("cancelación fallida" in m for m in captured)
