"""
Estrategia de pujas: decide a quién pujar y cuánto, dado el score de
engine/evaluator.py y el presupuesto restante.

Estos límites de seguridad son innegociables: ninguna decisión del modelo
puede saltárselos, sea cual sea el score calculado. El objetivo es que un
fallo del modelo (bug, datos corruptos, score mal calculado) nunca pueda
fundir el equipo.
"""
from __future__ import annotations

import config


class BudgetExceededError(Exception):
    """Una decisión intentó saltarse un límite de seguridad configurado."""


def max_biddable_amount(remaining_budget: int, already_risked_this_matchday: int) -> int:
    """
    Calcula el máximo que se puede pujar ahora mismo respetando:
      - el tope absoluto por jugador
      - el % máximo de presupuesto arriesgable en la jornada
      - la reserva mínima que nunca se toca
    """
    limits = config.BIDDING_SAFETY_LIMITS

    usable_budget = max(0, remaining_budget - limits["min_budget_reserve"])
    matchday_cap = int(usable_budget * limits["max_budget_risk_per_matchday_pct"])
    matchday_remaining = max(0, matchday_cap - already_risked_this_matchday)

    return min(limits["max_spend_per_player"], matchday_remaining, usable_budget)


def decide_bid(
    player: dict,
    remaining_budget: int,
    already_risked_this_matchday: int,
    min_score_threshold: float = None,
) -> dict | None:
    """
    Decide si pujar por `player` (debe incluir "id", "score" de
    engine/evaluator.py, y "price"/"recommended_price" — el VM real de
    Comunio, ver db.models.get_player_features) y cuánto, respetando los
    límites de seguridad.

    El importe se ancla al precio/VM REAL del jugador, no a una fracción
    arbitraria del presupuesto: se puja el VM + una prima que crece con el
    score (config.BIDDING_SAFETY_LIMITS["max_premium_over_price_pct"]),
    topado siempre por max_biddable_amount(). Un jugador con score 0 no se
    puja por encima de su VM; uno con score 1.0 se puja hasta el máximo de
    prima configurado.

    Devuelve None si no se debe pujar, o un dict:
        {"player_id": ..., "amount": ..., "score": ..., "reason": "..."}
    listo para persistir en la tabla `bids` (auditoría).
    """
    min_score_threshold = config.BIDDING_MIN_SCORE_THRESHOLD if min_score_threshold is None else min_score_threshold

    score = player.get("score", 0)
    if score < min_score_threshold:
        return None

    price = player.get("price") or player.get("recommended_price") or 0
    if price <= 0:
        # Sin precio de referencia real no hay base segura para calcular
        # una puja — mejor no pujar que inventar un importe a ciegas.
        return None

    premium_pct = max(0.0, score) * config.BIDDING_SAFETY_LIMITS["max_premium_over_price_pct"]
    desired_amount = int(price * (1 + premium_pct))

    cap = max_biddable_amount(remaining_budget, already_risked_this_matchday)
    if cap <= 0:
        return None

    amount = min(desired_amount, cap)
    if amount <= 0:
        return None

    return {
        "player_id": player["id"],
        "amount": amount,
        "score": score,
        "reason": (
            f"score={score:.3f} >= umbral={min_score_threshold}; "
            f"precio_base={price}, prima={premium_pct:.1%} -> deseado={desired_amount}; "
            f"cap_seguro={cap} -> puja_final={amount}"
        ),
    }


def decide_bids_for_market(
    ranked_candidates: list[dict],
    remaining_budget: int,
    min_score_threshold: float = None,
    max_bids: int = None,
) -> list[dict]:
    """
    Recorre `ranked_candidates` (ya ordenados por score descendente, ver
    engine.evaluator.rank_players/evaluate_players) y decide una puja por
    cada uno mientras haya margen de seguridad, acumulando en la misma
    pasada el riesgo ya comprometido por las pujas anteriores de esta
    ejecución (no solo el de jornadas pasadas) — así dos pujas en la misma
    llamada no pueden sumar más que el límite de jornada entre las dos.
    """
    decisions = []
    risked_this_pass = 0
    for player in ranked_candidates:
        if max_bids is not None and len(decisions) >= max_bids:
            break
        decision = decide_bid(player, remaining_budget, risked_this_pass, min_score_threshold)
        if decision is None:
            continue
        decisions.append(decision)
        risked_this_pass += decision["amount"]
    return decisions
