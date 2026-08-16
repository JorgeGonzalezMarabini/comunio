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
    - Pujar: **100% confirmado** interceptando la llamada POST real del
      frontend + réplica exacta devolviendo 200. Verbo y body reales
      (distintos de lo inferido inicialmente — ver nota abajo):
        POST /communities/{communityId}/users/{userId}/offers
        body: {"offers": [{"price": amount, "tradableid": player_id, "type": "NEW"}]}
      Nota: "offers" es una LISTA — la API admite pujar por varios
      jugadores en una sola llamada (no usado todavía, ver TODO en
      place_bid). "type" al CREAR es "NEW", no "PURCHASE" (ese valor
      aparece luego en el recurso ya creado, releído con GET
      .../offers?current — son conceptos distintos: "NEW" = acción de
      crear, "PURCHASE"/"SALE" = naturaleza de la oferta ya existente).
      Respuesta real (200 aunque la oferta individual falle a nivel de
      negocio — HAY QUE MIRAR response[].status, no solo el HTTP status):
        {"status": "OK", "response": [{"offerid": <int>, "tradableid": ...,
         "price": ..., "type": "NEW", "status": "OK"|"ERROR",
         "message": "" o motivo (ej. "player is not on exchangemarket"),
         "processImmediately": bool}], "opponentIds": ...}
    - Retirar puja: **100% confirmado** (interceptando la llamada real del
      frontend al pulsar "Retirar oferta" + réplica exacta + comprobación
      de que la oferta desaparece de GET .../offers?current). Verbo real
      **PUT** (NO DELETE, como se asumía inicialmente) con body VACÍO:
        PUT /communities/{communityId}/users/{userId}/offers/{offerId}
        body: {}
    - Poner en venta / quitar de la venta: **100% confirmado** (2026-08-16,
      interceptando las llamadas reales del frontend en la pestaña
      "Ventas" + réplica exacta). Poner en venta NO es instantáneo, solo
      lista al jugador para que alguien lo compre después:
        POST /communities/{communityId}/users/{userId}/exchangemarket/addplayer
        body: {"items": [{"tradableId": player_id, "price": price}]}
        respuesta: {"status": "OK", "notPlaced": [], "purchasePrices": {...}, "remaining": <int>}
        POST /communities/{communityId}/users/{userId}/exchangemarket/removeplayer
        body: {"tradableIds": [player_id]}
      Confirmado también que `get_squad()` trae el precio real de compra
      en `purchaseInfo.price` (null si el jugador no se compró vía puja,
      p.ej. plantilla inicial) — necesario para calcular plusvalía, ver
      engine/selling_strategy.py.
    - Guardar alineación: **100% confirmado** con una prueba real completa
      (2026-08-15, once completo de 11 jugadores + interceptación de la
      llamada XHR real del propio frontend + réplica exacta del body
      devolviendo 200 "status": "OK"). Verbo y body reales:
        PUT /communities/{communityId}/users/{userId}/lineup
        body: {
            "userId": <user_id, int>,
            "tactic": "442",  # SIN guiones (distinto de "4-4-2" de la UI)
            "lineup": {"1": "<playerId>", ..., "11": "<playerId>"},  # strings
            "substitutes": {"striker": "", "midfielder": "", "defender": "", "keeper": ""},
            "type": "default",
        }
      Numeración de slots CONFIRMADA (patrón fijo, no depende del jugador):
      se numeran 1..11 consecutivos agrupando por posición en ESTE orden
      fijo: delanteros -> centrocampistas -> defensas -> portero (portero
      SIEMPRE el último slot). P.ej. en un 4-4-2: slots 1-2 delanteros,
      3-6 centrocampistas, 7-10 defensas, 11 portero. Ver
      engine.lineup_optimizer.build_lineup_slots(). `substitutes` es UN
      suplente por categoría de posición (no una lista), en inglés
      ("striker"/"midfielder"/"defender"/"keeper" — igual que el `position`
      de squad/market), vacío ("") si no hay suplente para esa posición.

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


