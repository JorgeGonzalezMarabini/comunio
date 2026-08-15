"""
Job: evalúa el mercado y ejecuta pujas automáticas, 100% autónomo (sin
confirmación manual). Cada puja (o intento fallido) se audita en la tabla
`bids` con el score y motivo que la justificó.

Asume que jobs/sync_data.py ya corrió antes en el cron (así `players`/
`comunio_snapshots`/`external_stats` están al día) — este job solo lee de
la BD y decide, no vuelve a sincronizar stats.

place_bid() está **100% confirmado** (ver clients/comunio_client.py) —
puede fallar por HTTP (requests.RequestException) o por rechazo de negocio
con HTTP 200 (ComunioOfferError, ej. el jugador ya no está en el mercado);
cada intento va en su propio try/except para que un fallo de una puja no
tumbe las demás ni la ejecución completa.
"""
from datetime import datetime, timezone

import requests

from clients.comunio_client import ComunioClient, ComunioOfferError
from db.models import get_connection, get_bids_risked_today, get_player_features
from engine.bidding_strategy import decide_bids_for_market
from engine.evaluator import evaluate_players
from notifier import notify


def _persist_bid(conn, decision: dict, status: str, now: str, comunio_offer_id: int = None) -> None:
    conn.execute(
        """
        INSERT INTO bids (player_id, comunio_offer_id, amount, status, score, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (str(decision["player_id"]), comunio_offer_id, decision["amount"], status, decision["score"], decision["reason"], now),
    )


def run():
    client = ComunioClient()
    client.login()

    raw_candidates = get_player_features(only_on_market=True)
    if not raw_candidates:
        notify("run_market: no hay candidatos en mercado en la BD (¿corrió sync_data antes?).")
        return

    ranked = evaluate_players(raw_candidates)

    offers = client.get_offers()
    remaining_budget = offers.get("credit", 0)
    already_risked = get_bids_risked_today()

    decisions = decide_bids_for_market(ranked, remaining_budget, already_risked)

    if not decisions:
        notify(
            f"run_market: sin pujas esta ejecución (presupuesto={remaining_budget}, "
            f"ya arriesgado hoy={already_risked}, candidatos evaluados={len(ranked)})."
        )
        return

    now = datetime.now(timezone.utc).isoformat()
    placed, failed = [], []

    with get_connection() as conn:
        for decision in decisions:
            try:
                offer = client.place_bid(decision["player_id"], decision["amount"])
                _persist_bid(conn, decision, "placed", now, comunio_offer_id=offer.get("offerid"))
                placed.append(decision)
            except (requests.RequestException, ComunioOfferError) as e:
                _persist_bid(conn, decision, "failed", now)
                failed.append((decision, str(e)))

    summary = [f"run_market: {len(placed)} puja(s) realizada(s)."]
    for d in placed:
        summary.append(f"  - jugador {d['player_id']}: {d['amount']} (score={d['score']:.3f})")
    if failed:
        summary.append(f"{len(failed)} puja(s) fallida(s):")
        for d, err in failed:
            summary.append(f"  - jugador {d['player_id']}: {err}")

    notify("\n".join(summary))


if __name__ == "__main__":
    run()
