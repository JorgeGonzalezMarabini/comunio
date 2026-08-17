"""
Cliente de Futmondo (ingeniería inversa, sin API pública oficial).

Endpoints y forma de las respuestas capturados el 2026-08-17 con Chrome
DevTools contra una liga de prueba real ("Liga de prueba bot", modo
"Social" — presupuesto inicial + fichajes de mercado, el equivalente
directo al modelo de Comunio; se descartó el modo "Clásico", que resetea
el equipo cada jornada con presupuesto fijo, un juego distinto). Referencia
adicional: el reverse-engineering comunitario de
https://github.com/vicenteqa/futmondo-utils, que documenta la mayoría de
estos mismos endpoints — se marca explícitamente qué se confirmó aquí con
captura real propia y qué viene solo de esa referencia sin verificación
propia todavía.

Un único dominio: https://api.futmondo.com — no hay separación
frontend/API como en Comunio (la web, app.futmondo.com, es una app Flutter
Web que habla contra este mismo dominio).

Auth: NO es un header Authorization. `token` y `userid` (obtenidos al hacer
login en la app, guardados por el cliente Flutter en
`localStorage["flutter.token"]`/`localStorage["flutter.id_user"]` —
confirmado inspeccionando localStorage real) se mandan en el BODY de cada
petición, dentro de `{"header": {"token": ..., "userid": ...}, "query": {...}}`.
Todas las llamadas observadas son POST, incluso las de solo lectura.

TODO importante, sin resolver: no se ha encontrado un endpoint de login
(ni en la captura propia ni en la referencia comunitaria, que tampoco lo
tiene) — el token se obtiene a mano iniciando sesión una vez en
app.futmondo.com y copiando `localStorage.getItem("flutter.token")` /
`"flutter.id_user"`. No se ha confirmado cuánto dura el token ni si hay
refresh automático; si expira, la única vía conocida por ahora es repetir
la captura manual. A diferencia de Comunio, aquí no hay ni siquiera un
`POST /login` inferible a partir de HATEOAS — la app parece delegar el
login en Firebase Auth (se ven botones de Google/Facebook/Twitter/Outlook
además de email+contraseña) y de ahí cambiar por un token propio de
Futmondo, pero el intercambio exacto no se ha capturado.

Endpoints confirmados por captura real (2026-08-17, con permiso explícito,
sobre la liga de prueba; token/userid nunca expuestos ni registrados —
comprobado con la misma protección anti-cookie que en la sesión de
Comunio, ver README):
    POST /1/userteam/roster          -> plantilla propia
    POST /1/userteam/information     -> resumen del equipo (budget, teamValue, config de la liga...)
    POST /1/userteam/lineup          -> alineación actualmente guardada
    POST /2/userteam/changeplayer    -> ESCRITURA: coloca a un jugador en un slot de la alineación
    POST /1/market/players           -> mercado de fichajes (compra)
    POST /1/market/myplayers         -> jugadores propios puestos en venta
    POST /1/market/bid               -> ESCRITURA: puja por un jugador del mercado
    POST /1/market/putonmarket       -> ESCRITURA: pone un jugador propio en venta
    POST /1/player/summary           -> ficha de un jugador (incluye histórico de precio)

Confirmado solo por la referencia comunitaria (vicenteqa/futmondo-utils),
NO verificado con una captura real propia todavía — se implementan igual
porque el patrón (mismo dominio, mismo shape de body, mismos nombres de
query) es consistente con todo lo demás, pero un cambio de contrato aquí
no se detectaría hasta usarlos de verdad:
    POST /1/market/cancelsell        -> quita un jugador propio del mercado de ventas
    POST /1/market/rosterclause      -> paga la cláusula de rescisión de un jugador
    POST /5/market/toggleplayer      -> oculta/muestra un jugador propio en el mercado (no usado por el bot)
    POST /1/locker/pressroom         -> sala de prensa / fichajes recientes de la liga (no usado por el bot)

Vistos en la captura pero NO usados por el bot (fuera de alcance de esta
primera versión, documentados por si hacen falta más adelante):
    POST /1/market/rosterbids        -> ofertas de cláusula que otros managers han hecho sobre TU plantilla
                                         (type="roster" en la query; distinto de "mis pujas de compra", que
                                         no tienen endpoint propio, ver nota en total_pending_bid_amount())
    POST /1/league/championshipteams -> equipos/managers de la liga
    POST /5/league/championshipplayers -> plantillas de todos los managers de la liga

Nombres de campo reales confirmados por fetch autenticado real:
    roster item / market item: {
        id, name, slug, role: "portero"|"defensa"|"centrocampista"|"delantero"
            (palabra completa en ESPAÑOL — a diferencia de Comunio, que usaba
            inglés; ver FUTMONDO_POSITION_MAP), role2 (posición secundaria,
            vacía en la muestra), photo, points, value (precio/VM actual),
            team, logo, status (visto: "" = sano; "ok" en la respuesta de
            /1/userteam/lineup para el mismo jugador — inconsistencia sin
            explicar todavía; NO se ha visto un valor de lesión/sanción real
            en esta liga de prueba recién creada en pretemporada, así que
            FUTMONDO_INJURY_STATUSES/is_injury_status() son una
            aproximación basada en vicenteqa/futmondo-utils
            (`status.includes('injured')`), sin confirmar con un caso real),
        average: {average, homeAverage, awayAverage, averageLastFive,
            matches, fitness: [...]} — `fitness` parece ser la puntuación de
            los últimos partidos (usado por la referencia comunitaria para
            calcular una "forma" reciente), pero no se ha podido confirmar
            el orden cronológico (¿más reciente al final o al principio?)
            porque en esta liga de prueba, recién creada, viene siempre
            vacío (pretemporada, cero partidos jugados),
        change (variación de valor reciente), computer (bool, si el
            propietario es el "Computer" del juego), teamId, rating,
        - solo en roster: buyPrice, market (bool: si TÚ lo has puesto en
            venta — equivalente a `onMarket` en Comunio), direct (sin
            confirmar qué significa),
        - solo en market: creationDate, expirationDate, price, isClause
            (bool), type, numberOfBids ("-" si nadie ha pujado todavía,
            visto en captura real).
    }

TODO abierto e importante para engine/selling_strategy.py: en Comunio,
`purchaseInfo == null` distinguía sin ambigüedad "plantilla inicial" de
"comprado por el bot". Aquí, `buyPrice` NO sirve para eso tal cual: se ha
visto tanto un jugador de la plantilla inicial con buyPrice=0 (Matz Sels)
como otro también inicial con buyPrice>0 (Tzolakis, 14.017.740€ en su
ficha de alineación) — buyPrice parece ser más bien "valor de referencia
en el momento de entrar al equipo" (incluida la asignación inicial de
plantilla), no "importe pagado en una puja real nuestra". Sin poder ganar
una puja real de prueba en el tiempo disponible para confirmarlo,
`engine/selling_strategy.py` trata cualquier `buyPrice > 0` como precio de
referencia válido para calcular plusvalía, SIN filtrar por "solo
comprados por el bot" (a diferencia de Comunio) — ver docstring de ese
módulo para el razonamiento completo.
"""
from __future__ import annotations

