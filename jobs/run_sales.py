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
from datetime import datetime, timezone

import requests

import config
from clients.futmondo_client import FutmondoClient, FutmondoOfferError
from db.models import (
    get_connection,
    get_player_features,
    get_won_bid_prices,
    mark_offer_accepted,
    record_received_offer,
)
from engine.selling_strategy import apply_revaluation_premium, decide_sales
from notifier import notify, track_job_run, format_number


def _persist_sale(conn, decision: dict, status: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO sales (player_id, asking_price, purchase_price, profit, profit_pct, status, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(decision["player_id"]),
            decision["asking_price"],
            decision["purchase_price"],
            decision["profit"],
            decision["profit_pct"],
            status,
            decision["reason"],
            now,
        ),
    )


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


def _process_received_offers(client: FutmondoClient, seen_at: str) -> tuple[list[dict], list[tuple[dict, str]]]:
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

    Devuelve (aceptadas, fallidas) para el resumen de notificación de
    `run()`. Un fallo al aceptar una oferta concreta (red o rechazo de
    negocio) no aborta el resto -- se audita en `fallidas` y se sigue con
    el resto de listados.
    """
    listings = client.get_my_players_in_market().get("answer", [])
    accepted, failed = [], []

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

        try:
            client.accept_sale_offer(str(best["id"]), str(item["id"]))
            mark_offer_accepted(best["id"])
            accepted.append(
                {
                    "player_id": item["id"],
                    "name": item.get("name"),
                    "listing_price": listing_price,
                    "offer_price": best["price"],
                    "bidder": (best.get("userTeam") or {}).get("name"),
                }
            )
        except (requests.RequestException, FutmondoOfferError) as e:
            failed.append((item, str(e)))

    return accepted, failed


def run():
    if not config.ENABLE_BOT:
        print("run_sales: ENABLE_BOT=false, no se ejecuta.")
        return

    client = FutmondoClient()
    now = datetime.now(timezone.utc).isoformat()
    report_lines = []

    offers_accepted, offers_failed = _process_received_offers(client, now)
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

    roster = client.get_roster()
    roster_items = roster.get("answer", [])
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

    # Oportunidad de mercado (a petición del usuario, 2026-08-22, ver
    # docstring de engine/selling_strategy.py) -- features CRUDAS de la
    # plantilla propia y del mercado abierto AHORA MISMO (mismo formato
    # que usa jobs/run_market.py), para que decide_sales() pueda comparar
    # el score de alineación de un suplente contra lo mejor disponible en
    # el mercado en su misma posición. Si algo falla o viene vacío, esta
    # vía queda desactivada sin más dentro de decide_sales() -- nunca
    # bloquea las otras tres vías (rentabilidad/pérdida/concentración).
    roster_ids = {str(p["id"]) for p in roster_items}
    all_players = get_player_features(only_on_market=False)
    own_squad_features = [p for p in all_players if p["id"] in roster_ids]
    market_candidates = get_player_features(only_on_market=True)

    # Tiempo restante de cada listado de mercado (para el refinamiento
    # "ventana de tiempo" de la vía 5, ver docstring de
    # engine/selling_strategy.py) -- llamada en vivo aparte de
    # get_player_features(): ese snapshot local (futmondo_snapshots) NO
    # guarda `expirationDate`, solo la llamada real a get_market() lo
    # trae. Un fallo de red aquí no bloquea nada -- decide_sales() trata
    # un player_id sin entrada aquí como "sin dato", ese refinamiento
    # simplemente queda desactivado para ese candidato.
    try:
        market_listing_expirations = {
            str(p["id"]): p.get("expirationDate") for p in client.get_market().get("answer", [])
        }
    except requests.RequestException:
        market_listing_expirations = {}

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
        bought_by_bot=get_won_bid_prices(),
        budget=budget,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
        market_listing_expirations=market_listing_expirations,
        own_lineup_player_ids=own_lineup_player_ids,
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
                _persist_sale(conn, decision, "listed", now)
                listed.append(decision)
            except (requests.RequestException, FutmondoOfferError) as e:
                _persist_sale(conn, decision, "failed", now)
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
