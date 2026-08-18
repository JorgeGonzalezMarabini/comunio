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
from clients.futmondo_client import is_injury_status

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


def build_lineup_changes(
    players: list[dict],
    starter_ids: list,
    current_lineup_by_position: dict = None,
    current_bench_by_position: dict = None,
) -> list[dict]:
    """
    Construye la lista `changes` que espera
    clients.futmondo_client.FutmondoClient.change_lineup(), con la
    numeración confirmada para 4-4-2 (ver LINEUP_SLOT_POSITION_ORDER) y el
    mecanismo real de "vaciar slot" + "rellenar slot" (ver más abajo).

    `players`: lista completa (con "id"/"position") de donde sacar la
    posición de cada titular — normalmente el mismo `squad` pasado a
    pick_lineup(). `starter_ids`: pick_lineup(...)["starters"].

    `current_lineup_by_position` (opcional): {position: player_id_actual},
    la alineación YA guardada en Futmondo justo antes de este cambio (ver
    `FutmondoClient.get_lineup()`). `current_bench_by_position` (opcional,
    mismo shape que en build_bench_changes()): el banquillo YA guardado —
    hace falta para saber si un titular nuevo viene del banquillo (y de
    qué slot, para vaciarlo antes de colocarlo en el campo).

    **Mecanismo real confirmado interceptando la propia app web de
    Futmondo (2026-08-18, ver TODO.md #1 para el detalle completo del
    hallazgo)** — sustituye por completo la comprensión anterior (basada
    en capturas parciales del 2026-08-17) de que un solo `change` con
    `"to"` + `"from"` a la vez bastaba para sustituir a alguien:

      Futmondo NUNCA acepta un `change` que combine `"to"` y `"from"` al
      mismo tiempo. Cada `change` es una de estas dos operaciones,
      mutuamente excluyentes:

      1. **Vaciar** un slot ocupado: `{"from": <id del que sale>,
         "position": <slot>, "isBench": <bool>}` (sin `"to"`). El jugador
         queda como reserva libre (ni en el campo ni en el banquillo).
      2. **Rellenar** un slot vacío: `{"to": <id del que entra>,
         "position": <slot>, "isBench": <bool>}` (sin `"from"`).

      Mandar ambos junto a la vez (lo que hacía este módulo hasta ahora)
      lo rechaza con `"api.error.in_bench"` si el que entra viene del
      banquillo, o `"api.error.in_field"` si viene del campo — el código
      de error nombra el sitio de ORIGEN del jugador, no el destino.
      Confirmado también que mandar el par vaciar+rellenar como una sola
      llamada HTTP (un array de 2 `changes`) falla igual — no es un
      problema de atomicidad, la API simplemente no permite esa forma en
      el mismo `change`.

      Por tanto, sustituir a alguien SIEMPRE son (al menos) dos llamadas
      HTTP separadas, en este orden: vaciar el slot de destino (si tenía
      ocupante) y vaciar el slot de ORIGEN del que entra (si ya estaba en
      el campo o el banquillo) — en cualquier orden entre sí — y solo
      DESPUÉS rellenar el slot de destino con el que entra.
      `FutmondoClient.change_lineup()` ya manda una llamada HTTP por
      `change`, en el orden de la lista — por eso esta función devuelve
      primero TODOS los `change` de vaciar y luego TODOS los de rellenar.

    Esto simplifica y generaliza el diseño anterior: ya no hace falta
    ningún caso especial para un titular "multiposition" que estuviera en
    el campo en el slot de otro grupo — vaciar su slot de origen (sea
    campo o banquillo, en cualquier posición) siempre es la primera
    operación, sin depender de que haya sitio libre en ningún otro lado.
    Ese mismo jugador puede detectarse dos veces desde ángulos distintos
    (como "ocupante a desalojar" del grupo que abandona y como "entrante
    a vaciar" del grupo al que se une) — es la misma operación física, se
    deduplica por jugador para no mandar el mismo `change` de vaciar dos
    veces (el segundo fallaría, ya no estaría ahí).

    Igual que antes, esta función NO reasigna los slots de un grupo de
    posición desde cero cada vez (lo que forzaba `change` innecesarios
    entre titulares que ya estaban bien colocados, solo en un slot
    numérico distinto dentro del mismo grupo): a los titulares de esta
    semana que YA ocupan uno de los slots de su grupo se les deja en su
    sitio; solo se generan `changes` para los slots que de verdad quedan
    libres, emparejados con los titulares nuevos que entran.

    Si no se pasa `current_lineup_by_position`/`current_bench_by_position`
    (p.ej. no se pudieron leer), se asume que todos esos slots están
    vacíos — jobs/set_lineup.py siempre debería pasarlos cuando pueda.

    No incluye el banquillo/suplentes propiamente dicho — ver
    build_bench_changes() más abajo, con su propia numeración (fija, no
    depende de la formación).
    """
    current_lineup_by_position = current_lineup_by_position or {}
    current_bench_by_position = current_bench_by_position or {}
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

    # Reverse lookup de TODO el campo y TODO el banquillo (no solo el
    # grupo que se esté procesando en cada vuelta) -- para saber, de cada
    # entrante, si ya está en algún slot ahora mismo y hay que vaciarlo
    # primero.
    current_field_slot_by_player = {pid: slot for slot, pid in current_lineup_by_position.items() if pid is not None}
    current_bench_slot_by_player = {pid: slot for slot, pid in current_bench_by_position.items() if pid is not None}

    vacate_changes = []
    fill_changes = []
    # Un mismo jugador puede aparecer como "ocupante a desalojar" de un
    # grupo (visto desde fuera) Y como "entrante que hay que vaciar de su
    # slot actual" de otro grupo (visto desde dentro) -- ej. un jugador
    # "multiposition" que deja de jugar en su grupo de origen y entra en
    # otro. Es la MISMA operación física (solo puede estar en un slot a
    # la vez) -- deduplicar por jugador para no mandar dos veces el mismo
    # `change` de vaciar (el segundo fallaría, ya no estaría ahí).
    vacated_players = set()

    def _vacate(player_id, slot, is_bench):
        if player_id in vacated_players:
            return
        vacated_players.add(player_id)
        vacate_changes.append(
            {"cpt": False, "from": player_id, "position": slot, "isBench": is_bench, "multiposition": False}
        )

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
            if occupant is not None:
                _vacate(occupant, slot, False)

            if player_id in current_field_slot_by_player:
                _vacate(player_id, current_field_slot_by_player[player_id], False)
            elif player_id in current_bench_slot_by_player:
                _vacate(player_id, current_bench_slot_by_player[player_id], True)

            fill_changes.append({"cpt": False, "to": player_id, "position": slot, "isBench": False, "multiposition": False})

    return vacate_changes + fill_changes


