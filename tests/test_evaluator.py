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


def test_score_player_doubt_penalty_is_independent_of_injury_penalty():
    """
    config.EVALUATOR_WEIGHTS ya no lleva "injury_penalty" (la lesión
    confirmada se descarta antes de llegar aquí, ver jobs/run_market.py) --
    solo "doubt_penalty", que lee "is_doubtful", no "is_injured_or_doubtful".
    """
    weights = {
        "futmondo_points_per_price": 0.35,
        "futmondo_trend": 0.15,
        "xg": 0.25,
        "minutes_played": 0.15,
        "doubt_penalty": 0.20,
    }
    sano = score_player(
        {"points_per_price": 1.0, "trend": 1.0, "xg": 1.0, "minutes_played_ratio": 1.0, "is_doubtful": False},
        weights=weights,
    )
    en_duda = score_player(
        {"points_per_price": 1.0, "trend": 1.0, "xg": 1.0, "minutes_played_ratio": 1.0, "is_doubtful": True},
        weights=weights,
    )
    max_positivo = sum(v for k, v in weights.items() if k != "doubt_penalty")
    assert sano == pytest.approx(max_positivo)
    assert en_duda == pytest.approx(max_positivo - weights["doubt_penalty"])
    # Una lesión confirmada (is_injured_or_doubtful=True) NO debe restar
    # nada aquí -- estos pesos no llevan "injury_penalty".
    lesionado_sin_duda = score_player(
        {
            "points_per_price": 1.0,
            "trend": 1.0,
            "xg": 1.0,
            "minutes_played_ratio": 1.0,
            "is_doubtful": False,
            "is_injured_or_doubtful": True,
        },
        weights=weights,
    )
    assert lesionado_sin_duda == pytest.approx(max_positivo)


def test_score_player_config_evaluator_weights_doubt_penalty_doubles_old_injury_penalty():
    """
    A petición del usuario (2026-08-22): "doubt" debe pesar más que antes
    en la decisión de puja. config.EVALUATOR_WEIGHTS["doubt_penalty"] es
    ahora 0.20, el doble del antiguo "injury_penalty" (0.10).
    """
    assert config.EVALUATOR_WEIGHTS["doubt_penalty"] == pytest.approx(0.20)
    assert "injury_penalty" not in config.EVALUATOR_WEIGHTS


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
    # "injured" es lesión confirmada, no duda -- is_doubtful debe ser False.
    assert by_id["lesionado"]["is_doubtful"] is False
    assert by_id["caro"]["is_doubtful"] is False


def test_normalize_pool_distinguishes_doubt_from_confirmed_injury():
    """
    "doubt" (duda) marca is_doubtful=True pero is_injured_or_doubtful
    también True (sigue siendo "no sano" para squad_risk/lineup_optimizer/
    selling_strategy); "injured2" (lesión confirmada) marca is_doubtful=False.
    """
    raw = [
        {"id": "en_duda", "price": 1_000_000, "average_points": 5.0, "status": "doubt"},
        {"id": "lesion_confirmada", "price": 1_000_000, "average_points": 5.0, "status": "injured2"},
        {"id": "sano", "price": 1_000_000, "average_points": 5.0, "status": "ok"},
    ]
    normalized = normalize_pool(raw)
    by_id = {p["id"]: p for p in normalized}

    assert by_id["en_duda"]["is_doubtful"] is True
    assert by_id["en_duda"]["is_injured_or_doubtful"] is True

    assert by_id["lesion_confirmada"]["is_doubtful"] is False
    assert by_id["lesion_confirmada"]["is_injured_or_doubtful"] is True

    assert by_id["sano"]["is_doubtful"] is False
    assert by_id["sano"]["is_injured_or_doubtful"] is False


