import pytest

import config
from engine.evaluator import evaluate_players, normalize_pool, rank_players, score_player


def test_score_player_weights_and_injury_penalty():
    weights = {
        "futmondo_points_per_price": 0.35,
        "futmondo_trend": 0.15,
        "xg": 0.25,
        "minutes_played": 0.15,
        "injury_penalty": 0.10,
    }
    sano = score_player(
        {"points_per_price": 1.0, "trend": 1.0, "xg": 1.0, "minutes_played_ratio": 1.0, "is_injured_or_doubtful": False},
        weights=weights,
    )
    lesionado = score_player(
        {"points_per_price": 1.0, "trend": 1.0, "xg": 1.0, "minutes_played_ratio": 1.0, "is_injured_or_doubtful": True},
        weights=weights,
    )
    max_positivo = sum(v for k, v in weights.items() if k != "injury_penalty")  # 0.90, no 1.0: injury_penalty es aparte
    assert sano == pytest.approx(max_positivo)
    assert lesionado == pytest.approx(max_positivo - weights["injury_penalty"])


def test_rank_players_orders_by_score_desc():
    ranked = rank_players(
        [
            {"id": "a", "points_per_price": 0.2},
            {"id": "b", "points_per_price": 0.9},
        ],
        weights={"futmondo_points_per_price": 1.0, "futmondo_trend": 0, "xg": 0, "minutes_played": 0, "injury_penalty": 0},
    )
    assert [p["id"] for p in ranked] == ["b", "a"]


def test_normalize_pool_minmax_and_injury_flag():
    raw = [
        {"id": "caro", "price": 9_000_000, "average_points": 15.0, "last_points": 15, "xg": 12.5, "minutes_played": 1200, "games": 13, "status": ""},
        {"id": "barato", "price": 350_000, "average_points": 8.0, "last_points": 8, "xg": 0.3, "minutes_played": 450, "games": 5, "status": ""},
        {"id": "lesionado", "price": 1_000_000, "average_points": 4.0, "last_points": 2, "xg": 0.1, "minutes_played": 900, "games": 10, "status": "injured"},
    ]
    normalized = normalize_pool(raw)
    by_id = {p["id"]: p for p in normalized}

    # El barato tiene mejor relación puntos/precio que el caro -> debe normalizar a 1.0 en ese eje
    assert by_id["barato"]["points_per_price"] == 1.0
    assert by_id["caro"]["points_per_price"] == 0.0
    # El lesionado se marca correctamente
    assert by_id["lesionado"]["is_injured_or_doubtful"] is True
    assert by_id["caro"]["is_injured_or_doubtful"] is False


def test_normalize_pool_handles_tied_values_without_crash():
    # Rango 0 en todas las features -> no debe dividir por cero
    raw = [{"id": "a", "price": 1_000_000, "average_points": 5.0}, {"id": "b", "price": 1_000_000, "average_points": 5.0}]
    normalized = normalize_pool(raw)
    assert all(p["points_per_price"] == 0.5 for p in normalized)


def test_evaluate_players_end_to_end_ranks_by_combined_score():
    """
    Con dos candidatos, min-max normaliza cada feature a 0/1 (o 0.5 si
    empatan) -- aquí "Caro" gana en xG90/minutos y solo empata en trend,
    así que gana incluso con los pesos de puja (el precio es un peso más,
    no el único criterio). Ver test_lineup_weights_dont_penalize_* para el
    caso que sí demuestra el sesgo de precio con datos exactos.
    """
    raw = [
        {"id": "1074", "name": "Caro", "price": 9_000_000, "average_points": 15.0, "last_points": 15, "xg": 12.5, "minutes_played": 1200, "games": 13, "status": ""},
        {"id": "4069", "name": "Barato", "price": 350_000, "average_points": 8.0, "last_points": 8, "xg": 0.3, "minutes_played": 450, "games": 5, "status": ""},
    ]
    ranked = evaluate_players(raw, weights=config.EVALUATOR_WEIGHTS)
    assert ranked[0]["score"] > ranked[1]["score"]
    assert {p["id"] for p in ranked} == {"1074", "4069"}


def test_lineup_weights_dont_penalize_expensive_player_by_price():
    """
    Regresión del sesgo real detectado probando jobs/set_lineup.py con
    datos sintéticos (ver README): un jugador caro pero mejor en todo lo
    demás puede salir peor puntuado que uno barato y mediocre solo por el
    precio si se usan los pesos de PUJA para elegir alineación; con los
    pesos de ALINEACIÓN (sin futmondo_points_per_price) no debe pasar.

    Se llama a score_player() directamente con features ya normalizadas
    (no via evaluate_players/normalize_pool) para poder fijar valores
    intermedios exactos -- con min-max sobre solo 2 candidatos cada
    feature cae en 0/0.5/1, lo que no permite construir este escenario.
    """
    caro_mejor = {
        "points_per_price": 0.0,  # caro, la métrica que peor le sale
        "trend": 0.6,
        "xg": 0.6,
        "minutes_played_ratio": 0.6,
        "is_injured_or_doubtful": False,
    }
    barato_mediocre = {
        "points_per_price": 1.0,  # barato, gana claro en esta métrica
        "trend": 0.4,
        "xg": 0.4,
        "minutes_played_ratio": 0.4,
        "is_injured_or_doubtful": False,
    }

    assert score_player(barato_mediocre, weights=config.EVALUATOR_WEIGHTS) > score_player(
        caro_mejor, weights=config.EVALUATOR_WEIGHTS
    )
    assert score_player(caro_mejor, weights=config.LINEUP_EVALUATOR_WEIGHTS) > score_player(
        barato_mediocre, weights=config.LINEUP_EVALUATOR_WEIGHTS
    )