def _rank_healthy_first(candidates: list[dict]) -> list[dict]:
    """
    Ordena `candidates` por `expected_score` descendente, pero SIEMPRE
    primero los sanos (`is_injury_status(status)` False) y solo después
    los lesionados/en duda — cada grupo ordenado por score entre sí.

    Por qué hace falta esto además de la penalización de
    `config.EVALUATOR_WEIGHTS["injury_penalty"]`: esa penalización es
    suave (resta hasta 0.10 sobre un score que puede llegar a ~0.90), así
    que un lesionado con muy buenas stats previas puede seguir ganando en
    score a un sano mediocre y colarse como titular o, peor todavía, como
    EL suplente designado de su propia posición — inútil el día que de
    verdad haga falta sustituir a alguien ahí (ver
    `build_substitution_changes()`, que descarta al suplente asignado si
    también está lesionado, sin sustituir a nadie en ese caso). Un
    jugador lesionado/en duda solo se elige aquí si no queda NINGÚN sano
    disponible en esa posición — mejor eso que dejar la posición sin
    cobertura (mismo criterio conservador que `engine/squad_risk.py`).
    """
    healthy = [p for p in candidates if not is_injury_status(p.get("status"))]
    injured = [p for p in candidates if is_injury_status(p.get("status"))]
    by_score = lambda p: p["expected_score"]
    return sorted(healthy, key=by_score, reverse=True) + sorted(injured, key=by_score, reverse=True)