import requests

import config


class FutmondoAuthError(Exception):
    """Token ausente, inválido o expirado (ver nota de login más arriba)."""


class FutmondoOfferError(Exception):
    """
    La API respondió 200 pero la operación se rechazó a nivel de negocio.
    Confirmado por captura real (place_bid): la respuesta siempre trae
    `answer.code` — "api.general.ok" si fue bien, otro código de error si
    no (ej. "api.market.max_number_players_in_roster", visto documentado
    en la referencia comunitaria, no en captura propia).
    """


# Futmondo usa la palabra completa en ESPAÑOL para el rol (confirmado por
# fetch real), a diferencia de Comunio (inglés). Traducir en el punto de
# ingesta (jobs/sync_data.py), no repartir este mapeo por todos lados.
FUTMONDO_POSITION_MAP = {
    "portero": "POR",
    "defensa": "DEF",
    "centrocampista": "MED",
    "delantero": "DEL",
}

# Sin confirmar con un caso real todavía (liga de prueba en pretemporada,
# cero lesionados observados) — aproximación a partir de
# vicenteqa/futmondo-utils, que comprueba `status.includes('injured')`.
# is_injury_status() usa ese mismo criterio de subcadena en vez de una lista
# cerrada de valores exactos, precisamente porque no sabemos los valores
# reales todavía.
FUTMONDO_INJURY_STATUS_SUBSTRINGS = ("injured", "lesion", "lesión")


def is_injury_status(status: str | None) -> bool:
    """
    Aproximación sin confirmar (ver docstring del módulo): considera
    lesionado/en duda cualquier `status` que contenga alguna de las
    subcadenas de FUTMONDO_INJURY_STATUS_SUBSTRINGS. TODO: reemplazar por
    valores exactos confirmados en cuanto se observe un jugador lesionado
    real en una liga con partidos jugados.
    """
    if not status:
        return False
    status_lower = status.lower()
    return any(s in status_lower for s in FUTMONDO_INJURY_STATUS_SUBSTRINGS)


