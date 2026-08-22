"""
Tests del cliente de Futmondo contra una FakeSession (ver conftest.py) --
nunca contra la red real. Cada escenario replica exactamente una forma de
respuesta capturada de verdad durante la sesión (ver docstrings de
clients/futmondo_client.py para la fuente de cada una).
"""
import pytest
import requests

import config
from clients.futmondo_client import (
    FUTMONDO_POSITION_MAP,
    FutmondoAuthError,
    FutmondoClient,
    FutmondoOfferError,
    is_confirmed_injured_status,
    is_doubtful_status,
    is_injury_status,
    real_pending_bid_amount,
    total_pending_bid_amount,
)
from tests.conftest import FakeResponse, FakeSession


def test_body_includes_header_and_query_with_ids(fake_client):
    """Body real confirmado: {"header": {"token", "userid"}, "query": {"championshipId", "userteamId", ...}}."""
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(200, {"answer": []})
    fake_client.get_roster()

    call = fake_client.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/1/userteam/roster")
    assert call["json"] == {
        "header": {"token": "fake-token", "userid": "fake-user-id"},
        "query": {
            "championshipId": "6a82c086b4e159a76ab3b3d8",
            "userteamId": "6a82c08704b95c71b37179a4",
        },
    }


def test_constructor_takes_credentials_directly_no_login():
    """No hay login() ni endpoint de login conocido -- las credenciales se pasan al constructor."""
    client = FutmondoClient(
        token="t", user_id="u", championship_id="c", userteam_id="ut"
    )
    assert client.token == "t"
    assert client.user_id == "u"
    assert client.championship_id == "c"
    assert client.userteam_id == "ut"
    assert not hasattr(client, "login")


def test_missing_token_or_user_id_raises_auth_error(monkeypatch):
    import config

    monkeypatch.setattr(config, "FUTMONDO_TOKEN", None)
    monkeypatch.setattr(config, "FUTMONDO_USER_ID", None)
    client = FutmondoClient(token=None, user_id=None, championship_id="c", userteam_id="ut")
    client.session = FakeSession()
    with pytest.raises(FutmondoAuthError):
        client.get_roster()


def test_get_roster_returns_answer_list(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": [{"id": "1", "name": "Jugador", "buyPrice": 500_000, "market": False}]}
    )
    result = fake_client.get_roster()
    assert result["answer"][0]["id"] == "1"


def test_get_information_returns_budget(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"budget": 20_000_000, "teamValue": 50_000_000}}
    )
    result = fake_client.get_information()
    assert result["answer"]["budget"] == 20_000_000


def test_get_lineup_returns_strategy_and_players(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"strategy": "4-4-2", "players": [{"id": "1", "position": 10}]}}
    )
    result = fake_client.get_lineup()
    assert result["answer"]["strategy"] == "4-4-2"


def test_get_market_returns_answer_list(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(200, {"answer": [{"id": "2"}]})
    result = fake_client.get_market()
    assert result["answer"] == [{"id": "2"}]
    call = fake_client.session.calls[0]
    assert call["url"].endswith("/1/market/players")


def test_get_my_players_in_market(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(200, {"answer": []})
    result = fake_client.get_my_players_in_market()
    assert result["answer"] == []
    call = fake_client.session.calls[0]
    assert call["url"].endswith("/1/market/myplayers")


def test_get_my_players_in_market_returns_real_listed_item_shape(fake_client):
    """
    Forma real de un item listado, confirmada en vivo (TODO.md #6,
    2026-08-18: Sergi Canós puesto en venta y releído antes de cancelar).
    """
    listed_item = {
        "id": "5738fcf88179bfa8526e19ce",
        "name": "Sergi Canós",
        "slug": "28094954",
        "role": "centrocampista",
        "role2": "",
        "photo": "28094954.png",
        "points": 0,
        "value": 1_000_000,
        "team": "Valencia",
        "logo": "valencia.png",
        "status": "injured2",
        "expirationDate": "2026-08-20T10:32:15.001Z",
        "price": 100_000_000,
        "buyPrice": 0,
        "isClause": False,
        "bids": [],
        "change": 0,
        "average": {"average": 0, "homeAverage": 0, "awayAverage": 0, "averageLastFive": 0, "matches": 0, "fitness": []},
    }
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(200, {"answer": [listed_item]})
    result = fake_client.get_my_players_in_market()
    assert result["answer"] == [listed_item]


def test_get_player_summary_includes_player_id_in_query(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"data": {"id": "5"}, "prices": []}}
    )
    fake_client.get_player_summary("5")
    call = fake_client.session.calls[0]
    assert call["url"].endswith("/1/player/summary")
    assert call["json"]["query"]["playerId"] == "5"


