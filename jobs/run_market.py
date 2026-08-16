"""
Job: evalúa el mercado y ejecuta pujas automáticas, 100% autónomo (sin
confirmación manual). Cada puja (o intento fallido) se audita en la tabla
`bids` con el score y motivo que la justificó.

Asume que jobs/sync_data.py ya corrió antes en el cron (así `players`/
`comunio_snapshots`/`external_stats` están al día) — este job solo lee de
la BD y decide, no vuelve a sincronizar stats.

Antes de evaluar el mercado, calcula el riesgo de plantilla (ver
engine/squad_risk.py: posiciones sin ningún suplente sano, donde un
"clausulazo"/lesión/sanción más dejaría un hueco en la alineación y -4
puntos) y prioriza esas posiciones al pujar (engine.bidding_strategy.
apply_position_priority) — sin dejar de respetar el umbral mínimo de score
ni los límites de seguridad, solo da ventaja frente a un candidato de
score similar en una posición ya cubierta.

Regla de negocio CRÍTICA (confirmada en la FAQ oficial de Comunio,
2026-08-16): Comunio no descuenta el saldo (`credit`) al colocar una
oferta, solo cuando se EJECUTA al cerrar el periodo de transferencias (que
puede durar más de un día) — y **saldo negativo al cierre de jornada son
0 puntos esa jornada entera**, sea cual sea la alineación. Por eso el
presupuesto disponible se calcula restando TODAS las ofertas pendientes
sin resolver (clients.comunio_client.total_pending_purchase_amount, la
fuente de verdad real de Comunio), no solo lo arriesgado hoy según nuestra
propia BD — eso último (`get_bids_risked_today`) sigue usándose, pero solo
como ritmo de gasto por jornada, no como protección de saldo.

place_bid() está **100% confirmado** (ver clients/comunio_client.py) —
puede fallar por HTTP (requests.RequestException) o por rechazo de negocio
con HTTP 200 (ComunioOfferError, ej. el jugador ya no está en el mercado);
cada intento va en su propio try/except para que un fallo de una puja no
tumbe las demás ni la ejecución completa.
"""
from datetime import datetime, timezone

import requests

import config
from clients.comunio_client import ComunioClient, ComunioOfferError, total_pending_purchase_amount
from db.models import get_connection, get_bids_risked_today, get_player_features
from engine.bidding_strategy import apply_position_priority, decide_bids_for_market
from engine.evaluator import evaluate_players
from engine.squad_risk import assess_squad_depth, depth_warnings
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

    # Riesgo de plantilla: posiciones sin ningún suplente sano (ver
    # engine/squad_risk.py). Necesita la plantilla propia, no solo el
    # mercado -- se resuelve igual que en jobs/set_lineup.py.
    squad_response = client.get_squad()
    squad_ids = {str(p["id"]) for p in squad_response.get("items", [])}
    all_players = get_player_features(only_on_market=False)
    squad_raw = [p for p in all_players if p["id"] in squad_ids]

    at_risk_positions = set()
    if squad_raw:
        depth = assess_squad_depth(squad_raw, formation=config.DEFAULT_FORMATION)
        at_risk_positions = {pos for pos, info in depth.items() if info["at_risk"]}
        risk_warnings = depth_warnings(depth)
    else:
        risk_warnings = []

    ranked = evaluate_players(raw_candidates)
    prioritized = apply_position_priority(ranked, at_risk_positions)

    offers = client.get_offers()
    remaining_budget = offers.get("credit", 0)
    already_risked = get_bids_risked_today()
    pending_committed = total_pending_purchase_amount(offers)

    decisions = decide_bids_for_market(
        prioritized, remaining_budget, already_risked, pending_committed=pending_committed
    )

    if not decisions:
        notify(
            f"run_market: sin pujas esta ejecución (saldo={remaining_budget}, "
            f"comprometido en ofertas pendientes={pending_committed}, ya arriesgado hoy={already_risked}, "
            f"candidatos evaluados={len(ranked)})."
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
        priority_tag = " [prioridad: posición en riesgo]" if d.get("position_at_risk") else ""
        summary.append(f"  - jugador {d['player_id']}: {d['amount']} (score={d['score']:.3f}){priority_tag}")
    if failed:
        summary.append(f"{len(failed)} puja(s) fallida(s):")
        for d, err in failed:
            summary.append(f"  - jugador {d['player_id']}: {err}")
    if risk_warnings:
        summary.append("⚠️ Riesgo de plantilla detectado (priorizado al pujar):")
        summary.extend(f"  - {w}" for w in risk_warnings)
    if pending_committed:
        summary.append(f"(comprometido en ofertas pendientes sin resolver: {pending_committed})")

    notify("\n".join(summary))


if __name__ == "__main__":
    run()
