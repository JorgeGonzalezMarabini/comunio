"""
Motor de evaluación: calcula un score combinado por jugador a partir de
datos internos de Futmondo (puntos, precio, tendencia, estado) y stats
externas de Understat (xG, minutos).

Dos capas:
  - `score_player`/`rank_players`: funciones puras sobre features ya
    normalizadas 0..1 (fáciles de testear sin BD).
  - `normalize_pool`/`evaluate_players`: puente real desde
    `db.models.get_player_features()` (columnas crudas de Futmondo +
    Understat) a esas features normalizadas.

Los pesos NO están hardcodeados aquí: viven en config.EVALUATOR_WEIGHTS
para poder ajustarlos con el tiempo sin tocar esta lógica.
"""
from __future__ import annotations

import config
from clients.futmondo_client import is_doubtful_status, is_injury_status


def score_player(player_stats: dict, weights: dict = None) -> float:
    """
    Calcula el score de un jugador.

    `player_stats` espera (todas ya normalizadas/comparables, ver
    `normalize_pool` para cómo se obtienen a partir de datos reales):

        {
            "points_per_price": float,   # normalizado 0..1 dentro del pool
            "trend": float,              # normalizado 0..1 dentro del pool
            "xg": float,                 # normalizado 0..1 dentro del pool (xG/90)
            "minutes_played_ratio": float,  # normalizado 0..1 dentro del pool
            "is_injured_or_doubtful": bool,  # duda O lesión confirmada -- consumido por "injury_penalty"
            "is_doubtful": bool,              # SOLO duda (no lesión confirmada) -- consumido por "doubt_penalty"
        }

    `weights`: por defecto config.EVALUATOR_WEIGHTS (pensado para decidir
    pujas: el precio importa). Para decidir ALINEACIÓN usar
    config.LINEUP_EVALUATOR_WEIGHTS — un jugador de la plantilla ya está
    comprado, su precio es coste hundido y no debería influir en quién
    juega (ver jobs/set_lineup.py).

    Los pesos deciden qué penalización por estado aplicar, según qué
    clave traigan (ambas son opcionales e independientes, pueden
    combinarse si algún día hiciera falta):
      - "injury_penalty": resta si `is_injured_or_doubtful` es True (duda
        O lesión confirmada, sin distinguir) — el caso de
        LINEUP_EVALUATOR_WEIGHTS, donde no tiene sentido "descartar" a un
        jugador ya propio.
      - "doubt_penalty": resta si `is_doubtful` es True (SOLO duda) — el
        caso de EVALUATOR_WEIGHTS (pujas): la lesión CONFIRMADA ya se
        descarta antes de llegar aquí (ver jobs/run_market.py y
        clients.futmondo_client.is_confirmed_injured_status()), así que
        no necesita penalización de score, solo la duda la necesita.

    Devuelve un score comparable entre jugadores (mayor = mejor). Con los
    pesos de EVALUATOR_WEIGHTS, el rango típico es aprox. [-0.20, 0.90] (la
    suma de pesos positivos es 0.90, doubt_penalty resta hasta 0.20 más).

    TODO: los pesos son un punto de partida razonado, no calibrado todavía
    contra resultados reales de la liga — ajustar con el tiempo en
    config.EVALUATOR_WEIGHTS/LINEUP_EVALUATOR_WEIGHTS según se vea qué
    correlaciona con puntos reales.
    """
    w = weights or config.EVALUATOR_WEIGHTS

    score = (
        w["futmondo_points_per_price"] * player_stats.get("points_per_price", 0)
        + w["futmondo_trend"] * player_stats.get("trend", 0)
        + w["xg"] * player_stats.get("xg", 0)
        + w["minutes_played"] * player_stats.get("minutes_played_ratio", 0)
    )

    if "injury_penalty" in w and player_stats.get("is_injured_or_doubtful"):
        score -= w["injury_penalty"]
    if "doubt_penalty" in w and player_stats.get("is_doubtful"):
        score -= w["doubt_penalty"]

    return score


def rank_players(players_stats: list[dict], weights: dict = None) -> list[dict]:
    """Ordena una lista de jugadores por score descendente, añadiendo el score."""
    scored = []
    for p in players_stats:
        p = dict(p)
        p["score"] = score_player(p, weights)
        scored.append(p)
    return sorted(scored, key=lambda p: p["score"], reverse=True)


