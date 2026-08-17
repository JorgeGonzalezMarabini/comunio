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
    plantilla, no solo los titulares de esta jornada.

    Devuelve un dict por posición:
        {
            "POR": {"total": int, "available": int, "required": int,
                     "bench": int, "at_risk": bool},
            "DEF": {...}, "MED": {...}, "DEL": {...},
        }
    `available` cuenta solo jugadores SIN lesión/sanción (un suplente
    lesionado no protege de verdad). `bench` = available - required; si
    `bench <= 0`, perder a un solo jugador más de esa posición (cláusula,
    lesión...) ya deja un hueco en la alineación -> `at_risk = True`.
    """
    formation = formation or config.DEFAULT_FORMATION
    slots = FORMATIONS.get(formation)
    if slots is None:
        raise ValueError(f"Formación no soportada: {formation}")

    assessment = {}
    for position, required in slots.items():
        players_in_position = [p for p in squad if p.get("position") == position]
        available = [p for p in players_in_position if not is_injury_status(p.get("status"))]
        bench = len(available) - required
        assessment[position] = {
            "total": len(players_in_position),
            "available": len(available),
            "required": required,
            "bench": bench,
            "at_risk": bench <= 0,
        }
    return assessment


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
