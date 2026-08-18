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


def _weighted_average(values_weights: list[tuple[float, float]]) -> float:
    """
    Media ponderada de `[(valor, peso), ...]`. Si la suma de pesos es 0
    (todos los pesos son 0, p. ej. ningún candidato con score positivo) cae
    a la media simple en vez de dividir por cero. Lista vacía -> 0.0.
    """
    if not values_weights:
        return 0.0
    weight_sum = sum(w for _, w in values_weights)
    if weight_sum <= 0:
        return sum(v for v, _ in values_weights) / len(values_weights)
    return sum(v * w for v, w in values_weights) / weight_sum


def dynamic_player_cap(
    remaining_budget: int,
    squad: list[dict],
    market_candidates: list[dict],
    weights: dict = None,
) -> int:
    """
    Tope dinámico por jugador — sustituye al antiguo tope FIJO de 15M
    (`config.BIDDING_SAFETY_LIMITS["max_spend_per_player_floor"]`, que
    ahora es solo el suelo, ver docstring en config.py). Un número fijo en
    euros se queda obsoleto con el tiempo: el valor de los jugadores en
    Futmondo sube con el rendimiento a lo largo de la temporada, así que un
    tope fijo bloquea cada vez a más jugadores (los mejores, normalmente)
    aunque el presupuesto disponible también haya crecido — `decide_bid()`
    nunca puja por debajo del precio real, así que un jugador con precio
    por encima del tope queda excluido de pujas para siempre, sin importar
    su score.

    Combina tres señales (`config.BIDDING_DYNAMIC_CAP_WEIGHTS`, deben sumar
    1.0), cada una capturando una noción distinta de "qué es razonable
    pujar por UN jugador ahora mismo":

      - squad_value: precio medio de TU plantilla actual (`squad`, espera
        "price" por jugador) — escala con el crecimiento real de tu
        equipo; estable porque no depende de qué haya a la venta hoy.
      - market_value: precio medio de `market_candidates` PONDERADO por
        `score` (no precio simple, espera "price" y "score" por
        candidato) — sigue la inflación general del mercado sin dejar que
        un jugador carísimo con score bajo (mal rendimiento, lesión, a la
        venta por casualidad un día concreto) descalibre el tope; los
        candidatos realmente atractivos (score alto) pesan más en la
        media. Scores negativos (penalización por lesión) se tratan como 0
        de peso, no restan.
      - budget_pct: `remaining_budget * max_pct_of_budget_per_player` — %
        del saldo disponible ahora, ligado a lo que de verdad se puede
        permitir hoy.

    El resultado nunca baja del suelo de seguridad
    (`max_spend_per_player_floor`) — protege casos degenerados (plantilla
    o mercado vacíos/muy baratos, típico al empezar la temporada) sin
    abrir una vía para gastar de más: `max_biddable_amount()` sigue
    aplicando DESPUÉS los topes de jornada y saldo usable, que no cambian.
    """
    weights = weights or config.BIDDING_DYNAMIC_CAP_WEIGHTS
    limits = config.BIDDING_SAFETY_LIMITS

    squad_prices = [p["price"] for p in squad if p.get("price")]
    avg_squad_value = sum(squad_prices) / len(squad_prices) if squad_prices else 0.0

    market_priced = [
        (p["price"], max(p.get("score", 0.0), 0.0)) for p in market_candidates if p.get("price")
    ]
    avg_market_value = _weighted_average(market_priced)

    budget_component = max(0, remaining_budget) * limits["max_pct_of_budget_per_player"]

    blended = (
        weights["squad_value"] * avg_squad_value
        + weights["market_value"] * avg_market_value
        + weights["budget_pct"] * budget_component
    )
    return max(limits["max_spend_per_player_floor"], int(blended))