def test_place_bid_body_and_success(fake_client):
    """Body real confirmado: query trae player_slug/player_id/price/isClause."""
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"code": "api.general.ok"}}
    )
    answer = fake_client.place_bid("2381", "jugador-2381", 190_000)

    assert answer["code"] == "api.general.ok"
    call = fake_client.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/1/market/bid")
    assert call["json"]["query"] == {
        "championshipId": "6a82c086b4e159a76ab3b3d8",
        "userteamId": "6a82c08704b95c71b37179a4",
        "player_slug": "jugador-2381",
        "player_id": "2381",
        "price": 190_000,
        "isClause": False,
    }


def test_place_bid_raises_on_business_rejection_despite_http_200():
    """
    Regresión de un hallazgo real (patrón heredado de Comunio): la API
    devuelve HTTP 200 incluso si la operación se rechaza a nivel de
    negocio -- hay que detectarlo mirando `answer.code`, no solo el código
    HTTP.
    """
    client = FutmondoClient(token="fake", user_id="fake", championship_id="c", userteam_id="ut")
    client.session = FakeSession(
        response_fn=lambda method, url, payload: FakeResponse(
            200, {"answer": {"code": "api.market.max_number_players_in_roster"}}
        )
    )
    with pytest.raises(FutmondoOfferError, match="max_number_players_in_roster"):
        client.place_bid("9999", "jugador-9999", 100)


def test_list_for_sale_body_and_not_ok_error(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"code": "api.general.ok"}}
    )
    fake_client.list_for_sale("2381", 180_000)
    call = fake_client.session.calls[0]
    assert call["url"].endswith("/1/market/putonmarket")
    assert call["json"]["query"] == {
        "championshipId": "6a82c086b4e159a76ab3b3d8",
        "userteamId": "6a82c08704b95c71b37179a4",
        "price": 180_000,
        "player_id": "2381",
        "isClause": None,
        "mode": None,
        "toLoan": None,
    }

    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"code": "api.market.some_error"}}
    )
    with pytest.raises(FutmondoOfferError):
        fake_client.list_for_sale("2381", 180_000)


def test_cancel_sale_body(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"code": "api.general.ok"}}
    )
    fake_client.cancel_sale("2381")
    call = fake_client.session.calls[0]
    assert call["url"].endswith("/1/market/cancelsell")
    assert call["json"]["query"]["player_id"] == "2381"


def test_cancel_bid_body(fake_client):
    """Body real confirmado (2026-08-18, TODO.md #13): query trae solo "bid"
    (el id de la PUJA, no el player_id -- a diferencia del resto de
    escrituras de compra/venta)."""
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"code": "api.general.ok"}}
    )
    answer = fake_client.cancel_bid("6a82c21df996a839bdb2abd0")

    assert answer["code"] == "api.general.ok"
    call = fake_client.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/1/market/cancelbid")
    assert call["json"]["query"] == {
        "championshipId": "6a82c086b4e159a76ab3b3d8",
        "userteamId": "6a82c08704b95c71b37179a4",
        "bid": "6a82c21df996a839bdb2abd0",
    }


def test_cancel_bid_raises_on_business_rejection_despite_http_200():
    """
    Código de ejemplo genérico, no confirmado -- no se ha observado en
    captura real qué código devuelve Futmondo al cancelar una puja que ya
    no existe (ver TODO.md #13, "sin confirmar todavía"). Solo confirma que
    `cancel_bid()` sigue el mismo patrón de detección que el resto de
    escrituras (HTTP 200 no implica `answer.code == "api.general.ok"`).
    """
    client = FutmondoClient(token="fake", user_id="fake", championship_id="c", userteam_id="ut")
    client.session = FakeSession(
        response_fn=lambda method, url, payload: FakeResponse(200, {"answer": {"code": "api.market.some_error"}})
    )
    with pytest.raises(FutmondoOfferError):
        client.cancel_bid("nonexistent-bid-id")


def test_pay_clause_body(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"code": "api.general.ok"}}
    )
    fake_client.pay_clause("2381", "jugador-2381", 14_000_000)
    call = fake_client.session.calls[0]
    assert call["url"].endswith("/1/market/rosterclause")
    assert call["json"]["query"] == {
        "championshipId": "6a82c086b4e159a76ab3b3d8",
        "userteamId": "6a82c08704b95c71b37179a4",
        "player_id": "2381",
        "player_slug": "jugador-2381",
        "price": 14_000_000,
    }


