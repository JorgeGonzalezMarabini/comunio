"""
Job: identifica jugadores de la plantilla con plusvalía suficiente y los
pone en venta. Vender es una fuente clave de ingresos en Futmondo, igual
que en Comunio (ver README, "Economía") — sin esto, el presupuesto del bot
solo puede reducirse con el tiempo, nunca crecer.

No es venta instantánea (ver clients/futmondo_client.py: list_for_sale):
poner en venta solo deja al jugador listado, visible para que otro manager
(o el "Computer") lo compre. jobs/sync_data.py reconcilia después si la
venta se completó (comparando la plantilla en cada sync).

Aceptar ofertas recibidas (TODO.md #15, resuelto en vivo 2026-08-22):
Futmondo NO vende automáticamente al mejor postor -- otros managers hacen
OFERTAS sobre el jugador listado, y hace falta ACEPTAR una explícitamente
para completar la venta (confirmado en vivo, dos veces, con
`FutmondoClient.accept_sale_offer()` -- ver su docstring). Por eso, ANTES
de decidir nuevos listados, este job llama a `_process_received_offers()`:
lee `get_my_players_in_market()[].bids` y acepta SIEMPRE la oferta más
alta que SUPERE el precio de salida pedido, o que quede dentro de un
margen de tolerancia por debajo de ese precio (criterio del usuario,
2026-08-22, ampliado 2026-08-28 -- ver `config.SELLING_OFFER_ACCEPTANCE_MARGIN`:
una puja "muy cercana" al precio pedido se acepta igual, en vez de dejar
el listado esperando indefinidamente a que alguien iguale o supere la
cifra exacta pedida). Si ni siquiera la mejor oferta entra dentro de ese
margen, el listado se deja tal cual esperando una mejor.

Solo ofertas de Futmondo, nunca de otro manager (a petición del usuario,
2026-08-28): de las ofertas anteriores, se ignoran por completo las que
vienen de OTRO MANAGER real -- solo se evalúa/acepta la mejor oferta que
identifique como puesta por el propio Futmondo/"Computer" (ver
`_is_futmondo_offer()`). Se siguen registrando TODAS las ofertas vistas en
`received_sale_offers` (para auditoría/análisis), pero una oferta de otro
manager nunca se acepta automáticamente, por alta que sea.

Riesgo de plantilla conjunto AL ACEPTAR, no solo al listar (a petición del
usuario, 2026-08-29, caso real: Musso y Ionuț Radu, los dos únicos
porteros con plusvalía/oportunidad, listados el mismo día por DOS vías
independientes de `decide_sales()` -- corte de pérdidas y oportunidad de
mercado, ver docstring de `engine/selling_strategy.py`, que no se limitan
entre sí). `decide_sales()` ya evita crear un NUEVO listado que deje una
posición sin margen de suplentes sanos, pero eso protege solo el momento
de LISTAR -- el paso que de verdad concreta la venta es ACEPTAR una
oferta (TODO.md #15), y hasta ahora `_process_received_offers()` evaluaba
cada listado de forma aislada: si dos listados de la MISMA posición
tenían ambos una oferta cualificada en la misma pasada, se aceptaban los
dos sin que ninguno supiera del otro, pudiendo dejar la posición sin
margen real (o peor, por debajo de los titulares requeridos) de golpe.

Por eso, antes de aceptar nada, se agrupan TODOS los candidatos con
oferta cualificada por posición (misma `FUTMONDO_POSITION_MAP` que
`engine/selling_strategy.py`) y se calcula el margen de banquillo REAL de
la plantilla ahora mismo (`engine.squad_risk.assess_squad_depth()`, pero
sin excluir a los ya puestos en venta -- aquí SÍ cuentan como
"disponibles": siguen físicamente en la plantilla hasta que se acepte de
verdad una oferta sobre ellos). Dentro de cada posición se ordena a los
candidatos por rentabilidad (más rentable primero, mismo criterio que
`decide_sales()`; sin referencia de compra local, al final). Sin
`get_roster()` (vacío, o jugador sin posición reconocible en la
plantilla actual), este chequeo queda desactivado para ese candidato,
permisivo por defecto -- igual que el resto de refinamientos opcionales
de este módulo.

Umbral MÁS ESTRICTO que el de listar, y excepción de "swap" (a petición
del usuario, 2026-08-29, ver docstring de `config.
SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT`): `decide_sales()` permite LISTAR
hasta dejar una posición en bench=0 (justo los titulares que exige la
alineación, sin margen) -- pero aceptar es lo que de verdad concreta la
venta, así que aquí el umbral es más conservador: se exige bench > 1
ANTES de aceptar (bench_después >= 1, al menos un suplente sano) salvo
que sea un "swap" verificado en marcha, el ÚNICO caso en que se permite
bajar a bench=0 al aceptar -- nunca se acepta si eso dejara la posición
en déficit real (bench <= 0 antes de aceptar), swap o no, eso NUNCA se
permite. Excepción para lesión/duda (ver docstring de
`_process_received_offers()`): un candidato ya lesionado/en duda nunca se
bloquea por este chequeo -- ya estaba excluido de "disponible" antes de
ponerlo en venta, así que aceptar su oferta no resta cobertura real.

"Swap": una venta que calificó ÚNICAMENTE por "oportunidad de mercado"
(`only_market_reason` en `engine/selling_strategy.py`) ya identifica, al
listar, un candidato de mercado concreto que la motivó -- desde este
cambio se persiste en `db.models.sale_swap_targets` (ver
`save_swap_target()`, llamado justo después de listar con éxito). Al
aceptar, si ese candidato (o uno equivalente, ver `_resolve_swap_target()`
más abajo) sigue realmente listado en el mercado AHORA MISMO
(`FutmondoClient.get_market()`, en vivo -- un swap nunca se fía del
snapshot local, que puede llevar horas sin sincronizar) con más de
`config.SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT` (1h por defecto) antes de
expirar, el swap se considera "en marcha" y se permite aceptar aunque
deje bench=0 -- ese margen de tiempo da hueco a que corra
`jobs/run_market.py` (cron aparte) y pueda pujar de verdad por él antes
de que el listado desaparezca.

Si el candidato original YA NO tiene ese margen (vendido, expirado, o a
punto de expirar), `_resolve_swap_target()` no da el swap por muerto sin
más: reevalúa el mercado abierto AHORA MISMO buscando un equivalente en
la misma posición (misma comparación de score que decidió el swap
original, `engine.selling_strategy.score_market_upgrade_candidates()`,
pero restringida a candidatos que SÍ siguen listados en vivo con margen
de tiempo suficiente) -- si lo encuentra, retargetea el swap
(`db.models.retarget_swap_target()`, guarda el nuevo objetivo sin perder
el original para auditoría) y se sigue aceptando: a todos los efectos, el
swap sigue en marcha, solo cambió de objetivo concreto. Si no encuentra
ningún equivalente, el swap se da por muerto y se aplica el umbral
estricto de arriba como a cualquier otra venta -- la oferta se deja sin
aceptar esta pasada.

Cada oferta vista (aceptada o no) se registra en `db.models.
received_sale_offers` -- pensado para analizar más adelante, con datos
reales acumulados, si el `asking_price` que calcula
`engine/selling_strategy.py` es realista frente a lo que el mercado
realmente ofrece (¿nos quedamos cortos? ¿pedimos de más y nunca llega
oferta?), y ajustar el cálculo si hace falta. Ver docstring de esa tabla
en `db/models.py`.

Igual que en Comunio, solo se consideran candidatos los jugadores
"comprados por el bot" — aquí, en vez de un campo de Futmondo (`buyPrice`
no sirve para distinguir origen, ver TODO.md #4/engine/selling_strategy.py),
se usa el registro LOCAL de pujas ganadas (`db.models.get_won_bid_prices()`)
como fuente de verdad de qué se compró y a qué precio.

Prima por revalorización rápida (config.ENABLE_SELLING_REVALUATION_PREMIUM,
activado por defecto desde que se confirmó en vivo el formato de
get_player_summary() -- ver docstring en config.py, TODO.md #14 y
engine.selling_strategy.compute_revaluation_premium_pct/
apply_revaluation_premium): si está activo, por cada candidato ya decidido
se pide el histórico diario de VM (FutmondoClient.get_player_summary()) y
se sube `asking_price` por encima del VM cuando hay una subida sostenida
reciente. Una llamada de red por candidato (no por toda la plantilla), y
nunca bloquea la venta -- si la llamada falla o no hay histórico, se pide
el VM tal cual, como si el flag estuviera apagado.

Oportunidad de mercado / plaza escasa (a petición del usuario, 2026-08-22,
tras el límite de plantilla de jobs/run_market.py -- ver docstring de
engine/selling_strategy.py): además de las tres vías de rentabilidad, este
job pasa a decide_sales() las features de la plantilla propia y del
mercado abierto ahora mismo (`db.models.get_player_features()`, mismos
datos que usa jobs/run_market.py) para que pueda vender también a un
suplente mediocre -- sin pérdida ni la plusvalía mínima -- si el mercado
ofrece ahora mismo alguien claramente mejor en su misma posición. Esto NO
libera el hueco al instante (ver arriba, poner en venta no es venta
instantánea): es una vía para mantener la plantilla más líquida de cara al
futuro, no para resolver una oportunidad concreta en la misma pasada --
por eso NUNCA hay que intentar "vender y pujar ya" en el mismo run: la
puja nueva fallaría igual por api.market.max_number_players_in_roster
mientras la venta no se haya resuelto de verdad.

Refinamientos de esa misma vía (a petición del usuario, 2026-08-23, caso
real: el mismo día se vendieron a la vez Raba+Brugué por DEL y
Camavinga+Dieng por MED, cada pareja justificada por un ÚNICO mejor
candidato de mercado en su posición -- de ese candidato solo se puede
fichar uno, ver docstring de engine/selling_strategy.py para el detalle
completo de los cuatro filtros): límite de una venta por posición y
ejecución por esta vía, eficiencia marginal de precio/score (no comparar
un jugador de 4M con uno de 50M solo por diferencia de score bruto),
presupuesto compartido entre posiciones si varias compiten a la vez, y
ventana de tiempo del listado objetivo. Este job pasa además dos datos
EN VIVO nuevos, ninguno disponible en el snapshot local de
`get_player_features()`: `expirationDate` real de cada listado de
mercado (de `FutmondoClient.get_market()`, para el filtro de tiempo) y la
alineación TITULAR guardada ahora mismo (de `FutmondoClient.get_lineup()`,
para el bloqueo de fin de semana). Un fallo de red en cualquiera de las
dos NO bloquea el resto del job -- ese refinamiento concreto queda
desactivado dentro de `decide_sales()`, igual que el resto de datos
opcionales de este módulo.
"""
from datetime import datetime, timedelta, timezone