def pick_lineup(squad: list[dict], formation: str = None) -> dict:
    """
    Selecciona el once inicial de `squad` (lista de jugadores con al menos
    "id", "position" y "expected_score", y opcionalmente "status") para
    `formation`.

    Devuelve:
        {"formation": ..., "starters": [...ids...], "bench": [...ids...]}

    `expected_score` se calcula fuera (evaluator.evaluate_players() +
    apply_fixture_difficulty() de este mismo módulo), no aquí — pick_lineup
    solo selecciona dado ese número ya calculado.

    Por posición, se prefiere siempre a los jugadores sanos sobre los
    lesionados/en duda (ver `_rank_healthy_first()`), y solo dentro de
    cada grupo se ordena por `expected_score`; un lesionado solo entra de
    titular si no hay suficientes sanos en esa posición para cubrir los
    huecos de la formación.
    """
    formation = formation or config.DEFAULT_FORMATION
    slots = FORMATIONS.get(formation)
    if slots is None:
        raise ValueError(f"Formación no soportada: {formation}")

    starters = []
    for position, count in slots.items():
        candidates = [p for p in squad if p["position"] == position]
        if len(candidates) < count:
            raise ValueError(
                f"No hay suficientes jugadores en posición {position} "
                f"para la formación {formation} (necesarios {count}, hay {len(candidates)})"
            )
        starters.extend(_rank_healthy_first(candidates)[:count])

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
#
# IMPORTANTE — colocar al suplente aquí NO basta para que sirva de algo:
# según la FAQ oficial (https://help.futmondo.com/article/159-entrenador-automatico),
# la sustitución real de un titular que no juega por su suplente la hace
# el "entrenador automático", una función APARTE y de pago (1.000
# mondos/jornada, gratis en modo PRO), que no está activada por defecto
# — `GET .../lineup` de la liga de prueba usada en esta sesión devuelve
# `"bench": {"enabled": true, "automatic": false, ...}`. Sin
# `automatic: true`, el suplente que coloca este módulo es decorativo. NO
# investigado todavía si se puede activar vía API ni si haría falta
# gestionar mondos para ello.
BENCH_SLOT_BY_POSITION = {"MED": 0, "DEL": 1, "POR": 2, "DEF": 3}


def pick_substitutes(bench_players: list[dict]) -> dict:
    """
    Elige, de entre `bench_players` (los NO titulares de pick_lineup() —
    con "id"/"position"/"expected_score" y opcionalmente "status"), el
    mejor suplente por categoría de posición: Futmondo solo tiene UN slot
    de banquillo por posición (ver BENCH_SLOT_BY_POSITION), no una lista
    donde meter a varios.

    Igual que en pick_lineup(), se prefiere siempre a un sano sobre un
    lesionado/en duda (ver `_rank_healthy_first()`) antes de mirar
    `expected_score` — un suplente designado que está él mismo lesionado
    no sirve de nada el día que haga falta usarlo (ver
    `build_substitution_changes()`, que lo descarta en ese caso sin
    sustituir a nadie). Solo se elige un lesionado si es el único
    candidato que queda en esa posición.

    Devuelve {"POR": id_o_None, "DEF": ..., "MED": ..., "DEL": ...} — None
    si no queda ningún jugador de esa posición en el banquillo (normal:
    p.ej. si todos los defensas de la plantilla son titulares esta semana,
    no hay nadie con quien rellenar el suplente de defensa).
    """
    substitutes = {}
    for position in BENCH_SLOT_BY_POSITION:
        candidates = [p for p in bench_players if p["position"] == position]
        ranked = _rank_healthy_first(candidates)
        substitutes[position] = ranked[0]["id"] if ranked else None
    return substitutes


