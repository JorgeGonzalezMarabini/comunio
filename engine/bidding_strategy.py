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


def max_biddable_amount(remaining_budget: int, already_risked_this_matchday: int, pending_committed: int = 0) -> int:
    """
    Calcula el máximo que se puede pujar ahora mismo respetando:
      - el tope absoluto por jugador
      - el % máximo de presupuesto arriesgable en la jornada
      - la reserva mínima que nunca se toca
      - el dinero YA comprometido en ofertas pendientes sin resolver

    `pending_committed`: aproximación a TODAS tus pujas de compra todavía
    pendientes (sin resolver), sea de hoy o de días anteriores — ver
    db.models.get_pending_bid_amount() / clients.futmondo_client.
    total_pending_bid_amount(). En Comunio esto estaba confirmado por su
    propia FAQ oficial (saldo negativo al cierre de jornada = 0 puntos esa
    jornada entera) y por un endpoint real que listaba las ofertas
    pendientes; en Futmondo no se ha encontrado ni la regla exacta de
    penalización por saldo negativo confirmada en una fuente oficial ni un
    endpoint equivalente (ver TODO en clients/futmondo_client.py) — se
    mantiene la misma resta defensiva por precaución: si el saldo
    (`budget`/`information`) tampoco se descuenta hasta resolver el
    mercado (razonable asumirlo, visto que el `budget` que devuelve
    `/2/userteam/changeplayer` no cambia al pujar), un bot que ignore esto
    podría comprometer más de lo que el saldo real soporta.
    """
    limits = config.BIDDING_SAFETY_LIMITS

    truly_available = max(0, remaining_budget - pending_committed)
    usable_budget = max(0, truly_available - limits["min_budget_reserve"])
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
    pending_committed: int = 0,
) -> dict | None:
    """
    Decide si pujar por `player` (debe incluir "id", "score" de
    engine/evaluator.py, y "price" — el VM/precio real de Futmondo, ver
    db.models.get_player_features) y cuánto, respetando los límites de
    seguridad.

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

    `pending_committed`: ver max_biddable_amount() — dinero ya comprometido
    en pujas pendientes sin resolver (protección defensiva de saldo, ver
    README).

    Devuelve None si no se debe pujar, o un dict:
        {"player_id": ..., "amount": ..., "score": ..., "reason": "..."}
    listo para persistir en la tabla `bids` (auditoría).
    """
    min_score_threshold = config.BIDDING_MIN_SCORE_THRESHOLD if min_score_threshold is None else min_score_threshold

    score = player.get("score", 0)
    if score < min_score_threshold:
        return None

    price = player.get("price") or 0
    if price <= 0:
        # Sin precio de referencia real no hay base segura para calcular
        # una puja — mejor no pujar que inventar un importe a ciegas.
        return None

    premium_pct = max(0.0, score) * config.BIDDING_SAFETY_LIMITS["max_premium_over_price_pct"]
    desired_amount = int(price * (1 + premium_pct))

    cap = max_biddable_amount(remaining_budget, already_risked_this_matchday, pending_committed)
    if cap <= 0:
        return None

    amount = min(desired_amount, cap)

    # Bug real detectado en producción con Comunio (2026-08-15, ver
    # historial de commits): el cap de seguridad puede recortar `amount`
    # por debajo del precio/VM real del jugador (p.ej. si el presupuesto
    # de jornada ya está casi agotado por pujas anteriores en la misma
    # pasada) — Comunio rechazaba esas pujas por ir por debajo del precio;
    # de Futmondo no se ha confirmado el mismo rechazo exacto, pero mejor
    # no pujar que mandar una oferta con toda probabilidad condenada a
    # fallar: si el cap no llega ni al precio base, no hay margen seguro
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
    pending_committed: int = 0,
) -> list[dict]:
    """
    Recorre `ranked_candidates` (ya ordenados por score descendente, ver
    engine.evaluator.rank_players/evaluate_players) y decide una puja por
    cada uno mientras haya margen de seguridad.

    `already_risked_this_matchday`: importe ya arriesgado ANTES de esta
    llamada dentro del ritmo de gasto por jornada (pacing, no protección de
    saldo — ver db.models.get_bids_risked_today). Se acumula además el
    riesgo de las pujas decididas en esta misma pasada, así que ni una sola
    llamada ni varias llamadas en la misma jornada pueden superar el límite
    configurado entre todas.

    `pending_committed`: la protección de saldo (defensiva, ver TODO en
    clients/futmondo_client.py) — aproximación a TODAS las pujas de compra
    pendientes sin resolver ahora mismo (ver db.models.
    get_pending_bid_amount(), no solo las de hoy). Se pasa tal cual a cada
    decide_bid() por si Futmondo tampoco descuenta el saldo hasta que una
    puja se resuelve (razonable asumirlo, sin confirmar la regla exacta de
    penalización por saldo negativo — ver README).
    """
    decisions = []
    risked = already_risked_this_matchday
    for player in ranked_candidates:
        if max_bids is not None and len(decisions) >= max_bids:
            break
        decision = decide_bid(player, remaining_budget, risked, min_score_threshold, pending_committed=pending_committed)
        if decision is None:
            continue
        decisions.append(decision)
        risked += decision["amount"]
    return decisions