def test_normalize_pool_normalizes_xg_within_position_group_not_whole_pool():
    """
    Regresión del sesgo real señalado en el propio TODO del código
    (arreglado 2026-08-17): comparar xG de un defensa contra el de un
    delantero en crudo penalizaba siempre a defensas/porteros (su xG real
    es casi 0 aunque rindan de maravilla en su rol) frente a cualquier
    delantero mediocre. Ahora cada posición se normaliza contra sus
    propios compañeros de posición, no contra todo el mercado mezclado.
    """
    raw = [
        # Dos delanteros con xG alto -- entre ellos, uno claramente mejor.
        {"id": "del_bueno", "position": "DEL", "price": 1_000_000, "average_points": 5.0, "xg": 12.0, "minutes_played": 900, "games": 10},
        {"id": "del_flojo", "position": "DEL", "price": 1_000_000, "average_points": 5.0, "xg": 3.0, "minutes_played": 900, "games": 10},
        # Dos defensas con xG mínimo (normal en su posición) -- entre ellos, uno claramente mejor.
        {"id": "def_bueno", "position": "DEF", "price": 1_000_000, "average_points": 5.0, "xg": 0.3, "minutes_played": 900, "games": 10},
        {"id": "def_flojo", "position": "DEF", "price": 1_000_000, "average_points": 5.0, "xg": 0.05, "minutes_played": 900, "games": 10},
    ]
    normalized = normalize_pool(raw)
    by_id = {p["id"]: p for p in normalized}

    # Dentro de su propio grupo, el mejor de cada posición normaliza a 1.0 -- ya
    # no se queda aplastado cerca de 0 solo por compartir pool con delanteros.
    assert by_id["del_bueno"]["xg"] == 1.0
    assert by_id["del_flojo"]["xg"] == 0.0
    assert by_id["def_bueno"]["xg"] == 1.0
    assert by_id["def_flojo"]["xg"] == 0.0


def test_normalize_pool_shrinks_xg90_towards_zero_for_low_minutes(monkeypatch):
    """
    Regresión real de producción (2026-09-21): un jugador con pocos
    minutos y un solo remate podía salir con xG/90 disparatado (xg /
    minutes_played * 90 sin ningún suelo), fijando el 1.0 normalizado de
    todo su grupo y aplastando a un compañero con minutos y rendimiento
    reales. Con EVALUATOR_XG90_MIN_MINUTES_RATIO=0.5 y team_games=10
    (umbral=450 minutos), un jugador con 1 minuto y 0.08 xG (xG/90
    crudo=7.2, muy por encima del titular real) debe encogerse hacia 0 --
    muy por debajo del titular, no por encima.
    """
    monkeypatch.setattr(config, "EVALUATOR_XG90_MIN_MINUTES_RATIO", 0.5)
    raw = [
        {
            "id": "titular",
            "position": "DEL",
            "price": 1_000_000,
            "average_points": 5.0,
            "xg": 0.9,
            "minutes_played": 900,
            "games": 10,
            "team_games": 10,
        },
        {
            "id": "ruido_1min",
            "position": "DEL",
            "price": 1_000_000,
            "average_points": 1.0,
            "xg": 0.08,
            "minutes_played": 1,
            "games": 1,
            "team_games": 10,
        },
    ]
    normalized = normalize_pool(raw)
    by_id = {p["id"]: p for p in normalized}

    # Sin el suelo: ruido_1min (xG/90 crudo=7.2) normalizaría a 1.0 y
    # titular (xG/90 crudo=0.09) a 0.0 -- justo al revés de lo esperado.
    assert by_id["titular"]["xg"] == 1.0
    assert by_id["ruido_1min"]["xg"] == 0.0