def test_change_lineup_sends_one_http_call_per_change(fake_client):
    """
    Regresión del bug real de producción (2026-08-17): mandar los 11
    cambios de golpe en una sola llamada hacía que Futmondo solo aplicara
    el primero, sin ningún error que lo delatara -- change_lineup() debe
    mandar una llamada POR CADA jugador, nunca varios en el mismo body.
    """
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"answer": {"code": "api.general.ok", "budget": 20_000_000, "rc": "-1"}}
    )
    changes = [
        {"cpt": False, "to": "1001", "position": 0, "isBench": False, "multiposition": False},
        {"cpt": False, "to": "1002", "position": 1, "isBench": False, "multiposition": False},
    ]
    results = fake_client.change_lineup(changes)

    assert [r["ok"] for r in results] == [True, True]
    assert len(fake_client.session.calls) == 2  # una llamada por change, no una para toda la lista
    for call, change in zip(fake_client.session.calls, changes):
        assert call["url"].endswith("/2/userteam/changeplayer")
        assert call["json"]["query"]["changes"] == [change]  # cada llamada lleva UN solo change


def test_change_lineup_reports_per_player_failure_without_stopping(fake_client):
    """
    Un jugador rechazado no debe impedir intentar colocar al resto (mismo
    criterio que jobs/run_market.py con las pujas) -- change_lineup() nunca
    lanza en el primer fallo, lo audita por jugador.
    """
    responses = iter([
        FakeResponse(200, {"answer": {"code": "api.market.some_rejection"}}),
        FakeResponse(200, {"answer": {"code": "api.general.ok"}}),
    ])
    fake_client.session.response_fn = lambda method, url, payload: next(responses)
    changes = [
        {"cpt": False, "to": "1001", "position": 0, "isBench": False, "multiposition": False},
        {"cpt": False, "to": "1002", "position": 1, "isBench": False, "multiposition": False},
    ]
    results = fake_client.change_lineup(changes)

    assert results[0]["ok"] is False
    assert results[1]["ok"] is True
    assert len(fake_client.session.calls) == 2  # sí intentó el segundo pese al fallo del primero


def test_futmondo_position_map_translates_spanish_roles_to_short_codes():
    assert FUTMONDO_POSITION_MAP == {
        "portero": "POR",
        "defensa": "DEF",
        "centrocampista": "MED",
        "delantero": "DEL",
    }


@pytest.mark.parametrize(
    "status,expected",
    [
        (None, False),
        ("", False),
        ("ok", False),
        ("injured", True),
        ("INJURED", True),
        ("lesion", True),
        ("lesión", True),
        # Valores confirmados con datos reales de producción 2026-08-18
        # (ver TODO.md #5): "doubt" (duda) e "injuredN" con tier numérico.
        ("doubt", True),
        ("DOUBT", True),
        ("injured2", True),
    ],
)
def test_is_injury_status(status, expected):
    assert is_injury_status(status) is expected


@pytest.mark.parametrize(
    "status,expected",
    [
        (None, False),
        ("", False),
        ("ok", False),
        ("doubt", True),
        ("DOUBT", True),
        # "doubt" es duda, NO lesión confirmada -- is_doubtful_status()
        # debe distinguirlo de "injuredN"/"lesion"/"lesión".
        ("injured", False),
        ("injured2", False),
        ("lesion", False),
        ("lesión", False),
    ],
)
def test_is_doubtful_status_only_matches_doubt(status, expected):
    assert is_doubtful_status(status) is expected


@pytest.mark.parametrize(
    "status,expected",
    [
        (None, False),
        ("", False),
        ("ok", False),
        ("doubt", False),
        ("DOUBT", False),
        ("injured", True),
        ("INJURED", True),
        ("injured2", True),
        ("lesion", True),
        ("lesión", True),
    ],
)
def test_is_confirmed_injured_status_excludes_doubt(status, expected):
    assert is_confirmed_injured_status(status) is expected


@pytest.mark.parametrize("status", ["doubt", "injured", "injured2", "lesion", "lesión", None, "", "ok"])
def test_is_injury_status_is_the_union_of_doubtful_and_confirmed_injured(status):
    """
    Regresión: is_injury_status() debe seguir siendo exactamente el OR de
    is_doubtful_status()/is_confirmed_injured_status() para no romper a
    engine/squad_risk.py, engine/lineup_optimizer.py y
    engine/selling_strategy.py, que solo distinguen sano/no-sano.
    """
    assert is_injury_status(status) == (is_doubtful_status(status) or is_confirmed_injured_status(status))


def test_total_pending_bid_amount_sums_amounts():
    bids_placed_locally = [
        {"id": 1, "player_id": "1", "amount": 500_000},
        {"id": 2, "player_id": "2", "amount": 300_000},
    ]
    assert total_pending_bid_amount(bids_placed_locally) == 800_000


def test_total_pending_bid_amount_empty_list():
    assert total_pending_bid_amount([]) == 0