def build_bench_changes(
    substitutes_by_position: dict, current_bench_by_position: dict = None, current_lineup_by_position: dict = None
) -> list[dict]:
    """
    Construye la lista `changes` (mismo mecanismo de vaciar+rellenar que
    build_lineup_changes(), ver su docstring para el hallazgo real de
    2026-08-18) para el banquillo, con la numeración FIJA confirmada
    (BENCH_SLOT_BY_POSITION) e `isBench: true`.

    `substitutes_by_position`: la salida de pick_substitutes() — las
    entradas con valor `None` (sin candidato para esa posición) se
    ignoran, no generan ningún `change`.

    `current_bench_by_position` (opcional): {position: player_id_actual},
    el banquillo YA guardado (ver `FutmondoClient.get_lineup()["answer"]
    ["bench"]["players"]`). Si el slot ya tiene exactamente ese jugador,
    no se genera `change`; si tiene uno DISTINTO, se vacía primero (`change`
    con `"from"`, SIN `"to"` — Futmondo rechaza combinar ambos en el mismo
    `change`, ver build_lineup_changes()) y se rellena después.

    `current_lineup_by_position` (opcional): {slot_de_campo: player_id},
    por si el suplente que entra ya está ahora mismo en el CAMPO (no solo
    en otro slot de banquillo) — hace falta vaciarlo de ahí primero por el
    mismo motivo.
    """
    current_bench_by_position = current_bench_by_position or {}
    current_lineup_by_position = current_lineup_by_position or {}
    current_field_slot_by_player = {pid: slot for slot, pid in current_lineup_by_position.items() if pid is not None}

    vacate_changes = []
    fill_changes = []
    for position, player_id in substitutes_by_position.items():
        if player_id is None:
            continue
        slot = BENCH_SLOT_BY_POSITION[position]
        current_occupant = current_bench_by_position.get(slot)
        if current_occupant == player_id:
            continue
        if current_occupant is not None:
            vacate_changes.append(
                {"cpt": False, "from": current_occupant, "position": slot, "isBench": True, "multiposition": False}
            )
        if player_id in current_field_slot_by_player:
            vacate_changes.append(
                {
                    "cpt": False,
                    "from": player_id,
                    "position": current_field_slot_by_player[player_id],
                    "isBench": False,
                    "multiposition": False,
                }
            )
        fill_changes.append({"cpt": False, "to": player_id, "position": slot, "isBench": True, "multiposition": False})
    return vacate_changes + fill_changes


