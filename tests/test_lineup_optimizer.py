import pytest

from engine.lineup_optimizer import (
    LINEUP_SLOT_POSITION_ORDER,
    apply_fixture_difficulty,
    build_lineup_changes,
    pick_lineup,
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