def test_real_pending_bid_amount_sums_only_items_with_bid_field():
    """
    Resuelve TODO.md #3: forma real confirmada en vivo (2026-08-18) del
    campo "bid" de get_market() -- {"id": ..., "price": ...} solo en los
    items donde tenemos puja propia pendiente, ausente en el resto.
    """
    market_items = [
        {"id": "1", "name": "Con puja", "bid": {"id": "abc", "price": 1_044_999}},
        {"id": "2", "name": "Sin puja"},
        {"id": "3", "name": "Otra con puja", "bid": {"id": "def", "price": 7_265_939}},
    ]
    assert real_pending_bid_amount(market_items) == 8_310_938


def test_real_pending_bid_amount_ignores_stale_amount_after_a_silent_no_op_rebid():
    """
    Regresión del hallazgo real (2026-08-18): pujar dos veces sobre el
    mismo jugador no actualiza "bid.price" en el mercado (Futmondo ignora
    la segunda llamada en silencio) -- esta función debe reflejar el
    importe REAL server-side (el de la primera puja), no lo que la BD
    local pudiera creer tras una "escalada" que en realidad no surtió
    efecto.
    """
    market_items = [{"id": "1", "bid": {"id": "abc", "price": 15_000_000}}]  # primera puja, la que de verdad cuenta
    assert real_pending_bid_amount(market_items) == 15_000_000


def test_real_pending_bid_amount_empty_market():
    assert real_pending_bid_amount([]) == 0


# --- Reintentos ante fallo de conexión transitorio (ver clients/futmondo_client.py:_post) ---
# Regresión del fallo real en producción (GitHub Actions, 2026-08-17):
# jobs/run_market.py cayó entero por un RemoteDisconnected sin ningún
# reintento al llamar a /1/userteam/information.

def test_read_method_retries_on_connection_error_then_succeeds(fake_client, monkeypatch):
    monkeypatch.setattr(config, "FUTMONDO_READ_MAX_RETRIES", 2)
    monkeypatch.setattr("clients.futmondo_client.time.sleep", lambda seconds: None)

    attempts = {"n": 0}

    def flaky(method, url, payload):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise requests.exceptions.ConnectionError("Remote end closed connection without response")
        return FakeResponse(200, {"answer": {"budget": 20_000_000}})

    fake_client.session.response_fn = flaky
    result = fake_client.get_information()

    assert result["answer"]["budget"] == 20_000_000
    assert attempts["n"] == 3  # 2 fallos + 1 éxito, dentro del margen (max_retries=2)


def test_read_method_exhausts_retries_and_raises(fake_client, monkeypatch):
    monkeypatch.setattr(config, "FUTMONDO_READ_MAX_RETRIES", 2)
    monkeypatch.setattr("clients.futmondo_client.time.sleep", lambda seconds: None)

    attempts = {"n": 0}

    def always_fails(method, url, payload):
        attempts["n"] += 1
        raise requests.exceptions.ConnectionError("Remote end closed connection without response")

    fake_client.session.response_fn = always_fails
    with pytest.raises(requests.exceptions.ConnectionError):
        fake_client.get_information()

    assert attempts["n"] == 3  # intento inicial + 2 reintentos, ninguno recuperó


def test_write_method_never_retries_on_connection_error(fake_client, monkeypatch):
    """
    Las escrituras (place_bid...) NO deben reintentar nunca, aunque
    FUTMONDO_READ_MAX_RETRIES esté activado -- un ConnectionError ahí puede
    pasar DESPUÉS de que Futmondo ya procesara la puja de verdad, y
    reintentar podría duplicarla (ver docstring de _post).
    """
    monkeypatch.setattr(config, "FUTMONDO_READ_MAX_RETRIES", 2)

    attempts = {"n": 0}

    def always_fails(method, url, payload):
        attempts["n"] += 1
        raise requests.exceptions.ConnectionError("boom")

    fake_client.session.response_fn = always_fails
    with pytest.raises(requests.exceptions.ConnectionError):
        fake_client.place_bid("1", "jugador-1", 100)

    assert attempts["n"] == 1  # ni un solo reintento


def test_http_error_with_real_response_is_never_retried(fake_client, monkeypatch):
    """Un 4xx/5xx CON respuesta real del servidor no debe reintentarse -- repetir no cambiaría el resultado."""
    monkeypatch.setattr(config, "FUTMONDO_READ_MAX_RETRIES", 2)
    monkeypatch.setattr("clients.futmondo_client.time.sleep", lambda seconds: None)

    attempts = {"n": 0}

    def server_error(method, url, payload):
        attempts["n"] += 1
        return FakeResponse(500, {})

    fake_client.session.response_fn = server_error
    with pytest.raises(requests.HTTPError):
        fake_client.get_information()

    assert attempts["n"] == 1