def build_substitution_changes(
    players_by_id: dict,
    current_lineup_by_position: dict,
    current_bench_by_position: dict,
    confirmed_out_ids: set = None,
) -> list[dict]:
    """
    Construye la lista `changes` (mismo shape que build_lineup_changes()/
    build_bench_changes()) para sustituir, dentro de la alineación YA
    guardada en Futmondo, a cada titular "confirmado fuera" por el suplente
    de su misma posición ya asignado en el banquillo
    (BENCH_SLOT_BY_POSITION) — pensado para jobs/manage_substitutes.py, NO
    para jobs/set_lineup.py (que decide antes del cierre de jornada, sin
    saber todavía quién estará confirmado fuera).

    "Confirmado fuera" cubre DOS señales independientes, cualquiera de las
    dos basta:
      1. `clients.futmondo_client.is_injury_status(starter["status"])` —
         lesión/duda según el campo `status` de Futmondo.
      2. `starter_id in confirmed_out_ids` (opcional) — el titular está sano
         según Futmondo pero NO aparece en el once REAL de su equipo hoy,
         según `clients.football_lineups_client.
         find_players_confirmed_out_of_real_lineup()`. Cubre el caso más
         frecuente en la práctica: rotación/decisión táctica del
         entrenador real, no solo lesión — ver README, sección "Banquillo/
         suplentes". `confirmed_out_ids=None` (o vacío) desactiva esta
         segunda señal sin más (p.ej. si config.ENABLE_REAL_LINEUP_CHECK es
         False) — el comportamiento queda igual que antes de que existiera.

    La MISMA doble señal se aplica también al suplente que entraría: uno
    confirmado fuera (lesionado o no incluido en el once real de SU equipo)
    tampoco sirve, igual que ya pasaba solo con lesión.

    Por qué hace falta esto aparte de set_lineup.py: según la FAQ oficial
    (https://help.futmondo.com/article/159-entrenador-automatico, ver
    también README "Banquillo/suplentes"), la entrada real del suplente
    cuando un titular no juega la hace el "entrenador automático", función
    de pago (gratis en modo PRO) que NO está activada por defecto — sin
    ella, el suplente que coloca set_lineup.py es decorativo. En una liga
    donde esa función esté desactivada, hace falta sustituir a mano, y eso
    solo puede decidirse cerca de cada partido (lesión/sanción confirmada),
    no una semana antes.

    **Arreglado 2026-08-18 tras confirmar el mecanismo real interceptando
    la propia app web de Futmondo** (ver TODO.md #1 y el docstring de
    build_lineup_changes() para el hallazgo completo): Futmondo nunca
    acepta un `change` que combine `"to"` y `"from"` a la vez — cada
    `change` es o bien "vaciar" (`"from"` sin `"to"`) o "rellenar" (`"to"`
    sin `"from"`). El diseño anterior (una pareja de `changes` con `"to"`
    + `"from"` cruzado) fallaba siempre, confirmado con una prueba real
    (2026-08-18, intercambio DEF real en la liga de pruebas): el primer
    `change` (suplente entra con `"from"` del titular) devolvía
    `"api.error.in_bench"`; en orden invertido, el segundo (titular sale
    con `"from"` del suplente) devolvía `"api.error.in_field"` — en
    ambos casos, el código nombra el sitio de ORIGEN del jugador que se
    intenta mover, no el destino. Mandar el par junto en una sola llamada
    HTTP (un array de 2 `changes`) falla igual — no era un problema de
    atomicidad.

    Ahora genera CUATRO `changes` por sustitución, dos de vaciar y dos de
    rellenar (en ese orden, ya que `FutmondoClient.change_lineup()` manda
    una llamada HTTP por `change` en el orden de la lista): vaciar al
    titular de su slot de campo, vaciar al suplente de su slot de
    banquillo, rellenar el slot de campo con el suplente, rellenar el
    slot de banquillo con el titular. Confirmado con una prueba real
    (2026-08-18, mismo intercambio DEF, en este orden) que las cuatro
    llamadas se aplican correctamente — releído con `get_lineup()`
    después de cada paso.

    `players_by_id`: {id: {..., "position", "status"}} — normalmente toda
    la plantilla (db.models.get_player_features()), para poder leer
    "status" tanto del titular como del suplente.
    `current_lineup_by_position`/`current_bench_by_position`: {slot: id},
    la alineación/banquillo YA guardados en Futmondo AHORA MISMO (ver
    FutmondoClient.get_lineup()["answer"]["players"]/["bench"]["players"]),
    no la decisión (potencialmente desfasada) de jobs/set_lineup.py.

    Solo genera una sustitución por posición en cada pasada — Futmondo
    solo tiene sitio para un suplente por posición (igual que
    pick_substitutes()); si hay más de un titular fuera en la misma
    categoría, el resto queda sin suplente disponible hasta que se libere
    otro slot de banquillo (fuera de alcance de esta función). Se salta
    una posición si el titular no está lesionado/en duda, si no hay nadie
    asignado en el slot de banquillo de esa posición, o si el suplente
    asignado TAMBIÉN está lesionado/en duda (no hay a quién meter).
    """
    confirmed_out_ids = confirmed_out_ids or set()

    def _is_confirmed_out(player_id: str, player: dict | None) -> bool:
        return (player is not None and is_injury_status(player.get("status"))) or player_id in confirmed_out_ids

    changes = []
    replaced_positions = set()
    for slot, starter_id in current_lineup_by_position.items():
        starter = players_by_id.get(starter_id)
        if starter is None or not _is_confirmed_out(starter_id, starter):
            continue
        position = starter["position"]
        if position in replaced_positions:
            continue  # ya se usó el único suplente de esta posición en esta pasada
        bench_slot = BENCH_SLOT_BY_POSITION.get(position)
        substitute_id = current_bench_by_position.get(bench_slot)
        if substitute_id is None:
            continue  # sin suplente asignado para esta posición
        substitute = players_by_id.get(substitute_id)
        if _is_confirmed_out(substitute_id, substitute):
            continue  # el suplente asignado tampoco puede jugar

        changes.append(
            {"cpt": False, "from": starter_id, "position": slot, "isBench": False, "multiposition": False}
        )
        changes.append(
            {"cpt": False, "from": substitute_id, "position": bench_slot, "isBench": True, "multiposition": False}
        )
        changes.append(
            {"cpt": False, "to": substitute_id, "position": slot, "isBench": False, "multiposition": False}
        )
        changes.append(
            {"cpt": False, "to": starter_id, "position": bench_slot, "isBench": True, "multiposition": False}
        )
        replaced_positions.add(position)
    return changes
