"""
Job: evalúa el mercado y ejecuta pujas automáticas, 100% autónomo (sin
confirmación manual). Cada puja (o intento fallido) se audita en la tabla
`bids` con el score y motivo que la justificó.

Asume que jobs/sync_data.py ya corrió antes en el cron (así `players`/
`futmondo_snapshots`/`external_stats` están al día) — este job solo lee de
la BD y decide, no vuelve a sincronizar stats.

Antes de evaluar el mercado, calcula dos señales de prioridad sobre la
plantilla propia (ambas se suman si coinciden en el mismo candidato, ver
engine.bidding_strategy.apply_position_priority — ninguna fuerza la puja
de un candidato realmente malo, solo dan ventaja frente a otro de score
similar):

  1. Riesgo de plantilla (engine/squad_risk.py:assess_squad_depth):
     posiciones sin ningún suplente sano, donde un "clausulazo"/lesión/
     sanción más dejaría un hueco en la alineación. Señal de CANTIDAD.

  2. Mejora del once (engine/squad_risk.py:weakest_starter_scores): el
     candidato, evaluado con los MISMOS pesos con los que se elige la
     alineación real (config.LINEUP_EVALUATOR_WEIGHTS — el precio no debe
     importar), supera en calidad al titular más flojo de su posición
     hoy. Señal de CALIDAD: aunque la posición ya tenga suplente de sobra,
     fichar a alguien mejor que el peor titular actual sigue siendo una
     mejora real del equipo que sale a jugar. Para comparar en igualdad de
     condiciones, este score de alineación se calcula sobre plantilla +
     mercado juntos en la misma llamada a evaluate_players() (normalizar
     cada uno por separado los dejaría en escalas distintas, no
     comparables entre sí — ver engine/evaluator.py:normalize_pool).

El presupuesto disponible se calcula restando una aproximación a TODAS las
pujas pendientes sin resolver (db.models.get_pending_bid_amount() — nuestra
propia auditoría local, ver TODO en clients/futmondo_client.py sobre por
qué no hay una fuente externa mejor todavía en Futmondo), no solo lo
arriesgado hoy (get_bids_risked_today, que sigue usándose como ritmo de
gasto por jornada, no como protección de saldo).

place_bid() está **100% confirmado** (ver clients/futmondo_client.py) —
puede fallar por HTTP (requests.RequestException) o por rechazo de negocio
con HTTP 200 (FutmondoOfferError); cada intento va en su propio try/except
para que un fallo de una puja no tumbe las demás ni la ejecución completa.
"""
from datetime import datetime, timezone

import requests

import config
from clients.futmondo_client import FutmondoClient, FutmondoOfferError
from db.models import get_connection, get_bids_risked_today, get_pending_bid_amount, get_player_features
from engine.bidding_strategy import apply_position_priority, decide_bids_for_market
from engine.evaluator import evaluate_players
from engine.squad_risk import assess_squad_depth, depth_warnings, weakest_starter_scores
from notifier import notify


