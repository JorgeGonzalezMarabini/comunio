"""
Fixtures compartidas por toda la batería de tests.

`no_real_telegram` es autouse=True A PROPÓSITO: durante el desarrollo, un
script de prueba cargó sin querer las credenciales reales de `.env` y
mandó una notificación de prueba real al Telegram del usuario (ver
README/historial de commits). Esta fixture es una red de seguridad
adicional a nivel de config, independiente de que cada test module
intercepte `notify()` explícitamente o no — así ningún test puede tocar
la Telegram API real por descuido, aunque se le olvide a alguien mockear
`notify` en un test nuevo.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", None)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", None)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """BD SQLite temporal y aislada por test, con el esquema ya creado."""
    from db.models import init_db

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DATABASE_PATH", str(db_path))
    init_db()
    return str(db_path)


@pytest.fixture
def squad_player_factory():
    """
    Fábrica de items realistas de ComunioClient.get_squad()["items"]:
    posición en inglés (keeper/defender/midfielder/striker), precio en
    "quotedprice"/"recommendedprice" (minúsculas), `purchaseInfo` real
    (None si el jugador no se compró vía puja) — formas confirmadas por
    captura real, ver clients/comunio_client.py.
    """

    def make(
        id,
        name="Jugador",
        position="defender",
        status="ACTIVE",
        status_info="",
        quotedprice=1_000_000,
        recommendedprice=None,
        points=10,
        last_points=2,
        average_points=2.0,
        on_market=False,
        purchase_info=None,
        club="Equipo",
        linedup=False,
    ):
        return {
            "id": id,
            "name": name,
            "club": {"name": club},
            "position": position,
            "status": status,
            "statusInfo": status_info,
            "points": points,
            "lastPoints": last_points,
            "averagePoints": average_points,
            "quotedprice": quotedprice,
            "recommendedprice": recommendedprice if recommendedprice is not None else quotedprice,
            "onMarket": on_market,
            "purchaseInfo": purchase_info,
            "linedup": linedup,
        }

    return make


@pytest.fixture
def market_player_factory():
    """
    Fábrica de `_embedded.player` de ComunioClient.get_market()["items"]:
    camelCase distinto de squad ("quotedPrice"/"recommendedPrice") y SIN
    campo "onMarket" (confirmado real — ver bug de on_market en
    jobs/sync_data.py, corregido a raíz de esto).
    """

    def make(
        id,
        name="Jugador Mercado",
        position="striker",
        status="ACTIVE",
        status_info="",
        quoted_price=1_000_000,
        recommended_price=None,
        points=10,
        club="Equipo",
    ):
        return {
            "id": id,
            "name": name,
            "club": {"name": club},
            "position": position,
            "status": status,
            "statusInfo": status_info,
            "points": points,
            "quotedPrice": quoted_price,
            "recommendedPrice": recommended_price if recommended_price is not None else quoted_price,
            "trend": 0,
            "purchasePrice": None,
            "onWatchlist": "false",
        }

    return make


class FakeResponse:
    """Réplica mínima de requests.Response para no hacer HTTP real en los tests."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.content = b"1"

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json


class FakeSession:
    """
    Sustituye a requests.Session dentro de un ComunioClient de prueba.
    `response_fn(method, url, payload) -> FakeResponse` decide qué
    devolver; `calls` guarda cada llamada hecha para poder comprobar en el
    test qué verbo/URL/body se usó de verdad.
    """

    def __init__(self, response_fn=None, default_response=None):
        self.response_fn = response_fn
        self.default_response = default_response or FakeResponse(200, {"status": "OK"})
        self.calls = []

    def _handle(self, method, url, payload=None, params=None):
        self.calls.append({"method": method, "url": url, "json": payload, "params": params})
        if self.response_fn:
            return self.response_fn(method, url, payload)
        return self.default_response

    def get(self, url, headers=None, timeout=None, params=None):
        return self._handle("GET", url, params=params)

    def post(self, url, json=None, headers=None, timeout=None):
        return self._handle("POST", url, payload=json)

    def request(self, method, url, headers=None, json=None, timeout=None):
        return self._handle(method, url, payload=json)


@pytest.fixture
def fake_client():
    """
    ComunioClient ya "logueado" (token/user_id de mentira) con una
    FakeSession en vez de requests.Session real — para probar
    place_bid/withdraw_bid/set_lineup/list_for_sale/etc. sin red.
    Cada test puede sustituir `client.session.response_fn` para simular
    la respuesta que necesite.
    """
    from clients.comunio_client import ComunioClient

    client = ComunioClient(email="x", password="y", community_id="5243734")
    client.token = "fake-token"
    client.user_id = "21161679"
    client.session = FakeSession()
    return client
