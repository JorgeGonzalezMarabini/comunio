from engine.squad_risk import assess_squad_depth, depth_warnings


def test_assess_squad_depth_flags_positions_without_healthy_bench():
    squad = (
        [{"id": "por1", "position": "POR", "status": "ACTIVE"}]  # 1 para 1 -> en riesgo
        + [{"id": f"def{i}", "position": "DEF", "status": "ACTIVE"} for i in range(4)]  # 4 para 4 -> en riesgo
        + [{"id": "def_lesionado", "position": "DEF", "status": "INJURED"}]  # no cuenta como disponible
        + [{"id": f"med{i}", "position": "MED", "status": "ACTIVE"} for i in range(6)]  # 6 para 4 -> con margen
        + [{"id": f"del{i}", "position": "DEL", "status": "ACTIVE"} for i in range(2)]  # 2 para 2 -> en riesgo
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
        [{"id": "por1", "position": "POR", "status": "ACTIVE"}]
        + [{"id": f"def{i}", "position": "DEF", "status": "ACTIVE"} for i in range(6)]  # sobra
        + [{"id": f"med{i}", "position": "MED", "status": "ACTIVE"} for i in range(4)]  # justo
        + [{"id": f"del{i}", "position": "DEL", "status": "ACTIVE"} for i in range(2)]  # justo
    )
    assessment = assess_squad_depth(squad, formation="4-4-2")
    warnings = depth_warnings(assessment)

    warned_positions = {w.split(":")[0] for w in warnings}
    assert warned_positions == {"POR", "MED", "DEL"}
    assert "DEF" not in warned_positions
    assert all("-4 puntos" in w for w in warnings)