def _persist_bid(conn, decision: dict, status: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO bids (player_id, amount, status, score, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (str(decision["player_id"]), decision["amount"], status, decision["score"], decision["reason"], now),
    )


def run():
    client = FutmondoClient()

    raw_candidates = get_player_features(only_on_market=True)
    if not raw_candidates:
        notify("run_market: no hay candidatos en mercado en la BD (¿corrió sync_data antes?).")
        return

    # Riesgo de plantilla: posiciones sin ningún suplente sano (ver
    # engine/squad_risk.py). Necesita la plantilla propia, no solo el
    # mercado -- se resuelve igual que en jobs/set_lineup.py.
    roster_response = client.get_roster()
    roster_ids = {str(p["id"]) for p in roster_response.get("answer", [])}
    all_players = get_player_features(only_on_market=False)
    squad_raw = [p for p in all_players if p["id"] in roster_ids]

    at_risk_positions = set()
    upgrade_thresholds = {}
    lineup_score_by_id = {}
    if squad_raw:
        depth = assess_squad_depth(squad_raw, formation=config.DEFAULT_FORMATION)
        at_risk_positions = {pos for pos, info in depth.items() if info["at_risk"]}
        risk_warnings = depth_warnings(depth)

        # Score de alineación (LINEUP_EVALUATOR_WEIGHTS, sin precio) de
        # plantilla + mercado EN LA MISMA llamada, para que sean
        # comparables entre sí (ver docstring del módulo) -- de aquí sale
        # tanto el listón (squad) como el "lineup_score" de cada candidato
        # de mercado que se compara contra ese listón.
        lineup_scored = evaluate_players(squad_raw + raw_candidates, weights=config.LINEUP_EVALUATOR_WEIGHTS)
        lineup_score_by_id = {p["id"]: p["score"] for p in lineup_scored}
        squad_ids = {p["id"] for p in squad_raw}
        squad_lineup_ranked = [p for p in lineup_scored if p["id"] in squad_ids]
        upgrade_thresholds = weakest_starter_scores(squad_lineup_ranked, formation=config.DEFAULT_FORMATION)
    else:
        risk_warnings = []

    ranked = evaluate_players(raw_candidates)
    for p in ranked:
        if p["id"] in lineup_score_by_id:
            p["lineup_score"] = lineup_score_by_id[p["id"]]
    prioritized = apply_position_priority(ranked, at_risk_positions, upgrade_thresholds=upgrade_thresholds)

    information = client.get_information()
    remaining_budget = information.get("answer", {}).get("budget", 0)
    already_risked = get_bids_risked_today()
    pending_committed = get_pending_bid_amount()

    decisions = decide_bids_for_market(
        prioritized, remaining_budget, already_risked, pending_committed=pending_committed
    )

    if not decisions:
        notify(
            f"run_market: sin pujas esta ejecución (saldo={remaining_budget}, "
            f"comprometido en pujas pendientes={pending_committed}, ya arriesgado hoy={already_risked}, "
            f"candidatos evaluados={len(ranked)})."
        )
        return

    # `player_slug` no viene en `get_player_features()` (columnas de BD),
    # hace falta el candidato de mercado tal cual para pujar (place_bid lo
    # exige, ver clients/futmondo_client.py) -- se cruza por id.
    market_by_id = {str(p["id"]): p for p in client.get_market().get("answer", [])}

    now = datetime.now(timezone.utc).isoformat()
    placed, failed = [], []

    with get_connection() as conn:
        for decision in decisions:
            market_player = market_by_id.get(str(decision["player_id"]))
            if market_player is None:
                # Ya no está en el mercado (mercado cambió entre evaluar y pujar) -- no se puede pujar.
                _persist_bid(conn, decision, "failed", now)
                failed.append((decision, "el jugador ya no está en el mercado"))
                continue
            try:
                client.place_bid(decision["player_id"], market_player["slug"], decision["amount"])
                _persist_bid(conn, decision, "placed", now)
                placed.append(decision)
            except (requests.RequestException, FutmondoOfferError) as e:
                _persist_bid(conn, decision, "failed", now)
                failed.append((decision, str(e)))

    summary = [f"run_market: {len(placed)} puja(s) realizada(s)."]
    for d in placed:
        tags = []
        if d.get("position_at_risk"):
            tags.append("prioridad: posición en riesgo")
        if d.get("would_upgrade_lineup"):
            tags.append("mejora el once titular")
        priority_tag = f" [{', '.join(tags)}]" if tags else ""
        summary.append(f"  - jugador {d['player_id']}: {d['amount']} (score={d['score']:.3f}){priority_tag}")
    if failed:
        summary.append(f"{len(failed)} puja(s) fallida(s):")
        for d, err in failed:
            summary.append(f"  - jugador {d['player_id']}: {err}")
    if risk_warnings:
        summary.append("⚠️ Riesgo de plantilla detectado (priorizado al pujar):")
        summary.extend(f"  - {w}" for w in risk_warnings)
    if pending_committed:
        summary.append(f"(comprometido en pujas pendientes sin resolver: {pending_committed})")

    notify("\n".join(summary))


if __name__ == "__main__":
    run()
