import pytest

from engine.lineup_optimizer import (
    BENCH_SLOT_BY_POSITION,
    LINEUP_SLOT_POSITION_ORDER,
    apply_fixture_difficulty,
    build_bench_changes,
    build_lineup_changes,
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
