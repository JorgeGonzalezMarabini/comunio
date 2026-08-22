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

Corte de pérdidas en lesión CONFIRMADA (a petición del usuario,
2026-08-22): hasta ahora un jugador solo era candidato a venta si
superaba `min_profit_pct`, sea sano, en duda o lesionado — sin ninguna
otra vía. Para un lesionado eso es un problema real: su valor tiende a
seguir bajando cuanto más tiempo pasa sin jugar (no puntúa, el mercado lo
penaliza más), así que esperar a que "recupere" plusvalía para poder
venderlo es la falacia del coste hundido — aferrarse a cuánto se pagó en
el pasado en vez de valorar el jugador por lo que es AHORA, un activo con
pinta de seguir devaluándose. Por eso, además de la vía normal
(`min_profit_pct`), un jugador con lesión CONFIRMADA (no "doubt" —
ver `clients.futmondo_client.is_confirmed_injured_status()`) que ya haya
perdido más de `injury_max_loss_pct` (config.SELLING_INJURY_MAX_LOSS_PCT)
se pone en venta igualmente, aunque sea con pérdidas. "doubt" queda
DELIBERADAMENTE fuera de esta segunda vía (a diferencia de lesión
confirmada, todavía puede llegar a jugar — no hay la misma base para
asumir que solo va a perder valor) — sigue necesitando `min_profit_pct`
como cualquier sano, sin cambios.
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
    injury_max_loss_pct: float = None,
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

    `injury_max_loss_pct`: por defecto config.SELLING_INJURY_MAX_LOSS_PCT
    — umbral de PÉRDIDA (positivo, ej. 0.15 = -15%) a partir del cual un
    jugador con lesión CONFIRMADA se pone en venta aunque no llegue a
    `min_profit_pct`, incluso con pérdidas (ver docstring del módulo:
    corte de pérdidas para no caer en la falacia del coste hundido con un
    jugador que tiende a seguir perdiendo valor). No aplica a "doubt".

    Devuelve una decisión por cada jugador que cumpla CUALQUIERA de estas
    dos condiciones (Y cuya posición siga teniendo margen de suplentes
    sanos después de la venta, salvo que ya esté lesionado/en duda —
    ver más abajo):
      1. Su revalorización (`value` vs. el precio pagado en
         `bought_by_bot`) supera `min_profit_pct` (por defecto
         config.SELLING_MIN_PROFIT_PCT) — vía normal, para todos.
      2. Tiene lesión CONFIRMADA y ya ha perdido más de
         `injury_max_loss_pct` — corte de pérdidas, solo para lesión
         confirmada (no "doubt").

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
    injury_max_loss_pct = (
        config.SELLING_INJURY_MAX_LOSS_PCT if injury_max_loss_pct is None else injury_max_loss_pct
    )
    bought_by_bot = bought_by_bot or {}

    candidates = []
    for player in squad:
        purchase_price = bought_by_bot.get(str(player["id"])) or 0
        current_price = player.get("value", 0)
        if purchase_price <= 0 or current_price <= 0:
            continue  # no comprado por el bot (o sin precio de referencia): no es candidato

        profit = current_price - purchase_price
        profit_pct = profit / purchase_price

        # Corte de pérdidas: solo lesión CONFIRMADA (no "doubt", ver
        # docstring del módulo) y solo si ya ha perdido más del umbral --
        # se vende aunque no llegue a min_profit_pct, incluso con pérdidas.
        cutting_losses = is_confirmed_injured_status(player.get("status")) and profit_pct <= -injury_max_loss_pct

        if profit_pct < min_profit_pct and not cutting_losses:
            continue

        candidates.append((player, purchase_price, profit, profit_pct, cutting_losses))

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
    for player, purchase_price, profit, profit_pct, cutting_losses in candidates:
        position = FUTMONDO_POSITION_MAP.get(player.get("role"), player.get("role"))
        is_injured_or_doubtful = is_injury_status(player.get("status"))

        # Un jugador ya lesionado/sancionado no contaba como "disponible"
        # en assess_squad_depth, así que venderlo no empeora la cobertura
        # real de la posición -- no hace falta descontar margen por él.
        if not is_injured_or_doubtful:
            if bench_remaining.get(position, 0) <= 0:
                continue  # vender aquí dejaría la posición sin cubrir -- no se vende, por rentable que sea
            bench_remaining[position] -= 1

        if cutting_losses:
            reason = (
                f"lesión confirmada: pagado por el bot {purchase_price}, ahora {player.get('value', 0)} "
                f"({profit_pct:+.1%}) -- pérdida >= umbral de corte {injury_max_loss_pct:.1%}; se vende "
                "aunque no llegue al umbral de rentabilidad, para no aferrarse a un jugador que tiende a "
                "seguir perdiendo valor (falacia del coste hundido)"
            )
        else:
            reason = (
                f"pagado por el bot {purchase_price}, ahora {player.get('value', 0)} "
                f"({profit_pct:+.1%}) >= umbral {min_profit_pct:.1%}; posición {position} con margen suficiente"
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
