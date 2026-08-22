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

Endpoints confirmados por captura real (2026-08-17/18, con permiso
explícito, sobre la liga de prueba; token/userid nunca expuestos ni
registrados — comprobado con la misma protección anti-cookie que en la
sesión de Comunio, ver README):
    POST /1/userteam/roster          -> plantilla propia
    POST /1/userteam/information     -> resumen del equipo (budget, teamValue, config de la liga...)
    POST /1/userteam/lineup          -> alineación actualmente guardada
    POST /2/userteam/changeplayer    -> ESCRITURA: coloca a un jugador en un slot de la alineación
    POST /1/market/players           -> mercado de fichajes (compra)
    POST /1/market/myplayers         -> jugadores propios puestos en venta
    POST /1/market/bid               -> ESCRITURA: puja por un jugador del mercado
    POST /1/market/cancelbid         -> ESCRITURA: cancela una puja de compra propia todavía
                                         abierta (confirmado 2026-08-18, TODO.md #13 -- NO
                                         documentado ni en vicenteqa/futmondo-utils ni en ninguna
                                         referencia previa, encontrado inspeccionando la UI real)
    POST /1/market/putonmarket       -> ESCRITURA: pone un jugador propio en venta
    POST /1/market/cancelsell        -> ESCRITURA: quita un jugador propio del mercado de ventas
                                         (confirmado 2026-08-18, TODO.md #6)
    POST /1/player/summary           -> ficha de un jugador (incluye histórico de precio)

Confirmado solo por la referencia comunitaria (vicenteqa/futmondo-utils),
NO verificado con una captura real propia todavía — se implementan igual
porque el patrón (mismo dominio, mismo shape de body, mismos nombres de
query) es consistente con todo lo demás, pero un cambio de contrato aquí
no se detectaría hasta usarlos de verdad:
    POST /1/market/rosterclause      -> paga la cláusula de rescisión de un jugador
    POST /5/market/toggleplayer      -> oculta/muestra un jugador propio en el mercado (no usado por el bot)
    POST /1/locker/pressroom         -> sala de prensa / fichajes recientes de la liga (no usado por el bot)

Vistos en la captura pero NO usados por el bot (fuera de alcance de esta
primera versión, documentados por si hacen falta más adelante):
    POST /1/market/rosterbids        -> ofertas de cláusula que otros managers han hecho sobre TU plantilla
                                         (type="roster" en la query; distinto de "mis pujas de compra", que
                                         no tienen endpoint propio dedicado -- se leen del campo "bid" de
                                         cada item de /1/market/players, ver real_pending_bid_amount())
    POST /1/league/championshipteams -> equipos/managers de la liga
    POST /5/league/championshipplayers -> plantillas de todos los managers de la liga

Nombres de campo reales confirmados por fetch autenticado real:
    roster item / market item: {
        id, name, slug, role: "portero"|"defensa"|"centrocampista"|"delantero"
            (palabra completa en ESPAÑOL — a diferencia de Comunio, que usaba
            inglés; ver FUTMONDO_POSITION_MAP), role2 (posición secundaria,
            vacía en la muestra), photo, points, value (precio/VM actual),
            team, logo, status (CONFIRMADO con datos reales de producción
            2026-08-18, roster/market de la liga real vía jobs/sync_data.py
            en cron — ver también TODO.md #5: valores vistos "" y "ok"
            (ambos sanos; por qué hay dos valores distintos para lo mismo
            sigue sin explicarse, pero ninguno se trata como lesión),
            "doubt" (duda — jugador en riesgo, Vivian/Athletic, Pablo
            Durán/Celta, Boayar/Elche vistos con este valor) e "injuredN"
            (lesionado, tier numerado — "injured2" visto en Sergi Canós/
            Valencia; no se ha visto todavía si existen otros tiers como
            "injured1" o "injured3"). Esto CONTRADICE la referencia
            comunitaria en la que se basaba la heurística original
            (esperaba subcadenas en español, "lesion"/"lesión"): el campo
            real es en inglés. Ver FUTMONDO_INJURY_STATUSES/
            is_injury_status()),
        average: {average, homeAverage, awayAverage, averageLastFive,
            matches, fitness: [...]} — `fitness` parece ser la puntuación de
            los últimos partidos (usado por la referencia comunitaria para
            calcular una "forma" reciente). Ya NO viene siempre vacío
            (CONFIRMADO 2026-08-18 contra la cuenta real: con la jornada 1
            de LaLiga en curso, los jugadores que ya jugaron traen
            `fitness` de longitud 1, p. ej. `matches: 1, fitness: [16]`),
            pero con longitud máxima 1 vista hasta ahora sigue sin poder
            confirmarse el orden cronológico (¿más reciente al final o al
            principio?) — hace falta repetir la consulta en la jornada 2 y
            comparar contra el `points` de la jornada 1 ya conocido (ver
            TODO.md #7),
        change (variación de valor reciente), computer (bool, si el
            propietario es el "Computer" del juego), teamId, rating,
        - solo en roster: buyPrice, market (bool: si TÚ lo has puesto en
            venta — equivalente a `onMarket` en Comunio), direct (sin
            confirmar qué significa),
        - solo en market: creationDate, expirationDate, price, isClause
            (bool), type, numberOfBids ("-" si nadie ha pujado todavía,
            visto en captura real).
    }

TODO.md #4, resuelto sin necesitar confirmar este campo: en Comunio,
`purchaseInfo == null` distinguía sin ambigüedad "plantilla inicial" de
"comprado por el bot". Aquí, `buyPrice` NO sirve para eso tal cual: se ha
visto tanto un jugador de la plantilla inicial con buyPrice=0 (Matz Sels)
como otro también inicial con buyPrice>0 (Tzolakis, 14.017.740€ en su
ficha de alineación) — buyPrice parece ser más bien "valor de referencia
en el momento de entrar al equipo" (incluida la asignación inicial de
plantilla), no "importe pagado en una puja real nuestra". En vez de
esperar a poder confirmarlo ganando una puja real, `engine/
selling_strategy.py` dejó de mirar `buyPrice` para esta decisión: usa el
registro LOCAL de pujas ganadas por el bot (tabla `bids`, ver
`db.models.get_won_bid_prices()`), que es una fuente propia e
independiente de la ambigüedad de este campo — ver docstring de ese
módulo para el razonamiento completo.
"""
from __future__ import annotations

import time

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

# CONFIRMADO con datos reales (2026-08-18, ver TODO.md #5 y docstring del
# módulo): la propia liga real, sincronizada por jobs/sync_data.py en cron,
# ha mostrado "doubt" (duda, ej. Vivian/Athletic) e "injured2" (lesionado,
# Sergi Canós/Valencia) junto a los ya conocidos "" y "ok" (ambos sanos).
# El campo es en INGLÉS, no en español como asumía la referencia
# comunitaria original (vicenteqa/futmondo-utils, `status.includes(
# 'injured')`) — "lesion"/"lesión" se mantienen igualmente por si acaso
# (no hacen daño: no son subcadena de ningún valor sano confirmado), pero
# ya no son la base de la heurística, solo un colchón de seguridad.
# Se sigue usando coincidencia de subcadena en vez de una lista cerrada de
# valores exactos porque "injured2" sugiere que hay más tiers sin
# confirmar todavía (¿"injured1"? ¿"injured3"?) y "injured" los cubre a
# todos sin tener que enumerarlos.
#
# Separado en dos subconjuntos (a petición del usuario, 2026-08-22) porque
# "doubt" e "injuredN" ya NO se tratan igual en la decisión de FICHAJE
# (ver engine/evaluator.py y jobs/run_market.py): "doubt" es duda -- el
# jugador todavía puede llegar a jugar -- mientras que "injuredN" es
# lesión ya CONFIRMADA -- normalmente baja segura varias jornadas. El
# resto de módulos (engine/squad_risk.py, engine/lineup_optimizer.py,
# engine/selling_strategy.py) sigue usando is_injury_status() sin
# distinguir gravedad -- ahí solo importa sano/no-sano, no por qué.
FUTMONDO_DOUBT_STATUS_SUBSTRINGS = ("doubt",)
FUTMONDO_INJURED_STATUS_SUBSTRINGS = ("injured", "lesion", "lesión")
FUTMONDO_INJURY_STATUS_SUBSTRINGS = FUTMONDO_DOUBT_STATUS_SUBSTRINGS + FUTMONDO_INJURED_STATUS_SUBSTRINGS


def is_injury_status(status: str | None) -> bool:
    """
    Considera lesionado/en duda cualquier `status` que contenga alguna de
    las subcadenas de FUTMONDO_INJURY_STATUS_SUBSTRINGS. Confirmado con
    datos reales de producción para "doubt" e "injuredN" (ver TODO.md #5);
    "lesion"/"lesión" quedan solo como colchón defensivo sin confirmar,
    ya que la evidencia real apunta a que el campo es en inglés.

    No distingue gravedad -- para eso ver is_doubtful_status()/
    is_confirmed_injured_status(), pensadas para quien necesite tratar
    duda y lesión confirmada de forma distinta.
    """
    return is_doubtful_status(status) or is_confirmed_injured_status(status)


def is_doubtful_status(status: str | None) -> bool:
    """Solo "doubt" -- duda/riesgo de no jugar, sin lesión confirmada."""
    if not status:
        return False
    status_lower = status.lower()
    return any(s in status_lower for s in FUTMONDO_DOUBT_STATUS_SUBSTRINGS)


def is_confirmed_injured_status(status: str | None) -> bool:
    """
    Lesión ya confirmada ("injuredN", tier numerado -- o "lesion"/"lesión"
    como colchón defensivo, ver comentario de FUTMONDO_INJURY_STATUS_SUBSTRINGS),
    a diferencia de is_doubtful_status() ("doubt", solo duda). Usado por
    jobs/run_market.py para descartar directamente a un candidato de
    fichaje del mercado, en vez de solo penalizarlo en el score.
    """
    if not status:
        return False
    status_lower = status.lower()
    return any(s in status_lower for s in FUTMONDO_INJURED_STATUS_SUBSTRINGS)


def total_pending_bid_amount(bids_placed_locally: list[dict]) -> int:
    """
    Suma el importe de nuestras propias pujas de compra que seguimos
    creyendo pendientes, según NUESTRA PROPIA tabla `bids` (status=
    'placed'), NO según un endpoint de Futmondo. Ver `real_pending_bid_amount()`
    más abajo para la fuente confirmada del lado de Futmondo (resuelve
    TODO.md #3) — esta función local se mantiene como colchón/fallback si
    esa consulta al mercado fallara, no como la única fuente ya.

    `bids_placed_locally`: filas de `db.models.get_open_bids()`.
    """
    return sum(b.get("amount", 0) for b in bids_placed_locally)


def real_pending_bid_amount(market_items: list[dict]) -> int:
    """
    Resuelve TODO.md #3: suma del importe REAL que Futmondo reconoce como
    puja propia pendiente en cada listado del mercado, confirmado por fetch
    autenticado real (2026-08-18, liga de prueba, 12 pujas propias
    realmente colocadas ese día y el anterior).

    Cada item de `get_market()` en el que TENEMOS una puja pendiente trae
    un campo extra `"bid": {"id": <str>, "price": <int>}` que no aparece en
    absoluto en los items donde no hemos pujado (confirmado cruzando los 14
    items con `"bid"` contra nuestra propia tabla `bids`: coinciden 1:1,
    ningún "bid" huérfano de otro manager) — así que, a diferencia de lo
    que se sospechaba antes de esta confirmación, SÍ es una fuente
    equivalente al `GET .../offers?current` de Comunio: una lista de nuestras
    propias ofertas pendientes servida por el propio Futmondo, más fuerte
    que la auditoría local porque no depende de que `jobs/sync_data.py`
    haya reconciliado a tiempo ni de que la BD local no se haya perdido.

    HALLAZGO IMPORTANTE (mismo fetch, 2026-08-18): en 5 de los 12 casos
    reales, el bot había pujado DOS VECES sobre el mismo listado todavía
    abierto (una escalada de precio decidida por `decide_bids_for_market`
    en dos ejecuciones distintas del cron). Las dos llamadas a `place_bid()`
    devolvieron `"api.general.ok"` (no se detecta como fallo), pero
    `"bid.price"` en el mercado siempre coincide con el importe de la
    PRIMERA puja, nunca con el de la segunda (más alta o más baja según el
    caso) — Futmondo acepta la llamada pero no actualiza el precio de una
    puja ya abierta sobre el mismo jugador. Por eso `jobs/run_market.py`
    ahora excluye de los candidatos a cualquier jugador con una puja local
    ya `'placed'` (ver `db.models.get_open_bids()`), en vez de reintentar
    una "mejora" que Futmondo ignora en silencio — y por eso esta función
    (fuente del propio Futmondo) es más fiable que sumar la tabla `bids`
    local sin más: esta última duplicaría el compromiso de esos 5
    jugadores (cuenta las dos filas 'placed'), mientras que esta función
    refleja el importe real por el que Futmondo nos haría pagar si
    ganáramos cada listado.

    `market_items`: la lista `answer` de `get_market()` tal cual.
    """
    return sum(item["bid"]["price"] for item in market_items if "bid" in item)


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

    def _post(self, path: str, extra_query: dict = None, max_retries: int = 0) -> dict:
        """
        `max_retries`: reintentos ante fallo de CONEXIÓN (`requests.
        exceptions.ConnectionError` — incluye `RemoteDisconnected`/
        `ProtocolError` de urllib3) con backoff corto (1s, 2s, 4s...).

        Caso real confirmado en producción (GitHub Actions, 2026-08-17,
        `jobs/run_market.py` cayó entero sin notificar nada): Futmondo cerró
        la conexión sin responder a `/1/userteam/information`, sin relación
        aparente con los datos de la petición (probablemente un hiccup de
        red transitorio o un bloqueo puntual de la IP del runner) — sin
        ningún reintento, un solo fallo de este tipo tumbaba el job entero.

        Deliberadamente **0 por defecto** (solo lo activan explícitamente
        los métodos de LECTURA, ver más abajo): un ConnectionError puede
        pasar DESPUÉS de que Futmondo ya procesara la petición de verdad —
        se perdió la respuesta, no necesariamente la petición — así que
        reintentar una ESCRITURA no idempotente (place_bid, changeplayer...)
        podría duplicar el efecto (pujar dos veces, mandar el mismo cambio
        de alineación dos veces). No confirmado si eso sería inofensivo o
        no, así que no vale la pena arriesgarlo sin haberlo probado — las
        escrituras se quedan en 0 reintentos automáticos, igual que antes.

        HTTPError (4xx/5xx CON respuesta, ver `raise_for_status()`) nunca se
        reintenta aquí — ya hubo respuesta del servidor, repetir la misma
        petición no cambiaría el resultado.
        """
        body = self._body(extra_query or {})
        attempt = 0
        while True:
            try:
                resp = self.session.post(f"{self.base_url}{path}", json=body, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.ConnectionError:
                if attempt >= max_retries:
                    raise
                time.sleep(2**attempt)
                attempt += 1

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

        Idempotente (solo lectura) -> reintenta ante fallo de conexión
        transitorio (ver `_post`, `config.FUTMONDO_READ_MAX_RETRIES`).
        """
        return self._post("/1/userteam/roster", max_retries=config.FUTMONDO_READ_MAX_RETRIES)

    def get_information(self) -> dict:
        """
        Resumen del equipo. **100% confirmado**. Respuesta real (recortada):
            {"answer": {"budget": <int>, "teamValue": <int>, "points": ...,
             "championship": <nombre>, "championshipMode": "social"|...,
             "league": {...}, "configuration": {"budget", "numberOfPlayers",
             "maxPlayersInRoster", ...}, ...}}
        `budget` es el saldo TOTAL (no descuenta pujas pendientes, igual que
        el "credit" de Comunio — ver real_pending_bid_amount()).

        `configuration.numberOfPlayers` vs. `configuration.maxPlayersInRoster`
        (a petición del usuario, 2026-08-22): son dos campos DISTINTOS que
        se confundieron en `jobs/run_market.py`/`jobs/run_sales.py` el
        mismo día que se añadió el límite de plantilla, causando que el
        bot cortara las pujas de golpe muy por debajo del máximo real de
        la liga.
          - `numberOfPlayers`: número de jugadores INICIALES de la liga
            (el reparto al crearla), NO un límite máximo -- confirmado
            inspeccionando el bundle `main.dart.js` de la propia app
            (dart2js no minifica los literales de string): el código de
            creación de liga lo inicializa a `15` por defecto.
          - `maxPlayersInRoster`: el máximo REAL de jugadores en plantilla
            ("Máximo número de jugadores en plantilla" en la pantalla de
            info de la liga) -- **confirmado con prueba manual real**
            (2026-08-22, a petición del usuario). Es este campo el que hay
            que comparar contra `len(roster)` para el límite real.
            Ojo, `configuration.playersInRoster` (sin "max") también
            existe y es fácil confundirlo con este -- primer intento de
            arreglo de este mismo bug, descartado tras la prueba manual:
            es el nombre usado en el payload de ESCRITURA al guardar la
            configuración de la liga, no el campo de esta respuesta.
        Confirmado contra una cuenta real: una liga con `numberOfPlayers=15`
        (plantilla ya en 15/15 según ese campo) tenía en realidad
        `maxPlayersInRoster=18` en su pantalla de info.

        Idempotente (solo lectura) -> reintenta ante fallo de conexión
        transitorio (ver `_post`, `config.FUTMONDO_READ_MAX_RETRIES`) — el
        propio fallo real que motivó esto (2026-08-17) fue justo en esta
        llamada, ver `_post`.
        """
        return self._post("/1/userteam/information", max_retries=config.FUTMONDO_READ_MAX_RETRIES)

    def get_lineup(self) -> dict:
        """
        Alineación actualmente guardada. **100% confirmado**. Respuesta
        real: {"answer": {"strategy": "4-4-2" (CON guiones, a diferencia de
        Comunio), "players": [{..., "position": <int 0..10>}, ...]}}.
        Ver engine/lineup_optimizer.py para el mapeo de `position`
        confirmado (solo para 4-4-2, ver TODO ahí).

        Idempotente (solo lectura) -> reintenta ante fallo de conexión
        transitorio (ver `_post`, `config.FUTMONDO_READ_MAX_RETRIES`).
        """
        return self._post("/1/userteam/lineup", max_retries=config.FUTMONDO_READ_MAX_RETRIES)

    def get_market(self) -> dict:
        """
        Jugadores disponibles en el mercado de fichajes. **100% confirmado**.
        Respuesta: {"answer": [...jugadores...]} — ver docstring del
        módulo para la forma de cada item (incluye `price`, `numberOfBids`,
        `expirationDate`).

        Idempotente (solo lectura) -> reintenta ante fallo de conexión
        transitorio (ver `_post`, `config.FUTMONDO_READ_MAX_RETRIES`).
        """
        return self._post("/1/market/players", max_retries=config.FUTMONDO_READ_MAX_RETRIES)

    def get_my_players_in_market(self) -> dict:
        """
        Jugadores propios puestos en venta. **100% confirmado**, incluida
        la forma del item con un jugador realmente listado (2026-08-18,
        TODO.md #6: Sergi Canós puesto en venta con list_for_sale() y
        releído con esta llamada antes de cancelar). Respuesta real:
            {"answer": [{"id", "name", "slug", "role", "role2", "photo",
             "points", "value", "team", "logo", "status",
             "expirationDate", "price" (el pedido, no `value`), "buyPrice",
             "isClause", "bids": [] (pujas recibidas por CLÁUSULA sobre
             este listado, no confirmado su shape con una no vacía),
             "change", "average": {...}}], ...}
        Prácticamente el mismo shape que un item de roster + los campos de
        venta (`expirationDate`, `price`, `isClause`, `bids`) que también
        trae un item de `get_market()`.

        Idempotente (solo lectura) -> reintenta ante fallo de conexión
        transitorio (ver `_post`, `config.FUTMONDO_READ_MAX_RETRIES`).
        """
        return self._post("/1/market/myplayers", max_retries=config.FUTMONDO_READ_MAX_RETRIES)

    def get_player_summary(self, player_id: str) -> dict:
        """
        Ficha de un jugador. **100% confirmado**, incluido `prices` con
        histórico real (2026-08-22, ver TODO.md #14 -- la captura anterior
        había traído esa lista vacía). Respuesta real:
            {"answer": {"data": {...igual que un item de roster/market...},
             "prices": [{"_id", "c", "s", "date", "price"}, ...] (una
             entrada por día, orden ASCENDENTE -- más antigua primero),
             "points": [...], "championship": {...}, ...}}
        `prices` es el histórico de VALOR DE MERCADO día a día — `date` es
        ISO-8601 con milisegundos y `Z` (igual que `expirationDate`/
        `creationDate`, NO epoch) y `price` SÍ es el VM diario (coincide
        exacto con `value` del roster/market del mismo jugador en la misma
        fecha). Consumido por `engine.selling_strategy.
        compute_revaluation_premium_pct()`. `c`/`s` siguen sin confirmar
        qué representan (no se usan).

        Idempotente (solo lectura) -> reintenta ante fallo de conexión
        transitorio (ver `_post`, `config.FUTMONDO_READ_MAX_RETRIES`).
        """
        return self._post("/1/player/summary", {"playerId": player_id}, max_retries=config.FUTMONDO_READ_MAX_RETRIES)

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
        real_pending_bid_amount() para cómo afecta esto al seguimiento de
        pujas pendientes.

        OJO, confirmado en vivo (2026-08-18, ver real_pending_bid_amount()):
        llamar dos veces sobre el MISMO jugador mientras la primera puja
        sigue abierta responde igualmente `"api.general.ok"` (no lanza
        FutmondoOfferError), pero Futmondo NO actualiza el precio de la
        puja ya abierta — el importe real que se pagaría si se gana el
        listado se queda en el de la primera llamada, pase lo que pase en
        la segunda. No hay forma de "subir" una puja propia ya colocada;
        `jobs/run_market.py` evita esta llamada por completo cuando ya hay
        una puja local `'placed'` sobre ese jugador, en vez de confiar en
        el código de respuesta para detectar el no-op.
        """
        result = self._post(
            "/1/market/bid",
            {"player_slug": player_slug, "player_id": player_id, "price": amount, "isClause": is_clause},
        )
        return self._check_ok(result)

    def cancel_bid(self, bid_id: str) -> dict:
        """
        Cancela una puja de compra propia todavía abierta (la contrapartida
        de `place_bid()` — antes de esto, `place_bid()` no se podía "deshacer"
        de ninguna forma conocida). **100% confirmado** (2026-08-18, TODO.md
        #13): NO estaba documentado en ninguna referencia (ni captura previa
        propia ni vicenteqa/futmondo-utils) — se encontró inspeccionando la
        UI real con permiso explícito del usuario, no adivinando el endpoint.

            POST /1/market/cancelbid
            body.query: {..., "bid": <bid_id>}
            respuesta real: {"answer": {"code": "api.general.ok"}, ...}

        `bid_id`: el id de la PUJA, no el `player_id` (a diferencia de
        `place_bid`/`cancel_sale`) — sale del campo `"bid": {"id": ..., "price":
        ...}` que trae cada item de `get_market()` en el que tenemos una oferta
        pendiente (ver `real_pending_bid_amount()`).

        Confirmado en vivo cancelando una puja real (1.072.021€ sobre un
        jugador propiedad del "Computer") desde la UI con captura de red:
        tras cancelar, el importe correspondiente desapareció de `"Ofertas"`
        (compromiso total) y de `real_pending_bid_amount()` en la siguiente
        lectura de `get_market()`. Efecto secundario observado en la misma
        prueba: al quedar el jugador libre de nuevo, el cron real de
        `run_market` (ejecutándose en paralelo sobre la cuenta real) volvió a
        pujar por él con un importe distinto en la siguiente pasada — prueba
        en vivo de que `jobs/run_market.py` sí vuelve a considerar cualquier
        jugador en cuanto deja de tener una puja local `'placed'` (ver
        docstring de `place_bid`).

        Lanza FutmondoOfferError si `answer.code` no es "api.general.ok" —
        no se ha observado en la prueba real qué código devuelve si se
        intenta cancelar una puja que ya no existe (ganada, perdida, o
        cancelada antes).
        """
        result = self._post("/1/market/cancelbid", {"bid": bid_id})
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
        Quita un jugador propio del mercado de ventas. **100% confirmado**
        (resuelve TODO.md #6, 2026-08-18): puesto en venta de verdad un
        jugador de la plantilla real (Sergi Canós, lesionado, mínimo valor
        de la plantilla — elegido para minimizar impacto si alguien
        hubiera pujado en el intervalo), releído con
        get_my_players_in_market() confirmando el listado real, cancelado
        con esta llamada y verificado con get_my_players_in_market()
        (vacío de nuevo) + get_roster() (jugador de vuelta en plantilla,
        `market: false`, idéntico al estado previo) — sin que nadie llegara
        a pujar por él en el intervalo.

            POST /1/market/cancelsell
            body.query: {..., "player_id": <id>}
            respuesta real: {"answer": {"code": "api.general.ok"}, ...}

        Lanza FutmondoOfferError si `answer.code` no es "api.general.ok"
        (mismo patrón que el resto de escrituras de este cliente) — no se
        ha observado en la prueba real qué código devuelve si se intenta
        cancelar un listado que ya no existe (vendido o ya cancelado antes).
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

        **Tercer hallazgo real el mismo día (2026-08-17)**: sustituir un
        slot ocupado incluyendo `"from"` funciona bien cuando el jugador
        que ENTRA no está ya en el campo en otra posición — pero si SÍ lo
        está (una rotación entre varios jugadores ya colocados, ej. A pasa
        a la posición de B y B a la de C), la API rechaza esos cambios con
        `"api.error.in_field"` ("este jugador ya está en el campo"), aunque
        cada `change` individual lleve su `"from"` correcto.

        `engine.lineup_optimizer.build_lineup_changes()` ya evita por
        construcción la rotación falsa entre titulares que ya estaban bien
        colocados (nunca genera `change` para ellos). Para el caso que
        queda — un titular nuevo de esta semana que ya está en el campo en
        el slot de OTRO grupo de posición (jugador "multiposition") —
        manda primero un `change` intermedio al banquillo (numeración
        CONFIRMADA, ver `BENCH_SLOT_BY_POSITION` en
        engine/lineup_optimizer.py) y después el `change` final, que así
        entra desde el banquillo (el caso confirmado). Esto asume, sin
        confirmar todavía con una prueba real, que un jugador del campo SÍ
        puede mandarse a un slot de banquillo vacío — si fuera falso, ese
        `change` intermedio fallaría (auditado igual que cualquier otro,
        no en silencio) y el titular se quedaría sin colocar esa jornada.
        Si el slot de banquillo de esa posición ya está ocupado por otro
        jugador, `build_lineup_changes()` NO intenta resolverlo (encadenar
        más cambios sin confirmar sería escribir a ciegas contra una
        cuenta real) — deja al titular sin colocar y lo reporta en
        `conflicts`. `change_lineup()` audita el fallo de cada jugador
        afectado (`answer_or_error` trae literalmente
        `"api.error.in_field"`) en vez de fallar en silencio, así que el
        síntoma queda siempre visible aunque el paso intermedio no
        funcionara como se espera.

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
