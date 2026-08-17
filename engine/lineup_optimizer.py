"""
Optimizador de alineaciones: elige el once inicial válido para una
formación configurable (por defecto config.DEFAULT_FORMATION) maximizando
el score esperado de cada jugador para la próxima jornada.

El score esperado combina el score de engine.evaluator (forma reciente,
puntos/precio, xG...) con la dificultad del próximo rival vía
apply_fixture_difficulty() (dato real: clients.laliga_stats_client.
next_match_difficulty(), basado en el forecast de Understat).
"""
import config

# formación -> nº de jugadores por posición (sin contar portero, que es fijo).
FORMATIONS = {
    "4-4-2": {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2},
    "4-3-3": {"POR": 1, "DEF": 4, "MED": 3, "DEL": 3},
    "3-4-3": {"POR": 1, "DEF": 3, "MED": 4, "DEL": 3},
    "5-3-2": {"POR": 1, "DEF": 5, "MED": 3, "DEL": 2},
}


def apply_fixture_difficulty(
    players: list[dict], difficulty_by_team: dict[str, float], weight: float = None
) -> list[dict]:
    """
    Ajusta el score base de cada jugador (campo "score", el que pone
    engine.evaluator) por la dificultad de su próximo rival, guardando el
    resultado en "expected_score" — el campo que espera pick_lineup().

    `difficulty_by_team`: {nombre_equipo: dificultad 0..1, más alto = rival
    más difícil}, ver clients.laliga_stats_client.next_match_difficulty().
    Un jugador de un equipo sin entrada en `difficulty_by_team` (sin
    próximo partido conocido) se deja con su score base sin ajustar.
    """
    weight = config.LINEUP_DIFFICULTY_WEIGHT if weight is None else weight
    adjusted = []
    for p in players:
        difficulty = difficulty_by_team.get(p.get("team"))
        base_score = p.get("score", 0.0)
        expected = base_score * (1 - weight * difficulty) if difficulty is not None else base_score
        adjusted.append({**p, "expected_score": expected})
    return adjusted


# Orden CONFIRMADO al 100% pero SOLO para la formación 4-4-2 (2026-08-17,
# alineación real de prueba, cada jugador colocado interceptando la
# llamada POST real del frontend a /2/userteam/changeplayer + relectura
# con GET /1/userteam/lineup confirmando el `position` numérico asignado):
# delanteros -> centrocampistas -> defensas -> portero, numerados
# consecutivamente EMPEZANDO EN 0 (a diferencia de Comunio, que empezaba
# en 1) — portero siempre el ÚLTIMO índice (10 en un 4-4-2 con 11
# titulares). Confirmado en la prueba real: Tzolakis (portero) -> 10,
# Calafiori/Struijk/(cuarto defensa) (defensas) -> 6, 7, 8(, 9).
#
# TODO sin confirmar: para otras formaciones (4-3-3, 3-4-3, 5-3-2) se
# GENERALIZA este mismo criterio (numeración consecutiva por el orden de
# LINEUP_SLOT_POSITION_ORDER, portero siempre el último índice) porque es
# lo más simple consistente con lo observado, pero no se ha probado con
# una formación distinta de 4-4-2 — si Futmondo usara en realidad una
# tabla de slots fija por posición (p.ej. delantero siempre 0-1 aunque
# haya 3), esta generalización sería incorrecta para 4-3-3/3-4-3. Antes de
# activar config.ENABLE_LINEUP_AUTO_SUBMIT con una formación distinta de
# 4-4-2 en una liga real, conviene confirmarlo con una prueba igual que se
# hizo aquí.
LINEUP_SLOT_POSITION_ORDER = ["DEL", "MED", "DEF", "POR"]


def build_lineup_changes(players: list[dict], starter_ids: list) -> list[dict]:
    """
    Construye la lista `changes` que espera
    clients.futmondo_client.FutmondoClient.change_lineup(), con la
    numeración confirmada para 4-4-2 (ver LINEUP_SLOT_POSITION_ORDER).

    `players`: lista completa (con "id"/"position") de donde sacar la
    posición de cada titular — normalmente el mismo `squad` pasado a
    pick_lineup(). `starter_ids`: pick_lineup(...)["starters"].

    No incluye banquillo/suplentes: la numeración de esos slots no se ha
    confirmado con ninguna prueba real (ver docstring del módulo) — de
    momento jobs/set_lineup.py solo manda los titulares, más seguro que
    adivinar y mandar algo que Futmondo podría rechazar o, peor,
    interpretar mal en silencio.
    """
    by_id = {p["id"]: p for p in players}
    starters_by_position = {pos: [] for pos in LINEUP_SLOT_POSITION_ORDER}
    for player_id in starter_ids:
        starters_by_position[by_id[player_id]["position"]].append(player_id)

    changes = []
    position_num = 0
    for position in LINEUP_SLOT_POSITION_ORDER:
        for player_id in starters_by_position[position]:
            changes.append(
                {"cpt": False, "to": player_id, "position": position_num, "isBench": False, "multiposition": False}
            )
            position_num += 1
    return changes


def pick_lineup(squad: list[dict], formation: str = None) -> dict:
    """
    Selecciona el once inicial de `squad` (lista de jugadores con al menos
    "id", "position" y "expected_score") para `formation`.

    Devuelve:
        {"formation": ..., "starters": [...ids...], "bench": [...ids...]}

    `expected_score` se calcula fuera (evaluator.evaluate_players() +
    apply_fixture_difficulty() de este mismo módulo), no aquí — pick_lineup
    solo selecciona dado ese número ya calculado.
    """
    formation = formation or config.DEFAULT_FORMATION
    slots = FORMATIONS.get(formation)
    if slots is None:
        raise ValueError(f"Formación no soportada: {formation}")

    starters = []
    for position, count in slots.items():
        candidates = sorted(
            (p for p in squad if p["position"] == position),
            key=lambda p: p["expected_score"],
            reverse=True,
        )
        if len(candidates) < count:
            raise ValueError(
                f"No hay suficientes jugadores en posición {position} "
                f"para la formación {formation} (necesarios {count}, hay {len(candidates)})"
            )
        starters.extend(candidates[:count])

    starter_ids = {p["id"] for p in starters}
    bench = [p for p in squad if p["id"] not in starter_ids]

    return {
        "formation": formation,
        "starters": [p["id"] for p in starters],
        "bench": [p["id"] for p in bench],
    }
