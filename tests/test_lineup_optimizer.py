import pytest

from engine.lineup_optimizer import (
    BENCH_SLOT_BY_POSITION,
    LINEUP_SLOT_POSITION_ORDER,
    apply_fixture_difficulty,
    build_bench_changes,
    build_lineup_changes,
    build_substitution_changes,
    pick_lineup,
    pick_substitutes,
)


def _squad_442():
    """Plantilla justa para un 4-4-2: exactamente 1 POR, 4 DEF, 4 MED, 2 DEL."""
    squad = [{"id": "por1", "position": "POR", "expected_score": 0.9}]
    squad += [{"id": f"def{i}", "position": "DEF", "expected_score": 0.5 + i * 0.01} for i in range(4)]
    squad += [{"id": f"med{i}", "position": "MED", "expected_score": 0.5 + i * 0.01} for i in range(4)]
    squad += [{"id": f"del{i}", "position": "DEL", "expected_score": 0.5 + i * 0.01} for i in range(2)]
    return squad


def test_lineup_slot_position_order_is_del_med_def_por():
    """Confirmado por captura real: numeración consecutiva empezando en 0, portero siempre el último."""
    assert LINEUP_SLOT_POSITION_ORDER == ["DEL", "MED", "DEF", "POR"]


def test_pick_lineup_selects_best_scored_per_position():
    squad = _squad_442() + [{"id": "def_suplente", "position": "DEF", "expected_score": 0.99}]
    lineup = pick_lineup(squad, formation="4-4-2")
    assert lineup["formation"] == "4-4-2"
    assert "def_suplente" in lineup["starters"]  # el de mayor score debe entrar
    assert len(lineup["starters"]) == 11
    assert "def_suplente" not in lineup["bench"]


def test_pick_lineup_prefers_healthy_over_higher_scored_injured():
    """
    Un lesionado/en duda no debe entrar de titular si hay un sano
    disponible en su posición, aunque el lesionado tenga mejor
    expected_score -- la penalización de evaluator (injury_penalty) es
    suave y no basta por sí sola para garantizar esto (ver
    _rank_healthy_first()).
    """
    squad = _squad_442() + [
        {"id": "del_lesionado", "position": "DEL", "expected_score": 0.99, "status": "injured"},
    ]
    lineup = pick_lineup(squad, formation="4-4-2")
    assert "del_lesionado" not in lineup["starters"]
    assert "del_lesionado" in lineup["bench"]


def test_pick_lineup_falls_back_to_injured_when_no_healthy_left():
    """Sin nadie sano en la posición, mejor un lesionado de titular que dejar el hueco sin cubrir."""
    squad = [{"id": "por1", "position": "POR", "expected_score": 0.9}]
    squad += [{"id": f"def{i}", "position": "DEF", "expected_score": 0.5 + i * 0.01} for i in range(4)]
    squad += [{"id": f"med{i}", "position": "MED", "expected_score": 0.5 + i * 0.01} for i in range(4)]
    # Ambos delanteros disponibles están lesionados/en duda -- no hay alternativa sana.
    squad += [
        {"id": "del0", "position": "DEL", "expected_score": 0.6, "status": "injured"},
        {"id": "del1", "position": "DEL", "expected_score": 0.5, "status": "doubtful lesión"},
    ]
    lineup = pick_lineup(squad, formation="4-4-2")
    assert set(pid for pid in lineup["starters"] if pid.startswith("del")) == {"del0", "del1"}


def test_pick_lineup_raises_when_not_enough_players():
    squad = _squad_442()[:-1]  # falta un delantero
    with pytest.raises(ValueError):
        pick_lineup(squad, formation="4-4-2")


def test_pick_lineup_rejects_unknown_formation():
    with pytest.raises(ValueError):
        pick_lineup(_squad_442(), formation="9-9-9")