def total_pending_bid_amount(bids_placed_locally: list[dict]) -> int:
    """
    Suma el importe de nuestras propias pujas de compra que seguimos
    creyendo pendientes, según NUESTRA PROPIA tabla `bids` (status=
    'placed'), NO según un endpoint de Futmondo.

    A diferencia de Comunio (`clients.comunio_client.
    total_pending_purchase_amount`, que sí tiene un endpoint real
    `GET .../offers?current` con la lista de ofertas pendientes propias),
    no se ha encontrado un endpoint equivalente en Futmondo: la única pista
    real es que cada item de `get_market()` puede traer un campo "bid" con
    la puja propia sobre ESE jugador en concreto (visto documentado así en
    vicenteqa/futmondo-utils, `player.bid.id` / `player.bid`), pero no se ha
    podido confirmar en captura propia la forma exacta de ese sub-objeto
    (¿trae el importe? ¿"price"?) ni si de verdad está siempre filtrado a
    "tu" puja o podría incluir la puja ganadora de otro manager.

    Por eso, de momento, la protección de presupuesto (ver
    engine.bidding_strategy.max_biddable_amount) se apoya en
    `db.models.get_open_bids_total()` — nuestra propia auditoría local de
    pujas 'placed' — confiando en que `jobs/sync_data.py` reconcilia el
    estado a 'won'/'lost' en cada ejecución. Es una protección más débil
    que la de Comunio (si la BD se pierde o no se reconcilia a tiempo,
    podría subestimar el compromiso real), pero es lo único verificable con
    los datos que tenemos. `bids_placed_locally`: filas de
    `db.models.get_open_bids()`.
    """
    return sum(b.get("amount", 0) for b in bids_placed_locally)


