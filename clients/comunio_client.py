"""
Cliente de Comunio (ingeniería inversa de la web, sin API pública oficial).

Endpoints, headers y forma de las respuestas capturados con Chrome DevTools
el 2026-08-15 sobre una liga de prueba real. Dos dominios distintos:
    - https://www.comunio.es   -> frontend Next.js (no se usa desde el bot)
    - https://api.comunio.es   -> API REST real (todo lo de abajo apunta aquí)

Auth: NO es cookie de sesión. El login devuelve access_token/refresh_token
que el frontend guarda en localStorage y reenvía en cada llamada autenticada
como header `Authorization: Bearer <token>` — **esquema confirmado con una
petición real autenticada** (antes solo era una suposición razonable).

La API es HAL/HATEOAS: casi todas las respuestas traen un `_links` con URLs
completas para las acciones disponibles sobre ese recurso (pujar, retirar
una oferta, añadir/quitar del mercado, etc.). Cuando existe un link real
capturado se documenta tal cual debajo; son más fiables que adivinar rutas.

Endpoints confirmados por captura real:
    POST /login
        body: {"username": ..., "password": ..., "tzoffset": ...}
        headers: Accept, Content-Type, X-Timezone (sin Authorization)
    GET  /login/state
    GET  /users/{userId}/squad                                   -> plantilla
    GET  /communities/{communityId}/standings?period=total&wpe=true  -> clasificación
    GET  /communities/{communityId}/members                      -> miembros de la liga
    GET  /communities/{communityId}/users/{userId}/exchangemarket -> mercado (compra/venta)
    GET  /communities/{communityId}/users/{userId}/offers?current -> ofertas/pujas activas
         (el query param "?current" es obligatorio, sin él da 500)
    GET  /communities/{communityId}/users/{userId}/offers/new/since/{unix_ts}
    GET  /communities/{communityId}/users/{userId}/lineup         -> alineación actual
    POST /communities/{communityId}/users/{userId}/logout  body: {"userId": ...}

Validado con una prueba real controlada (2026-08-15, liga de prueba, con
permiso explícito del usuario):
    - Pujar: el `_links["game:exchangemarket:placeoffers"]` de
      GET .../exchangemarket apunta literalmente a
      .../users/{userId}/offers (el mismo path que GET) -> confirma que es
      POST a ese path. El objeto resultante (releído después con GET
      .../offers?current) tiene forma:
        {id, type: "PURCHASE", tradable: {id, name, ...}, user: {id,...},
         tradingPartner: {id,...}, price, datecreated, datechanged, state,
         _links: {"game:offer:decline": ..., "game:offer:withdraw": ...}}
      -> el body de creación más plausible (nunca confirmado con un POST
      real, solo inferido de la forma del recurso resultante) es
      {"type": "PURCHASE", "tradable": {"id": player_id}, "price": amount}.
      "game:offer:withdraw" en la respuesta de una oferta propia es la URL
      para cancelarla (DELETE, sin confirmar el verbo tampoco).
    - Guardar alineación: reutiliza el mismo path que leerla
      (.../users/{userId}/lineup). OJO: `tactic` en la respuesta real es
      "442" (SIN guiones), no "4-4-2" como se asumió inicialmente. Los
      titulares van en `items.lineup`, un MAPA por nº de slot (no una lista
      plana) — ej. visto en captura real: slot "11" = portero, slot "7" =
      un defensa. La numeración completa de los 11 slots según posición NO
      está confirmada (solo se han visto 2 de 11 en la prueba real); hace
      falta una prueba con el once completo para terminar de mapearla.

Nombres de campo reales confirmados por fetch autenticado real (sin exponer
nunca el token — se leyó dentro del propio navegador y solo se devolvió la
forma/valores de los datos de negocio, no sensibles):
    squad item / market item.tradable: {
        id, name, club: {id, name, _links}, position: "keeper"|"defender"|
        "midfielder"|"striker" (palabra completa, NO "POR/DEF/MED/DEL"),
        status: "ACTIVE"|"WEAKENED"|"INJURED"|... (no se ha visto SUSPENDED
        en esta muestra, pero es de esperar para sanciones),
        statusInfo: texto libre ("Lesión muscular", "Fractura de peroné", ""),
        points, lastPoints, averagePoints, matchdayPoints,
        quotedprice (squad) / quotedPrice (market, distinta capitalización!),
        recommendedprice / recommendedPrice, purchasePrice, onMarket,
        linedup, watched, trend (solo en market), ...
    }

No confirmado todavía: nombres de campo exactos donde vienen community_id y
user_id en la respuesta de /login (de momento configurables a mano, ver
config.py). En esta liga de prueba: community_id=5243734, user_id=21161679.
"""
from __future__ import annotations