def test_build_lineup_changes_confirmed_order_del_med_def_por_starting_at_zero():
    """
    Numeración CONFIRMADA por captura real (varios jugadores colocados
    interceptando la llamada POST real + relectura con get_lineup()
    confirmando el `position` numérico asignado): consecutiva EMPEZANDO EN
    0, agrupada por posición en el orden fijo delanteros ->
    centrocampistas -> defensas -> portero, portero siempre el último
    índice.
    """
    squad = _squad_442()
    lineup = pick_lineup(squad, formation="4-4-2")
    changes = build_lineup_changes(squad, lineup["starters"])

    assert len(changes) == 11
    by_id = {c["to"]: c for c in changes}

    # Portero siempre el último índice (10 en un 4-4-2 con 11 titulares).
    assert by_id["por1"]["position"] == 10

    del_positions = {by_id[pid]["position"] for pid in lineup["starters"] if pid.startswith("del")}
    med_positions = {by_id[pid]["position"] for pid in lineup["starters"] if pid.startswith("med")}
    def_positions = {by_id[pid]["position"] for pid in lineup["starters"] if pid.startswith("def")}

    assert del_positions == {0, 1}
    assert med_positions == {2, 3, 4, 5}
    assert def_positions == {6, 7, 8, 9}

    # Numeración consecutiva sin huecos ni repeticiones.
    assert sorted(c["position"] for c in changes) == list(range(11))


def test_build_lineup_changes_shape_matches_change_lineup_contract():
    squad = _squad_442()
    lineup = pick_lineup(squad, formation="4-4-2")
    changes = build_lineup_changes(squad, lineup["starters"])

    for change in changes:
        assert change.keys() == {"cpt", "to", "position", "isBench", "multiposition"}
        assert change["cpt"] is False
        assert change["isBench"] is False
        assert change["multiposition"] is False


def test_build_lineup_changes_includes_from_when_slot_already_has_different_player():
    """
    Regresión del bug real de producción (2026-08-17, ver docstring del
    módulo y clients.futmondo_client.FutmondoClient.change_lineup):
    sustituir un slot que YA tiene un jugador distinto exige incluir
    "from" con el id del que sale -- confirmado interceptando la llamada
    real del frontend haciendo el mismo cambio a mano.
    """
    squad = _squad_442()
    lineup = pick_lineup(squad, formation="4-4-2")
    # Alineación previa: en el slot 10 (portero) hay un jugador DISTINTO de "por1".
    current_lineup_by_position = {10: "otro_portero_de_antes"}

    changes = build_lineup_changes(squad, lineup["starters"], current_lineup_by_position)
    by_id = {c["to"]: c for c in changes}

    assert by_id["por1"]["from"] == "otro_portero_de_antes"
    # Los slots que antes estaban vacíos (no aparecen en current_lineup_by_position) no llevan "from".
    assert "from" not in by_id["del0"]


def test_build_lineup_changes_skips_slots_already_correct():
    """
    Si un slot ya tiene EXACTAMENTE al jugador que le corresponde, no debe
    generarse ningún `change` para él -- repetir un cambio ya aplicado
    también se vio rechazado en la prueba real (mismo día).
    """
    squad = _squad_442()
    lineup = pick_lineup(squad, formation="4-4-2")
    changes_from_empty = build_lineup_changes(squad, lineup["starters"])
    by_id = {c["to"]: c for c in changes_from_empty}
    current_lineup_by_position = {c["position"]: c["to"] for c in changes_from_empty}

    # Simula un segundo cambio idéntico: nada debería quedar por enviar.
    changes_again = build_lineup_changes(squad, lineup["starters"], current_lineup_by_position)
    assert changes_again == []

    # Si solo UN slot difiere (ej. el portero cambia), solo ese genera change.
    current_lineup_by_position[by_id["por1"]["position"]] = "otro_portero"
    changes_partial = build_lineup_changes(squad, lineup["starters"], current_lineup_by_position)
    assert len(changes_partial) == 1
    assert changes_partial[0]["to"] == "por1"
    assert changes_partial[0]["from"] == "otro_portero"


