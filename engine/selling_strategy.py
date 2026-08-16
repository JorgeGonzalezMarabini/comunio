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
"""
from __future__ import annotations

import config


def decide_sales(squad: list[dict], min_profit_pct: float = None) -> list[dict]:
    """
    `squad`: items reales de ComunioClient.get_squad()["items"] (necesita
    "id", "name", "quotedprice", "purchaseInfo").

    Devuelve una decisión por jugador cuya revalorización (quotedprice vs.
    purchaseInfo.price) supera `min_profit_pct` (por defecto
    config.SELLING_MIN_PROFIT_PCT):
        {"player_id", "asking_price", "purchase_price", "profit",
         "profit_pct", "reason"}
    listo para persistir en la tabla `sales` (auditoría) y pasar a
    ComunioClient.list_for_sale().

    El precio de venta pedido (`asking_price`) es el valor de mercado
    actual (`quotedprice`) — pedir de más no ayuda: Comunio parece ajustar
    el precio de venta según referencias del propio mercado (se vio en
    captura real que un listado a 180.000 devolvió una referencia de
    199.500 en `purchasePrices` de la respuesta — sin confirmar del todo
    qué representa ese campo, ver clients/comunio_client.py), así que
    pedir el valor de mercado tal cual es la opción más simple y segura.
    """
    min_profit_pct = config.SELLING_MIN_PROFIT_PCT if min_profit_pct is None else min_profit_pct

    decisions = []
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

        decisions.append(
            {
                "player_id": player["id"],
                "asking_price": current_price,
                "purchase_price": purchase_price,
                "profit": profit,
                "profit_pct": profit_pct,
                "reason": (
                    f"comprado a {purchase_price}, ahora {current_price} "
                    f"({profit_pct:+.1%}) >= umbral {min_profit_pct:.1%}"
                ),
            }
        )
    return decisions