def test_normalize_pool_xg90_shrinkage_scales_linearly_with_minutes_not_towards_group_average(monkeypatch):
    """
    El factor de encogimiento es `minutes_played / (RATIO * team_games *
    90)` (hacia 0), NO una mezcla con la tasa media del grupo -- con pools
    pequeños (el propio mercado de candidatos de jobs/run_market.py, unos
    20 jugadores en 4 posiciones) mezclar con la media del grupo puede
    "contagiar" a un jugador de pocos minutos la tasa de OTRO compañero de
    grupo con minutos reales, en vez de neutralizarlo.
    """
    monkeypatch.setattr(config, "EVALUATOR_XG90_MIN_MINUTES_RATIO", 0.5)
    # team_games=10 para los tres -> umbral = 0.5 * 10 * 90 = 450 minutos.
    raw = [
        # 0 minutos -> xG/90 crudo ya es 0 (fija el suelo del grupo).
        {"id": "suelo", "position": "DEL", "price": 1_000_000, "average_points": 5.0, "xg": 0.0, "minutes_played": 0, "games": 0, "team_games": 10},
        # A mitad del umbral (225/450): xG/90 crudo (0.4) escalado x0.5 -> 0.2.
        {"id": "media_credibilidad", "position": "DEL", "price": 1_000_000, "average_points": 5.0, "xg": 1.0, "minutes_played": 225, "games": 3, "team_games": 10},
        # Con minutos de sobra (>=450): xG/90 crudo intacto, fija el techo del grupo.
        {"id": "techo", "position": "DEL", "price": 1_000_000, "average_points": 5.0, "xg": 5.0, "minutes_played": 900, "games": 10, "team_games": 10},
    ]
    normalized = normalize_pool(raw)
    by_id = {p["id"]: p for p in normalized}
    # xg90 crudo: suelo=0, media_credibilidad=0.4->0.2 (encogido x0.5), techo=0.5
    # minmax sobre [0, 0.2, 0.5] -> media_credibilidad = 0.2/0.5 = 0.4
    assert by_id["suelo"]["xg"] == pytest.approx(0.0)
    assert by_id["techo"]["xg"] == pytest.approx(1.0)
    assert by_id["media_credibilidad"]["xg"] == pytest.approx(0.4)


def test_normalize_pool_xg90_credibility_threshold_scales_with_matchday(monkeypatch):
    """
    A petición del usuario (2026-09-21): el suelo de minutos NO puede ser
    una constante -- 270 minutos en la jornada 6 no representan lo mismo
    que en la jornada 20. Con EVALUATOR_XG90_MIN_MINUTES_RATIO fijo, el
    MISMO jugador con los MISMOS 270 minutos jugados debe normalizar más
    alto en la jornada 6 (270 min sobre un umbral de 0.5*6*90=270 ->
    credibilidad total) que en la jornada 20 (mismo umbral pero calculado
    sobre 0.5*20*90=900 -> credibilidad parcial, 270/900=0.3).
    """
    monkeypatch.setattr(config, "EVALUATOR_XG90_MIN_MINUTES_RATIO", 0.5)

    def _pool(team_games):
        return [
            # Jugador bajo estudio: mismos xg/minutos en ambos escenarios.
            {"id": "sujeto", "position": "DEL", "price": 1_000_000, "average_points": 5.0, "xg": 1.0, "minutes_played": 270, "games": 3, "team_games": team_games},
            # Suelo del grupo (0 minutos, no depende de la jornada).
            {"id": "suelo", "position": "DEL", "price": 1_000_000, "average_points": 5.0, "xg": 0.0, "minutes_played": 0, "games": 0, "team_games": team_games},
            # Techo del grupo: minutos de sobra en CUALQUIER jornada -> credibilidad 1.0 en ambos escenarios.
            {"id": "techo", "position": "DEL", "price": 1_000_000, "average_points": 5.0, "xg": 10.0, "minutes_played": 2000, "games": 20, "team_games": team_games},
        ]

    jornada_6 = {p["id"]: p for p in normalize_pool(_pool(team_games=6))}
    jornada_20 = {p["id"]: p for p in normalize_pool(_pool(team_games=20))}

    # jornada 6: umbral=270 -> credibilidad=270/270=1.0 -> xg90 crudo intacto (0.333)
    # jornada 20: umbral=900 -> credibilidad=270/900=0.3 -> xg90 encogido a 0.333*0.3=0.1
    # techo fija 0.45 en ambos casos (10/2000*90=0.45, credibilidad=1.0 siempre)
    assert jornada_6["sujeto"]["xg"] == pytest.approx((1.0 / 270 * 90) / 0.45)
    assert jornada_20["sujeto"]["xg"] == pytest.approx((1.0 / 270 * 90 * 0.3) / 0.45)
    assert jornada_6["sujeto"]["xg"] > jornada_20["sujeto"]["xg"]