def test_build_lineup_changes_keeps_unchanged_starters_in_their_current_slot():
    """
    Regresión de un TERCER bug/límite real de producción (2026-08-17, ver
    docstring del módulo y de clients.futmondo_client.FutmondoClient.
    change_lineup): colocar a un jugador que en ESE MOMENTO está en el
    campo en otra posición se rechaza con "api.error.in_field", incluso
    con el "from" correcto -- sustituir SIEMPRE funciona si el que entra
    viene del banquillo, nunca si viene de otro slot del campo (confirmado
    en vivo: dos jugadores YA colocados que se querían rotar entre sí
    fallaban, y solo se pudo resolver metiendo a uno en el banquillo antes
    de recolocar al otro).

    Por eso, si tres de los cuatro defensas de esta semana YA estaban en
    el campo la semana pasada (en cualquier slot del grupo DEF) y solo
    entra uno nuevo, build_lineup_changes() NO debe generar ningún
    `change` para los tres que se quedan -- solo uno, para el slot que de
    verdad queda libre. Reasignar los cuatro slots desde cero (el diseño
    anterior) habría generado rotaciones falsas entre los tres que ya
    estaban bien, y esas rotaciones son justo las que la API rechaza.
    """
    squad = _squad_442()
    # Alineación completa de la semana pasada, en los slots confirmados
    # para 4-4-2 (delanteros 0-1, medios 2-5, defensas 6-9, portero 10).
    current_lineup_by_position = {
        0: "del0", 1: "del1",
        2: "med0", 3: "med1", 4: "med2", 5: "med3",
        6: "def0", 7: "def1", 8: "def2", 9: "def3",
        10: "por1",
    }
    # Esta semana el once sigue siendo el mismo salvo un cambio en defensa:
    # def3 sale, entra un central nuevo del banquillo ("def_nuevo").
    squad_this_week = squad + [{"id": "def_nuevo", "position": "DEF", "expected_score": 0.6}]
    starters = ["por1", "def0", "def1", "def2", "def_nuevo", "med0", "med1", "med2", "med3", "del0", "del1"]

    changes = build_lineup_changes(squad_this_week, starters, current_lineup_by_position)

    # Solo debe generarse UN cambio: el que libera el slot de def3 para def_nuevo.
    assert len(changes) == 1
    assert changes[0]["to"] == "def_nuevo"
    assert changes[0]["from"] == "def3"
    assert changes[0]["position"] == 9  # el slot que ocupaba def3


# --- Banquillo: pick_substitutes() / build_bench_changes() ---
#
# CONFIRMADO AL 100% (2026-08-17): un slot FIJO por posición
# (0=MED, 1=DEL, 2=POR, 3=DEF), no una lista -- ver docstring del módulo.


def test_bench_slot_by_position_is_fixed_mapping():
    assert BENCH_SLOT_BY_POSITION == {"MED": 0, "DEL": 1, "POR": 2, "DEF": 3}


def test_pick_substitutes_takes_best_scored_per_position():
    bench = [
        {"id": "def_bueno", "position": "DEF", "expected_score": 0.8},
        {"id": "def_malo", "position": "DEF", "expected_score": 0.2},
        {"id": "med_unico", "position": "MED", "expected_score": 0.5},
        # sin nadie en POR ni DEL en el banquillo esta semana
    ]
    substitutes = pick_substitutes(bench)
    assert substitutes == {"POR": None, "DEF": "def_bueno", "MED": "med_unico", "DEL": None}


def test_pick_substitutes_prefers_healthy_over_higher_scored_injured():
    """Igual que en pick_lineup(): el suplente designado no debe ser un lesionado si hay un sano disponible."""
    bench = [
        {"id": "def_lesionado", "position": "DEF", "expected_score": 0.9, "status": "injured"},
        {"id": "def_sano", "position": "DEF", "expected_score": 0.3},
    ]
    substitutes = pick_substitutes(bench)
    assert substitutes["DEF"] == "def_sano"


def test_pick_substitutes_falls_back_to_injured_when_no_healthy_left():
    """Sin ningún sano en el banquillo para esa posición, mejor un suplente lesionado que ninguno."""
    bench = [{"id": "def_lesionado", "position": "DEF", "expected_score": 0.9, "status": "injured"}]
    substitutes = pick_substitutes(bench)
    assert substitutes["DEF"] == "def_lesionado"


