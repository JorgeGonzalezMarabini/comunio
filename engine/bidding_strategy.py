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
    min_score_threshold: float = 0.0,
) -> dict | None:
    """
    Decide si pujar por `player` (debe incluir al menos "id" y "score") y
    cuánto, respetando los límites de seguridad.

    Devuelve None si no se debe pujar, o un dict:
        {"player_id": ..., "amount": ..., "score": ..., "reason": "..."}
    listo para persistir en la tabla `bids` (auditoría).

    TODO: definir la fórmula real score -> cantidad a pujar (por ahora es
    un placeholder lineal simple) una vez haya datos reales para calibrar.
    """
    if player.get("score", 0) < min_score_threshold:
        return None

    cap = max_biddable_amount(remaining_budget, already_risked_this_matchday)
    if cap <= 0:
        return None

    # Placeholder: cuanto mayor el score, mayor fracción del cap se arriesga.
    amount = int(cap * min(1.0, max(0.0, player["score"])))
    if amount <= 0:
        return None

    return {
        "player_id": player["id"],
        "amount": amount,
        "score": player["score"],
        "reason": (
            f"score={player['score']:.3f} >= umbral={min_score_threshold}; "
            f"puja={amount} dentro de cap seguro={cap}"
        ),
    }