def test_normalize_pool_minutes_ratio_uses_team_games_over_player_games_when_available():
    """
    Regresión de TODO.md #12: sin `team_games`, minutes_played_ratio se
    basaba en `minutes_played / (games * 90)` -- "games" es cuántos
    partidos jugó ÉL, no los del equipo, así que un jugador con varias
    lesiones pero 100% de minutos en los partidos que sí disputó salía con
    ratio máximo. Con `team_games` (partidos YA JUGADOS por su equipo esta
    temporada, ver jobs/sync_data._team_games_by_title()) el mismo jugador
    debe salir con ratio más bajo que un compañero de posición que sí jugó
    todos los partidos del equipo.
    """
    raw = [
        # Se perdió 2 de los 5 partidos del equipo (lesión), pero jugó
        # completos los 3 que sí disputó.
        {"id": "lesionado_recurrente", "position": "DEL", "price": 1_000_000, "average_points": 5.0,
         "minutes_played": 270, "games": 3, "team_games": 5},
        # Titular indiscutible: jugó completos los 5 partidos del equipo.
        {"id": "titular_fijo", "position": "DEL", "price": 1_000_000, "average_points": 5.0,
         "minutes_played": 450, "games": 5, "team_games": 5},
    ]
    normalized = normalize_pool(raw)
    by_id = {p["id"]: p for p in normalized}

    # Sin el arreglo, ambos habrían normalizado igual (100% de minutos en
    # sus partidos jugados); con team_games, el que se perdió partidos por
    # lesión queda claramente por debajo del titular fijo.
    assert by_id["titular_fijo"]["minutes_played_ratio"] == 1.0
    assert by_id["lesionado_recurrente"]["minutes_played_ratio"] == 0.0


def test_normalize_pool_minutes_ratio_falls_back_to_player_games_without_team_games():
    """Sin `team_games` (Understat aún no lo publica, o fila de fallback a temporada anterior), se mantiene la aproximación anterior."""
    raw = [{"id": "a", "price": 1_000_000, "average_points": 5.0, "minutes_played": 450, "games": 5}]
    normalized = normalize_pool(raw)
    assert normalized[0]["minutes_played_ratio"] == 0.5  # único jugador del grupo -> empate consigo mismo


def test_normalize_pool_clean_sheet_rate_from_team_clean_sheets_and_team_games():
    """
    Análisis a petición del usuario (2026-09-11): Futmondo da puntos extra
    por portería a cero -- clean_sheet_rate = team_clean_sheets / team_games,
    normalizado dentro del grupo de posición como el resto de features (ver
    jobs.sync_data._team_clean_sheets_by_title()).
    """
    raw = [
        {"id": "def_solido", "position": "DEF", "price": 1_000_000, "average_points": 5.0,
         "team_games": 10, "team_clean_sheets": 8},
        {"id": "def_goleado", "position": "DEF", "price": 1_000_000, "average_points": 5.0,
         "team_games": 10, "team_clean_sheets": 1},
    ]
    normalized = normalize_pool(raw)
    by_id = {p["id"]: p for p in normalized}
    assert by_id["def_solido"]["clean_sheet_rate"] == 1.0
    assert by_id["def_goleado"]["clean_sheet_rate"] == 0.0


def test_normalize_pool_clean_sheet_rate_zero_without_team_games():
    """Sin `team_games` (0 o ausente) no se puede calcular la tasa -- 0, igual que el resto de features sin dato."""
    raw = [{"id": "a", "position": "DEF", "price": 1_000_000, "average_points": 5.0, "team_clean_sheets": 5}]
    normalized = normalize_pool(raw)
    assert normalized[0]["clean_sheet_rate"] == 0.5  # único jugador del grupo -> empate consigo mismo (valor crudo 0)


def test_score_player_clean_sheet_rate_only_applies_to_por_and_def():
    """
    El bonus de portería a cero real de Futmondo es (sobre todo) para
    POR/DEF -- score_player no debe sumarlo para MED/DEL aunque su
    clean_sheet_rate normalizado no sea 0 (comparten el valor de su equipo).
    """
    weights = {
        "futmondo_points_per_price": 0,
        "futmondo_trend": 0,
        "xg": 0,
        "minutes_played": 0,
        "clean_sheet_rate": 1.0,
    }
    base = {"points_per_price": 0, "trend": 0, "xg": 0, "minutes_played_ratio": 0, "clean_sheet_rate": 1.0}

    assert score_player({**base, "position": "POR"}, weights=weights) == pytest.approx(1.0)
    assert score_player({**base, "position": "DEF"}, weights=weights) == pytest.approx(1.0)
    assert score_player({**base, "position": "MED"}, weights=weights) == pytest.approx(0.0)
    assert score_player({**base, "position": "DEL"}, weights=weights) == pytest.approx(0.0)


