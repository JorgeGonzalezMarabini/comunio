from engine.clause_strategy import select_clause_targets


def _cand(id, position="DEL", lineup_score=0.6, clause_price=10_000_000, status="", recent=(8, 8, 8, 8, 8), matches=10):
    return {
        "id": id,
        "name": f"J{id}",
        "position": position,
        "lineup_score": lineup_score,
        "clause_price": clause_price,
        "status": status,
        "average_points": 8.0,
        "recent_points": "[" + ", ".join(str(x) for x in recent) + "]",
        "matches": matches,
    }


def test_deficit_position_accepted_even_below_weakest_starter():
    targets = select_clause_targets(
        [_cand("1", lineup_score=0.2)], {"DEL": 0.5}, {"DEL"}, available_budget=100_000_000, max_budget_pct=0.2
    )
    assert [t["id"] for t in targets] == ["1"]
    assert targets[0]["fills_deficit"] is True


def test_no_deficit_requires_beating_weakest_starter():
    targets = select_clause_targets(
        [_cand("1", lineup_score=0.4), _cand("2", lineup_score=0.6)],
        {"DEL": 0.5},
        set(),
        available_budget=100_000_000,
        max_budget_pct=0.2,
    )
    assert [t["id"] for t in targets] == ["2"]
    assert abs(targets[0]["lineup_gain"] - 0.1) < 1e-9


def test_clause_above_budget_pct_is_excluded():
    targets = select_clause_targets(
        [_cand("1", clause_price=21_000_000), _cand("2", clause_price=20_000_000)],
        {"DEL": 0.1},
        set(),
        available_budget=100_000_000,
        max_budget_pct=0.2,
    )
    assert [t["id"] for t in targets] == ["2"]


def test_injured_doubtful_low_form_and_few_matches_are_excluded():
    targets = select_clause_targets(
        [
            _cand("injured", status="injured"),
            _cand("doubt", status="doubt"),
            _cand("lowform", recent=(0, 1, 0, 1, 0)),
            _cand("fewmatches", matches=1),
            _cand("ok"),
        ],
        {"DEL": 0.1},
        set(),
        available_budget=100_000_000,
        max_budget_pct=0.2,
    )
    assert [t["id"] for t in targets] == ["ok"]


def test_ordering_deficit_first_then_gain_then_cheaper():
    targets = select_clause_targets(
        [
            _cand("med_big_gain", position="MED", lineup_score=0.9),
            _cand("del_cheap", position="DEL", lineup_score=0.5, clause_price=5_000_000),
            _cand("del_expensive", position="DEL", lineup_score=0.5, clause_price=9_000_000),
            _cand("del_better", position="DEL", lineup_score=0.7, clause_price=15_000_000),
        ],
        {"DEL": 0.3, "MED": 0.3},
        {"DEL"},
        available_budget=100_000_000,
        max_budget_pct=0.2,
    )
    assert [t["id"] for t in targets] == ["del_better", "del_cheap", "del_expensive", "med_big_gain"]