class FutmondoClient:
    def __init__(
        self,
        token: str = None,
        user_id: str = None,
        championship_id: str = None,
        userteam_id: str = None,
    ):
        self.token = token or config.FUTMONDO_TOKEN
        self.user_id = user_id or config.FUTMONDO_USER_ID
        self.championship_id = championship_id or config.FUTMONDO_CHAMPIONSHIP_ID
        self.userteam_id = userteam_id or config.FUTMONDO_USERTEAM_ID
        self.base_url = config.FUTMONDO_BASE_URL
        self.session = requests.Session()

    def _body(self, extra_query: dict) -> dict:
        if not self.token or not self.user_id:
            raise FutmondoAuthError(
                "Falta token/user_id — no hay endpoint de login conocido, "
                "hay que capturarlos a mano de localStorage (ver docstring del módulo)."
            )
        return {
            "header": {"token": self.token, "userid": self.user_id},
            "query": {"championshipId": self.championship_id, "userteamId": self.userteam_id, **extra_query},
        }

    def _post(self, path: str, extra_query: dict = None) -> dict:
        resp = self.session.post(f"{self.base_url}{path}", json=self._body(extra_query or {}), timeout=15)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _check_ok(result: dict) -> dict:
        """
        Comprueba `answer.code == "api.general.ok"` (confirmado por
        captura real en place_bid/list_for_sale/change_lineup) y lanza
        FutmondoOfferError si no. Devuelve `answer` tal cual si todo fue
        bien.
        """
        answer = result.get("answer", {})
        if answer.get("code") != "api.general.ok":
            raise FutmondoOfferError(f"Futmondo rechazó la operación: {answer.get('code') or answer!r}")
        return answer

    # --- lectura ---

    def get_roster(self) -> dict:
        """
        Plantilla propia. **100% confirmado** (2026-08-17, captura real).
        Respuesta: {"answer": [...jugadores...]} — ver docstring del
        módulo para la forma de cada item (incluye `buyPrice`, `market`).
        """
        return self._post("/1/userteam/roster")

    def get_information(self) -> dict:
        """
        Resumen del equipo. **100% confirmado**. Respuesta real (recortada):
            {"answer": {"budget": <int>, "teamValue": <int>, "points": ...,
             "championship": <nombre>, "championshipMode": "social"|...,
             "league": {...}, "configuration": {"budget", "numberOfPlayers",
             ...}, ...}}
        `budget` es el saldo TOTAL (no descuenta pujas pendientes, igual que
        el "credit" de Comunio — ver total_pending_bid_amount()).
        """
        return self._post("/1/userteam/information")

    def get_lineup(self) -> dict:
        """
        Alineación actualmente guardada. **100% confirmado**. Respuesta
        real: {"answer": {"strategy": "4-4-2" (CON guiones, a diferencia de
        Comunio), "players": [{..., "position": <int 0..10>}, ...]}}.
        Ver engine/lineup_optimizer.py para el mapeo de `position`
        confirmado (solo para 4-4-2, ver TODO ahí).
        """
        return self._post("/1/userteam/lineup")

    def get_market(self) -> dict:
        """
        Jugadores disponibles en el mercado de fichajes. **100% confirmado**.
        Respuesta: {"answer": [...jugadores...]} — ver docstring del
        módulo para la forma de cada item (incluye `price`, `numberOfBids`,
        `expirationDate`).
        """
        return self._post("/1/market/players")

    def get_my_players_in_market(self) -> dict:
        """
        Jugadores propios puestos en venta. **100% confirmado** (vacío en
        la prueba real hasta que se puso a alguien en venta con
        list_for_sale(); no se ha vuelto a leer después para confirmar la
        forma exacta del item en este caso — se espera igual que un item de
        roster).
        """
        return self._post("/1/market/myplayers")

    def get_player_summary(self, player_id: str) -> dict:
        """
        Ficha de un jugador. **100% confirmado**. Respuesta real:
            {"answer": {"data": {...igual que un item de roster/market...},
             "prices": [{"date", "price", "c", "s", ...}, ...]}}
        `prices` es el histórico de valor de mercado día a día — `c`/`s`
        sin confirmar qué representan exactamente (¿compras/ventas del
        día?), no se usan todavía.
        """
        return self._post("/1/player/summary", {"playerId": player_id})

    # --- escritura ---

    def place_bid(self, player_id: str, player_slug: str, amount: int, is_clause: bool = False) -> dict:
        """
        Puja por un jugador del mercado. **100% confirmado** (2026-08-17,
        puja real de prueba sobre un jugador del mercado + relectura
        posterior confirmando el precio en el listado).

            POST /1/market/bid
            body.query: {..., "player_slug": ..., "player_id": ...,
                         "price": <amount>, "isClause": false}
            respuesta real: {"answer": {"code": "api.general.ok"}, ...}

        Lanza FutmondoOfferError si `answer.code` no es "api.general.ok".
        No hay id de oferta que devolver (a diferencia de Comunio) — ver
        total_pending_bid_amount() para cómo afecta esto al seguimiento de
        pujas pendientes.
        """
        result = self._post(
            "/1/market/bid",
            {"player_slug": player_slug, "player_id": player_id, "price": amount, "isClause": is_clause},
        )
        return self._check_ok(result)

    def list_for_sale(self, player_id: str, price: int) -> dict:
        """
        Pone un jugador propio en venta. **100% confirmado** (2026-08-17,
        puesto en venta real + comprobado en la UI que aparece en "Mis
        ventas" con "Cancelar venta" disponible).

            POST /1/market/putonmarket
            body.query: {..., "price": <price>, "player_id": <id>,
                         "isClause": null, "mode": null, "toLoan": null}
            respuesta real: {"answer": {"code": "api.general.ok"}, ...}

        `isClause`/`mode`/`toLoan` se mandan a `null` tal cual los capturó
        el frontend real al listar una venta simple (no de cláusula ni
        cesión) — no se ha probado qué hacen con otro valor.
        """
        result = self._post(
            "/1/market/putonmarket",
            {"price": price, "player_id": player_id, "isClause": None, "mode": None, "toLoan": None},
        )
        return self._check_ok(result)

    def cancel_sale(self, player_id: str) -> dict:
        """
        Quita un jugador propio del mercado de ventas. **NO confirmado con
        captura propia** — se intentó en la sesión de captura pero el botón
        "Cancelar venta" del frontend no llegó a disparar la llamada de red
        esperada en el tiempo disponible (posible confirmación intermedia
        en la propia UI no completada). Implementado siguiendo el patrón de
        vicenteqa/futmondo-utils (mismo shape que el resto de escrituras de
        este cliente, todas confirmadas):

            POST /1/market/cancelsell
            body.query: {..., "player_id": <id>}

        TODO: confirmar con una prueba real antes de confiar en esto en
        producción.
        """
        result = self._post("/1/market/cancelsell", {"player_id": player_id})
        return self._check_ok(result)

    def pay_clause(self, player_id: str, player_slug: str, price: int) -> dict:
        """
        Paga la cláusula de rescisión de un jugador de OTRO manager. **NO
        confirmado con captura propia** (no se ha probado en la sesión de
        captura — implica gastar dinero real de otro equipo, no se hizo sin
        un caso de prueba claro). Según vicenteqa/futmondo-utils:

            POST /1/market/rosterclause
            body.query: {..., "player_id": ..., "player_slug": ..., "price": ...}
                        (sin "type", a diferencia del resto de escrituras)

        No usado todavía por ningún job del bot — Comunio tampoco lo tenía
        automatizado (función de pago fuera de alcance, ver README).
        """
        result = self._post("/1/market/rosterclause", {"player_id": player_id, "player_slug": player_slug, "price": price})
        return self._check_ok(result)

    def change_lineup(self, changes: list[dict]) -> list[dict]:
        """
        Coloca jugadores en slots de la alineación, UNO POR LLAMADA.

            POST /2/userteam/changeplayer
            body.query.changes: [{"cpt": false, "to": <playerId>,
                                   "position": <int>, "isBench": false,
                                   "multiposition": false}]
            respuesta real: {"answer": {"code": "api.general.ok",
                              "budget": <int>, "rc": "-1"}, ...}

        **Bug real confirmado en producción (2026-08-17)**: este método
        aceptaba `changes` como una lista y mandaba los 11 cambios de golpe
        en una única llamada — el propio docstring lo marcaba como "no
        probado con más de uno en la misma llamada, pero el shape lo
        admite literalmente". En la primera ejecución real de
        `jobs/set_lineup.py` contra la liga de prueba, de 11 cambios
        mandados así en una sola llamada, la API devolvió
        `"api.general.ok"` pero **solo aplicó el primero** — los otros 10
        slots se quedaron con la alineación previa (confirmado comparando
        el `player_ids` auditado en `lineup_decisions` contra una relectura
        real de `get_lineup()` justo después: solo coincidía el jugador
        que iba primero en la lista). Nada en la respuesta delataba el
        fallo parcial.

        Corregido: ahora se manda **una llamada HTTP por cada item de
        `changes`** — el único patrón confirmado de verdad durante la
        sesión de captura real (cada jugador se colocó uno a uno,
        interceptando cada POST del frontend por separado). Sigue
        aceptando una lista para no cambiar la forma de llamar desde
        `jobs/set_lineup.py`, pero ahora itera internamente.

        Devuelve una lista de resultados, uno por cada `change`, en el
        mismo orden: `{"change": <el dict original>, "ok": bool,
        "answer_or_error": <answer si ok, mensaje de error si no>}`. NO
        lanza en el primer fallo — sigue con el resto de la lista (igual
        que jobs/run_market.py con las pujas: un jugador que falle no debe
        impedir colocar al resto). El llamador decide qué hacer con los
        que fallaron (ver jobs/set_lineup.py).

        Ver engine/lineup_optimizer.py para cómo construir `changes` con
        la numeración de `position` confirmada (solo 4-4-2, ver TODO ahí),
        `"from"` para sustituir a alguien que ya ocupa el slot, y
        `isBench` para el banquillo.

        **Tercer hallazgo real el mismo día (2026-08-17), sin resolver
        todavía**: sustituir un slot ocupado incluyendo `"from"` funciona
        bien cuando el jugador que ENTRA no está ya en el campo en otra
        posición — pero si SÍ lo está (una rotación entre varios jugadores
        ya colocados, ej. A pasa a la posición de B y B a la de C), la API
        rechaza esos cambios con `"api.error.in_field"` ("este jugador ya
        está en el campo"), aunque cada `change` individual lleve su
        `"from"` correcto. Probablemente haga falta primero mandar al
        jugador al banquillo (con `isBench: true`) como paso intermedio
        antes de colocarlo en su nueva posición, pero la numeración de
        slots del banquillo no está confirmada (ver TODO en
        engine/lineup_optimizer.py) — no se ha intentado resolver esto
        todavía para no seguir escribiendo a ciegas contra una cuenta
        real. `change_lineup()` audita el fallo de cada jugador afectado
        (`answer_or_error` trae literalmente `"api.error.in_field"`) en
        vez de fallar en silencio, así que el síntoma queda siempre
        visible aunque la causa de fondo siga sin arreglarse.

        "rc" en la respuesta no se ha confirmado qué significa (visto
        siempre "-1" en la prueba real).
        """
        results = []
        for change in changes:
            try:
                result = self._post("/2/userteam/changeplayer", {"changes": [change]})
                answer = self._check_ok(result)
                results.append({"change": change, "ok": True, "answer_or_error": answer})
            except (requests.RequestException, FutmondoOfferError) as e:
                results.append({"change": change, "ok": False, "answer_or_error": str(e)})
        return results
