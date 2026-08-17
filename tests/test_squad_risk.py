from engine.squad_risk import assess_squad_depth, depth_warnings, weakest_starter_scores


def test_assess_squad_depth_flags_positions_without_healthy_bench():
    squad = (
        [{"id": "por1", "position": "POR", "status": ""}]  # 1 para 1 -> en riesgo
        + [{"id": f"def{i}", "position": "DEF", "status": ""} for i in range(4)]  # 4 para 4 -> en riesgo
        + [{"id": "def_lesionado", "position": "DEF", "status": "INJURED"}]  # no cuenta como disponible
        + [{"id": f"med{i}", "position": "MED", "status": ""} for i in range(6)]  # 6 para 4 -> con margen
        + [{"id": f"del{i}", "position": "DEL", "status": ""} for i in range(2)]  # 2 para 2 -> en riesgo
    )
    assessment = assess_squad_depth(squad, formation="4-4-2")

    assert assessment["POR"]["at_risk"] is True
    assert assessment["DEF"]["available"] == 4  # el lesionado no cuenta
    assert assessment["DEF"]["at_risk"] is True
    assert assessment["MED"]["bench"] == 2
    assert assessment["MED"]["at_risk"] is False
    assert assessment["DEL"]["at_risk"] is True


def test_depth_warnings_only_lists_at_risk_positions():
    squad = (
        [{"id": "por1", "position": "POR", "status": ""}]
        + [{"id": f"def{i}", "position": "DEF", "status": ""} for i in range(6)]  # sobra
        + [{"id": f"med{i}", "position": "MED", "status": ""} for i in range(4)]  # justo
        + [{"id": f"del{i}", "position": "DEL", "status": ""} for i in range(2)]  # justo
    )
    assessment = assess_squad_depth(squad, formation="4-4-2")
    warnings = depth_warnings(assessment)

    warned_positions = {w.split(":")[0] for w in warnings}
    assert warned_positions == {"POR", "MED", "DEL"}
    assert "DEF" not in warned_positions
    assert all("-4 puntos" in w for w in warnings)


def test_weakest_starter_scores_returns_score_of_last_starter_picked():
    """
    Con 5 MED (4 titulares + 1 en el banquillo), el listón debe ser el
    score del PEOR de los 4 titulares (0.5), no el del quinto (0.3, que ni
    siquiera jugaría) ni el mejor (0.9).
    """
    squad = [
        {"id": "med_top", "position": "MED", "status": "", "score": 0.9},
        {"id": "med_2", "position": "MED", "status": "", "score": 0.8},
        {"id": "med_3", "position": "MED", "status": "", "score": 0.7},
        {"id": "med_weakest_starter", "position": "MED", "status": "", "score": 0.5},
        {"id": "med_bench", "position": "MED", "status": "", "score": 0.3},
        {"id": "por1", "position": "POR", "status": "", "score": 0.6},
    ]
    thresholds = weakest_starter_scores(squad, formation="4-4-2")
    assert thresholds["MED"] == 0.5


def test_weakest_starter_scores_ignores_injured_when_healthy_available():
    """
    Un lesionado con score alto no debe fijar el listón si hay sanos de
    sobra para cubrir la formación (mismo criterio que
    engine.lineup_optimizer._rank_healthy_first).
    """
    squad = [
        {"id": "med_injured_top", "position": "MED", "status": "INJURED", "score": 0.95},
        {"id": "med_1", "position": "MED", "status": "", "score": 0.6},
        {"id": "med_2", "position": "MED", "status": "", "score": 0.5},
        {"id": "med_3", "position": "MED", "status": "", "score": 0.4},
        {"id": "med_4", "position": "MED", "status": "", "score": 0.2},
    ]
    thresholds = weakest_starter_scores(squad, formation="4-4-2")
    # Titulares sanos: med_1..med_4 (los 4 necesarios) -- el lesionado
    # queda fuera pese a su score más alto, y el listón es el del sano más flojo.
    assert thresholds["MED"] == 0.2


def test_weakest_starter_scores_none_when_position_has_no_players_yet():
    squad = [{"id": "med1", "position": "MED", "status": "", "score": 0.5}]
    thresholds = weakest_starter_scores(squad, formation="4-4-2")
    assert thresholds["DEL"] is None


def test_weakest_starter_scores_rejects_unknown_formation():
    import pytest

    with pytest.raises(ValueError):
        weakest_starter_scores([], formation="9-9-9")
