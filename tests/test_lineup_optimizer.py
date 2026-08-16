import pytest

from engine.lineup_optimizer import (
    apply_fixture_difficulty,
    build_lineup_slots,
    pick_lineup,
    pick_substitutes,
    to_api_tactic,
)


def _squad_442():
    """Plantilla justa para un 4-4-2: exactamente 1 POR, 4 DEF, 4 MED, 2 DEL."""
    squad = [{"id": "por1", "position": "POR", "expected_score": 0.9}]
    squad += [{"id": f"def{i}", "position": "DEF", "expected_score": 0.5 + i * 0.01} for i in range(4)]
    squad += [{"id": f"med{i}", "position": "MED", "expected_score": 0.5 + i * 0.01} for i in range(4)]
    squad += [{"id": f"del{i}", "position": "DEL", "expected_score": 0.5 + i * 0.01} for i in range(2)]
    return squad


def test_to_api_tactic_strips_dashes():
    """Confirmado por captura real: la API espera 'tactic' sin guiones ('442', no '4-4-2')."""
    assert to_api_tactic("4-4-2") == "442"
    assert to_api_tactic("4-3-3") == "433"


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


def test_build_lineup_slots_confirmed_order_del_med_def_por():
    """
    Numeración CONFIRMADA por captura real (once completo + interceptación
    de la llamada PUT real): slots 1..11 agrupados por posición en el
    orden fijo delanteros -> centrocampistas -> defensas -> portero,
    portero siempre el último.
    """
    squad = _squad_442()
    lineup = pick_lineup(squad, formation="4-4-2")
    slots = build_lineup_slots(squad, lineup["starters"])

    assert slots["11"] == "por1"  # portero siempre el último slot
    del_slots = {slots[str(i)] for i in range(1, 3)}
    med_slots = {slots[str(i)] for i in range(3, 7)}
    def_slots = {slots[str(i)] for i in range(7, 11)}
    assert all(pid.startswith("del") for pid in del_slots)
    assert all(pid.startswith("med") for pid in med_slots)
    assert all(pid.startswith("def") for pid in def_slots)


def test_pick_substitutes_picks_best_score_per_position():
    bench = [
        {"id": "suplente_bueno", "position": "DEL", "expected_score": 0.8},
        {"id": "suplente_malo", "position": "DEL", "expected_score": 0.2},
        {"id": "suplente_por", "position": "POR", "expected_score": 0.5},
    ]
    substitutes = pick_substitutes(bench)
    assert substitutes["striker"] == "suplente_bueno"
    assert substitutes["keeper"] == "suplente_por"
    assert substitutes["defender"] == ""  # sin suplente disponible en esa posición
    assert substitutes["midfielder"] == ""


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
