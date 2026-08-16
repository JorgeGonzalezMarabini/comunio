"""
Estrategia de ventas: identifica jugadores de la plantilla que conviene
poner en venta ahora mismo.

Investigado 2026-08-16 (ver README, "Economía de Comunio"): en Comunio NO
hay ingreso pasivo — vender jugadores es la ÚNICA forma de generar dinero.
La estrategia documentada por la comunidad es especular con el valor de
mercado: comprar barato/infravalorado y vender cuando el valor sube
(fluctuación máx. ±15%/día, o ±250k si el jugador vale <1,6M).

Solo se consideran jugadores con `purchaseInfo` real (comprados por el bot
vía puja, ver clients.comunio_client.ComunioClient.place_bid) — confirmado
por captura real: el campo viene en la respuesta de get_squad() como
{"date": ..., "price": <precio pagado>, ...}, y es `null` para los
jugadores de la plantilla inicial (nunca comprados por nosotros, así que
no hay precio de compra real con el que calcular una plusvalía). No se
fuerza la venta de esos jugadores a ciegas.

Riesgo de plantilla (ver engine/squad_risk.py): vender es una acción tan
capaz de dejarte sin cobertura en una posición como que te "clausulen" un
jugador — la diferencia es que esta la causa el propio bot, así que es
100% evitable. Por eso decide_sales() NUNCA vende un jugador si eso deja
esa posición sin margen de suplentes sanos (bench <= 0 tras la venta),
por muy rentable que sea la operación — a diferencia de la prioridad de
puja (un empujón blando), esto es un bloqueo duro: no hay ninguna
plusvalía que compense quedarte con una posición vacía (-4 puntos, ver
README).
"""
from __future__ import annotations

import config
from clients.comunio_client import COMUNIO_INJURY_STATUSES, COMUNIO_POSITION_MAP
from engine.squad_risk import assess_squad_depth


def decide_sales(squad: list[dict], min_profit_pct: float = None, formation: str = None) -> list[dict]:
    """
    `squad`: items reales de ComunioClient.get_squad()["items"] (necesita
    "id", "name", "position", "status", "quotedprice", "purchaseInfo") —
    tal cual, sin normalizar antes; aquí dentro se traduce la posición
    (COMUNIO_POSITION_MAP) para poder cruzarla con engine.squad_risk.

    Devuelve una decisión por jugador cuya revalorización (quotedprice vs.
    purchaseInfo.price) supera `min_profit_pct` (por defecto
    config.SELLING_MIN_PROFIT_PCT) Y cuya posición sigue teniendo margen de
    suplentes sanos después de la venta:
        {"player_id", "asking_price", "purchase_price", "profit",
         "profit_pct", "reason"}
    listo para persistir en la tabla `sales` (auditoría) y pasar a
    ComunioClient.list_for_sale().

    Si hay varios candidatos rentables en la misma posición pero no hay
    margen para vender a todos, se prioriza al de mayor plusvalía (orden
    descendente) — el resto se descarta esta vez, no se difiere ni se
    fuerza.

    El precio de venta pedido (`asking_price`) es el valor de mercado
    actual (`quotedprice`) — pedir de más no ayuda: Comunio parece ajustar
    el precio de venta según referencias del propio mercado (se vio en
    captura real que un listado a 180.000 devolvió una referencia de
    199.500 en `purchasePrices` de la respuesta — sin confirmar del todo
    qué representa ese campo, ver clients/comunio_client.py), así que
    pedir el valor de mercado tal cual es la opción más simple y segura.
    """
    min_profit_pct = config.SELLING_MIN_PROFIT_PCT if min_profit_pct is None else min_profit_pct

    candidates = []
    for player in squad:
        purchase_info = player.get("purchaseInfo")
        if not purchase_info or not purchase_info.get("price"):
            continue  # plantilla inicial u otro origen sin precio de compra conocido

        purchase_price = purchase_info["price"]
        current_price = player.get("quotedprice", 0)
        if purchase_price <= 0 or current_price <= 0:
            continue

        profit = current_price - purchase_price
        profit_pct = profit / purchase_price
        if profit_pct < min_profit_pct:
            continue

        candidates.append((player, profit, profit_pct))

    # Más rentables primero: si el margen de plantilla en una posición no
    # alcanza para vender a todos los candidatos de esa posición, se
    # prioriza el de mayor plusvalía.
    candidates.sort(key=lambda c: c[2], reverse=True)

    # Margen de suplentes sanos por posición ANTES de vender nada (ver
    # engine.squad_risk.assess_squad_depth) — se va descontando según se
    # aceptan ventas de esta misma pasada, para no autorizar de golpe dos
    # ventas que juntas sí dejarían la posición sin cubrir.
    normalized_squad = [{**p, "position": COMUNIO_POSITION_MAP.get(p.get("position"), p.get("position"))} for p in squad]
    depth = assess_squad_depth(normalized_squad, formation=formation)
    bench_remaining = {position: info["bench"] for position, info in depth.items()}

    decisions = []
    for player, profit, profit_pct in candidates:
        position = COMUNIO_POSITION_MAP.get(player.get("position"), player.get("position"))
        is_injured_or_doubtful = player.get("status") in COMUNIO_INJURY_STATUSES

        # Un jugador ya lesionado/sancionado no contaba como "disponible"
        # en assess_squad_depth, así que venderlo no empeora la cobertura
        # real de la posición -- no hace falta descontar margen por él.
        if not is_injured_or_doubtful:
            if bench_remaining.get(position, 0) <= 0:
                continue  # vender aquí dejaría la posición sin cubrir -- no se vende, por rentable que sea
            bench_remaining[position] -= 1

        decisions.append(
            {
                "player_id": player["id"],
                "asking_price": player.get("quotedprice", 0),
                "purchase_price": player["purchaseInfo"]["price"],
                "profit": profit,
                "profit_pct": profit_pct,
                "reason": (
                    f"comprado a {player['purchaseInfo']['price']}, ahora {player.get('quotedprice', 0)} "
                    f"({profit_pct:+.1%}) >= umbral {min_profit_pct:.1%}; posición {position} con margen suficiente"
                ),
            }
        )
    return decisions
