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


class _BaseFakeClient(FutmondoClient):
    """
    Base común de los `FakeClient` de este módulo: `_process_received_offers()`
    (TODO.md #15) llama a `get_my_players_in_market()` ANTES que a
    `get_roster()` en cada `run()` -- sin este override por defecto (sin
    ofertas pendientes), cada test que no le interese este paso heredaría
    la implementación real y lanzaría FutmondoAuthError contra credenciales
    None (ver `no_real_futmondo_network` en conftest.py). Los tests que sí
    quieren ejercitar ofertas recibidas sobrescriben este método aparte.

    Mismo motivo para `get_market()`/`get_lineup()` (añadidas 2026-08-23,
    ver docstring del módulo de jobs/run_sales.py -- expirationDate de
    mercado y alineación titular para los refinamientos de la vía 5 de
    engine/selling_strategy.py): por defecto, sin mercado ni alineación,
    para que los tests que no ejercitan esos refinamientos no necesiten
    sobrescribirlas una por una.
    """

    def get_my_players_in_market(self):
        return {"answer": []}

    def get_market(self):
        return {"answer": []}

    def get_lineup(self):
        return {"answer": {"players": []}}


def _mark_won(player_id, amount):
    """Registra una puja ya ganada por el bot (ver db.models.get_won_bid_prices) -- la
    fuente que ahora usa run_sales/decide_sales en vez de `buyPrice` de Futmondo."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)",
            (str(player_id), amount, "won", "2026-08-01T00:00:00+00:00"),
        )


def test_run_sales_no_profitable_candidates_notifies_and_returns(tmp_db):
    class FakeClient(_BaseFakeClient):
        def get_roster(self):
            return {"answer": [dict(p) for p in ROSTER_442_BASE]}  # nadie comprado por el bot -> nada que vender

        def get_information(self):
            return {"answer": {"budget": 0}}

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "ningún jugador supera el umbral" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"] == 0


def test_run_sales_reports_roster_occupancy_alongside_futmondo_client(tmp_db):
    """
    Regresión (2026-08-22, a petición del usuario): la notificación debe
    incluir siempre la ocupación de plantilla (mismo campo
    `configuration.maxPlayersInRoster` que usa jobs/run_market.py -- NO
    `configuration.numberOfPlayers`, que es el número de jugadores
    INICIALES de la liga, ver docstring de clients/futmondo_client.py) --
    para poder correlacionar a simple vista "plantilla llena/casi llena +
    nada que vender aquí" con que run_market esté bloqueando pujas por
    falta de plazas.
    """
    class FakeClient(_BaseFakeClient):
        def get_roster(self):
            return {"answer": [dict(p) for p in ROSTER_442_BASE]}  # 11 jugadores

        def get_information(self):
            return {"answer": {"budget": 0, "configuration": {"maxPlayersInRoster": 20}}}

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "11/20" in captured[0]


def test_run_sales_passes_own_squad_and_market_features_to_decide_sales(tmp_db):
    """
    Regresión (2026-08-22, a petición del usuario): run_sales debe pasar a
    decide_sales() las features de la plantilla propia (jugadores en
    plantilla, cualquiera que sea su on_market en BD) y del mercado
    abierto ahora mismo (solo on_market=1) -- ver docstring del módulo,
    "Oportunidad de mercado / plaza escasa". Si no las pasa, esa vía queda
    siempre desactivada aunque decide_sales() la soporte.
    """
    roster = [dict(p) for p in ROSTER_442_BASE]  # id=20 (Medio0) en plantilla
    _mark_won(20, 700_000)

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)",
            ("20", "Medio0", "E", "MED", "2026-08-16T18:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO futmondo_snapshots "
            "(player_id, price, points, last_points, average_points, on_market, status, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("20", 700_000, 10, 10, 10.0, 0, "", "2026-08-16T18:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO players (id, name, team, position, updated_at) VALUES (?,?,?,?,?)",
            ("999", "Candidato", "E", "MED", "2026-08-16T18:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO futmondo_snapshots "
            "(player_id, price, points, last_points, average_points, on_market, status, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("999", 700_000, 10, 10, 10.0, 1, "", "2026-08-16T18:00:00+00:00"),
        )

    class FakeClient(_BaseFakeClient):
        def get_roster(self):
            return {"answer": roster}

        def get_information(self):
            return {"answer": {"budget": 0}}

    with (
        patch("jobs.run_sales.FutmondoClient", FakeClient),
        patch("jobs.run_sales.decide_sales", return_value=[]) as mock_decide,
        patch("jobs.run_sales.notify"),
    ):
        run_sales.run()

    assert mock_decide.called
    kwargs = mock_decide.call_args.kwargs
    own_ids = {str(p["id"]) for p in kwargs["own_squad_features"]}
    market_ids = {str(p["id"]) for p in kwargs["market_candidates"]}
    assert "20" in own_ids  # en plantilla -- pasa aunque on_market=0
    assert "999" in market_ids  # en mercado (on_market=1), no en plantilla
    assert "999" not in own_ids
    assert "20" not in market_ids  # no está listado en el mercado


def test_run_sales_lists_profitable_player_and_persists(tmp_db):
    roster = [dict(p) for p in ROSTER_442_BASE]
    roster.append({"id": 25, "role": "centrocampista", "status": "", "value": 900_000})
    _mark_won(25, 700_000)

    class FakeClient(_BaseFakeClient):
        def get_roster(self):
            return {"answer": roster}

        def get_information(self):
            return {"answer": {"budget": 0}}

        def get_player_summary(self, player_id):
            return {"answer": {"prices": []}}  # sin histórico -- ENABLE_SELLING_REVALUATION_PREMIUM activo por defecto, sin prima aquí

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

    class FakeClient(_BaseFakeClient):
        def get_roster(self):
            return {"answer": roster}

        def get_information(self):
            return {"answer": {"budget": 0}}

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

    class FakeClient(_BaseFakeClient):
        def get_roster(self):
            return {"answer": roster}

        def get_information(self):
            return {"answer": {"budget": 0}}

        def get_player_summary(self, player_id):
            return {"answer": {"prices": []}}  # sin histórico -- ENABLE_SELLING_REVALUATION_PREMIUM activo por defecto, sin prima aquí

        def list_for_sale(self, player_id, price):
            raise FutmondoOfferError("algo salió mal")

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()  # no debe lanzar

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM sales").fetchone()
    assert row["status"] == "failed"


def test_run_sales_empty_roster_notifies_without_crashing(tmp_db):
    class FakeClient(_BaseFakeClient):
        def get_roster(self):
            return {"answer": []}

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "plantilla vino vacía" in captured[0]


def _offer_listing(player_id=25, name="Fer Niño", listing_price=2_623_496, bid_id="bid1", offer_price=2_700_000, is_clause=False, bidder_name="", bidder_slug=""):
    """
    Item de get_my_players_in_market()["answer"] con una única oferta -- ver
    TODO.md #15. Por defecto la oferta es "de Futmondo" (bidder_name/slug
    vacíos, ver _is_futmondo_offer()) -- pasa un bidder_name/slug real para
    simular una oferta de otro manager.
    """
    return {
        "id": player_id,
        "name": name,
        "price": listing_price,
        "isClause": is_clause,
        "bids": [{"id": bid_id, "price": offer_price, "userTeam": {"name": bidder_name, "slug": bidder_slug}}],
    }


def test_run_sales_accepts_highest_futmondo_offer_above_asking_price(tmp_db):
    """Criterio del usuario (2026-08-22, TODO.md #15): aceptar SIEMPRE la oferta que SUPERE el precio pedido."""
    accept_calls = []

    class FakeClient(_BaseFakeClient):
        def get_my_players_in_market(self):
            return {"answer": [_offer_listing(listing_price=2_623_496, bid_id="bid1", offer_price=2_700_000)]}

        def accept_sale_offer(self, bid_id, player_id):
            accept_calls.append((bid_id, player_id))
            return {"code": "api.general.ok"}

        def get_roster(self):
            return {"answer": []}  # corta pronto tras procesar ofertas -- no es lo que este test comprueba

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert accept_calls == [("bid1", "25")]
    assert "1 oferta(s) recibida(s) ACEPTADA(S)" in captured[0]
    assert "2700000" in captured[0]

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM received_sale_offers WHERE futmondo_bid_id = 'bid1'").fetchone()
    assert row["accepted"] == 1
    assert row["listing_price"] == 2_623_496
    assert row["offer_price"] == 2_700_000
    assert row["bidder_name"] == ""


def test_run_sales_ignores_offer_from_another_manager_even_if_above_asking_price(tmp_db):
    """A petición del usuario (2026-08-28): una oferta de OTRO MANAGER nunca se acepta, por alta que sea."""
    class FakeClient(_BaseFakeClient):
        def get_my_players_in_market(self):
            return {
                "answer": [
                    _offer_listing(
                        listing_price=1_000_000,
                        bid_id="bid_manager",
                        offer_price=5_000_000,
                        bidder_name="Javier Mediavilla",
                        bidder_slug="javier-mediavilla",
                    )
                ]
            }

        def accept_sale_offer(self, bid_id, player_id):
            raise AssertionError("no debería aceptar una oferta de otro manager")

        def get_roster(self):
            return {"answer": []}

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "ACEPTADA" not in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT accepted, bidder_name FROM received_sale_offers WHERE futmondo_bid_id = 'bid_manager'").fetchone()
    assert row["accepted"] == 0
    assert row["bidder_name"] == "Javier Mediavilla"  # se registra para auditoría igualmente


def test_run_sales_does_not_accept_offer_at_or_below_asking_price(tmp_db):
    """Una oferta que SOLO IGUALA el precio pedido (no lo supera) no se acepta -- ver docstring de _process_received_offers."""
    class FakeClient(_BaseFakeClient):
        def get_my_players_in_market(self):
            return {"answer": [_offer_listing(listing_price=1_000_000, bid_id="bid2", offer_price=1_000_000)]}

        def accept_sale_offer(self, bid_id, player_id):
            raise AssertionError("no debería intentar aceptar una oferta que no supera el precio pedido")

        def get_roster(self):
            return {"answer": []}

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()

    assert "ACEPTADA" not in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT accepted FROM received_sale_offers WHERE futmondo_bid_id = 'bid2'").fetchone()
    assert row["accepted"] == 0


def test_run_sales_ignores_clause_listings_when_processing_offers(tmp_db):
    """isClause=true queda fuera de accept_sale_offer() (ver su docstring) -- ni se registra ni se intenta aceptar."""
    class FakeClient(_BaseFakeClient):
        def get_my_players_in_market(self):
            return {"answer": [_offer_listing(listing_price=1_000_000, bid_id="bid3", offer_price=5_000_000, is_clause=True)]}

        def accept_sale_offer(self, bid_id, player_id):
            raise AssertionError("no debería tocar un listado de cláusula")

        def get_roster(self):
            return {"answer": []}

    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify"):
        run_sales.run()

    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM received_sale_offers WHERE futmondo_bid_id = 'bid3'").fetchone()
    assert row["n"] == 0


def test_run_sales_offer_acceptance_failure_is_audited_without_crashing(tmp_db):
    class FakeClient(_BaseFakeClient):
        def get_my_players_in_market(self):
            return {"answer": [_offer_listing(listing_price=1_000_000, bid_id="bid4", offer_price=1_200_000)]}

        def accept_sale_offer(self, bid_id, player_id):
            raise FutmondoOfferError("ya no existe esa oferta")

        def get_roster(self):
            return {"answer": []}

    captured = []
    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify", side_effect=lambda m: captured.append(m)):
        run_sales.run()  # no debe lanzar

    assert "fallida(s) al aceptar" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT accepted FROM received_sale_offers WHERE futmondo_bid_id = 'bid4'").fetchone()
    assert row["accepted"] == 0  # se registró, pero NO se marca aceptada -- la llamada real falló


def test_run_sales_does_not_duplicate_offer_seen_across_two_runs(tmp_db):
    """Una oferta todavía abierta (no aceptada, por debajo del precio pedido) vista en dos pasadas solo genera una fila."""
    class FakeClient(_BaseFakeClient):
        def get_my_players_in_market(self):
            return {"answer": [_offer_listing(listing_price=1_000_000, bid_id="bid5", offer_price=900_000)]}

        def get_roster(self):
            return {"answer": []}

    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify"):
        run_sales.run()
        run_sales.run()

    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM received_sale_offers WHERE futmondo_bid_id = 'bid5'").fetchone()
    assert row["n"] == 1


def test_run_sales_does_not_call_get_player_summary_when_premium_flag_is_off(tmp_db, monkeypatch):
    """Por defecto (ENABLE_SELLING_REVALUATION_PREMIUM=False) el comportamiento no cambia: ni se llama a get_player_summary."""
    monkeypatch.setattr(config, "ENABLE_SELLING_REVALUATION_PREMIUM", False)
    roster = [dict(p) for p in ROSTER_442_BASE]
    roster.append({"id": 25, "role": "centrocampista", "status": "", "value": 900_000})
    _mark_won(25, 700_000)

    class FakeClient(_BaseFakeClient):
        def get_roster(self):
            return {"answer": roster}

        def get_information(self):
            return {"answer": {"budget": 0}}

        def get_player_summary(self, player_id):
            raise AssertionError("no debería llamarse con el flag apagado")

        def list_for_sale(self, player_id, price):
            return {"code": "api.general.ok"}

    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify"):
        run_sales.run()  # no debe lanzar

    with get_connection() as conn:
        row = conn.execute("SELECT asking_price FROM sales").fetchone()
    assert row["asking_price"] == 900_000  # VM tal cual, sin prima


def test_run_sales_applies_revaluation_premium_when_flag_is_on(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_SELLING_REVALUATION_PREMIUM", True)
    monkeypatch.setattr(
        config,
        "SELLING_REVALUATION_LOOKBACK_DAYS",
        30,  # las fechas fijas de abajo (2026-08-*) deben caer dentro de la ventana, sea cual sea "hoy" al correr el test
    )
    roster = [dict(p) for p in ROSTER_442_BASE]
    roster.append({"id": 25, "role": "centrocampista", "status": "", "value": 900_000})
    _mark_won(25, 700_000)

    rising_prices = [
        {"date": "2026-08-18T00:00:00+00:00", "price": 700_000},
        {"date": "2026-08-19T00:00:00+00:00", "price": 800_000},
        {"date": "2026-08-20T00:00:00+00:00", "price": 900_000},  # +28.6% sostenido en la ventana
    ]

    class FakeClient(_BaseFakeClient):
        def get_roster(self):
            return {"answer": roster}

        def get_information(self):
            return {"answer": {"budget": 0}}

        def get_player_summary(self, player_id):
            assert player_id == "25"
            return {"answer": {"data": {"id": player_id}, "prices": rising_prices}}

        def list_for_sale(self, player_id, price):
            return {"code": "api.general.ok"}

    with patch("jobs.run_sales.FutmondoClient", FakeClient), patch("jobs.run_sales.notify"):
        run_sales.run()

    with get_connection() as conn:
        row = conn.execute("SELECT asking_price, reason FROM sales").fetchone()
    assert row["asking_price"] > 900_000  # prima aplicada por encima del VM
    assert "revalorización sostenida" in row["reason"]


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