def test_build_bench_changes_uses_fixed_slots_and_skips_missing_positions():
    substitutes = {"POR": None, "DEF": "def_bueno", "MED": "med_unico", "DEL": None}
    changes = build_bench_changes(substitutes)

    assert len(changes) == 2  # POR y DEL sin candidato -- no generan change
    by_id = {c["to"]: c for c in changes}
    assert by_id["def_bueno"]["position"] == 3
    assert by_id["med_unico"]["position"] == 0
    for change in changes:
        assert change["isBench"] is True
        assert change["cpt"] is False
        assert change["multiposition"] is False
        assert "from" not in change  # banquillo vacío antes -- ver siguiente test para el caso ocupado


def test_build_bench_changes_includes_from_and_skips_already_correct():
    substitutes = {"POR": None, "DEF": "def_bueno", "MED": "med_unico", "DEL": None}
    # DEF (slot 3) ya tiene a otro jugador -- debe llevar "from".
    # MED (slot 0) ya tiene exactamente a "med_unico" -- no debe generar change.
    current_bench_by_position = {3: "def_viejo", 0: "med_unico"}

    changes = build_bench_changes(substitutes, current_bench_by_position)

    assert len(changes) == 1
    assert changes[0]["to"] == "def_bueno"
    assert changes[0]["from"] == "def_viejo"
    assert changes[0]["position"] == 3


# --- Sustitución manual: build_substitution_changes() ---
#
# jobs/manage_substitutes.py -- ver docstring de la función para el
# porqué de la regla "entrar siempre desde el banquillo" y por qué el
# orden de la pareja de changes generada importa.


def _players_by_id(entries):
    """entries: [(id, position, status), ...] -> {id: {"position", "status"}}."""
    return {pid: {"position": position, "status": status} for pid, position, status in entries}


def test_build_substitution_changes_swaps_injured_starter_for_bench_substitute():
    players_by_id = _players_by_id(
        [("def_titular", "DEF", "lesionado"), ("def_suplente", "DEF", "")]
    )
    current_lineup_by_position = {6: "def_titular"}
    current_bench_by_position = {3: "def_suplente"}  # slot fijo DEF, ver BENCH_SLOT_BY_POSITION

    changes = build_substitution_changes(players_by_id, current_lineup_by_position, current_bench_by_position)

    assert len(changes) == 2
    entering, leaving = changes

    # El suplente entra al slot de campo que dejaba el titular, viniendo
    # del banquillo -- el caso CONFIRMADO en producción, nunca roto.
    assert entering == {
        "cpt": False, "to": "def_suplente", "position": 6, "isBench": False,
        "multiposition": False, "from": "def_titular",
    }
    # El titular sale al slot de banquillo que el suplente deja libre,
    # SIN "from" (se asume vacío tras el primer change -- ver docstring).
    assert leaving == {
        "cpt": False, "to": "def_titular", "position": 3, "isBench": True, "multiposition": False,
    }


def test_build_substitution_changes_ignores_healthy_starters():
    players_by_id = _players_by_id([("def_titular", "DEF", ""), ("def_suplente", "DEF", "")])
    current_lineup_by_position = {6: "def_titular"}
    current_bench_by_position = {3: "def_suplente"}

    assert build_substitution_changes(players_by_id, current_lineup_by_position, current_bench_by_position) == []


def test_build_substitution_changes_skips_position_without_bench_substitute():
    players_by_id = _players_by_id([("def_titular", "DEF", "lesionado")])
    current_lineup_by_position = {6: "def_titular"}
    current_bench_by_position = {}  # nadie asignado en el slot de banquillo de DEF

    assert build_substitution_changes(players_by_id, current_lineup_by_position, current_bench_by_position) == []


def test_build_substitution_changes_skips_when_substitute_also_injured():
    players_by_id = _players_by_id(
        [("def_titular", "DEF", "lesionado"), ("def_suplente", "DEF", "lesionado")]
    )
    current_lineup_by_position = {6: "def_titular"}
    current_bench_by_position = {3: "def_suplente"}

    assert build_substitution_changes(players_by_id, current_lineup_by_position, current_bench_by_position) == []


