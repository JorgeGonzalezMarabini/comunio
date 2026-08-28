"""
Evalúa riesgos estructurales de la plantilla, independientes de qué once
se ponga una jornada concreta (eso es cosa de engine.lineup_optimizer).

Motivación — Futmondo también tiene cláusula de rescisión (endpoint
`POST /1/market/rosterclause`, ver clients/futmondo_client.py:pay_clause,
visto en la propia UI de la app): si está activada en la liga, CUALQUIER
manager puede fichar un jugador de tu plantilla sin tu aprobación pagando
su cláusula. No se ha confirmado con una fuente oficial si Futmondo
penaliza igual que Comunio (-4 puntos por posición vacía en la alineación)
cuando eso te deja sin jugadores suficientes para cubrir una posición —
se mantiene la misma vigilancia por precaución conservadora: aunque la
penalización exacta no esté confirmada, quedarte sin poder alinear a
nadie en una posición nunca es deseable.

El bot no puede (todavía) saber si a un jugador concreto se lo van a
clausular, pero SÍ puede vigilar si la plantilla tiene margen de reserva
por posición — si una posición no tiene ningún suplente disponible, perder
a su único titular (por cláusula, lesión o sanción) deja el hueco
automáticamente. Eso es lo que comprueba este módulo.
"""
from __future__ import annotations

import config
from clients.futmondo_client import is_injury_status
from engine.lineup_optimizer import FORMATIONS


def assess_squad_depth(squad: list[dict], formation: str = None) -> dict:
    """
    Evalúa la profundidad de plantilla por posición para `formation`
    (por defecto config.DEFAULT_FORMATION).

    `squad`: lista de jugadores con al menos "position" (POR/DEF/MED/DEL)
    y "status" (para poder descontar lesionados/sancionados — ver
    clients.futmondo_client.is_injury_status()). Normalmente toda la
    plantilla, no solo los titulares de esta jornada. Si el jugador ya
    está puesto en venta (`on_market` truthy — `get_player_features()`,
    ver jobs/run_market.py — o `market` truthy — item crudo de
    `FutmondoClient.get_roster()`, ver engine/selling_strategy.py) también
    se descuenta de "disponible", igual que un lesionado: sigue en la
    plantilla hasta que alguien lo compre, pero no protege de verdad de un
    hueco si su venta se resuelve o si mientras tanto pierdes a otro
    titular de la misma posición.

    Devuelve un dict por posición:
        {
            "POR": {"total": int, "available": int, "required": int,
                     "bench": int, "at_risk": bool, "deficit": int},
            "DEF": {...}, "MED": {...}, "DEL": {...},
        }
    `available` cuenta solo jugadores SIN lesión/sanción y que no estén ya
    puestos en venta (ni uno ni otro protege de verdad). `bench` =
    available - required; si `bench <= 0`, perder a un solo jugador más de
    esa posición (cláusula, lesión...) ya deja un hueco en la alineación
    -> `at_risk = True`. `deficit = max(0, -bench)`: a diferencia de
    `at_risk` (dispara con margen CERO, ya usado para bloquear NUEVAS
    ventas en `engine.selling_strategy.decide_sales()`), `deficit` solo es
    positivo con margen NEGATIVO -- ni siquiera contando de vuelta como
    disponibles a los jugadores ya puestos en venta llegaríamos a los
    titulares requeridos. Ver `sales_to_cancel()`: es el umbral que sí
    justifica cancelar una venta YA LISTADA, no solo bloquear una nueva.
    """
    formation = formation or config.DEFAULT_FORMATION
    slots = FORMATIONS.get(formation)
    if slots is None:
        raise ValueError(f"Formación no soportada: {formation}")

    assessment = {}
    for position, required in slots.items():
        players_in_position = [p for p in squad if p.get("position") == position]
        available = [
            p
            for p in players_in_position
            if not is_injury_status(p.get("status")) and not p.get("on_market") and not p.get("market")
        ]
        bench = len(available) - required
        assessment[position] = {
            "total": len(players_in_position),
            "available": len(available),
            "required": required,
            "bench": bench,
            "at_risk": bench <= 0,
            "deficit": max(0, -bench),
        }
    return assessment


