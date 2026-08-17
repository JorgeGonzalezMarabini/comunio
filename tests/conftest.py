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

`no_real_futmondo_network` es la misma idea aplicada a Futmondo, añadida
al descubrir el riesgo real (2026-08-17): con `.env` local ya relleno con
credenciales reales (necesarias para depurar el bug de alineación de ese
mismo día), un `FakeClient(FutmondoClient)` de test que se olvide de
sobreescribir un método concreto (p.ej. `get_lineup`) hereda la
implementación real -- que usaría esas credenciales reales de verdad
contra `api.futmondo.com` en vez de fallar limpiamente. Forzar
`FUTMONDO_TOKEN`/`FUTMONDO_USER_ID` a `None` aquí hace que cualquier
llamada real no mockeada lance `FutmondoAuthError` de inmediato (antes de
tocar la red), delatando el hueco en vez de disparar una escritura real
por descuido.
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


@pytest.fixture(autouse=True)
def no_real_futmondo_network(monkeypatch):
    monkeypatch.setattr(config, "FUTMONDO_TOKEN", None)
    monkeypatch.setattr(config, "FUTMONDO_USER_ID", None)
    monkeypatch.setattr(config, "FUTMONDO_CHAMPIONSHIP_ID", None)
    monkeypatch.setattr(config, "FUTMONDO_USERTEAM_ID", None)


@pytest.fixture(autouse=True)
def no_real_api_football_network(monkeypatch):
    """
    Misma idea que no_real_futmondo_network pero para
    clients/football_lineups_client.py (API-Football): sin key y con el
    flag apagado por defecto en todo test, aunque el `.env` local tenga una
    key real rellena -- cualquier test que quiera ejercitar esta rama debe
    reactivarlo explícitamente (y mockear la red igualmente).
    """
    monkeypatch.setattr(config, "API_FOOTBALL_KEY", None)
    monkeypatch.setattr(config, "ENABLE_REAL_LINEUP_CHECK", False)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """BD SQLite temporal y aislada por test, con el esquema ya creado."""
    from db.models import init_db

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DATABASE_PATH", str(db_path))
    init_db()
    return str(db_path)


@pytest.fixture
def roster_player_factory():
    """
    Fábrica de items realistas de FutmondoClient.get_roster()["answer"]:
    rol en ESPAÑOL (portero/defensa/centrocampista/delantero), precio en
    "value", precio de referencia en "buyPrice", y el flag propio de roster
    "market" (si TÚ lo has puesto en venta) -- formas confirmadas por
    captura real, ver clients/futmondo_client.py.
    """

    def make(
        id,
        name="Jugador",
        role="defensa",
        status="",
        value=1_000_000,
        buy_price=0,
        points=10,
        last_points=2,
        average_points=2.0,
        on_market=False,
        team="Equipo",
        slug=None,
    ):
        return {
            "id": id,
            "name": name,
            "slug": slug or f"jugador-{id}",
            "role": role,
            "role2": "",
            "photo": "",
            "points": points,
            "value": value,
            "team": team,
            "logo": "",
            "status": status,
            "rating": 0,
            "average": {
                "average": average_points,
                "homeAverage": average_points,
                "awayAverage": average_points,
                "averageLastFive": average_points,
                "matches": 1,
                "fitness": [last_points] if last_points is not None else [],
            },
            "change": 0,
            "computer": False,
            "buyPrice": buy_price,
            "market": on_market,
            "direct": False,
            "teamId": team,
        }

    return make


@pytest.fixture
def market_player_factory():
    """
    Fábrica de items realistas de FutmondoClient.get_market()["answer"]:
    mismo shape base que roster (rol en español, "value") pero SIN
    "buyPrice"/"market" (exclusivos de roster) y con los campos exclusivos
    de mercado: "price", "numberOfBids", "creationDate"/"expirationDate" --
    formas confirmadas por captura real, ver clients/futmondo_client.py.
    """

    def make(
        id,
        name="Jugador Mercado",
        role="delantero",
        status="",
        value=1_000_000,
        price=None,
        points=10,
        team="Equipo",
        slug=None,
        is_clause=False,
        number_of_bids="-",
    ):
        return {
            "id": id,
            "name": name,
            "slug": slug or f"jugador-{id}",
            "role": role,
            "role2": "",
            "photo": "",
            "points": points,
            "value": value,
            "team": team,
            "logo": "",
            "status": status,
            "rating": 0,
            "average": {
                "average": 0,
                "homeAverage": 0,
                "awayAverage": 0,
                "averageLastFive": 0,
                "matches": 0,
                "fitness": [],
            },
            "change": 0,
            "computer": False,
            "creationDate": "2026-08-15T00:00:00+00:00",
            "expirationDate": "2026-08-19T00:00:00+00:00",
            "price": price if price is not None else value,
            "isClause": is_clause,
            "type": "market",
            "numberOfBids": number_of_bids,
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
    Sustituye a requests.Session dentro de un FutmondoClient de prueba.
    `response_fn(method, url, payload) -> FakeResponse` decide qué
    devolver; `calls` guarda cada llamada hecha para poder comprobar en el
    test qué verbo/URL/body se usó de verdad.
    """

    def __init__(self, response_fn=None, default_response=None):
        self.response_fn = response_fn
        self.default_response = default_response or FakeResponse(200, {"answer": {"code": "api.general.ok"}})
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
    FutmondoClient con token/user_id/championship_id/userteam_id de mentira
    (pasados directo al constructor, no hay login()) y una FakeSession en
    vez de requests.Session real -- para probar
    place_bid/list_for_sale/cancel_sale/pay_clause/change_lineup/etc. sin
    red. Cada test puede sustituir `client.session.response_fn` para
    simular la respuesta que necesite.
    """
    from clients.futmondo_client import FutmondoClient

    client = FutmondoClient(
        token="fake-token",
        user_id="fake-user-id",
        championship_id="6a82c086b4e159a76ab3b3d8",
        userteam_id="6a82c08704b95c71b37179a4",
    )
    client.session = FakeSession()
    return client