import requests

import config


class ComunioAuthError(Exception):
    """Login fallido o token inválido/expirado."""


# Comunio usa la palabra completa en inglés para la posición (confirmado por
# fetch real), NO la abreviatura "POR/DEF/MED/DEL" que se usa en el resto
# del proyecto (engine/, db/models.py). Traducir en el punto de ingesta
# (jobs/sync_data.py), no repartir este mapeo por todos lados.
COMUNIO_POSITION_MAP = {
    "keeper": "POR",
    "defender": "DEF",
    "midfielder": "MED",
    "striker": "DEL",
}

# Valores de "status" vistos en captura real. INJURED/WEAKENED traen
# statusInfo con el motivo en texto libre ("Lesión muscular", "Fractura de
# peroné"...). No se ha visto un valor de sanción/tarjetas en esta muestra
# (liga recién empezada) — es de esperar algo tipo "SUSPENDED", a confirmar.
COMUNIO_INJURY_STATUSES = {"INJURED", "WEAKENED"}


class ComunioClient:
    def __init__(self, email: str = None, password: str = None, community_id: str = None):
        self.email = email or config.COMUNIO_EMAIL
        self.password = password or config.COMUNIO_PASSWORD
        self.community_id = community_id or config.COMUNIO_COMMUNITY_ID
        self.base_url = config.COMUNIO_BASE_URL
        self.session = requests.Session()
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.user_id: str | None = None

    # --- auth ---

    def login(self) -> str:
        """
        Autentica contra Comunio y guarda access_token/refresh_token.

        TODO: confirmar contra la respuesta real:
          - nombres de campo (access_token/accessToken, etc.)
          - dónde viene el user_id (necesario para casi todos los demás
            endpoints, que van scoped a /users/{userId}/...). De momento
            hay que pasarlo a mano (COMUNIO_USER_ID) si no viene en la
            respuesta de login.
        """
        resp = self.session.post(
            f"{self.base_url}/login",
            json={
                "username": self.email,
                "password": self.password,
                "tzoffset": 0,  # TODO: confirmar formato real (minutos vs horas)
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Timezone": "Europe/Madrid",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise ComunioAuthError(f"Login falló ({resp.status_code}): {resp.text[:200]}")

        data = resp.json()
        # TODO: ajustar nombres de campo reales una vez confirmados.
        self.token = data.get("access_token") or data.get("accessToken")
        self.refresh_token = data.get("refresh_token") or data.get("refreshToken")
        self.user_id = data.get("user_id") or data.get("userId") or config.COMUNIO_USER_ID

        if not self.token:
            raise ComunioAuthError(f"Login OK pero no se encontró el token en la respuesta: {data!r}")

        return self.token

    def _auth_headers(self) -> dict:
        if not self.token:
            raise ComunioAuthError("No hay token; llama a login() antes.")
        # Esquema "Bearer <token>" confirmado con una petición real autenticada.
        return {
            config.COMUNIO_AUTH_HEADER: f"{config.COMUNIO_AUTH_SCHEME} {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Timezone": "Europe/Madrid",
        }

    def _get(self, path: str, **kwargs) -> dict:
        resp = self.session.get(f"{self.base_url}{path}", headers=self._auth_headers(), timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _write(self, method: str, path: str, json_body: dict) -> dict:
        resp = self.session.request(
            method, f"{self.base_url}{path}", headers=self._auth_headers(), json=json_body, timeout=15
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # --- lectura ---

    def get_standings(self) -> dict:
        """Clasificación de la liga."""
        return self._get(f"/communities/{self.community_id}/standings", params={"period": "total", "wpe": "true"})

    def get_squad(self) -> dict:
        """
        Plantilla actual del usuario. Respuesta real:
            {"items": [...jugadores...], "tactic": ..., "_links": {...}}
        Ver el docstring del módulo para la forma de cada item.
        """
        return self._get(f"/users/{self.user_id}/squad")

    def get_market(self) -> dict:
        """
        Jugadores disponibles en el mercado (compra). Respuesta real:
            {
              "credit": ..., (¿?, no visto en exchangemarket, sí en offers)
              "items": [{"date", "remaining", "watched",
                         "_embedded": {"player": {...}, "owner": {...}}}],
              "nextTransfersDateTime": ..., "dailyTransfersProcessed": ...,
              "_links": {"game:exchangemarket:placeoffers": ".../offers", ...}
            }
        """
        return self._get(f"/communities/{self.community_id}/users/{self.user_id}/exchangemarket")

    def get_offers(self) -> dict:
        """
        Ofertas/pujas activas propias. El query param "?current" es
        OBLIGATORIO (sin él la API responde 500 "An error occurred.").
        Respuesta real: {"credit": ..., "items": [...], "hasMore": ..., "_links": {...}}
        """
        return self._get(f"/communities/{self.community_id}/users/{self.user_id}/offers", params={"current": ""})

    def get_lineup(self) -> dict:
        """
        Alineación actualmente guardada. Respuesta real:
            {"tactic": "442", "items": {"lineup": {"<slot>": {...jugador...}}},
             "promotion": ..., "_links": {...}}
        `tactic` SIN guiones (ver docstring del módulo).
        """
        return self._get(f"/communities/{self.community_id}/users/{self.user_id}/lineup")

    # --- escritura ---

    def place_bid(self, player_id: int, amount: int) -> dict:
        """
        Puja por un jugador. Path confirmado por el propio `_links` de
        get_market() (game:exchangemarket:placeoffers). Body inferido de la
        forma del recurso resultante (ver docstring del módulo) — NUNCA
        probado con un POST real para no generar más pujas de las
        necesarias en la liga de prueba. Si da 4xx, revisar primero el
        nombre/anidamiento de "tradable".
        """
        return self._write(
            "POST",
            f"/communities/{self.community_id}/users/{self.user_id}/offers",
            {"type": "PURCHASE", "tradable": {"id": player_id}, "price": amount},
        )

    def withdraw_bid(self, offer_id: int) -> dict:
        """
        Retira una puja propia todavía pendiente. Path confirmado (link real
        `game:offer:withdraw` de una oferta propia en estado PENDING); verbo
        DELETE por convención REST, sin confirmar con una llamada real.
        """
        resp = self.session.request(
            "DELETE",
            f"{self.base_url}/communities/{self.community_id}/users/{self.user_id}/offers/{offer_id}",
            headers=self._auth_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def set_lineup(self, tactic: str, lineup_by_slot: dict[str, int]) -> dict:
        """
        Fija el once inicial para la próxima jornada.

        `tactic`: formato real SIN guiones, ej. "442", "433" (no "4-4-2").
        `lineup_by_slot`: mapa {slot: player_id} replicando la forma real de
        GET get_lineup()["items"]["lineup"]. La numeración completa de los
        11 slots según posición NO está confirmada del todo (solo se
        verificaron 2 de 11 en la prueba real: portero=11, un defensa=7) —
        antes de usar esto en producción, conviene guardar un once completo
        una vez a mano y volcar get_lineup() para terminar el mapeo.

        Verbo (PUT, por convención REST) y forma exacta del body sin
        confirmar al 100% — ver nota en el docstring del módulo.
        """
        return self._write(
            "PUT",
            f"/communities/{self.community_id}/users/{self.user_id}/lineup",
            {"tactic": tactic, "items": {"lineup": lineup_by_slot}},
        )
