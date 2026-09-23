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

import json

import config
from clients.futmondo_client import is_doubtful_status, is_injury_status


def weighted_recent_points(recent_points, season_average=None, decay: float = None) -> float | None:
    """
    Forma ponderada por recencia (ver config.EVALUATOR_RECENT_POINTS_DECAY):
    media de `recent_points` (`average.fitness` de Futmondo -- últimas
    jornadas del equipo, de la más antigua a la más reciente, 0 si no jugó;
    lista o JSON tal cual lo guarda `futmondo_snapshots.recent_points`) con
    peso `decay**k` para la jornada de hace k, más `season_average` como
    representante de las jornadas anteriores con el peso restante de la
    misma serie geométrica (`decay**n / (1 - decay)`).

    Devuelve None si no hay `recent_points` utilizables (el llamador cae a
    la media de temporada); si falta `season_average`, solo el array.
    """
    decay = config.EVALUATOR_RECENT_POINTS_DECAY if decay is None else decay
    if isinstance(recent_points, str):
        try:
            recent_points = json.loads(recent_points)
        except ValueError:
            return None
    values = [float(x) for x in (recent_points or []) if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if not values or not 0 < decay < 1:
        return None
    n = len(values)
    weights = [decay ** (n - 1 - i) for i in range(n)]  # la última (más reciente) pesa 1
    total = sum(w * v for w, v in zip(weights, values))
    weight_sum = sum(weights)
    if isinstance(season_average, (int, float)) and not isinstance(season_average, bool):
        tail_weight = decay ** n / (1 - decay)
        total += tail_weight * season_average
        weight_sum += tail_weight
    return total / weight_sum


def form_points(player: dict) -> float:
    """
    Puntos por jornada a usar en decisiones: forma ponderada
    (`weighted_recent_points`) si el jugador trae `recent_points`, si no su
    `average_points` de temporada (0 si tampoco).
    """
    form = weighted_recent_points(player.get("recent_points"), player.get("average_points"))
    if form is not None:
        return form
    return player.get("average_points") or 0


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
            "clean_sheet_rate": float,   # normalizado 0..1 dentro del pool -- solo se aplica si "position" es POR/DEF
            "position": str,             # POR | DEF | MED | DEL -- decide si "clean_sheet_rate" cuenta (ver más abajo)
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
      - "clean_sheet_rate": suma si `position` es "POR" o "DEF" (para
        cualquier otra posición no se aplica, sea cual sea su
        `clean_sheet_rate` -- ver docstring de `normalize_pool`). Futmondo
        da puntos extra por portería a cero casi siempre solo a estas dos
        posiciones (análisis a petición del usuario, 2026-09-11), así que
        aplicarlo también a MED/DEL premiaría a sus jugadores por algo que
        el juego no les puntúa a ellos.

    "xg" (a diferencia de las anteriores, no es opcional -- se aplica
    siempre que esté en `weights`) se excluye por completo para `position`
    "POR" (revisión 2026-09-21, ver config.EVALUATOR_WEIGHTS): Understat no
    traquea xG de porteros, así que su valor normalizado es siempre 0.5 (ver
    `normalize_pool`, rango 0 dentro del grupo) -- sumarlo no aporta ninguna
    información y solo distorsiona la comparación de score ENTRE posiciones
    (jobs/run_market.py compara el score de todas las posiciones en la
    misma escala). El resto de posiciones sí lo suman, con el xG/90
    encogido hacia 0 para minutos bajos -- ver
    `config.EVALUATOR_XG90_MIN_MINUTES_RATIO` y `normalize_pool`.

    Devuelve un score comparable entre jugadores (mayor = mejor). Con los
    pesos de EVALUATOR_WEIGHTS, el rango típico es aprox. [-0.20, 0.90] para
    MED/DEL (la suma de pesos positivos es 0.90, doubt_penalty resta hasta
    0.20 más), [-0.20, 0.65] para POR (sin "xg", con "clean_sheet_rate": 0.90
    - 0.40 + 0.15) y algo mayor para DEF, que suma ambas señales (0.90 +
    0.15 = 1.05).

    TODO: los pesos son un punto de partida razonado, no calibrado todavía
    contra resultados reales de la liga — ajustar con el tiempo en
    config.EVALUATOR_WEIGHTS/LINEUP_EVALUATOR_WEIGHTS según se vea qué
    correlaciona con puntos reales.
    """
    w = weights or config.EVALUATOR_WEIGHTS

    score = (
        w["futmondo_points_per_price"] * player_stats.get("points_per_price", 0)
        + w["futmondo_trend"] * player_stats.get("trend", 0)
        + w["minutes_played"] * player_stats.get("minutes_played_ratio", 0)
    )
    if player_stats.get("position") != "POR":
        score += w["xg"] * player_stats.get("xg", 0)

    if "clean_sheet_rate" in w and player_stats.get("position") in ("POR", "DEF"):
        score += w["clean_sheet_rate"] * player_stats.get("clean_sheet_rate", 0)

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
      - points_per_price: forma ponderada (`form_points()`, ver
        config.EVALUATOR_RECENT_POINTS_DECAY; media de temporada si no hay
        `recent_points`) / precio_en_millones. Puntos por jornada (no el
        total), para no penalizar a quien lleva menos jornadas jugadas por
        lesión/fichaje tardío, dando más peso a las jornadas recientes.
      - trend: forma ponderada - average_points (o last_points -
        average_points si no hay `recent_points`). Positivo si el
        rendimiento reciente es mejor que su media de temporada.
      - xg: xG por 90 minutos (xg / minutes_played * 90). Comparar xG total
        penalizaría a quien ha jugado menos minutos sin ser peor jugador.
        Con pocos minutos jugados, esa extrapolación a 90' es ruido -- un
        único remate en 5 minutos puede dar un xG/90 disparatado, muy por
        encima de cualquier titular real (confirmado con datos de
        producción, 2026-09-21: un jugador con 1 minuto jugado y 0.08 xG
        salía con xG/90=6.88, varias veces el de cualquier delantero con
        minutos reales, fijando el 1.0 normalizado de su grupo entero).
        Por debajo de un umbral se ENCOGE HACIA 0 (no hacia la media del
        grupo -- con pools pequeños, como el propio mercado de candidatos
        que evalúa jobs/run_market.py, esa media puede estar dominada por
        un único jugador con minutos reales, "contagiando" su tasa a un
        compañero de 0 minutos en vez de neutralizarlo), en proporción
        LINEAL a cuántos minutos reales respaldan el dato: con 0 minutos,
        factor 0 (mismo criterio que el resto de features sin dato); con
        el umbral o más, factor 1 (valor crudo intacto). El umbral NO es
        fijo -- escala con el calendario: `config.EVALUATOR_XG90_MIN_
        MINUTES_RATIO` (% de los minutos que el EQUIPO lleva disputados
        esta temporada, mismo `team_games` que minutes_played_ratio de
        abajo) por `team_games * 90` -- un número fijo de minutos no
        representa lo mismo en la jornada 6 que en la 20 (a petición del
        usuario, 2026-09-21). Aplicado ANTES del minmax por grupo de abajo.
      - minutes_played_ratio: mezcla (config.EVALUATOR_RECENT_MINUTES_WEIGHT)
        del ratio de las últimas jornadas del equipo -- (minutes_played -
        minutes_played_ref) / ((team_games - team_games_ref) * 90), con las
        referencias de `db.models.get_player_features()` -- y el de
        temporada descrito a continuación. Sin referencias (sin histórico
        local tan atrás), solo el de temporada.
        De temporada: minutes_played / (team_games * 90) cuando se
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
      - clean_sheet_rate: team_clean_sheets / team_games -- tasa de
        portería a cero del EQUIPO del jugador esta temporada (0 si no se
        conoce `team_games`, mismo criterio conservador que el resto de
        señales cuando falta el dato; ver
        `jobs.sync_data._team_clean_sheets_by_title()`). Es una señal de
        EQUIPO, no del jugador individual -- todos los jugadores de un
        mismo equipo comparten el mismo valor -- pero solo se aplica de
        verdad a POR/DEF en `score_player` (ver su docstring), que es a
        quienes Futmondo puntúa extra por portería a cero. Se normaliza
        aquí igual que el resto (por grupo de posición) para que
        `score_player` no tenga que normalizar nada por su cuenta.
    """
    n = len(raw_players)
    points_per_price = [0.0] * n
    trend = [0.0] * n
    xg90 = [0.0] * n
    minutes_ratio = [0.0] * n
    clean_sheet_rate = [0.0] * n

    xg90_min_minutes_ratio = config.EVALUATOR_XG90_MIN_MINUTES_RATIO

    for i, p in enumerate(raw_players):
        price = p.get("price") or 0
        avg_points = p.get("average_points") or 0
        recent_form = weighted_recent_points(p.get("recent_points"), p.get("average_points"))
        form = recent_form if recent_form is not None else avg_points
        points_per_price[i] = form / (price / 1_000_000) if price > 0 else 0

        if recent_form is not None:
            trend[i] = recent_form - avg_points
        else:
            last_points = p.get("last_points")
            trend[i] = (last_points if last_points is not None else avg_points) - avg_points

        minutes = p.get("minutes_played") or 0
        xg = p.get("xg") or 0
        xg90[i] = xg / minutes * 90 if minutes > 0 else 0

        team_games = p.get("team_games") or 0
        # Cuánto va de temporada, en minutos -- mismo dato que usa
        # minutes_ratio/clean_sheet_rate más abajo (team_games, partidos YA
        # JUGADOS por el EQUIPO) con el mismo fallback a los partidos del
        # propio jugador si Understat aún no publica el `history` del
        # equipo (ver docstring de arriba).
        season_games_so_far = team_games if team_games > 0 else (p.get("games") or 0)
        # Encoge xg90 hacia 0 (no hacia la media del grupo -- ver docstring
        # de arriba: con pools pequeños, como el propio mercado de
        # candidatos en jobs/run_market.py, la tasa media del grupo puede
        # estar dominada por un único jugador con minutos reales,
        # "contagiando" su xg90 a un compañero de 0 minutos en vez de
        # neutralizarlo) en proporción lineal a cuántos minutos reales
        # respaldan el dato, frente al % configurado (EVALUATOR_XG90_MIN_
        # MINUTES_RATIO) de los minutos que el EQUIPO lleva disputados esta
        # temporada -- un umbral que ESCALA con el calendario, no fijo (ver
        # docstring de config.EVALUATOR_XG90_MIN_MINUTES_RATIO: 270 minutos
        # fijos no representan lo mismo en la jornada 6 que en la 20). Sin
        # ninguna referencia de calendario (season_games_so_far=0, antes de
        # la primera jornada) no se puede juzgar nada -- se deja el valor
        # crudo intacto (en ese caso `minutes` también suele ser 0, así que
        # xg90 ya es 0 de por sí).
        if season_games_so_far > 0:
            min_minutes = xg90_min_minutes_ratio * season_games_so_far * 90
            credibility = min(1.0, minutes / min_minutes) if min_minutes > 0 else 1.0
            xg90[i] *= credibility

        if team_games > 0:
            minutes_ratio[i] = minutes / (team_games * 90)
            team_clean_sheets = p.get("team_clean_sheets") or 0
            clean_sheet_rate[i] = team_clean_sheets / team_games
        else:
            games = p.get("games") or 0
            minutes_ratio[i] = minutes / (games * 90) if games > 0 else 0

        # Minutos de las jornadas recientes (ver docstring), si hay
        # histórico local suficiente para este jugador.
        minutes_ref = p.get("minutes_played_ref")
        team_games_ref = p.get("team_games_ref")
        if minutes_ref is not None and team_games_ref is not None and team_games > team_games_ref:
            recent_ratio = (minutes - minutes_ref) / ((team_games - team_games_ref) * 90)
            recent_ratio = min(1.0, max(0.0, recent_ratio))
            w_recent = config.EVALUATOR_RECENT_MINUTES_WEIGHT
            minutes_ratio[i] = w_recent * recent_ratio + (1 - w_recent) * minutes_ratio[i]

    groups_idx: dict = {}
    for i, p in enumerate(raw_players):
        groups_idx.setdefault(p.get("position"), []).append(i)

    norm_ppp = [0.0] * n
    norm_trend = [0.0] * n
    norm_xg90 = [0.0] * n
    norm_minutes = [0.0] * n
    norm_clean_sheet = [0.0] * n

    for idxs in groups_idx.values():
        group_ppp = _minmax_normalize([points_per_price[i] for i in idxs])
        group_trend = _minmax_normalize([trend[i] for i in idxs])
        group_xg90 = _minmax_normalize([xg90[i] for i in idxs])
        group_minutes = _minmax_normalize([minutes_ratio[i] for i in idxs])
        group_clean_sheet = _minmax_normalize([clean_sheet_rate[i] for i in idxs])
        for j, i in enumerate(idxs):
            norm_ppp[i] = group_ppp[j]
            norm_trend[i] = group_trend[j]
            norm_xg90[i] = group_xg90[j]
            norm_minutes[i] = group_minutes[j]
            norm_clean_sheet[i] = group_clean_sheet[j]

    normalized = []
    for i, p in enumerate(raw_players):
        normalized.append(
            {
                **p,
                "points_per_price": norm_ppp[i],
                "trend": norm_trend[i],
                "xg": norm_xg90[i],
                "minutes_played_ratio": norm_minutes[i],
                "clean_sheet_rate": norm_clean_sheet[i],
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