def test_score_player_excludes_xg_only_for_por():
    """
    Revisión 2026-09-21 (a petición del usuario, tras 14 días con los
    pesos rebalanceados de config.EVALUATOR_WEIGHTS): Understat no traquea
    xG de porteros (su xg crudo siempre es 0), así que normalize_pool() les
    da a TODOS el mismo 0.5 normalizado -- una señal sin ninguna
    información que solo inflaba el score de POR frente al resto de
    posiciones. score_player NO debe sumar "xg" cuando `position` es "POR",
    pero SÍ debe seguir sumándolo para el resto de posiciones (a
    diferencia de "clean_sheet_rate", que es al revés: solo POR/DEF).
    """
    weights = {
        "futmondo_points_per_price": 0,
        "futmondo_trend": 0,
        "xg": 1.0,
        "minutes_played": 0,
    }
    base = {"points_per_price": 0, "trend": 0, "xg": 1.0, "minutes_played_ratio": 0}

    assert score_player({**base, "position": "POR"}, weights=weights) == pytest.approx(0.0)
    assert score_player({**base, "position": "DEF"}, weights=weights) == pytest.approx(1.0)
    assert score_player({**base, "position": "MED"}, weights=weights) == pytest.approx(1.0)
    assert score_player({**base, "position": "DEL"}, weights=weights) == pytest.approx(1.0)


def test_score_player_ignores_clean_sheet_rate_if_weight_not_configured():
    """Pesos sin "clean_sheet_rate" (p. ej. tests/llamadas antiguas) no deben romper ni sumar nada -- compatibilidad hacia atrás."""
    weights = {"futmondo_points_per_price": 0, "futmondo_trend": 0, "xg": 0, "minutes_played": 0}
    score = score_player({"clean_sheet_rate": 1.0, "position": "DEF"}, weights=weights)
    assert score == 0.0


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
    demás no debe salir peor puntuado que uno barato y mediocre solo por
    el precio -- ni con los pesos de ALINEACIÓN (sin
    "futmondo_points_per_price", precio = coste hundido) ni, desde
    2026-09-07, con los de PUJA: el peso de "futmondo_points_per_price" se
    bajó de 0.35 a 0.10 precisamente porque, al ser una ratio puntos/precio,
    favorecía sistemáticamente al barato mediocre sobre el caro realmente
    mejor (ver comentario de EVALUATOR_WEIGHTS en config.py y la
    conversación que lo motivó: fichajes de relleno mientras el
    presupuesto se acumulaba). El residuo de peso en precio (0.10) sigue
    ahí a propósito -- en compra el precio es dinero real todavía sin
    gastar, a diferencia de la alineación -- pero ya no basta para tapar
    una diferencia real de rendimiento como la de este test.

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

    assert score_player(caro_mejor, weights=config.EVALUATOR_WEIGHTS) > score_player(
        barato_mediocre, weights=config.EVALUATOR_WEIGHTS
    )
    assert score_player(caro_mejor, weights=config.LINEUP_EVALUATOR_WEIGHTS) > score_player(
        barato_mediocre, weights=config.LINEUP_EVALUATOR_WEIGHTS
    )


# --- Forma ponderada por recencia (a petición del usuario, 2026-09-23, ver
# config.EVALUATOR_RECENT_POINTS_DECAY) ---

from engine.evaluator import form_points, weighted_recent_points  # noqa: E402


def test_weighted_recent_points_gives_more_weight_to_latest_matchdays():
    # Mismos puntos, orden inverso: quien puntuó al FINAL (más reciente) sale mejor.
    late = weighted_recent_points([0, 0, 0, 0, 10], decay=0.7)
    early = weighted_recent_points([10, 0, 0, 0, 0], decay=0.7)
    assert late > early
    weights = [0.7 ** k for k in range(5)]
    assert late == pytest.approx(10 / sum(weights))


