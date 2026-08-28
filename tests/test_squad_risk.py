from engine.squad_risk import assess_squad_depth, depth_warnings, sales_to_cancel, weakest_starter_scores


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


def test_assess_squad_depth_excludes_players_already_listed_for_sale():
    """
    Un jugador ya puesto en venta (por una pasada anterior de run_sales) no
    debería contar como "disponible" en el cálculo de banquillo -- si su
    venta se resuelve mientras tanto, deja el mismo hueco que un lesionado.
    Cubre ambos formatos de "ya en venta" que usan los distintos llamantes:
    `on_market` (get_player_features(), jobs/run_market.py) y `market`
    (item crudo de FutmondoClient.get_roster(), engine/selling_strategy.py).
    """
    squad = [
        {"id": "del1", "position": "DEL", "status": "", "on_market": 1},  # ya listado -- no disponible
        {"id": "del2", "position": "DEL", "status": "", "market": {"price": 123}},  # ya listado -- no disponible
    ]
    assessment = assess_squad_depth(squad, formation="4-4-2")

    assert assessment["DEL"]["total"] == 2
    assert assessment["DEL"]["available"] == 0
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


def test_sales_to_cancel_ignores_zero_margin_only_deficit_triggers():
    """
    `at_risk` (margen cero) NO debe disparar la cancelación por sí solo --
    si no, cualquier venta recién listada se cancelaría en la primera
    pasada (listar ya resta uno de "disponible", ver docstring de
    `assess_squad_depth`). Solo `deficit > 0` (margen negativo) justifica
    recuperar una venta ya activa.
    """
    assessment = {"DEF": {"deficit": 0}}
    open_sales = [{"id": 1, "player_id": "d1"}]
    position_by_player_id = {"d1": "DEF"}

    assert sales_to_cancel(assessment, open_sales, position_by_player_id) == []


def test_sales_to_cancel_picks_oldest_first_up_to_the_deficit():
    """Con déficit=1 y dos ventas abiertas en esa posición, solo se cancela la más antigua (menor id)."""
    assessment = {"DEF": {"deficit": 1}}
    open_sales = [{"id": 5, "player_id": "newer"}, {"id": 2, "player_id": "older"}]
    position_by_player_id = {"newer": "DEF", "older": "DEF"}

    to_cancel = sales_to_cancel(assessment, open_sales, position_by_player_id)
    assert [s["id"] for s in to_cancel] == [2]


def test_sales_to_cancel_ignores_sale_for_player_no_longer_in_squad():
    """
    Si el jugador de la venta ya no aparece en la plantilla (p.ej.
    `_reconcile_sales()` ya lo marcó 'sold' en esta misma pasada), no hay
    nada que cancelar -- se ignora sin más.
    """
    assessment = {"DEF": {"deficit": 1}}
    open_sales = [{"id": 1, "player_id": "gone"}]
    position_by_player_id = {}  # "gone" ya no está en la plantilla

    assert sales_to_cancel(assessment, open_sales, position_by_player_id) == []


def test_sales_to_cancel_end_to_end_with_assess_squad_depth():
    """
    Escenario real completo: 5 DEF sanos (margen 1) -> se lista la venta
    del suplente sobrante -> bench cae a 0 (at_risk, pero deficit=0: nada
    que rescatar todavía). Si DESPUÉS otro DEF distinto desaparece de la
    plantilla entera (cláusula, venta, lesión...), el margen se vuelve
    NEGATIVO -- ahí sí hay que cancelar la venta ya listada.
    """
    squad_after_listing = [{"id": "def_listed", "position": "DEF", "status": "", "market": True}] + [
        {"id": f"def{i}", "position": "DEF", "status": ""} for i in range(4)
    ]
    assessment = assess_squad_depth(squad_after_listing, formation="4-4-2")
    assert assessment["DEF"]["at_risk"] is True
    assert assessment["DEF"]["deficit"] == 0

    open_sales = [{"id": 1, "player_id": "def_listed"}]
    position_by_player_id = {p["id"]: p["position"] for p in squad_after_listing}
    assert sales_to_cancel(assessment, open_sales, position_by_player_id) == []

    # Un DEF distinto desaparece por completo de la plantilla (p.ej. clausulado).
    squad_after_loss = squad_after_listing[:-1]
    assessment2 = assess_squad_depth(squad_after_loss, formation="4-4-2")
    assert assessment2["DEF"]["deficit"] == 1

    position_by_player_id2 = {p["id"]: p["position"] for p in squad_after_loss}
    to_cancel = sales_to_cancel(assessment2, open_sales, position_by_player_id2)
    assert [s["id"] for s in to_cancel] == [1]


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