def _minmax_normalize(values: list[float]) -> list[float]:
    """
    Normaliza a 0..1 dentro de la propia lista. Si todos los valores son
    iguales (rango 0, p. ej. un pool de un solo jugador, o en pretemporada
    con todo a cero), devuelve 0.5 para todos en vez de dividir por cero o
    sesgar a 0/1 arbitrariamente.
    """
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def normalize_pool(raw_players: list[dict]) -> list[dict]:
    """
    Convierte una lista de jugadores con columnas crudas (la forma que
    devuelve `db.models.get_player_features()`: price, points, last_points,
    average_points, status, xg, minutes_played, games, team_games, position...) en la
    forma que espera `score_player`, normalizando cada feature 0..1
    **dentro de su propio grupo de posición** (POR/DEF/MED/DEL) — el score
    resultante es comparable entre los jugadores pasados en la misma
    llamada, no un valor absoluto entre llamadas distintas.

    Normalizar por posición (y no contra todo el pool mezclado, como se
    hacía antes) importa de verdad: la fórmula de puntos real de Futmondo
    premia cosas distintas según la posición (porteros/defensas ganan por
    portería a cero, delanteros/centrocampistas por goles/asistencias), y
    el xG de Understat mide amenaza ofensiva — un buen central tiene un xG
    casi 0 no porque rinda mal, sino porque no es su función. Normalizar
    contra todo el pool mezclado hacía que un central top saliera siempre
    con "xg" normalizado cerca de 0 frente a cualquier delantero mediocre,
    sesgando sistemáticamente a la baja a porteros/defensas — más grave
    todavía en `config.LINEUP_EVALUATOR_WEIGHTS` (peso de "xg" 0.40, el
    más alto de los cuatro, precisamente porque excluye el precio). Un
    jugador sin `position` (no debería pasar con datos reales de Futmondo,
    ya mapeados por `FUTMONDO_POSITION_MAP`) se agrupa aparte, con los
    demás jugadores sin posición conocida — nunca junto a un grupo real.

    Definición de cada feature (razonada, no perfecta — ver TODOs):
      - points_per_price: average_points / precio_en_millones. Usa el
        promedio de puntos por jornada (no el total), para no penalizar a
        quien lleva menos jornadas jugadas por lesión/fichaje tardío.
      - trend: last_points - average_points. Positivo si el último
        rendimiento fue mejor que su media de temporada (jugador "caliente"
        ahora mismo), sin necesitar consultar el histórico completo.
      - xg: xG por 90 minutos (xg / minutes_played * 90). Comparar xG total
        penalizaría a quien ha jugado menos minutos sin ser peor jugador.
      - minutes_played_ratio: minutes_played / (team_games * 90) cuando se
        conoce `team_games` (partidos YA JUGADOS por el equipo esta
        temporada, ver `jobs/sync_data._team_games_by_title()` — resuelve
        TODO.md #12, confirmado 2026-08-18 que
        `laliga_stats_client.get_league_data()["teams"][id]["history"]` es
        exactamente eso). Así sí distingue "poco jugado en total" (varias
        lesiones) de "poco jugado por partido" (suplente de pocos
        minutos): un jugador con 3 lesiones y 100% de minutos en los
        partidos que sí jugó ya NO sale con ratio alto, porque se divide
        entre los partidos del EQUIPO, no solo los que él disputó.
        `team_games` viene `None` (fallback a minutes_played / (games *
        90), la aproximación anterior — misma escala solo cuando `games`
        es de la misma temporada que `minutes_played`) si Understat
        todavía no publica el `history` del equipo para la temporada
        actual, o si la fila es del fallback a temporada anterior (ver
        `jobs/sync_data.py`).
    """
    n = len(raw_players)
    points_per_price = [0.0] * n
    trend = [0.0] * n
    xg90 = [0.0] * n
    minutes_ratio = [0.0] * n

    for i, p in enumerate(raw_players):
        price = p.get("price") or 0
        avg_points = p.get("average_points") or 0
        points_per_price[i] = avg_points / (price / 1_000_000) if price > 0 else 0

        last_points = p.get("last_points")
        trend[i] = (last_points if last_points is not None else avg_points) - avg_points

        minutes = p.get("minutes_played") or 0
        xg = p.get("xg") or 0
        xg90[i] = xg / minutes * 90 if minutes > 0 else 0

        team_games = p.get("team_games") or 0
        if team_games > 0:
            minutes_ratio[i] = minutes / (team_games * 90)
        else:
            games = p.get("games") or 0
            minutes_ratio[i] = minutes / (games * 90) if games > 0 else 0

    groups_idx: dict = {}
    for i, p in enumerate(raw_players):
        groups_idx.setdefault(p.get("position"), []).append(i)

    norm_ppp = [0.0] * n
    norm_trend = [0.0] * n
    norm_xg90 = [0.0] * n
    norm_minutes = [0.0] * n

    for idxs in groups_idx.values():
        group_ppp = _minmax_normalize([points_per_price[i] for i in idxs])
        group_trend = _minmax_normalize([trend[i] for i in idxs])
        group_xg90 = _minmax_normalize([xg90[i] for i in idxs])
        group_minutes = _minmax_normalize([minutes_ratio[i] for i in idxs])
        for j, i in enumerate(idxs):
            norm_ppp[i] = group_ppp[j]
            norm_trend[i] = group_trend[j]
            norm_xg90[i] = group_xg90[j]
            norm_minutes[i] = group_minutes[j]

    normalized = []
    for i, p in enumerate(raw_players):
        normalized.append(
            {
                **p,
                "points_per_price": norm_ppp[i],
                "trend": norm_trend[i],
                "xg": norm_xg90[i],
                "minutes_played_ratio": norm_minutes[i],
                "is_injured_or_doubtful": is_injury_status(p.get("status")),
                "is_doubtful": is_doubtful_status(p.get("status")),
            }
        )
    return normalized


def evaluate_players(raw_players: list[dict], weights: dict = None) -> list[dict]:
    """
    Atajo: normaliza y puntúa en un solo paso (normalize_pool + rank_players).

    `weights`: config.EVALUATOR_WEIGHTS (default, para pujas) o
    config.LINEUP_EVALUATOR_WEIGHTS (para elegir alineación).
    """
    return rank_players(normalize_pool(raw_players), weights)
