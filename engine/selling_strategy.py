"""
Estrategia de ventas: identifica jugadores de la plantilla que conviene
poner en venta ahora mismo.

En Futmondo, igual que en Comunio (ver README, "Economía"), vender
jugadores es la vía principal para generar dinero: se compra
barato/infravalorado y se vende cuando el valor sube.

TODO importante, sin resolver (ver clients/futmondo_client.py para el
razonamiento completo): en Comunio, `purchaseInfo == null` distinguía sin
ambigüedad "plantilla inicial" de "comprado por el bot", así que
`decide_sales()` solo consideraba jugadores con precio de compra real
conocido. En Futmondo NO se ha encontrado un campo equivalente confiable
— `buyPrice` aparece tanto en jugadores de la plantilla inicial (a veces
0, a veces no) como, se espera, en jugadores comprados por puja real (sin
haber podido confirmar esto último ganando una puja de prueba en el
tiempo disponible). Por eso `decide_sales()` aquí trata cualquier
`buyPrice > 0` como precio de referencia válido para calcular plusvalía,
SIN filtrar por "solo comprados por el bot" — una diferencia deliberada de
comportamiento frente a Comunio, no un descuido. Si más adelante se
confirma que `buyPrice` sí puede distinguir el origen (p.ej. comparando el
roster antes/después de ganar una puja real), esta función debería volver
a filtrar como hacía la versión de Comunio.

Riesgo de plantilla (ver engine/squad_risk.py): vender es una acción tan
capaz de dejarte sin cobertura en una posición como que te "clausulen" un
jugador — la diferencia es que esta la causa el propio bot, así que es
100% evitable. Por eso decide_sales() NUNCA vende un jugador si eso deja
esa posición sin margen de suplentes sanos (bench <= 0 tras la venta),
por muy rentable que sea la operación — a diferencia de la prioridad de
puja (un empujón blando), esto es un bloqueo duro: no hay ninguna
plusvalía que compense quedarte con una posición vacía.
"""
from __future__ import annotations

import config
from clients.futmondo_client import FUTMONDO_POSITION_MAP, is_injury_status
from engine.squad_risk import assess_squad_depth


def decide_sales(squad: list[dict], min_profit_pct: float = None, formation: str = None) -> list[dict]:
    """
    `squad`: items reales de FutmondoClient.get_roster()["answer"] (necesita
    "id", "name", "role", "status", "value", "buyPrice") tal cual, sin
    normalizar antes; aquí dentro se traduce la posición
    (FUTMONDO_POSITION_MAP) para poder cruzarla con engine.squad_risk.

    Devuelve una decisión por jugador cuya revalorización (`value` vs.
    `buyPrice`) supera `min_profit_pct` (por defecto
    config.SELLING_MIN_PROFIT_PCT) Y cuya posición sigue teniendo margen de
    suplentes sanos después de la venta:
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

    candidates = []
    for player in squad:
        purchase_price = player.get("buyPrice") or 0
        current_price = player.get("value", 0)
        if purchase_price <= 0 or current_price <= 0:
            continue  # sin precio de referencia > 0 no hay plusvalía calculable

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
    normalized_squad = [{**p, "position": FUTMONDO_POSITION_MAP.get(p.get("role"), p.get("role"))} for p in squad]
    depth = assess_squad_depth(normalized_squad, formation=formation)
    bench_remaining = {position: info["bench"] for position, info in depth.items()}

    decisions = []
    for player, profit, profit_pct in candidates:
        position = FUTMONDO_POSITION_MAP.get(player.get("role"), player.get("role"))
        is_injured_or_doubtful = is_injury_status(player.get("status"))

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
                "asking_price": player.get("value", 0),
                "purchase_price": player["buyPrice"],
                "profit": profit,
                "profit_pct": profit_pct,
                "reason": (
                    f"referencia de compra {player['buyPrice']}, ahora {player.get('value', 0)} "
                    f"({profit_pct:+.1%}) >= umbral {min_profit_pct:.1%}; posición {position} con margen suficiente"
                ),
            }
        )
    return decisions
