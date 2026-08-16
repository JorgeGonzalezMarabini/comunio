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


def apply_position_priority(candidates: list[dict], at_risk_positions: set, boost: float = None) -> list[dict]:
    """
    Da prioridad a los candidatos de mercado en posiciones con riesgo de
    plantilla (ver engine.squad_risk.assess_squad_depth: posiciones sin
    ningún suplente sano, donde perder un titular más dejaría un hueco en
    la alineación y -4 puntos, ver README). Sube el score de esos
    candidatos un `boost` fijo (config.BIDDING_POSITION_RISK_BOOST) antes
    de decidir pujas, para que reforzar una posición en riesgo compita
    mejor frente a otro candidato de score similar en una posición ya
    cubierta — sin llegar a forzar la puja si el candidato es realmente
    malo (el boost es aditivo, no multiplica ni ignora el umbral mínimo).

    Guarda el score original en "base_score" (para la auditoría) y marca
    "position_at_risk": bool en cada candidato. Devuelve la lista
    reordenada por score ajustado (mayor primero) — decide_bids_for_market
    espera la lista ya en este orden.
    """
    boost = config.BIDDING_POSITION_RISK_BOOST if boost is None else boost
    adjusted = []
    for c in candidates:
        base_score = c.get("score", 0.0)
        is_at_risk = c.get("position") in at_risk_positions
        adjusted.append(
            {
                **c,
                "base_score": base_score,
                "score": base_score + boost if is_at_risk else base_score,
                "position_at_risk": is_at_risk,
            }
        )
    return sorted(adjusted, key=lambda p: p["score"], reverse=True)


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

    Si `player` viene de apply_position_priority() (tiene "position_at_risk"
    y "base_score"), la razón auditada deja constancia de si el score ya
    incluye el boost por posición en riesgo — para poder revisar después
    por qué se pujó por ese jugador en concreto.

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

    # Bug real detectado en producción (2026-08-15): el cap de seguridad
    # puede recortar `amount` por debajo del precio/VM real del jugador
    # (p.ej. si el presupuesto de jornada ya está casi agotado por pujas
    # anteriores en la misma pasada) — Comunio rechaza esas pujas por ir
    # por debajo del precio. Mejor no pujar que mandar una oferta condenada
    # a fallar: si el cap no llega ni al precio base, no hay margen seguro
    # para pujar por este jugador en este momento.
    if amount < price:
        return None

    if player.get("position_at_risk"):
        score_note = f"score={score:.3f} (base={player.get('base_score', score):.3f} + prioridad riesgo de plantilla)"
    else:
        score_note = f"score={score:.3f}"

    return {
        "player_id": player["id"],
        "amount": amount,
        "score": score,
        "position_at_risk": bool(player.get("position_at_risk")),
        "reason": (
            f"{score_note} >= umbral={min_score_threshold}; "
            f"precio_base={price}, prima={premium_pct:.1%} -> deseado={desired_amount}; "
            f"cap_seguro={cap} -> puja_final={amount}"
        ),
    }


def decide_bids_for_market(
    ranked_candidates: list[dict],
    remaining_budget: int,
    already_risked_this_matchday: int = 0,
    min_score_threshold: float = None,
    max_bids: int = None,
) -> list[dict]:
    """
    Recorre `ranked_candidates` (ya ordenados por score descendente, ver
    engine.evaluator.rank_players/evaluate_players) y decide una puja por
    cada uno mientras haya margen de seguridad.

    `already_risked_this_matchday`: importe ya arriesgado ANTES de esta
    llamada (p.ej. pujas de ejecuciones anteriores del cron en la misma
    jornada — ver db.models.get_bids_risked_today). Se acumula además el
    riesgo de las pujas decididas en esta misma pasada, así que ni una sola
    llamada ni varias llamadas en la misma jornada pueden superar el límite
    configurado entre todas.
    """
    decisions = []
    risked = already_risked_this_matchday
    for player in ranked_candidates:
        if max_bids is not None and len(decisions) >= max_bids:
            break
        decision = decide_bid(player, remaining_budget, risked, min_score_threshold)
        if decision is None:
            continue
        decisions.append(decision)
        risked += decision["amount"]
    return decisions
