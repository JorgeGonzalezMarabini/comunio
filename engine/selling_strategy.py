"""
Estrategia de ventas: identifica jugadores de la plantilla que conviene
poner en venta ahora mismo.

En Futmondo, igual que en Comunio (ver README, "Economía"), vender
jugadores es la vía principal para generar dinero: se compra
barato/infravalorado y se vende cuando el valor sube.

Resuelve TODO.md #4 (`buyPrice` no distingue "comprado por el bot" de
"plantilla inicial"): en Comunio, `purchaseInfo == null` distinguía sin
ambigüedad ambos casos. En Futmondo no se encontró un campo equivalente
confiable — `buyPrice` aparece tanto en plantilla inicial (a veces 0, a
veces no) como en compras reales, sin poder distinguir el origen por ese
campo (ver clients/futmondo_client.py). En vez de eso, `decide_sales()`
recibe `bought_by_bot` (ver `db.models.get_won_bid_prices()`): el registro
LOCAL de pujas que el propio bot colocó y ganó (tabla `bids`,
reconciliada en `jobs/sync_data.py`), independiente de `buyPrice`. Un
jugador solo es candidato a venta si aparece ahí, y el precio de
referencia usado es el importe realmente pagado en esa puja (más fiable
que `buyPrice`, que ni siquiera se necesita ya para esta decisión) — misma
semántica que `purchaseInfo != null` en Comunio, alcanzada sin depender de
un campo de Futmondo sin confirmar.

Riesgo de plantilla (ver engine/squad_risk.py): vender es una acción tan
capaz de dejarte sin cobertura en una posición como que te "clausulen" un
jugador — la diferencia es que esta la causa el propio bot, así que es
100% evitable. Por eso decide_sales() NUNCA vende un jugador si eso deja
esa posición sin margen de suplentes sanos (bench <= 0 tras la venta),
por muy rentable que sea la operación — a diferencia de la prioridad de
puja (un empujón blando), esto es un bloqueo duro: no hay ninguna
plusvalía que compense quedarte con una posición vacía.

Corte de pérdidas, genérico + más agresivo en lesión CONFIRMADA (a
petición del usuario, 2026-08-22, versión más radical que la primera
iteración de este mismo cambio -- esa primera versión limitaba el corte
solo a lesión confirmada, ver historial de commits): hasta ahora un
jugador solo era candidato a venta si superaba `min_profit_pct` — sin
ninguna otra vía, esperando indefinidamente a que "recupere" plusvalía
por muy mal que fuera la operación. Eso es la falacia del coste hundido
en estado puro: aferrarse a cuánto se pagó en el pasado en vez de decidir
por lo que el jugador es AHORA.

Por eso, además de la vía normal (`min_profit_pct`), CUALQUIER jugador
(sano, en duda o lesionado) que haya perdido más de `max_loss_pct`
(config.SELLING_MAX_LOSS_PCT, umbral genérico) se pone en venta
igualmente, aunque sea con pérdidas — cortar una pérdida grande no debería
depender de por qué bajó. Un jugador con lesión CONFIRMADA (no "doubt" —
ver `clients.futmondo_client.is_confirmed_injured_status()`) usa en su
lugar `injury_max_loss_pct` (config.SELLING_INJURY_MAX_LOSS_PCT), MÁS BAJO
que el genérico: su valor tiende a seguir bajando cuanto más tiempo pasa
sin jugar (no puntúa, el mercado lo penaliza más), así que aquí sí hay
motivo real para cortar la pérdida más pronto que en el caso genérico.
"doubt" usa el umbral genérico, no el de lesión — todavía puede llegar a
jugar, no hay la misma base para asumir que solo va a perder valor.

Concentración de capital en lesión CONFIRMADA (a petición del usuario,
2026-08-22): las dos vías de arriba solo miran RENTABILIDAD (plusvalía o
pérdida de LA OPERACIÓN). Pero un jugador lesionado es un problema
también si simplemente representa una parte demasiado grande del capital
del equipo (plantilla + presupuesto), aunque no esté ni cerca de
`injury_max_loss_pct` — mientras esté lesionado no se puede usar, así que
tener mucho capital inmovilizado ahí es un coste de oportunidad real
(ese dinero no puede fichar a nadie más). Por eso, un jugador con lesión
CONFIRMADA cuyo valor supera `injury_concentration_max_pct`
(config.SELLING_INJURY_CONCENTRATION_MAX_PCT) del capital total
(suma del valor de TODA la plantilla + `budget`) también se pone en
venta, sin mirar plusvalía/pérdida en absoluto. Como con las dos vías
anteriores, "doubt" queda fuera — todavía puede llegar a jugar.
"""
from __future__ import annotations

