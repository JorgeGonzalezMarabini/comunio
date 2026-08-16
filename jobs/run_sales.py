"""
Job: identifica jugadores de la plantilla con plusvalía suficiente y los
pone en venta. Vender es la ÚNICA fuente de ingresos en Comunio (ver
README, "Economía de Comunio") — sin esto, el presupuesto del bot solo
puede reducirse con el tiempo, nunca crecer.

No es venta instantánea (ver clients/comunio_client.py: list_for_sale):
poner en venta solo deja al jugador listado, visible para que otro manager
(o el "Computer") lo compre. jobs/sync_data.py reconcilia después si la
venta se completó (comparando la plantilla en cada sync).

Solo se consideran jugadores comprados por el propio bot (con
`purchaseInfo` real, ver engine/selling_strategy.py) — nunca se pone en
venta a ciegas un jugador de la plantilla inicial sin saber su precio de
compra real.
"""
from datetime import datetime, timezone

import requests

from clients.comunio_client import ComunioClient, ComunioOfferError
from db.models import get_connection
from engine.selling_strategy import decide_sales
from notifier import notify


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
    client = ComunioClient()
    client.login()

    squad = client.get_squad()
    squad_items = squad.get("items", [])
    if not squad_items:
        notify("run_sales: la plantilla vino vacía, nada que evaluar.")
        return

    decisions = decide_sales(squad_items)
    if not decisions:
        notify("run_sales: ningún jugador supera el umbral de plusvalía para vender esta ejecución.")
        return

    now = datetime.now(timezone.utc).isoformat()
    listed, failed = [], []

    with get_connection() as conn:
        for decision in decisions:
            try:
                client.list_for_sale(decision["player_id"], decision["asking_price"])
                _persist_sale(conn, decision, "listed", now)
                listed.append(decision)
            except (requests.RequestException, ComunioOfferError) as e:
                _persist_sale(conn, decision, "failed", now)
                failed.append((decision, str(e)))

    summary = [f"run_sales: {len(listed)} jugador(es) puesto(s) en venta."]
    for d in listed:
        summary.append(
            f"  - jugador {d['player_id']}: pide {d['asking_price']} "
            f"(comprado a {d['purchase_price']}, {d['profit_pct']:+.1%})"
        )
    if failed:
        summary.append(f"{len(failed)} fallido(s):")
        for d, err in failed:
            summary.append(f"  - jugador {d['player_id']}: {err}")

    notify("\n".join(summary))


if __name__ == "__main__":
    run()
