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
# Formato real del sitio (confirmado por captura): "4-4-2", sin el "1-" del
# portero (a diferencia de la convención "1-4-4-2" asumida inicialmente).
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


def to_api_tactic(formation: str) -> str:
    """
    Convierte el formato humano usado aquí ("4-4-2") al formato real que
    espera la API de Comunio en `tactic` ("442", SIN guiones — confirmado
    por captura real de GET lineup, distinto de lo que muestra la UI).
    """
    return formation.replace("-", "")


# Orden de slots CONFIRMADO al 100% con una prueba real completa (once de
# 11 jugadores + interceptación de la llamada PUT real del frontend,
# 2026-08-15): se numeran 1..11 agrupando por posición en ESTE orden fijo,
# portero SIEMPRE el último slot (11 en un 4-4-2). No depende de qué
# jugador concreto sea, solo de su posición.
LINEUP_SLOT_POSITION_ORDER = ["DEL", "MED", "DEF", "POR"]

# Traducción posición corta (usada en engine/db, ver clients.comunio_client.
# COMUNIO_POSITION_MAP) -> nombre real que espera la API en "substitutes".
API_POSITION_NAMES = {"DEL": "striker", "MED": "midfielder", "DEF": "defender", "POR": "keeper"}


def build_lineup_slots(players: list[dict], starter_ids: list) -> dict:
    """
    Construye el mapa {slot: player_id} que espera
    clients.comunio_client.ComunioClient.set_lineup(), con la numeración
    confirmada (ver LINEUP_SLOT_POSITION_ORDER).

    `players`: lista completa (con "id"/"position") de donde sacar la
    posición de cada titular — normalmente el mismo `squad` pasado a
    pick_lineup(). `starter_ids`: pick_lineup(...)["starters"].
    """
    by_id = {p["id"]: p for p in players}
    starters_by_position = {pos: [] for pos in LINEUP_SLOT_POSITION_ORDER}
    for player_id in starter_ids:
        starters_by_position[by_id[player_id]["position"]].append(player_id)

    slots = {}
    slot_num = 1
    for position in LINEUP_SLOT_POSITION_ORDER:
        for player_id in starters_by_position[position]:
            slots[str(slot_num)] = player_id
            slot_num += 1
    return slots


def pick_substitutes(bench: list[dict]) -> dict:
    """
    Elige un suplente por posición (el de mayor "expected_score" en cada
    categoría) entre `bench` (jugadores con "id"/"position"/"expected_score"
    no titulares, ver pick_lineup(...)["bench"] resuelto contra el squad
    completo). Comunio solo admite UN suplente por categoría de posición
    (visto en la UI: 4 slots fijos de banquillo, no una lista libre).

    Devuelve la forma exacta que espera set_lineup(): {"striker": id_o_"",
    "midfielder": ..., "defender": ..., "keeper": ...}.
    """
    substitutes = {name: "" for name in API_POSITION_NAMES.values()}
    for position, api_name in API_POSITION_NAMES.items():
        candidates = sorted(
            (p for p in bench if p.get("position") == position),
            key=lambda p: p.get("expected_score", 0),
            reverse=True,
        )
        if candidates:
            substitutes[api_name] = candidates[0]["id"]
    return substitutes


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