def test_build_substitution_changes_only_one_substitution_per_position():
    """Futmondo solo tiene un slot de banquillo por posición -- dos titulares
    lesionados de la misma categoría no pueden sustituirse los dos a la vez."""
    players_by_id = _players_by_id(
        [
            ("def_titular_1", "DEF", "lesionado"),
            ("def_titular_2", "DEF", "lesionado"),
            ("def_suplente", "DEF", ""),
        ]
    )
    current_lineup_by_position = {6: "def_titular_1", 7: "def_titular_2"}
    current_bench_by_position = {3: "def_suplente"}

    changes = build_substitution_changes(players_by_id, current_lineup_by_position, current_bench_by_position)

    assert len(changes) == 2  # solo una pareja, no dos
    assert changes[0]["to"] == "def_suplente"


# --- confirmed_out_ids: señal de alineación real (independiente de status) ---


def test_build_substitution_changes_swaps_healthy_starter_missing_from_real_lineup():
    """
    Un titular SANO (status vacío) pero confirmado fuera del once real de
    su equipo (rotación, no lesión -- ver
    clients.football_lineups_client.find_players_confirmed_out_of_real_lineup())
    debe sustituirse igual que uno lesionado.
    """
    players_by_id = _players_by_id([("def_titular", "DEF", ""), ("def_suplente", "DEF", "")])
    current_lineup_by_position = {6: "def_titular"}
    current_bench_by_position = {3: "def_suplente"}

    changes = build_substitution_changes(
        players_by_id, current_lineup_by_position, current_bench_by_position, confirmed_out_ids={"def_titular"}
    )

    assert len(changes) == 2
    assert changes[0]["to"] == "def_suplente"
    assert changes[1]["to"] == "def_titular"


def test_build_substitution_changes_skips_when_substitute_confirmed_out_of_real_lineup():
    """El suplente asignado tampoco sirve si ÉL está confirmado fuera del once real, aunque esté sano según status."""
    players_by_id = _players_by_id([("def_titular", "DEF", "lesionado"), ("def_suplente", "DEF", "")])
    current_lineup_by_position = {6: "def_titular"}
    current_bench_by_position = {3: "def_suplente"}

    changes = build_substitution_changes(
        players_by_id, current_lineup_by_position, current_bench_by_position, confirmed_out_ids={"def_suplente"}
    )
    assert changes == []


def test_build_substitution_changes_confirmed_out_ids_none_behaves_like_before():
    """confirmed_out_ids=None (valor por defecto) no debe cambiar nada frente al comportamiento previo."""
    players_by_id = _players_by_id([("def_titular", "DEF", ""), ("def_suplente", "DEF", "")])
    current_lineup_by_position = {6: "def_titular"}
    current_bench_by_position = {3: "def_suplente"}

    assert build_substitution_changes(players_by_id, current_lineup_by_position, current_bench_by_position) == []


def test_build_substitution_changes_handles_multiple_positions_independently():
    players_by_id = _players_by_id(
        [
            ("def_titular", "DEF", "lesionado"), ("def_suplente", "DEF", ""),
            ("med_titular", "MED", "lesionado"), ("med_suplente", "MED", ""),
            ("del_titular", "DEL", ""),  # sano -- no debe generar nada
        ]
    )
    current_lineup_by_position = {6: "def_titular", 2: "med_titular", 0: "del_titular"}
    current_bench_by_position = {3: "def_suplente", 0: "med_suplente"}

    changes = build_substitution_changes(players_by_id, current_lineup_by_position, current_bench_by_position)

    assert len(changes) == 4  # dos parejas, una por posición sustituida
    entering_ids = {c["to"] for c in changes if not c["isBench"]}
    assert entering_ids == {"def_suplente", "med_suplente"}


def test_apply_fixture_difficulty_discounts_score_for_hard_opponent():
    players = [
        {"id": "facil", "team": "Barcelona", "score": 0.8},
        {"id": "dificil", "team": "Girona", "score": 0.8},
        {"id": "sin_dato", "team": "SinPartido", "score": 0.8},
    ]
    difficulty_by_team = {"Barcelona": 0.1, "Girona": 0.9}
    adjusted = apply_fixture_difficulty(players, difficulty_by_team, weight=0.2)
    by_id = {p["id"]: p for p in adjusted}

    assert by_id["dificil"]["expected_score"] < by_id["facil"]["expected_score"]
    assert by_id["sin_dato"]["expected_score"] == 0.8  # sin dato de dificultad, no se toca