def max_biddable_amount(
    remaining_budget: int,
    already_risked_this_matchday: int,
    pending_committed: int = 0,
    player_cap: int = None,
) -> int:
    """
    Calcula el máximo que se puede pujar ahora mismo respetando:
      - el tope por jugador (dinámico, ver dynamic_player_cap() — o su
        suelo fijo si no se pasa `player_cap`)
      - el % máximo de presupuesto arriesgable en la jornada
      - la reserva mínima que nunca se toca
      - el dinero YA comprometido en ofertas pendientes sin resolver

    `player_cap`: tope por jugador ya calculado (ver dynamic_player_cap()).
    Si no se pasa, cae a `config.BIDDING_SAFETY_LIMITS
    ["max_spend_per_player_floor"]` — mismo comportamiento que el antiguo
    tope fijo, para no romper llamadas/tests que no calculan el dinámico.

    `pending_committed`: aproximación a TODAS tus pujas de compra todavía
    pendientes (sin resolver), sea de hoy o de días anteriores — ver
    db.models.get_pending_bid_amount() cruzado con clients.futmondo_client.
    real_pending_bid_amount() (TODO.md #3, resuelto: el segundo SÍ viene
    confirmado del propio Futmondo, vía el campo "bid" de get_market()).
    En Comunio esto estaba confirmado por su propia FAQ oficial (saldo
    negativo al cierre de jornada = 0 puntos esa jornada entera); en
    Futmondo no se ha encontrado esa regla exacta de penalización por
    saldo negativo en una fuente oficial — se mantiene la misma resta
    defensiva por precaución: si el saldo (`budget`/`information`) tampoco
    se descuenta hasta resolver el mercado (razonable asumirlo, visto que
    el `budget` que devuelve `/2/userteam/changeplayer` no cambia al
    pujar), un bot que ignore esto podría comprometer más de lo que el
    saldo real soporta.
    """
    limits = config.BIDDING_SAFETY_LIMITS
    if player_cap is None:
        player_cap = limits["max_spend_per_player_floor"]

    truly_available = max(0, remaining_budget - pending_committed)
    usable_budget = max(0, truly_available - limits["min_budget_reserve"])
    matchday_cap = int(usable_budget * limits["max_budget_risk_per_matchday_pct"])
    matchday_remaining = max(0, matchday_cap - already_risked_this_matchday)

    return min(player_cap, matchday_remaining, usable_budget)


def apply_position_priority(
    candidates: list[dict],
    at_risk_positions: set,
    boost: float = None,
    upgrade_thresholds: dict = None,
    upgrade_boost: float = None,
) -> list[dict]:
    """
    Da prioridad a los candidatos de mercado por dos señales distintas,
    que pueden coincidir en el mismo candidato y sumarse:

    1. Riesgo de plantilla (ver engine.squad_risk.assess_squad_depth):
       posiciones sin ningún suplente sano, donde perder un titular más
       dejaría un hueco en la alineación y -4 puntos (ver README). Sube el
       score `boost` (config.BIDDING_POSITION_RISK_BOOST). Señal de
       CANTIDAD: da igual lo bueno o malo que sea el titular actual, lo
       que falta es un cuerpo de más en el banquillo.

    2. Mejora del once (ver engine.squad_risk.weakest_starter_scores):
       `upgrade_thresholds` es {position: score|None} con el score (con
       config.LINEUP_EVALUATOR_WEIGHTS) del titular más flojo de cada
       posición HOY. Cada candidato necesita también un campo
       "lineup_score" — ese mismo candidato evaluado con esos MISMOS
       pesos (no el "score" de puja que ya trae, que valora precio y no es
       comparable: mezclar unidades distintas daría una comparación sin
       sentido). Si `lineup_score` supera el listón (o el listón es None —
       plantilla sin nadie todavía en esa posición), sube el score
       `upgrade_boost` (config.BIDDING_UPGRADE_BOOST). Señal de CALIDAD:
       el titular actual ya existe, pero fichar a este candidato y
       sentarlo en el banquillo mejoraría el once real, no solo el margen
       de suplentes.

    Ninguna de las dos fuerza la puja de un candidato realmente malo (los
    boosts son aditivos, no multiplican ni ignoran el umbral mínimo de
    score) — solo dan ventaja frente a otro candidato de score similar en
    una posición sin ese problema.

    Guarda el score original en "base_score" (para la auditoría) y marca
    "position_at_risk"/"would_upgrade_lineup": bool en cada candidato.
    Devuelve la lista reordenada por score ajustado (mayor primero) —
    decide_bids_for_market espera la lista ya en este orden.
    """
    boost = config.BIDDING_POSITION_RISK_BOOST if boost is None else boost
    upgrade_boost = config.BIDDING_UPGRADE_BOOST if upgrade_boost is None else upgrade_boost
    upgrade_thresholds = upgrade_thresholds or {}

    adjusted = []
    for c in candidates:
        base_score = c.get("score", 0.0)
        is_at_risk = c.get("position") in at_risk_positions

        would_upgrade = False
        if c.get("position") in upgrade_thresholds:
            threshold = upgrade_thresholds[c["position"]]
            would_upgrade = threshold is None or c.get("lineup_score", base_score) > threshold

        score = base_score
        if is_at_risk:
            score += boost
        if would_upgrade:
            score += upgrade_boost

        adjusted.append(
            {
                **c,
                "base_score": base_score,
                "score": score,
                "position_at_risk": is_at_risk,
                "would_upgrade_lineup": would_upgrade,
            }
        )
    return sorted(adjusted, key=lambda p: p["score"], reverse=True)


