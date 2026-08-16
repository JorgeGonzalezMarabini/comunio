"""
Tests del cliente de Comunio contra una FakeSession (ver conftest.py) --
nunca contra la red real. Cada escenario replica exactamente una forma de
respuesta capturada de verdad durante la sesión (ver README/docstrings de
clients/comunio_client.py para la fuente de cada una).
"""
import pytest

from clients.comunio_client import ComunioOfferError
from tests.conftest import FakeResponse


def test_place_bid_body_and_success(fake_client):
    """Body real confirmado: {"offers": [{"price", "tradableid", "type": "NEW"}]}."""
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200,
        {
            "status": "OK",
            "response": [{"offerid": 1314890726, "tradableid": 2381, "price": 190000, "type": "NEW", "status": "OK", "message": ""}],
            "opponentIds": "",
        },
    )
    offer = fake_client.place_bid(2381, 190000)

    assert offer["offerid"] == 1314890726
    call = fake_client.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/offers")
    assert call["json"] == {"offers": [{"price": 190000, "tradableid": 2381, "type": "NEW"}]}


def test_place_bid_raises_on_business_rejection_despite_http_200():
    """
    Regresión de un hallazgo real: la API devuelve HTTP 200 incluso si la
    oferta se rechaza a nivel de negocio (ej. "player is not on
    exchangemarket") -- place_bid() debe detectarlo mirando el status del
    item, no solo el código HTTP.
    """
    from clients.comunio_client import ComunioClient

    client = ComunioClient(email="x", password="y", community_id="5243734")
    client.token = "fake"
    client.user_id = "21161679"
    from tests.conftest import FakeSession

    client.session = FakeSession(
        response_fn=lambda method, url, payload: FakeResponse(
            200,
            {
                "status": "OK",
                "response": [{"offerid": 0, "tradableid": 9999, "price": 100, "type": "NEW", "status": "ERROR", "message": "player is not on exchangemarket"}],
                "opponentIds": "",
            },
        )
    )
    with pytest.raises(ComunioOfferError, match="not on exchangemarket"):
        client.place_bid(9999, 100)


def test_withdraw_bid_uses_put_with_empty_body_not_delete(fake_client):
    """Regresión: se asumía DELETE por convención REST; el verbo real confirmado es PUT con body vacío."""
    fake_client.withdraw_bid(1314890726)
    call = fake_client.session.calls[0]
    assert call["method"] == "PUT"
    assert call["url"].endswith("/offers/1314890726")
    assert call["json"] == {}


def test_set_lineup_body_shape_matches_real_capture(fake_client):
    """
    Body real confirmado interceptando la llamada del frontend: "lineup"
    va en la raíz (no anidado bajo "items" como se infirió al principio),
    incluye "userId" y "type": "default".
    """
    fake_client.set_lineup("442", {"1": "2403", "11": "2611"}, {"striker": "", "midfielder": "", "defender": "", "keeper": ""})
    call = fake_client.session.calls[0]
    assert call["method"] == "PUT"
    assert call["url"].endswith("/lineup")
    assert call["json"] == {
        "userId": 21161679,
        "tactic": "442",
        "lineup": {"1": "2403", "11": "2611"},
        "substitutes": {"striker": "", "midfielder": "", "defender": "", "keeper": ""},
        "type": "default",
    }


def test_list_for_sale_body_and_not_placed_error(fake_client):
    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"status": "OK", "notPlaced": [], "purchasePrices": {"2381": 199500}, "remaining": 38}
    )
    fake_client.list_for_sale(2381, 180000)
    call = fake_client.session.calls[0]
    assert call["url"].endswith("/exchangemarket/addplayer")
    assert call["json"] == {"items": [{"tradableId": 2381, "price": 180000}]}

    fake_client.session.response_fn = lambda method, url, payload: FakeResponse(
        200, {"status": "OK", "notPlaced": [2381], "purchasePrices": {}, "remaining": 38}
    )
    with pytest.raises(ComunioOfferError):
        fake_client.list_for_sale(2381, 180000)


def test_delist_from_sale_body(fake_client):
    fake_client.delist_from_sale(2381)
    call = fake_client.session.calls[0]
    assert call["url"].endswith("/exchangemarket/removeplayer")
    assert call["json"] == {"tradableIds": [2381]}


def test_total_pending_purchase_amount_filters_pending_purchases_only():
    from clients.comunio_client import total_pending_purchase_amount

    offers_response = {
        "credit": 20_000_000,
        "items": [
            {"id": 1, "type": "PURCHASE", "price": 8_000_000, "state": "PENDING"},
            {"id": 2, "type": "PURCHASE", "price": 3_000_000, "state": "PENDING"},
            {"id": 3, "type": "PURCHASE", "price": 500_000, "state": "DECLINED"},  # ya resuelta, no cuenta
        ],
    }
    assert total_pending_purchase_amount(offers_response) == 11_000_000


def test_total_pending_purchase_amount_empty_offers():
    from clients.comunio_client import total_pending_purchase_amount

    assert total_pending_purchase_amount({"items": []}) == 0