import config
from clients.futmondo_client import FUTMONDO_POSITION_MAP, is_confirmed_injured_status, is_injury_status
from engine.squad_risk import assess_squad_depth


def decide_sales(
    squad: list[dict],
    min_profit_pct: float = None,
    formation: str = None,
    bought_by_bot: dict[str, int] = None,
    max_loss_pct: float = None,
    injury_max_loss_pct: float = None,
    budget: int = 0,
    injury_concentration_max_pct: float = None,
) -> list[dict]:
    """
    `squad`: items reales de FutmondoClient.get_roster()["answer"] (necesita
    "id", "name", "role", "status", "value") tal cual, sin normalizar
    antes; aquí dentro se traduce la posición (FUTMONDO_POSITION_MAP) para
    poder cruzarla con engine.squad_risk.

    `bought_by_bot`: {player_id: precio pagado} — normalmente
    `db.models.get_won_bid_prices()`. Solo los jugadores presentes aquí son
    candidatos a venta (equivalente a `purchaseInfo != null` en Comunio,
    ver docstring del módulo); el precio de referencia para la plusvalía es
    el importe de ese dict, no `buyPrice` de Futmondo. Si se omite (o llega
    vacío/None), no hay ningún candidato — nunca se recurre a `buyPrice`
    como fallback silencioso, para no reintroducir la ambigüedad original.

    `max_loss_pct`: por defecto config.SELLING_MAX_LOSS_PCT — umbral de
    PÉRDIDA genérico (positivo, ej. 0.20 = -20%) a partir del cual
    CUALQUIER jugador (sano, en duda o lesionado) se pone en venta aunque
    no llegue a `min_profit_pct`, incluso con pérdidas (ver docstring del
    módulo: corte de pérdidas para no caer en la falacia del coste
    hundido).

    `injury_max_loss_pct`: por defecto config.SELLING_INJURY_MAX_LOSS_PCT
    — igual que `max_loss_pct` pero MÁS BAJO (corta antes) y solo para
    jugadores con lesión CONFIRMADA (no "doubt"): su valor tiende a seguir
    bajando cuanto más tiempo pasa sin jugar, así que aquí sí hay motivo
    para cortar la pérdida más pronto. "doubt" usa `max_loss_pct`, no este.

    `budget`: presupuesto disponible ahora mismo (normalmente
    `FutmondoClient.get_information()["answer"]["budget"]`) — se suma al
    valor de mercado de toda la plantilla para calcular el capital TOTAL
    del equipo, usado por `injury_concentration_max_pct` (ver abajo). Por
    defecto 0 (solo cuenta el valor de la plantilla) si el llamador no lo
    tiene a mano.

    `injury_concentration_max_pct`: por defecto
    config.SELLING_INJURY_CONCENTRATION_MAX_PCT — % máximo del capital
    total (plantilla + `budget`) que puede estar inmovilizado en UN jugador
    con lesión CONFIRMADA antes de ponerlo en venta, sin mirar
    rentabilidad en absoluto (ver docstring del módulo: no es un problema
    de plusvalía, es de concentración de capital en un jugador que no se
    puede usar). No aplica a "doubt".

    Devuelve una decisión por cada jugador que cumpla CUALQUIERA de estas
    tres condiciones (Y cuya posición siga teniendo margen de suplentes
    sanos después de la venta, salvo que ya esté lesionado/en duda —
    ver más abajo):
      1. Su revalorización (`value` vs. el precio pagado en
         `bought_by_bot`) supera `min_profit_pct` (por defecto
         config.SELLING_MIN_PROFIT_PCT) — vía normal, para todos.
      2. Ha perdido más del umbral de corte que le corresponda
         (`injury_max_loss_pct` si tiene lesión CONFIRMADA, `max_loss_pct`
         para cualquier otro caso, incluido "doubt") — corte de pérdidas.
      3. Tiene lesión CONFIRMADA y su valor supera
         `injury_concentration_max_pct` del capital total — concentración
         de capital, solo para lesión confirmada (no "doubt"), sin mirar
         plusvalía/pérdida.

    Cada decisión:
        {"player_id", "asking_price", "purchase_price", "profit",
         "profit_pct", "reason"}
    listo para persistir en la tabla `sales` (auditoría) y pasar a
    FutmondoClient.list_for_sale().

    Si hay varios candidatos rentables en la misma posición pero no hay
    margen para vender a todos, se prioriza al de mayor plusvalía (orden
    descendente) — el resto se descarta esta vez, no se difiere ni se
    fuerza.

    El precio de venta pedido (`asking_price`) es el valor de mercado
    actual (`value`) — pedir el valor de mercado tal cual es la opción más
    simple y segura (en Comunio se vio que pedir más no ayudaba porque el
    mercado ajustaba solo; no se ha confirmado si Futmondo hace algo
    parecido, pero no hay motivo para pedir un precio distinto al VM).
    """
    min_profit_pct = config.SELLING_MIN_PROFIT_PCT if min_profit_pct is None else min_profit_pct
    max_loss_pct = config.SELLING_MAX_LOSS_PCT if max_loss_pct is None else max_loss_pct
    injury_max_loss_pct = (
        config.SELLING_INJURY_MAX_LOSS_PCT if injury_max_loss_pct is None else injury_max_loss_pct
    )
    injury_concentration_max_pct = (
        config.SELLING_INJURY_CONCENTRATION_MAX_PCT
        if injury_concentration_max_pct is None
        else injury_concentration_max_pct
    )
    bought_by_bot = bought_by_bot or {}

    # Capital total del equipo (plantilla + presupuesto disponible) --
    # denominador de injury_concentration_max_pct, ver docstring. Se
    # calcula sobre TODA la plantilla (no solo los candidatos), es el
    # patrimonio real del equipo en este momento.
    total_capital = sum(p.get("value", 0) or 0 for p in squad) + max(0, budget)

    candidates = []
    for player in squad:
        purchase_price = bought_by_bot.get(str(player["id"])) or 0
        current_price = player.get("value", 0)
        if purchase_price <= 0 or current_price <= 0:
            continue  # no comprado por el bot (o sin precio de referencia): no es candidato

        profit = current_price - purchase_price
        profit_pct = profit / purchase_price

        # Corte de pérdidas: umbral distinto según el estado -- lesión
        # CONFIRMADA usa injury_max_loss_pct (más bajo, corta antes,
        # ver docstring del módulo); cualquier otro caso (sano o "doubt")
        # usa el umbral genérico max_loss_pct. Se vende aunque no llegue a
        # min_profit_pct, incluso con pérdidas.
        is_confirmed_injured = is_confirmed_injured_status(player.get("status"))
        loss_threshold = injury_max_loss_pct if is_confirmed_injured else max_loss_pct
        cutting_losses = profit_pct <= -loss_threshold

        # Concentración de capital: solo lesión CONFIRMADA, sin mirar
        # rentabilidad -- demasiado capital inmovilizado en un jugador que
        # no se puede usar es un problema en sí mismo (ver docstring).
        concentration_pct = (current_price / total_capital) if total_capital > 0 else 0.0
        overconcentrated = is_confirmed_injured and concentration_pct >= injury_concentration_max_pct

        if profit_pct < min_profit_pct and not cutting_losses and not overconcentrated:
            continue

        candidates.append(
            (
                player,
                purchase_price,
                profit,
                profit_pct,
                cutting_losses,
                is_confirmed_injured,
                loss_threshold,
                overconcentrated,
                concentration_pct,
            )
        )

    # Más rentables primero: si el margen de plantilla en una posición no
    # alcanza para vender a todos los candidatos de esa posición, se
    # prioriza el de mayor plusvalía. Los cortes de pérdidas (profit_pct
    # muy negativo) quedan naturalmente al final de este orden, pero no
    # compiten por margen de banquillo de todos modos (ver más abajo: un
    # lesionado nunca contó como "disponible", así que no descuenta bench).
    candidates.sort(key=lambda c: c[3], reverse=True)

    # Margen de suplentes sanos por posición ANTES de vender nada (ver
    # engine.squad_risk.assess_squad_depth) — se va descontando según se
    # aceptan ventas de esta misma pasada, para no autorizar de golpe dos
    # ventas que juntas sí dejarían la posición sin cubrir.
    normalized_squad = [{**p, "position": FUTMONDO_POSITION_MAP.get(p.get("role"), p.get("role"))} for p in squad]
    depth = assess_squad_depth(normalized_squad, formation=formation)
    bench_remaining = {position: info["bench"] for position, info in depth.items()}

    decisions = []
    for (
        player,
        purchase_price,
        profit,
        profit_pct,
        cutting_losses,
        is_confirmed_injured,
        loss_threshold,
        overconcentrated,
        concentration_pct,
    ) in candidates:
        position = FUTMONDO_POSITION_MAP.get(player.get("role"), player.get("role"))
        is_injured_or_doubtful = is_injury_status(player.get("status"))

        # Un jugador ya lesionado/sancionado no contaba como "disponible"
        # en assess_squad_depth, así que venderlo no empeora la cobertura
        # real de la posición -- no hace falta descontar margen por él.
        if not is_injured_or_doubtful:
            if bench_remaining.get(position, 0) <= 0:
                continue  # vender aquí dejaría la posición sin cubrir -- no se vende, por rentable que sea
            bench_remaining[position] -= 1

        # Prioridad del motivo mostrado (no son excluyentes entre sí, un
        # candidato puede cumplir varios a la vez): rentabilidad normal
        # primero (el caso más informativo/común), luego corte de
        # pérdidas, luego concentración de capital (la única vía que ni
        # siquiera mira profit_pct).
        if profit_pct >= min_profit_pct:
            reason = (
                f"pagado por el bot {purchase_price}, ahora {player.get('value', 0)} "
                f"({profit_pct:+.1%}) >= umbral {min_profit_pct:.1%}; posición {position} con margen suficiente"
            )
        elif cutting_losses:
            if is_confirmed_injured:
                motivo = "lesión confirmada, con umbral de corte más bajo (tiende a seguir perdiendo valor)"
            else:
                motivo = "corte de pérdidas genérico"
            reason = (
                f"{motivo}: pagado por el bot {purchase_price}, ahora {player.get('value', 0)} "
                f"({profit_pct:+.1%}) -- pérdida >= umbral de corte {loss_threshold:.1%}; se vende aunque no "
                "llegue al umbral de rentabilidad, para no caer en la falacia del coste hundido"
            )
        else:
            reason = (
                f"lesión confirmada, concentración de capital: vale {player.get('value', 0)} "
                f"({concentration_pct:.1%} del capital total del equipo) >= umbral "
                f"{injury_concentration_max_pct:.1%}; se vende sin mirar plusvalía/pérdida ({profit_pct:+.1%}), "
                "para no tener capital inmovilizado en un jugador que no se puede usar"
            )

        decisions.append(
            {
                "player_id": player["id"],
                "asking_price": player.get("value", 0),
                "purchase_price": purchase_price,
                "profit": profit,
                "profit_pct": profit_pct,
                "reason": reason,
            }
        )
    return decisions