def decide_bid(
    player: dict,
    remaining_budget: int,
    already_risked_this_matchday: int,
    min_score_threshold: float = None,
    pending_committed: int = 0,
    player_cap: int = None,
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

    Si `player` viene de apply_position_priority() (tiene "position_at_risk"/
    "would_upgrade_lineup" y "base_score"), la razón auditada deja
    constancia de qué boost(s) ya incluye el score — para poder revisar
    después por qué se pujó por ese jugador en concreto.

    `pending_committed`: ver max_biddable_amount() — dinero ya comprometido
    en pujas pendientes sin resolver (protección defensiva de saldo, ver
    README).

    `player_cap`: ver max_biddable_amount()/dynamic_player_cap() — tope
    dinámico por jugador ya calculado para esta pasada del mercado.

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

    cap = max_biddable_amount(remaining_budget, already_risked_this_matchday, pending_committed, player_cap=player_cap)
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

    boosts_applied = []
    if player.get("position_at_risk"):
        boosts_applied.append("prioridad riesgo de plantilla")
    if player.get("would_upgrade_lineup"):
        boosts_applied.append("mejora el once titular")

    if boosts_applied:
        score_note = f"score={score:.3f} (base={player.get('base_score', score):.3f} + " + " + ".join(boosts_applied) + ")"
    else:
        score_note = f"score={score:.3f}"

    return {
        "player_id": player["id"],
        "amount": amount,
        "score": score,
        "position_at_risk": bool(player.get("position_at_risk")),
        "would_upgrade_lineup": bool(player.get("would_upgrade_lineup")),
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
    player_cap: int = None,
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

    `pending_committed`: la protección de saldo (ver TODO.md #3, resuelto)
    — TODAS las pujas de compra pendientes sin resolver ahora mismo,
    combinando `db.models.get_pending_bid_amount()` (auditoría local) con
    `clients.futmondo_client.real_pending_bid_amount()` (confirmado del
    propio Futmondo vía el campo "bid" de get_market()), no solo las de
    hoy. Se pasa tal cual a cada decide_bid() por si Futmondo tampoco
    descuenta el saldo hasta que una puja se resuelve (razonable asumirlo,
    sin confirmar la regla exacta de penalización por saldo negativo — ver
    README).

    `player_cap`: tope dinámico por jugador ya calculado para esta pasada
    (ver dynamic_player_cap()) — se pasa tal cual a cada decide_bid().
    """
    decisions = []
    risked = already_risked_this_matchday
    for player in ranked_candidates:
        if max_bids is not None and len(decisions) >= max_bids:
            break
        decision = decide_bid(
            player, remaining_budget, risked, min_score_threshold, pending_committed=pending_committed, player_cap=player_cap
        )
        if decision is None:
            continue
        decisions.append(decision)
        risked += decision["amount"]
    return decisions