def test_weighted_recent_points_uses_season_average_as_tail():
    decay = 0.7
    tail = decay ** 5 / (1 - decay)
    value = weighted_recent_points([2, 2, 2, 2, 2], season_average=8.0, decay=decay)
    assert value == pytest.approx((2 * sum(decay ** k for k in range(5)) + 8.0 * tail) / (sum(decay ** k for k in range(5)) + tail))
    assert 2 < value < 8


def test_weighted_recent_points_accepts_json_and_rejects_missing():
    assert weighted_recent_points("[1, 2, 3]", decay=0.7) == pytest.approx(weighted_recent_points([1, 2, 3], decay=0.7))
    assert weighted_recent_points(None, season_average=5.0) is None
    assert weighted_recent_points("not json", season_average=5.0) is None


def test_form_points_falls_back_to_season_average():
    assert form_points({"average_points": 4.0}) == 4.0
    assert form_points({}) == 0
    assert form_points({"average_points": 6.0, "recent_points": [0, 0, 0, 0, 0]}) < 1.5  # vs 6.0 de media plana


def test_normalize_pool_prefers_recent_form_over_flat_average():
    """Misma media de temporada: el que puntúa ahora sale con más points_per_price y trend."""
    base = {"position": "MED", "price": 1_000_000, "average_points": 4.0, "status": ""}
    hot = {**base, "id": "hot", "recent_points": [0, 0, 2, 8, 10]}
    cold = {**base, "id": "cold", "recent_points": [10, 8, 2, 0, 0]}
    by_id = {p["id"]: p for p in normalize_pool([hot, cold])}
    assert by_id["hot"]["points_per_price"] > by_id["cold"]["points_per_price"]
    assert by_id["hot"]["trend"] > by_id["cold"]["trend"]


def test_normalize_pool_blends_recent_minutes(monkeypatch):
    """Mismos minutos de temporada: quien ha dejado de jugar las últimas jornadas sale peor."""
    monkeypatch.setattr(config, "EVALUATOR_RECENT_MINUTES_WEIGHT", 0.6)
    base = {"position": "DEF", "price": 1_000_000, "average_points": 3.0, "status": "",
            "minutes_played": 450, "team_games": 7}
    starter_now = {**base, "id": "a", "minutes_played_ref": 180, "team_games_ref": 4}  # 270' en 3 partidos
    benched_now = {**base, "id": "b", "minutes_played_ref": 450, "team_games_ref": 4}  # 0' en 3 partidos
    no_history = {**base, "id": "c"}
    by_id = {p["id"]: p for p in normalize_pool([starter_now, benched_now, no_history])}
    assert by_id["a"]["minutes_played_ratio"] > by_id["c"]["minutes_played_ratio"] > by_id["b"]["minutes_played_ratio"]


def test_lineup_score_includes_points_level():
    """
    Con config.LINEUP_EVALUATOR_WEIGHTS, a igualdad de xG/minutos/tendencia,
    quien puntúa más en Futmondo sale mejor (antes solo contaba la tendencia).
    """
    base = {"position": "DEL", "price": 1_000_000, "status": "", "xg": 1.0, "minutes_played": 450, "team_games": 5}
    good = {**base, "id": "good", "average_points": 5.0, "last_points": 5}
    poor = {**base, "id": "poor", "average_points": 1.0, "last_points": 1}
    by_id = {p["id"]: p["score"] for p in evaluate_players([good, poor], weights=config.LINEUP_EVALUATOR_WEIGHTS)}
    assert by_id["good"] > by_id["poor"]


def test_lineup_weights_keep_positive_sum_at_one():
    """Los umbrales en escala de score de alineación asumen que los pesos positivos (sin clean sheet) suman 1.0."""
    w = config.LINEUP_EVALUATOR_WEIGHTS
    positive = sum(v for k, v in w.items() if k not in ("injury_penalty", "doubt_penalty", "clean_sheet_rate"))
    assert positive == pytest.approx(1.0)
