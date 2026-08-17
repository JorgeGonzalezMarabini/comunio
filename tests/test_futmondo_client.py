"""
Tests del cliente de Futmondo contra una FakeSession (ver conftest.py) --
nunca contra la red real. Cada escenario replica exactamente una forma de
respuesta capturada de verdad durante la sesión (ver docstrings de
clients/futmondo_client.py para la fuente de cada una).
"""
import pytest

from clients.futmondo_client import (
    FUTMONDO_POSITION_MAP,
    FutmondoAuthError,
    FutmondoClient,
    FutmondoOfferError,
    is_injury_status,
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
    ],
)
def test_is_injury_status(status, expected):
    assert is_injury_status(status) is expected


def test_total_pending_bid_amount_sums_amounts():
    bids_placed_locally = [
        {"id": 1, "player_id": "1", "amount": 500_000},
        {"id": 2, "player_id": "2", "amount": 300_000},
    ]
    assert total_pending_bid_amount(bids_placed_locally) == 800_000


def test_total_pending_bid_amount_empty_list():
    assert total_pending_bid_amount([]) == 0
