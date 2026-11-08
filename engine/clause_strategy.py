"""
Elección de clausulazos sobre jugadores de otros managers (ver
jobs/run_clauses.py y README, "Clausulazos").

Criterio pedido por el usuario (2026-11-08): solo merece la pena pagar una
cláusula si el jugador entra en el once por delante del peor titular de su
posición, o si tapa un hueco (posición con menos jugadores disponibles que
titulares, ver engine.squad_risk.assess_squad_depth). Tope por clausulazo:
config.CLAUSE_MAX_BUDGET_PCT del saldo disponible.

Mismos filtros duros que las pujas de mercado (forma mínima y partidos
mínimos, ver engine.evaluator.form_points/meets_min_matches), más uno
propio: nada de jugadores lesionados NI en duda -- en una puja de mercado
se acepta la duda con penalización, pero una cláusula cuesta bastante más
que el valor del jugador (ver tabla de cláusulas de Futmondo, README) y
no se recupera vendiéndolo.
"""
from __future__ import annotations

import config
from clients.futmondo_client import is_injury_status
from engine.evaluator import form_points, meets_min_matches


def select_clause_targets(
    candidates: list[dict],
    upgrade_thresholds: dict,
    deficit_positions: set,
    available_budget: int,
    max_budget_pct: float = None,
    min_average_points: float = None,
) -> list[dict]:
    """
    `candidates`: jugadores de otros managers con los campos de
    `db.models.get_player_features()` más "lineup_score" (evaluados con
    config.LINEUP_EVALUATOR_WEIGHTS en el MISMO pool que la plantilla
    propia, para que sean comparables con `upgrade_thresholds`) y
    "clause_price".

    `upgrade_thresholds`: {posición: score del peor titular actual, o None
    si no hay nadie en esa posición}, ver
    engine.squad_risk.weakest_starter_scores().

    `deficit_positions`: posiciones donde falta al menos un titular.

    Devuelve los candidatos que cumplen, con "fills_deficit" y
    "lineup_gain" (lineup_score menos el listón de su posición), ordenados
    de mejor a peor: primero los que tapan un hueco, luego por mejora del
    once y, a igualdad, el más barato.
    """
    max_budget_pct = config.CLAUSE_MAX_BUDGET_PCT if max_budget_pct is None else max_budget_pct
    min_average_points = config.BIDDING_MIN_AVERAGE_POINTS if min_average_points is None else min_average_points
    max_price = int(max(0, available_budget) * max_budget_pct)

    selected = []
    for c in candidates:
        price = c.get("clause_price") or 0
        if price <= 0 or price > max_price:
            continue
        if is_injury_status(c.get("status")):
            continue
        if form_points(c) < min_average_points or not meets_min_matches(c):
            continue

        position = c.get("position")
        threshold = upgrade_thresholds.get(position)
        lineup_score = c.get("lineup_score", 0.0)
        gain = lineup_score - threshold if threshold is not None else lineup_score
        fills_deficit = position in deficit_positions
        if not fills_deficit and gain <= 0:
            continue
        selected.append({**c, "fills_deficit": fills_deficit, "lineup_gain": gain})

    selected.sort(key=lambda c: (not c["fills_deficit"], -c["lineup_gain"], c["clause_price"]))
    return selected
