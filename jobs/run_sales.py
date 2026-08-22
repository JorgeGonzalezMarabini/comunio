"""
Job: identifica jugadores de la plantilla con plusvalía suficiente y los
pone en venta. Vender es una fuente clave de ingresos en Futmondo, igual
que en Comunio (ver README, "Economía") — sin esto, el presupuesto del bot
solo puede reducirse con el tiempo, nunca crecer.

No es venta instantánea (ver clients/futmondo_client.py: list_for_sale):
poner en venta solo deja al jugador listado, visible para que otro manager
(o el "Computer") lo compre. jobs/sync_data.py reconcilia después si la
venta se completó (comparando la plantilla en cada sync).

**CRÍTICO, sin confirmar (TODO.md #15, a petición del usuario,
2026-08-22)**: según información del usuario, Futmondo NO vende
automáticamente al mejor postor -- otros managers hacen OFERTAS sobre el
jugador listado, y hace falta ACEPTAR una explícitamente para completar
la venta. Ese paso de aceptación NO está implementado en ningún sitio de
este código (ni aquí ni en jobs/sync_data.py) -- si se confirma, un
jugador puesto en venta por este job podría quedarse listado
indefinidamente sin venderse nunca, por muchas ofertas que reciba. Sin
confirmar todavía con captura real (ver TODO.md #15 para el endpoint
visto pero sin usar y el motivo por el que no se pudo capturar aún).

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
"""
from datetime import datetime, timezone

import requests

import config
from clients.futmondo_client import FutmondoClient, FutmondoOfferError
from db.models import get_connection, get_player_features, get_won_bid_prices
from engine.selling_strategy import apply_revaluation_premium, decide_sales
from notifier import notify, track_job_run


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


def run():
    if not config.ENABLE_BOT:
        print("run_sales: ENABLE_BOT=false, no se ejecuta.")
        return

    client = FutmondoClient()

    roster = client.get_roster()
    roster_items = roster.get("answer", [])
    if not roster_items:
        notify("run_sales: la plantilla vino vacía, nada que evaluar.")
        return

    # `budget` se pasa a decide_sales() para calcular el capital TOTAL del
    # equipo (plantilla + presupuesto) que usa la concentración de capital
    # en lesión confirmada (config.SELLING_INJURY_CONCENTRATION_MAX_PCT,
    # ver engine/selling_strategy.py) -- sin esto, esa señal solo vería el
    # valor de la plantilla, subestimando el capital real disponible.
    information = client.get_information()
    budget = information.get("answer", {}).get("budget", 0)

    # Ocupación de plantilla (mismo campo que jobs/run_market.py usa para
    # cortar pujas por límite de plazas -- `playersInRoster`, NO
    # `numberOfPlayers`, ver docstring de ese módulo: ese otro campo es el
    # número de jugadores INICIALES de la liga, no el máximo) -- se
    # incluye SIEMPRE en la notificación para poder correlacionar a simple
    # vista "plantilla llena/casi llena + nada que vender" con que
    # run_market esté bloqueando pujas por falta de plazas.
    max_roster_size = information.get("answer", {}).get("configuration", {}).get("playersInRoster")
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

    decisions = decide_sales(
        roster_items,
        bought_by_bot=get_won_bid_prices(),
        budget=budget,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )
    if not decisions:
        notify(
            f"run_sales: ningún jugador supera el umbral de plusvalía para vender esta ejecución "
            f"(plantilla {occupancy})."
        )
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

    now = datetime.now(timezone.utc).isoformat()
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

    summary = [f"run_sales: {len(listed)} jugador(es) puesto(s) en venta (plantilla {occupancy})."]
    for d in listed:
        summary.append(
            f"  - jugador {d['player_id']}: pide {d['asking_price']} "
            f"(referencia de compra {d['purchase_price']}, {d['profit_pct']:+.1%})"
        )
    if failed:
        summary.append(f"{len(failed)} fallido(s):")
        for d, err in failed:
            summary.append(f"  - jugador {d['player_id']}: {err}")

    notify("\n".join(summary))


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
