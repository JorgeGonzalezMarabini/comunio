"""
Optimizador de alineaciones: elige el once inicial válido para una
formación configurable (por defecto config.DEFAULT_FORMATION) maximizando
el score esperado de cada jugador para la próxima jornada.

El score esperado debe tener en cuenta forma reciente + dificultad del
rival (esto último aún no tiene fuente de datos definida).
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


def pick_lineup(squad: list[dict], formation: str = None) -> dict:
    """
    Selecciona el once inicial de `squad` (lista de jugadores con al menos
    "id", "position" y "expected_score") para `formation`.

    Devuelve:
        {"formation": ..., "starters": [...ids...], "bench": [...ids...]}

    TODO: incorporar dificultad del rival en expected_score una vez se
    defina esa fuente de datos (por ahora se asume ya calculado fuera).
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