import requests

import config
from clients.futmondo_client import FUTMONDO_POSITION_MAP, FutmondoClient, FutmondoOfferError, is_injury_status
from db.models import (
    init_db,
    get_connection,
    get_player_features,
    get_purchase_baselines,
    get_recent_price_history,
    get_swap_target_for_player,
    get_won_bid_prices,
    mark_offer_accepted,
    record_received_offer,
    retarget_swap_target,
    save_swap_target,
)
from engine.selling_strategy import apply_revaluation_premium, decide_sales, parse_iso_datetime, score_market_upgrade_candidates
from engine.squad_risk import assess_squad_depth
from notifier import notify, track_job_run, format_number


def _persist_sale(conn, decision: dict, status: str, now: str, error: str | None = None) -> int:
    """
    Devuelve el `id` real de la fila insertada -- hace falta para `save_swap_target()` (ver `run()`).

    `error`: mensaje real del fallo (`str(excepción)`) cuando `status` es
    'failed' -- mismo fix que TODO.md #18 aplicó a `bids.error` (ver
    jobs/run_market.py._persist_bid): antes solo se guardaba
    `decision["reason"]` (la justificación de NEGOCIO de por qué
    decide_sales() eligió vender, no el motivo por el que Futmondo rechazó
    el listado), así que un fallo repetido (caso real: Galarreta, 14
    intentos fallidos seguidos el 2026-09-01/02 antes de resolverse) no se
    podía diagnosticar desde la BD después de los hechos -- solo vivía en
    el mensaje de Telegram de esa pasada concreta.
    """
    cur = conn.execute(
        """
        INSERT INTO sales (player_id, asking_price, purchase_price, profit, profit_pct, status, reason, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(decision["player_id"]),
            decision["asking_price"],
            decision["purchase_price"],
            decision["profit"],
            decision["profit_pct"],
            status,
            decision["reason"],
            error,
            now,
        ),
    )
    return cur.lastrowid


def _is_futmondo_offer(bid: dict) -> bool:
    """
    Distingue una oferta puesta por el propio Futmondo ("Computer") de una
    de OTRO MANAGER real, dentro de `bids[].userTeam` (a petición del
    usuario, 2026-08-28: solo se aceptan automáticamente ofertas de
    Futmondo, nunca de otro manager -- ver docstring del módulo).

    Futmondo no documenta ningún campo para esto en `bids[]` (a diferencia
    de `computer: bool` en `get_market()`, que identifica al PROPIETARIO
    actual de un jugador del mercado de fichajes -- un concepto distinto,
    no quién puja sobre los tuyos puestos en venta). Es una heurística
    empírica sobre datos reales de producción (`received_sale_offers`,
    revisados 2026-08-28): las ofertas de otros managers reales siempre
    traen `userTeam.name`/`userTeam.slug` NO vacíos (p.ej.
    "Javier Mediavilla"/"javier-mediavilla"), mientras que las que no
    tienen manager real identificado llegan con ambos campos como cadena
    vacía "". No está confirmado visualmente en la UI de Futmondo (a
    diferencia del resto de mecanismos de este módulo, TODO.md #15) --
    revisar si aparece algún caso real que la contradiga.
    """
    user_team = bid.get("userTeam") or {}
    return not (user_team.get("name") or "").strip() and not (user_team.get("slug") or "").strip()


def _position_by_player_id(roster_items: list[dict]) -> dict[str, str]:
    """Mapa id (string) -> posición normalizada (POR/DEF/MED/DEL) a partir de `get_roster()`."""
    return {str(p["id"]): FUTMONDO_POSITION_MAP.get(p.get("role"), p.get("role")) for p in roster_items}


def _bench_by_position_now(roster_items: list[dict]) -> dict[str, int]:
    """
    Margen de banquillo REAL de la plantilla ahora mismo, por posición (ver
    docstring del módulo, "Riesgo de plantilla conjunto AL ACEPTAR").
    Reutiliza `engine.squad_risk.assess_squad_depth()`, pero forzando
    `market`/`on_market` a False para TODOS los jugadores: a diferencia de
    su uso en `decide_sales()` (donde un jugador ya listado no cuenta como
    "disponible", porque ahí se está decidiendo si crear un listado NUEVO),
    aquí un jugador ya listado SIGUE físicamente en la plantilla hasta que
    se acepte de verdad una oferta sobre él, así que sí debe contar.

    Vacío si `roster_items` viene vacío -- sin plantilla que evaluar, este
    chequeo queda desactivado (permisivo) para quien llame.
    """
    if not roster_items:
        return {}
    normalized = [
        {**p, "position": FUTMONDO_POSITION_MAP.get(p.get("role"), p.get("role")), "market": False, "on_market": False}
        for p in roster_items
    ]
    depth = assess_squad_depth(normalized)
    return {position: info["bench"] for position, info in depth.items()}


def _has_enough_runway(player_id, live_market_expirations: dict[str, str], now: datetime, min_hours: float) -> bool:
    """
    True si `player_id` sigue listado AHORA MISMO en el mercado
    (`live_market_expirations`, de `FutmondoClient.get_market()` en vivo --
    nunca el snapshot local, que puede llevar horas sin sincronizar) con
    más de `min_hours` antes de expirar. Sin dato real de vigencia (no
    aparece en el mercado en vivo, o `expirationDate` no parseable) ->
    False: un swap nunca se da por vigente sobre un dato que no se puede
    confirmar.
    """
    expiration = parse_iso_datetime(live_market_expirations.get(str(player_id)))
    if expiration is None:
        return False
    return (expiration - now) > timedelta(hours=min_hours)


def _resolve_swap_target(
    swap_target: dict,
    position: str,
    own_squad_features: list[dict],
    market_candidates: list[dict],
    live_market_expirations: dict[str, str],
    now: datetime,
) -> bool:
    """
    Comprueba si un swap sigue "en marcha" para poder aceptar una oferta
    que dejaría su posición en bench=0 (ver docstring del módulo,
    "Umbral MÁS ESTRICTO... y excepción de swap"). `swap_target`: fila de
    `db.models.get_swap_target_for_player()` (sale_id/player_id/
    target_player_id/target_price/original_target_player_id).

    1. Si el candidato VIGENTE (`target_player_id`, el original o uno ya
       retargeteado antes) sigue listado en vivo con margen de tiempo
       suficiente (`_has_enough_runway()`, `config.
       SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT`) -> swap en marcha, True.
    2. Si no, se reevalúa el mercado ABIERTO AHORA MISMO buscando un
       equivalente en la misma posición: mismo score de alineación que
       decidió el swap original (`engine.selling_strategy.
       score_market_upgrade_candidates()`), pero restringido a candidatos
       que SÍ siguen listados en vivo con margen de tiempo suficiente (para
       no proponer un retargeteo sobre un candidato igual de agotado). Si
       aparece uno que además supera `config.
       SELLING_UPGRADE_AVAILABLE_MIN_MARGIN` sobre el score propio (mismo
       umbral que exigió el swap original) y no es el mismo candidato ya
       descartado, se persiste (`db.models.retarget_swap_target()`) y se
       devuelve True -- a todos los efectos, el swap sigue en marcha, solo
       cambió de objetivo concreto.
    3. Si tampoco hay equivalente, el swap se da por muerto -- False.

    Sin `own_squad_features`/`market_candidates` (DB no sincronizada), no
    se puede confirmar ningún equivalente -- se prefiere el swap muerto
    (False) antes que aceptar a ciegas, a diferencia del resto de
    refinamientos "opcionales" de este módulo (aquí sí es el propio
    chequeo de riesgo, no un extra).
    """
    min_hours = config.SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT
    if _has_enough_runway(swap_target["target_player_id"], live_market_expirations, now, min_hours):
        return True  # el objetivo (original o ya retargeteado antes) sigue vigente

    live_candidates = [
        c for c in market_candidates if _has_enough_runway(c["id"], live_market_expirations, now, min_hours)
    ]
    own_scores, best_by_position = score_market_upgrade_candidates(own_squad_features, live_candidates)
    own_score = own_scores.get(str(swap_target["player_id"]))
    replacement = best_by_position.get(position)
    if (
        own_score is None
        or replacement is None
        or str(replacement["id"]) == str(swap_target["target_player_id"])  # el mismo agotado, no vale
        or (replacement["score"] - own_score) < config.SELLING_UPGRADE_AVAILABLE_MIN_MARGIN
    ):
        return False  # ningún equivalente disponible con margen suficiente -- el swap se da por muerto

    retarget_swap_target(
        swap_target["sale_id"], str(replacement["id"]), replacement.get("price") or 0, now.isoformat()
    )
    return True


def _process_received_offers(
    client: FutmondoClient,
    seen_at: str,
    position_by_player_id: dict[str, str],
    bench_by_position: dict[str, int],
    bought_by_bot: dict[str, int],
    own_squad_features: list[dict],
    market_candidates: list[dict],
    live_market_expirations: dict[str, str],
    now: datetime,
) -> tuple[list[dict], list[tuple[dict, str]], list[dict]]:
    """
    Lee las ofertas de compra recibidas sobre jugadores propios puestos en
    venta NORMAL (`client.get_my_players_in_market()[].bids`) y acepta
    SIEMPRE la oferta más alta -- SOLO entre las de Futmondo
    (`_is_futmondo_offer()`, ver docstring del módulo) -- que SUPERE el
    precio de salida pedido, o que quede dentro del margen de tolerancia
    `config.SELLING_OFFER_ACCEPTANCE_MARGIN` por debajo de ese precio (a
    petición del usuario, 2026-08-28: una puja muy cercana al precio
    pedido se acepta igual, en vez de dejar el listado esperando
    indefinidamente a que alguien lo iguale o supere exactamente). Si no
    hay ninguna oferta de Futmondo, o ninguna entra dentro de ese margen,
    el listado se deja tal cual; las ofertas de otro manager real nunca se
    aceptan automáticamente, por alta que sea su cuantía.

    Ignora listados de CLÁUSULA (`isClause: true`) -- fuera del alcance
    confirmado de `accept_sale_offer()` (ver su docstring): esos usan un
    mecanismo de compra distinto (`pay_clause()`), sin oferta que aceptar.

    Registra TODAS las ofertas vistas (aceptadas o no, de Futmondo o de
    otro manager) en `db.models.received_sale_offers`, una sola vez cada
    una por su id real de Futmondo -- ver `record_received_offer()`. Solo
    se marca `accepted` tras confirmar éxito real de `accept_sale_offer()`,
    nunca antes (si la llamada falla, la oferta queda registrada pero sin
    marcar).

    Riesgo de plantilla conjunto por posición, umbral estricto y excepción
    de swap (a petición del usuario, 2026-08-29, ver docstring del
    módulo): las ofertas que cualifican para aceptarse (de Futmondo, dentro
    de precio/margen) NO se aceptan una a una según se recorren los
    listados -- primero se juntan TODAS, se ordenan dentro de cada
    posición por rentabilidad (más rentable primero; sin referencia de
    compra local, al final) y se evalúan en ese orden contra
    `bench_by_position` (se decrementa según se aprueba cada una, para que
    la siguiente de la misma posición ya vea el margen real que
    quedaría):
      - bench <= 0 (déficit real si se acepta) -> NUNCA se acepta, swap o
        no.
      - bench == 1 (dejaría la posición en bench=0, sin ningún suplente
        sano) -> solo se acepta si hay un swap verificado en marcha para
        ESE jugador (`db.models.get_swap_target_for_player()` +
        `_resolve_swap_target()`).
      - bench >= 2 -> se acepta sin más, queda al menos un suplente.
    `position_by_player_id`/`bench_by_position` vacíos o sin la posición
    de un candidato concreto -> ese candidato no se bloquea (permisivo,
    sin dato para evaluar el riesgo).

    Excepción para lesión/duda (bug real detectado 2026-09-11, a petición
    del usuario: oferta sobre un jugador YA lesionado bloqueada por
    "riesgo de banquillo" que no era real): `bench_by_position` sale de
    `_bench_by_position_now()` -> `assess_squad_depth()`, que YA excluye a
    los lesionados/en duda de "disponible" con independencia de si están
    listados (ver docstring de esa función) -- así que un candidato
    lesionado/en duda NUNCA contaba como protección real de la posición, y
    aceptar su oferta no empeora la cobertura real ni debe descontar
    margen después, exactamente el mismo principio que
    `engine.selling_strategy.decide_sales()` ya aplicaba al LISTAR (ver su
    docstring: "un lesionado nunca contó como disponible, así que no hace
    falta descontar margen por él"). Antes de este fix, el chequeo bench
    <= 1 y el decremento posterior se aplicaban por igual a TODOS los
    candidatos, lesionados incluidos -- bloqueando ofertas reales sin
    motivo, y pudiendo además descontar margen fantasma que afectaba a
    otros candidatos SANOS de la misma posición procesados después en la
    misma pasada. Ahora, con `is_injury_status(item.get("status"))`, un
    candidato lesionado/en duda se acepta siempre que cualifique por
    precio (nunca se bloquea por bench, nunca decrementa `bench_by_position`).

    Devuelve (aceptadas, fallidas, omitidas_por_riesgo) para el resumen de
    notificación de `run()`. Un fallo al aceptar una oferta concreta (red
    o rechazo de negocio) no aborta el resto -- se audita en `fallidas` y
    se sigue con el resto de listados.
    """
    listings = client.get_my_players_in_market().get("answer", [])
    accepted, failed = [], []
    qualifying = []  # (item, best_bid) -- pendientes del chequeo de riesgo por posición

    for item in listings:
        if item.get("isClause"):
            continue
        bids = item.get("bids") or []
        if not bids:
            continue

        listing_price = item["price"]

        for bid in bids:
            record_received_offer(
                player_id=item["id"],
                futmondo_bid_id=bid["id"],
                listing_price=listing_price,
                offer_price=bid["price"],
                bidder_name=(bid.get("userTeam") or {}).get("name"),
                bidder_slug=(bid.get("userTeam") or {}).get("slug"),
                seen_at=seen_at,
            )

        futmondo_bids = [b for b in bids if _is_futmondo_offer(b)]
        if not futmondo_bids:
            continue  # ninguna oferta es de Futmondo -- se ignoran las de otro manager

        best = max(futmondo_bids, key=lambda b: b["price"])
        acceptance_threshold = listing_price * (1 - config.SELLING_OFFER_ACCEPTANCE_MARGIN)
        if best["price"] < acceptance_threshold:
            continue  # ni supera lo pedido ni queda dentro del margen de tolerancia -- se deja listado tal cual

        qualifying.append((item, best))

    def _sort_key(entry):
        item, best = entry
        purchase_price = bought_by_bot.get(str(item["id"])) or 0
        if purchase_price <= 0:
            return (1, 0.0)  # sin referencia de compra local -- se procesa al final
        return (0, -((best["price"] - purchase_price) / purchase_price))  # más rentable primero

    qualifying.sort(key=_sort_key)

    skipped_for_risk = []
    for item, best in qualifying:
        position = position_by_player_id.get(str(item["id"]))
        bench = bench_by_position.get(position) if position is not None else None

        # Un jugador YA lesionado/en duda no contaba como "disponible" al
        # calcular `bench_by_position` (`_bench_by_position_now()` ->
        # `assess_squad_depth()`, ver docstring de esa función: excluye
        # lesión/duda con independencia de si está listado) -- así que
        # aceptar una oferta sobre él no empeora la cobertura real de la
        # posición, ni hay que descontarle margen después (mismo principio
        # que `engine.selling_strategy.decide_sales()`, ver su docstring:
        # "un lesionado nunca contó como disponible, así que no hace falta
        # descontar margen por él"). Sin esto, se bloqueaba/descontaba
        # igual que si fuera un suplente sano real.
        is_injured_or_doubtful = is_injury_status(item.get("status"))

        if not is_injured_or_doubtful and bench is not None and bench <= 1:
            swap_target = None if bench <= 0 else get_swap_target_for_player(str(item["id"]))
            swap_in_progress = swap_target is not None and _resolve_swap_target(
                swap_target, position, own_squad_features, market_candidates, live_market_expirations, now
            )
            if not swap_in_progress:
                skipped_for_risk.append(item)
                continue  # dejaría la posición sin margen (o en déficit real) y no hay swap que lo justifique

        try:
            client.accept_sale_offer(str(best["id"]), str(item["id"]))
            mark_offer_accepted(best["id"])
            if not is_injured_or_doubtful and position is not None and position in bench_by_position:
                bench_by_position[position] -= 1  # para que el siguiente candidato de la misma posición lo vea
            accepted.append(
                {
                    "player_id": item["id"],
                    "name": item.get("name"),
                    "listing_price": item["price"],
                    "offer_price": best["price"],
                    "bidder": (best.get("userTeam") or {}).get("name"),
                }
            )
        except (requests.RequestException, FutmondoOfferError) as e:
            failed.append((item, str(e)))

    return accepted, failed, skipped_for_risk


def run():
    if not config.ENABLE_BOT:
        print("run_sales: ENABLE_BOT=false, no se ejecuta.")
        return
    # Columnas nuevas del esquema (p. ej. futmondo_snapshots.recent_points)
    # antes de consultar features, aunque este job corra antes que sync_data.
    init_db()

    client = FutmondoClient()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    report_lines = []

    # Roster, precios de compra del bot, features de plantilla/mercado y
    # vigencia en vivo del mercado -- TODO esto ANTES de procesar ofertas
    # (a diferencia de antes, que se procesaban las ofertas primero): hace
    # falta para el chequeo de riesgo de plantilla conjunto por posición al
    # ACEPTAR, swaps incluidos (ver docstring del módulo y de
    # `_process_received_offers()`/`_resolve_swap_target()`). Si el roster
    # viene vacío, los mapas de posición/banquillo quedan vacíos -- ese
    # chequeo se desactiva (permisivo), igual que antes de este cambio.
    roster = client.get_roster()
    roster_items = roster.get("answer", [])
    bought_by_bot = get_won_bid_prices()

    # Trailing-stop, confirmación de tendencia del corte de pérdidas y nota
    # de puntos ya extraídos (a petición del usuario, evaluación del
    # trigger de venta 2026-09-11, ver docstring de
    # engine/selling_strategy.py) -- histórico 100% LOCAL
    # (`futmondo_snapshots`, recogido por cada pasada de jobs/sync_data.py),
    # sin ninguna llamada de red adicional. Un fallo aquí (BD no
    # disponible) no debería tumbar el job entero, pero sí es una
    # dependencia local (a diferencia de la prima de revalorización, que
    # depende de la red) -- si falla, decide_sales() simplemente recibe
    # {} y esas tres vías/nota quedan desactivadas esta pasada, igual que
    # si el llamador no las pasara.
    purchase_baselines = get_purchase_baselines()
    recent_price_history = get_recent_price_history(
        list(bought_by_bot.keys()),
        max(config.SELLING_LOSS_CONFIRMATION_LOOKBACK_DAYS, config.SELLING_PROFIT_MOMENTUM_LOOKBACK_DAYS),
    )

    # Oportunidad de mercado / swaps (a petición del usuario, 2026-08-22 y
    # 2026-08-29, ver docstring de engine/selling_strategy.py) -- features
    # CRUDAS de la plantilla propia y del mercado abierto AHORA MISMO
    # (mismo formato que usa jobs/run_market.py). Si algo falla o viene
    # vacío, esta vía/el retargeteo de swaps quedan desactivados sin más --
    # nunca bloquean el resto del job.
    roster_ids = {str(p["id"]) for p in roster_items}
    all_players = get_player_features(only_on_market=False)
    own_squad_features = [p for p in all_players if p["id"] in roster_ids]
    market_candidates = get_player_features(only_on_market=True)

    # Vigencia EN VIVO de cada listado de mercado (para el refinamiento
    # "ventana de tiempo" de la vía 5 de decide_sales(), y para comprobar
    # si un swap sigue en marcha al aceptar, ver `_resolve_swap_target()`)
    # -- llamada en vivo aparte de get_player_features(): ese snapshot
    # local (futmondo_snapshots) NO guarda `expirationDate`, solo la
    # llamada real a get_market() lo trae. Un fallo de red aquí no bloquea
    # nada -- decide_sales()/_resolve_swap_target() tratan un player_id sin
    # entrada aquí como "sin dato", esos refinamientos quedan desactivados
    # para ese candidato.
    try:
        market_listing_expirations = {
            str(p["id"]): p.get("expirationDate") for p in client.get_market().get("answer", [])
        }
    except requests.RequestException:
        market_listing_expirations = {}

    offers_accepted, offers_failed, offers_skipped_for_risk = _process_received_offers(
        client,
        now,
        _position_by_player_id(roster_items),
        _bench_by_position_now(roster_items),
        bought_by_bot,
        own_squad_features,
        market_candidates,
        market_listing_expirations,
        now_dt,
    )
    if offers_accepted:
        report_lines.append(f"{len(offers_accepted)} oferta(s) recibida(s) ACEPTADA(S):")
        for o in offers_accepted:
            report_lines.append(
                f"  - jugador {o['player_id']} ({o.get('name')}): oferta {o['offer_price']} de "
                f"{o.get('bidder')} (pedíamos {o['listing_price']})"
            )
    if offers_failed:
        report_lines.append(f"{len(offers_failed)} oferta(s) recibida(s) fallida(s) al aceptar:")
        for item, err in offers_failed:
            report_lines.append(f"  - jugador {item.get('id')} ({item.get('name')}): {err}")
    if offers_skipped_for_risk:
        report_lines.append(
            f"{len(offers_skipped_for_risk)} oferta(s) recibida(s) NO aceptada(s) para no dejar su posición sin "
            "margen de suplentes sanos (se deja el listado, se reevalúa en la próxima pasada):"
        )
        for item in offers_skipped_for_risk:
            report_lines.append(f"  - jugador {item.get('id')} ({item.get('name')})")

    if not roster_items:
        report_lines.append("run_sales: la plantilla vino vacía, nada más que evaluar.")
        notify("\n".join(report_lines))
        return

    # `budget` se pasa a decide_sales() para calcular el capital TOTAL del
    # equipo (plantilla + presupuesto) que usa la concentración de capital
    # en lesión confirmada (config.SELLING_INJURY_CONCENTRATION_MAX_PCT,
    # ver engine/selling_strategy.py) -- sin esto, esa señal solo vería el
    # valor de la plantilla, subestimando el capital real disponible.
    information = client.get_information()
    budget = information.get("answer", {}).get("budget", 0)

    # Ocupación de plantilla (mismo campo que jobs/run_market.py usa para
    # cortar pujas por límite de plazas -- `maxPlayersInRoster`, confirmado
    # con prueba manual real; NO `numberOfPlayers` (jugadores INICIALES de
    # la liga) ni `playersInRoster` (nombre del payload de ESCRITURA al
    # guardar la configuración, no de esta respuesta) -- ver docstring de
    # jobs/run_market.py) -- se incluye SIEMPRE en la notificación para
    # poder correlacionar a simple vista "plantilla llena/casi llena +
    # nada que vender" con que run_market esté bloqueando pujas por falta
    # de plazas.
    max_roster_size = information.get("answer", {}).get("configuration", {}).get("maxPlayersInRoster")
    occupancy = f"{len(roster_items)}/{max_roster_size}" if max_roster_size is not None else str(len(roster_items))

    # Alineación TITULAR guardada ahora mismo (para el bloqueo de fin de
    # semana de decide_sales(), ver su docstring) -- un fallo de red aquí
    # tampoco bloquea nada, ese bloqueo simplemente queda desactivado sin
    # `own_lineup_player_ids`.
    try:
        own_lineup_player_ids = {
            str(p["id"]) for p in client.get_lineup().get("answer", {}).get("players", [])
        }
    except requests.RequestException:
        own_lineup_player_ids = set()

    decisions = decide_sales(
        roster_items,
        bought_by_bot=bought_by_bot,
        budget=budget,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
        market_listing_expirations=market_listing_expirations,
        own_lineup_player_ids=own_lineup_player_ids,
        purchase_baselines=purchase_baselines,
        recent_price_history=recent_price_history,
    )
    if not decisions:
        report_lines.append(
            f"run_sales: ningún jugador supera el umbral de plusvalía para vender esta ejecución "
            f"(plantilla {occupancy})."
        )
        notify("\n".join(report_lines))
        return

    # Prima por revalorización rápida (ver docstring del módulo) -- una
    # llamada de red por CANDIDATO YA DECIDIDO, no por toda la plantilla.
    # Cualquier fallo (de red, o de negocio) en una llamada individual deja
    # esa decisión sin tocar -- se pide el VM tal cual, nunca bloquea la
    # venta ni tumba el resto del job.
    if config.ENABLE_SELLING_REVALUATION_PREMIUM:
        adjusted_decisions = []
        for decision in decisions:
            try:
                summary = client.get_player_summary(str(decision["player_id"]))
                prices = summary.get("answer", {}).get("prices", [])
                adjusted_decisions.append(apply_revaluation_premium(decision, prices))
            except requests.RequestException:
                adjusted_decisions.append(decision)
        decisions = adjusted_decisions

    listed, failed = [], []

    with get_connection() as conn:
        for decision in decisions:
            try:
                client.list_for_sale(decision["player_id"], decision["asking_price"])
                sale_id = _persist_sale(conn, decision, "listed", now)
                # Registro del swap (ver docstring del módulo) -- solo si
                # decide_sales() identificó un candidato concreto (venta
                # que calificó ÚNICAMENTE por oportunidad de mercado, ver
                # engine/selling_strategy.py); None en el resto de vías.
                if decision.get("swap_target_player_id"):
                    save_swap_target(
                        sale_id,
                        decision["player_id"],
                        decision["swap_target_player_id"],
                        decision.get("swap_target_price") or 0,
                        now,
                    )
                listed.append(decision)
            except (requests.RequestException, FutmondoOfferError) as e:
                _persist_sale(conn, decision, "failed", now, error=str(e))
                failed.append((decision, str(e)))

    report_lines.append(f"run_sales: {len(listed)} jugador(es) puesto(s) en venta (plantilla {occupancy}).")
    for d in listed:
        report_lines.append(
            f"  - jugador {d['player_id']}: pide {format_number(d['asking_price'])} "
            f"(referencia de compra {format_number(d['purchase_price'])}, {d['profit_pct']:+.1%})"
        )
    if failed:
        report_lines.append(f"{len(failed)} fallido(s):")
        for d, err in failed:
            report_lines.append(f"  - jugador {d['player_id']}: {err}")

    notify("\n".join(report_lines))


if __name__ == "__main__":
    # Chequeo duplicado a propósito: el de dentro de run() protege a quien
    # llame a run() directamente (tests incluidos); este de aquí evita
    # además que se entre en track_job_run() -- si no, con ENABLE_BOT=false
    # igualmente se registraría una fila en `job_runs`, ese INSERT por sí
    # solo ensuciaría db/futmondo.db, y el step "Commit BD actualizada" del
    # workflow comitearía/pushearía igual aunque el bot no haga nada real.
    if not config.ENABLE_BOT:
        print("run_sales: ENABLE_BOT=false, no se ejecuta.")
    else:
        with track_job_run("run_sales"):
            run()