def sales_to_cancel(
    assessment: dict, open_sales: list[dict], position_by_player_id: dict[str, str]
) -> list[dict]:
    """
    A partir de un `assessment` de `assess_squad_depth()` y las ventas que
    seguimos creyendo listadas (`db.models.get_open_sales()`), decide
    cuáles hay que cancelar para no quedarnos sin jugadores suficientes
    para cubrir el once en alguna posición -- típicamente porque OTRO
    jugador de esa misma posición desapareció de la plantilla entre medias
    (cláusula pagada por otro manager, venta aceptada, lesión...; Futmondo
    no distingue la causa, ver docstring de jobs/sync_data.py).

    Deliberadamente se actúa solo sobre `deficit > 0` (margen NEGATIVO),
    NUNCA sobre `at_risk` a secas (margen cero): un jugador recién puesto
    en venta YA resta uno de "disponible" (ver docstring de
    `assess_squad_depth`), así que "sin margen" es el estado normal nada
    más listarlo -- usar `at_risk` aquí deshacería cualquier venta en la
    primera pasada después de crearla. `deficit` solo es positivo cuando
    ni siquiera contando de vuelta a estos jugadores como disponibles se
    llegaría a los titulares requeridos: ahí sí hace falta recuperar
    alguno de verdad, no solo dejar de crear más riesgo.

    Devuelve la sublista de `open_sales` a cancelar -- como mucho
    `deficit` por posición, las más antiguas primero (menor `id`, orden de
    creación) dentro de cada una: cancelar de más no hace daño real (el
    jugador solo vuelve a la plantilla), pero cancelar solo lo necesario
    evita renunciar a más plusvalía potencial de la imprescindible.
    Ventas de jugadores que ya no aparecen en `position_by_player_id`
    (típicamente porque `jobs.sync_data._reconcile_sales()` ya los marcó
    'sold' en esta misma pasada, o el sync aún no ha corrido) se ignoran:
    no hay nada que cancelar si el jugador ya no está en la plantilla.
    """
    sales_by_position: dict[str, list[dict]] = {}
    for sale in open_sales:
        position = position_by_player_id.get(str(sale["player_id"]))
        if position is None:
            continue
        sales_by_position.setdefault(position, []).append(sale)

    to_cancel = []
    for position, info in assessment.items():
        deficit = info["deficit"]
        if not deficit:
            continue
        candidates = sorted(sales_by_position.get(position, []), key=lambda s: s["id"])
        to_cancel.extend(candidates[:deficit])
    return to_cancel


def depth_warnings(assessment: dict) -> list[str]:
    """Mensajes legibles (uno por posición en riesgo) para notificar/loggear."""
    warnings = []
    for position, info in assessment.items():
        if info["at_risk"]:
            warnings.append(
                f"{position}: {info['available']} disponible(s) para {info['required']} titular(es) "
                f"({info['bench']} de margen) — una cláusula, lesión o sanción más en esta posición "
                f"dejaría un hueco en la alineación (-4 puntos por posición vacía)."
            )
    return warnings


def weakest_starter_scores(squad_ranked: list[dict], formation: str = None) -> dict:
    """
    Score (con `config.LINEUP_EVALUATOR_WEIGHTS`, sin ajuste de rival) del
    titular más flojo que hoy jugaría en cada posición dada la plantilla
    actual — el listón que un candidato de MERCADO debe superar para ser
    una mejora REAL del once, no solo tapar un hueco de banquillo (a
    diferencia de `assess_squad_depth()`, que solo mira CANTIDAD de sanos,
    nunca su calidad).

    `squad_ranked`: la plantilla ya evaluada con
    `engine.evaluator.evaluate_players(squad_raw, weights=config.
    LINEUP_EVALUATOR_WEIGHTS)` — MISMOS pesos con los que de verdad se
    decide la alineación (el precio no debe importar, un jugador de la
    plantilla ya está comprado). Cada jugador necesita al menos
    "position", "score" y "status".

    Deliberadamente SIN `apply_fixture_difficulty()` (a diferencia de
    `engine.lineup_optimizer.pick_lineup`): esto no elige el once de una
    jornada concreta, compara el valor estructural del jugador para la
    plantilla a medio plazo — mezclar la dificultad del próximo rival
    haría que el listón subiera o bajara solo porque le toca un rival
    distinto, sin que la plantilla haya cambiado en nada.

    Mismo criterio sanos-antes-que-lesionados que
    `engine.lineup_optimizer._rank_healthy_first()` (un lesionado con buen
    score no debería fijar el listón si hay alternativas sanas disponibles).

    Devuelve {position: score} con el score del titular menos valioso de
    cada posición, o None si esa posición no tiene todavía ni un solo
    jugador en plantilla (cualquier candidato de mercado sería
    automáticamente una mejora).
    """
    formation = formation or config.DEFAULT_FORMATION
    slots = FORMATIONS.get(formation)
    if slots is None:
        raise ValueError(f"Formación no soportada: {formation}")

    thresholds = {}
    for position, required in slots.items():
        candidates = [p for p in squad_ranked if p.get("position") == position]
        if not candidates:
            thresholds[position] = None
            continue
        healthy = [p for p in candidates if not is_injury_status(p.get("status"))]
        injured = [p for p in candidates if is_injury_status(p.get("status"))]
        ordered = sorted(healthy, key=lambda p: p["score"], reverse=True) + sorted(
            injured, key=lambda p: p["score"], reverse=True
        )
        starters = ordered[:required]
        thresholds[position] = starters[-1]["score"] if starters else None
    return thresholds