class ComunioOfferError(Exception):
    """
    La API respondió 200 pero rechazó la oferta a nivel de negocio (ver
    place_bid: la respuesta siempre es HTTP 200, el resultado real viene en
    response["response"][i]["status"]/"message").
    """


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


def total_pending_purchase_amount(offers_response: dict) -> int:
    """
    Suma el importe de tus ofertas de COMPRA pendientes sin resolver, a
    partir de la respuesta real de ComunioClient.get_offers().

    Es la fuente de verdad para saber cuánto dinero está ya comprometido
    pero todavía NO descontado de "credit" — Comunio no descuenta el saldo
    hasta que una oferta se ejecuta al cerrar el periodo de transferencias
    (que puede durar más de un día). Sin esto, engine.bidding_strategy
    podría comprometer más dinero del que el saldo soporta si varias
    ofertas de días distintos se ejecutan a la vez, dejando saldo negativo
    — y saldo negativo al cierre de jornada son 0 puntos esa jornada
    entera (regla oficial de Comunio, ver README).

    Filtra por state == "PENDING" y type == "PURCHASE" (confirmado por
    captura real de una oferta propia); no se ha visto un "type" distinto
    en la muestra real (p.ej. para ofertas de venta), pero se filtra
    explícito por si acaso, para no sumar por error algo que no sea una
    salida de dinero nuestra.
    """
    return sum(
        item.get("price", 0)
        for item in offers_response.get("items", [])
        if item.get("state") == "PENDING" and item.get("type") == "PURCHASE"
    )


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

        OJO — regla de negocio crítica confirmada (FAQ oficial de Comunio,
        2026-08-16): "credit" es tu saldo actual, pero Comunio NO lo
        descuenta al colocar una oferta, solo cuando se EJECUTA al cerrar
        el periodo de transferencias (que puede durar más de un día). Para
        saber cuánto está realmente comprometido (y no arriesgar más de lo
        que el saldo soporta si varias ofertas pendientes se ejecutan a la
        vez), usar total_pending_purchase_amount() sobre este resultado —
        ver también engine.bidding_strategy.decide_bid.
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
        Puja por un jugador. **Body y verbo 100% confirmados** (2026-08-15,
        interceptando la llamada POST real del frontend + réplica exacta
        con un `offerid` real devuelto). Ver docstring del módulo.

        La API devuelve HTTP 200 incluso si la oferta es rechazada a nivel
        de negocio (p.ej. el jugador ya no está en el mercado) — por eso se
        comprueba el `status` del resultado y se lanza ComunioOfferError si
        no es "OK", en vez de fiarse solo de resp.raise_for_status().

        TODO: la API admite pujar por varios jugadores en una sola llamada
        (`"offers"` es una lista) — no se usa todavía, cada puja hace su
        propia llamada; sería una optimización razonable para
        jobs/run_market.py si el número de pujas por ejecución crece.

        Devuelve el item de "response" tal cual (incluye "offerid", el id
        real de la oferta en Comunio para poder retirarla con withdraw_bid).
        """
        result = self._write(
            "POST",
            f"/communities/{self.community_id}/users/{self.user_id}/offers",
            {"offers": [{"price": amount, "tradableid": player_id, "type": "NEW"}]},
        )
        offer = (result.get("response") or [{}])[0]
        if offer.get("status") != "OK":
            raise ComunioOfferError(f"Oferta rechazada por Comunio: {offer.get('message') or offer!r}")
        return offer

    def withdraw_bid(self, offer_id: int) -> dict:
        """
        Retira una puja propia todavía pendiente. **100% confirmado**
        (2026-08-15, interceptando la llamada real del frontend al pulsar
        "Retirar oferta" + réplica exacta devolviendo 200 "status": "OK",
        y comprobando después que la oferta desaparece de get_offers()).

        Verbo real: **PUT** (NO DELETE, como se asumía inicialmente) con
        body VACÍO `{}` — el propio path (que ya incluye `offer_id`) es
        toda la información que necesita, el verbo+URL basta.
        """
        return self._write(
            "PUT",
            f"/communities/{self.community_id}/users/{self.user_id}/offers/{offer_id}",
            {},
        )

    def list_for_sale(self, player_id: int, price: int) -> dict:
        """
        Pone un jugador de tu plantilla en venta (visible para que otros
        managers pujen por él). **100% confirmado** (2026-08-16,
        interceptando la llamada POST real del frontend al pulsar "Añadir
        al mercado" + réplica exacta).

        NO es una venta instantánea: solo lo deja listado. La venta se
        confirma más tarde, cuando alguien (otro manager o el "Computer")
        complete una compra — no se ha podido confirmar cuánto tarda ni el
        mecanismo exacto de esa parte; jobs/sync_data.py lo reconcilia
        comparando la plantilla en cada sync (igual que con las pujas, ver
        _reconcile_bids/_reconcile_sales).

            POST /communities/{communityId}/users/{userId}/exchangemarket/addplayer
            body: {"items": [{"tradableId": player_id, "price": price}]}
            respuesta real: {"status": "OK", "notPlaced": [], "purchasePrices": {...}, "remaining": <int>}

        "remaining" parece un límite diario de acciones de mercado (añadir/
        quitar), sin confirmar el número exacto ni qué pasa al agotarlo.
        "purchasePrices" trae un valor por jugador que no coincidía con el
        precio pedido en la prueba real (180.000 pedido -> 199.500 en la
        respuesta) — sin confirmar qué representa exactamente, así que no
        se usa todavía para nada.

        Lanza ComunioOfferError si el jugador aparece en "notPlaced".
        """
        result = self._write(
            "POST",
            f"/communities/{self.community_id}/users/{self.user_id}/exchangemarket/addplayer",
            {"items": [{"tradableId": player_id, "price": price}]},
        )
        not_placed = {str(x) for x in result.get("notPlaced", [])}
        if str(player_id) in not_placed:
            raise ComunioOfferError(f"No se pudo poner en venta al jugador {player_id}: {result!r}")
        return result

    def delist_from_sale(self, player_id: int) -> dict:
        """
        Quita un jugador del mercado de ventas. **100% confirmado**
        (2026-08-16, interceptando la llamada real del frontend + réplica
        exacta devolviendo 200 "status": "OK").

            POST /communities/{communityId}/users/{userId}/exchangemarket/removeplayer
            body: {"tradableIds": [player_id]}
        """
        return self._write(
            "POST",
            f"/communities/{self.community_id}/users/{self.user_id}/exchangemarket/removeplayer",
            {"tradableIds": [player_id]},
        )

    def set_lineup(self, tactic: str, lineup_by_slot: dict, substitutes: dict = None) -> dict:
        """
        Fija el once inicial para la próxima jornada. **Body y verbo 100%
        confirmados** interceptando la llamada real del frontend + réplica
        exacta devolviendo 200 (ver docstring del módulo y README).

        `tactic`: formato real SIN guiones, ej. "442", "433" (no "4-4-2") —
        usar engine.lineup_optimizer.to_api_tactic().
        `lineup_by_slot`: mapa {slot: player_id}, numeración confirmada —
        ver engine.lineup_optimizer.build_lineup_slots().
        `substitutes`: {"striker": id_o_"", "midfielder": ..., "defender": ...,
        "keeper": ...} — un suplente por posición, ver
        engine.lineup_optimizer.pick_substitutes(). Por defecto, sin
        suplentes (todo vacío).
        """
        substitutes = substitutes or {"striker": "", "midfielder": "", "defender": "", "keeper": ""}
        return self._write(
            "PUT",
            f"/communities/{self.community_id}/users/{self.user_id}/lineup",
            {
                "userId": int(self.user_id),
                "tactic": tactic,
                "lineup": {str(slot): str(player_id) for slot, player_id in lineup_by_slot.items()},
                "substitutes": substitutes,
                "type": "default",
            },
        )
