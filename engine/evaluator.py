"""
Motor de evaluación: calcula un score combinado por jugador a partir de
datos internos de Comunio (puntos, precio, tendencia) y stats externas
(xG, minutos, estado de lesión).

Los pesos NO están hardcodeados aquí: viven en config.EVALUATOR_WEIGHTS
para poder ajustarlos con el tiempo sin tocar esta lógica.
"""
import config


def score_player(player_stats: dict) -> float:
    """
    Calcula el score de un jugador.

    `player_stats` es un dict que combina lo que salga de db/models.py
    (players + price_history + external_stats) una vez esas fuentes estén
    pobladas de verdad. Forma esperada (provisional):

        {
            "points_per_price": float,   # normalizado, ej. puntos/millón
            "trend": float,              # -1..1, tendencia reciente
            "xg": float,                 # normalizado 0..1
            "minutes_played_ratio": float,  # 0..1, % de minutos posibles jugados
            "is_injured_or_doubtful": bool,
        }

    Devuelve un score comparable entre jugadores (mayor = mejor).

    TODO: definir normalización real de cada input una vez haya datos
    reales en db/models.py (por ahora los pesos son un punto de partida,
    no valores validados).
    """
    w = config.EVALUATOR_WEIGHTS

    score = (
        w["comunio_points_per_price"] * player_stats.get("points_per_price", 0)
        + w["comunio_trend"] * player_stats.get("trend", 0)
        + w["xg"] * player_stats.get("xg", 0)
        + w["minutes_played"] * player_stats.get("minutes_played_ratio", 0)
    )

    if player_stats.get("is_injured_or_doubtful"):
        score -= w["injury_penalty"]

    return score


def rank_players(players_stats: list[dict]) -> list[dict]:
    """Ordena una lista de jugadores por score descendente, añadiendo el score."""
    scored = []
    for p in players_stats:
        p = dict(p)
        p["score"] = score_player(p)
        scored.append(p)
    return sorted(scored, key=lambda p: p["score"], reverse=True)
