"""
Job: identifica jugadores de la plantilla con plusvalía suficiente y los
pone en venta. Vender es una fuente clave de ingresos en Futmondo, igual
que en Comunio (ver README, "Economía") — sin esto, el presupuesto del bot
solo puede reducirse con el tiempo, nunca crecer.

No es venta instantánea (ver clients/futmondo_client.py: list_for_sale):
poner en venta solo deja al jugador listado, visible para que otro manager
(o el "Computer") lo compre. jobs/sync_data.py reconcilia después si la
venta se completó (comparando la plantilla en cada sync).

A diferencia de Comunio, no se filtra por "solo jugadores comprados por el
bot" — Futmondo no tiene un campo confirmado que distinga eso (ver TODO en
engine/selling_strategy.py) — cualquier jugador con `buyPrice > 0` y
plusvalía suficiente es candidato.
"""
from datetime import datetime, timezone

import requests

import config
from clients.futmondo_client import FutmondoClient, FutmondoOfferError
from db.models import get_connection
from engine.selling_strategy import decide_sales
from notifier import notify, notify_on_crash


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

    decisions = decide_sales(roster_items)
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
            except (requests.RequestException, FutmondoOfferError) as e:
                _persist_sale(conn, decision, "failed", now)
                failed.append((decision, str(e)))

    summary = [f"run_sales: {len(listed)} jugador(es) puesto(s) en venta."]
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
    with notify_on_crash("run_sales"):
        run()
