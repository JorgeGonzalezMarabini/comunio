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
#
# Solo 4-4-2: es la ÚNICA formación gratis y siempre disponible en
# Futmondo (confirmado en la FAQ oficial, https://help.futmondo.com/article/160).
# El resto de "formaciones extra" reales que ofrece Futmondo —
# 4-2-4, 3-6-1, 3-3-4, 4-6-0, 5-2-3 (ninguna coincide con las que tenía
# esta constante heredadas sin querer de la fase de Comunio: 4-3-3/3-4-3/
# 5-3-2, que NO existen en Futmondo) — son de pago (200 mondos/jornada o
# 4.000 mondos/temporada, gratis en modo PRO) y, contratadas por jornada,
# **vuelven solas a 4-4-2 al terminar esa jornada** — mal encaje para un
# bot automatizado que no gestiona mondos. No se añaden aquí hasta que
# alguna se necesite de verdad Y se confirme su numeración de slots con
# una prueba real (la de build_lineup_changes() solo está verificada para
# 4-4-2, ver más abajo).
FORMATIONS = {
    "4-4-2": {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2},
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
# TODO sin confirmar: si algún día se añade a FORMATIONS alguna de las
# "formaciones extra" reales de Futmondo (4-2-4, 3-6-1, 3-3-4, 4-6-0,
# 5-2-3 — ver FORMATIONS más arriba), este mismo criterio (numeración
# consecutiva por el orden de LINEUP_SLOT_POSITION_ORDER, portero siempre
# el último índice) es la generalización más simple consistente con lo
# observado, pero NO está probado con ninguna formación distinta de
# 4-4-2 — si Futmondo usara en realidad una tabla de slots fija por
# posición (p.ej. delantero siempre 0-1 aunque haya 4 en un 4-6-0), esta
# generalización sería incorrecta. Confirmarlo con una prueba real igual
# que se hizo para 4-4-2 antes de activar config.ENABLE_LINEUP_AUTO_SUBMIT
# con otra formación.
LINEUP_SLOT_POSITION_ORDER = ["DEL", "MED", "DEF", "POR"]


def build_lineup_changes(players: list[dict], starter_ids: list, current_lineup_by_position: dict = None) -> list[dict]:
    """
    Construye la lista `changes` que espera
    clients.futmondo_client.FutmondoClient.change_lineup(), con la
    numeración confirmada para 4-4-2 (ver LINEUP_SLOT_POSITION_ORDER).

    `players`: lista completa (con "id"/"position") de donde sacar la
    posición de cada titular — normalmente el mismo `squad` pasado a
    pick_lineup(). `starter_ids`: pick_lineup(...)["starters"].

    `current_lineup_by_position` (opcional): {position: player_id_actual},
    la alineación YA guardada en Futmondo justo antes de este cambio (ver
    `FutmondoClient.get_lineup()`).

    Dos bugs/límites reales confirmados en producción el mismo día
    (2026-08-17, ver README y clients/futmondo_client.py:change_lineup
    para el detalle completo):

      1. Sustituir un slot que YA tiene un jugador DISTINTO exige incluir
         `"from"` con el id del que sale, si no la API lo rechaza con
         `"api.error.not_allowed"`.
      2. Colocar en un slot a un jugador que en ESE MOMENTO está en el
         campo en OTRA posición (una rotación entre titulares) se rechaza
         con `"api.error.in_field"`, aunque el `"from"` sea correcto —
         confirmado que sustituir SIEMPRE funciona si el que entra viene
         del banquillo, nunca si viene de otro slot del campo.

    Por eso esta función NO reasigna los slots de un grupo de posición
    desde cero cada vez (lo que forzaba rotaciones falsas entre jugadores
    que ya estaban bien colocados, solo en un slot numérico distinto):
    para cada grupo (DEL/MED/DEF/POR), a los titulares de esta semana que
    YA ocupan uno de los slots del grupo se les deja en su sitio (sin
    `change`); solo se generan cambios para los slots que de verdad
    quedan libres (su ocupante actual ya no es titular esta semana),
    emparejados con los titulares nuevos que entran — que en el caso
    normal (evaluación semanal, la posición de un jugador no cambia de
    una semana a otra) siempre vienen del banquillo, nunca de otro slot
    del campo que se esté tocando en la misma pasada. Si aun así un
    jugador entrante estuviera en el campo en otra posición en este
    mismo momento (caso raro no cubierto), esa `change` en concreto
    volvería a fallar con `"api.error.in_field"` — `jobs/set_lineup.py`
    lo audita igual que cualquier otro fallo, no lo oculta.

    Si no se pasa `current_lineup_by_position` (p.ej. no se pudo leer la
    alineación actual), se asume que todos los slots están vacíos —
    jobs/set_lineup.py siempre debería pasarlo cuando pueda.

    No incluye banquillo/suplentes — ver build_bench_changes() más abajo,
    con su propia numeración (fija, no depende de la formación).
    """
    current_lineup_by_position = current_lineup_by_position or {}
    by_id = {p["id"]: p for p in players}
    starters_by_position = {pos: [] for pos in LINEUP_SLOT_POSITION_ORDER}
    for player_id in starter_ids:
        starters_by_position[by_id[player_id]["position"]].append(player_id)

    # Rango de slots fijo por grupo de posición, contiguo, en el orden
    # LINEUP_SLOT_POSITION_ORDER — confirmado solo para 4-4-2 (ver TODO
    # más arriba en el módulo).
    slot_ranges = {}
    next_slot = 0
    for position in LINEUP_SLOT_POSITION_ORDER:
        count = len(starters_by_position[position])
        slot_ranges[position] = list(range(next_slot, next_slot + count))
        next_slot += count

    changes = []
    for position in LINEUP_SLOT_POSITION_ORDER:
        target_players = starters_by_position[position]
        slots = slot_ranges[position]

        # Slots de este grupo cuyo ocupante actual ya es titular esta
        # semana en este mismo grupo -- se dejan quietos, sin `change`.
        occupant_by_slot = {slot: current_lineup_by_position.get(slot) for slot in slots}
        already_correct_players = {occupant for occupant in occupant_by_slot.values() if occupant in target_players}

        free_slots = [slot for slot, occupant in occupant_by_slot.items() if occupant not in target_players]
        entering_players = [p for p in target_players if p not in already_correct_players]

        for slot, player_id in zip(free_slots, entering_players):
            occupant = occupant_by_slot[slot]
            change = {"cpt": False, "to": player_id, "position": slot, "isBench": False, "multiposition": False}
            if occupant is not None:
                change["from"] = occupant
            changes.append(change)

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


# Banquillo: UN slot FIJO por categoría de posición, que NO depende de la
# formación ni de cuántos titulares haya de cada tipo — CONFIRMADO AL 100%
# (2026-08-17, los 4 suplentes añadidos uno a uno desde la pestaña
# "Suplentes" de la web, interceptando cada POST real a
# /2/userteam/changeplayer + relectura con GET /1/userteam/lineup
# confirmando la posición numérica de cada uno en `bench.players`):
#   0 = MED, 1 = DEL, 2 = POR, 3 = DEF
# A diferencia de los titulares (numeración consecutiva que sí depende de
# la formación), esto son siempre estos 4 números fijos, con
# `isBench: true`. Solo hay sitio para UN suplente por posición (no una
# lista, igual que en Comunio en su momento) — Futmondo no deja añadir un
# segundo suplente de la misma categoría mientras el primero siga ahí.
BENCH_SLOT_BY_POSITION = {"MED": 0, "DEL": 1, "POR": 2, "DEF": 3}


def pick_substitutes(bench_players: list[dict]) -> dict:
    """
    Elige, de entre `bench_players` (los NO titulares de pick_lineup() —
    con "id"/"position"/"expected_score"), el mejor suplente por categoría
    de posición: Futmondo solo tiene UN slot de banquillo por posición
    (ver BENCH_SLOT_BY_POSITION), no una lista donde meter a varios.

    Devuelve {"POR": id_o_None, "DEF": ..., "MED": ..., "DEL": ...} — None
    si no queda ningún jugador de esa posición en el banquillo (normal:
    p.ej. si todos los defensas de la plantilla son titulares esta semana,
    no hay nadie con quien rellenar el suplente de defensa).
    """
    substitutes = {}
    for position in BENCH_SLOT_BY_POSITION:
        candidates = sorted(
            (p for p in bench_players if p["position"] == position),
            key=lambda p: p["expected_score"],
            reverse=True,
        )
        substitutes[position] = candidates[0]["id"] if candidates else None
    return substitutes


def build_bench_changes(substitutes_by_position: dict, current_bench_by_position: dict = None) -> list[dict]:
    """
    Construye la lista `changes` (mismo shape que build_lineup_changes())
    para el banquillo, con la numeración FIJA confirmada
    (BENCH_SLOT_BY_POSITION) e `isBench: true`.

    `substitutes_by_position`: la salida de pick_substitutes() — las
    entradas con valor `None` (sin candidato para esa posición) se
    ignoran, no generan ningún `change`.

    `current_bench_by_position` (opcional): {position: player_id_actual},
    el banquillo YA guardado (ver `FutmondoClient.get_lineup()["answer"]
    ["bench"]["players"]`). Igual que en build_lineup_changes(): si el
    slot ya tiene exactamente ese jugador, no se genera `change`; si tiene
    uno DISTINTO, se incluye `"from"`. Esto no se ha probado en vivo
    específicamente para banquillo (solo se probó rellenar slots vacíos),
    pero es el mismo endpoint con el mismo shape que para titulares, donde
    sí está confirmado — razonable esperar el mismo comportamiento, sin
    darlo por 100% confirmado todavía.
    """
    current_bench_by_position = current_bench_by_position or {}
    changes = []
    for position, player_id in substitutes_by_position.items():
        if player_id is None:
            continue
        slot = BENCH_SLOT_BY_POSITION[position]
        current_occupant = current_bench_by_position.get(slot)
        if current_occupant == player_id:
            continue
        change = {"cpt": False, "to": player_id, "position": slot, "isBench": True, "multiposition": False}
        if current_occupant is not None:
            change["from"] = current_occupant
        changes.append(change)
    return changes
