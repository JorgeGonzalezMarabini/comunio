"""
Cliente de estadísticas externas (gratis, sin API de pago): Understat.

FBref quedó descartado (ver README): bloquea con un reto Cloudflare
("Just a moment...", 403) ante peticiones simples de `requests`, así que no
es viable desde un runner de GitHub Actions sin meter un navegador headless
completo — coste/complejidad que no compensa cuando Understat ya cubre casi
todo lo que FBref iba a aportar (minutos, goles, asistencias, tarjetas,
posición) además de xG/xA.

Understat expone un endpoint JSON real (no hace falta parsear HTML ni
`<script>` embebidos, a diferencia de lo asumido inicialmente): la propia
web lo usa vía AJAX para pintar la tabla de la liga.

    GET https://understat.com/getLeagueData/{league}/{season}
    -> {"teams": {...}, "players": [...], "dates": [...]}

Verificado el 2026-08-15 contra La_liga/2025 (600 jugadores, 20 equipos).
Nombres de liga válidos (los que acepta el desplegable de la web): "La_liga",
"EPL", "Bundesliga", "Serie_A", "Ligue_1", "RFPL".

Estado de lesión/duda: Understat NO lo tiene. La propia API de Futmondo
expone un campo `status` en cada jugador (aunque sin confirmar todavía qué
valores toma exactamente para lesión/sanción, ver
clients/futmondo_client.py:is_injury_status), así que ese dato sale de ahí
si acaba confirmándose, no de aquí — evita depender de una tercera fuente
para algo que en teoría ya tenemos.
"""
from __future__ import annotations

import time

import requests

UNDERSTAT_BASE_URL = "https://understat.com"

# Espaciado mínimo entre peticiones para no arriesgarse a un bloqueo por IP
# (Understat no ha dado problemas hasta ahora, pero mejor no abusar).
REQUEST_DELAY_SECONDS = 1.0

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; futmondo-liga-bot/1.0)",
    "X-Requested-With": "XMLHttpRequest",
}


def current_season() -> str:
    """
    Temporada de Understat vigente (año de inicio; La Liga corre
    agosto->mayo/junio, así que de enero a junio sigue siendo la temporada
    del año anterior).

    Nota: justo al arrancar una temporada nueva (agosto), Understat puede
    tardar unos días en publicar datos -> get_league_data devuelve
    `players: []`. Si pasa, el llamador debe manejarlo (loggear/notificar y
    reintentar más tarde), no asumir que siempre habrá datos.
    """
    from datetime import datetime

    now = datetime.now()
    return str(now.year if now.month >= 7 else now.year - 1)


def get_league_data(league: str = "La_liga", season: str = None) -> dict:
    """
    Devuelve {"teams": {...}, "players": [...], "dates": [...]} para toda la
    liga/temporada de una sola llamada (no hace falta ir jugador a jugador).

    `players[i]` (campos tal cual los devuelve Understat, todos strings salvo
    lo ya numérico): id, player_name, team_title, games, time (minutos),
    goals, npg (goles sin penalti), assists, xG, npxG, xA, xGChain,
    xGBuildup, shots, key_passes, yellow_cards, red_cards, position
    (código Understat: "F"/"M"/"D"/"GK" combinable, ej. "F M S").

    `teams[team_id]["history"]` es la lista de partidos de ese equipo con
    xG/xGA por partido — útil para estimar dificultad del rival.

    `dates` es el calendario de la liga con `forecast` (prob. w/d/l) por
    partido — la señal más directa de dificultad del próximo rival.
    """
    season = season or current_season()
    resp = requests.get(
        f"{UNDERSTAT_BASE_URL}/getLeagueData/{league}/{season}",
        headers=_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return resp.json()


def get_league_data_with_fallback(league: str = "La_liga", season: str = None) -> tuple[dict, str, bool]:
    """
    Como get_league_data(), pero si la temporada pedida (por defecto
    current_season()) todavía no tiene datos en Understat (normal las
    primeras jornadas de cada temporada nueva, confirmado en la práctica:
    0 jugadores para "2026" con la 2025/26 recién terminada dando 600),
    cae a la temporada anterior como aproximación temporal en vez de dejar
    a todos los jugadores sin stats externas durante ese hueco.

    Devuelve (league_data, season_usada, es_fallback). `season_usada` debe
    guardarse tal cual en external_stats.season (ver jobs/sync_data.py) para
    que quede claro en la BD que esos datos son de la temporada anterior,
    no inventados ni de la actual.
    """
    season = season or current_season()
    league_data = get_league_data(league, season)
    if league_data.get("players"):
        return league_data, season, False

    previous_season = str(int(season) - 1)
    fallback_data = get_league_data(league, previous_season)
    return fallback_data, previous_season, True


def _normalize(text: str) -> str:
    """Minúsculas, sin acentos/diacríticos, espacios repetidos colapsados."""
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def index_players_by_name(league_data: dict) -> dict:
    """
    Indexa `league_data["players"]` por NOMBRE COMPLETO normalizado
    (minúsculas, sin acentos). Cruce exacto simple, útil cuando la otra
    fuente también muestra el nombre completo — para el caso, muy
    frecuente en Futmondo, de mostrar solo el apellido ("Cairney" en vez de
    "Tom Cairney") esto solo no basta, ver build_player_index()/
    match_player() más abajo para el cruce robusto de verdad que usa
    jobs/sync_data.py.
    """
    return {_normalize(p["player_name"]): p for p in league_data.get("players", [])}


def build_player_index(league_data: dict) -> dict:
    """
    Índice de `league_data["players"]` pensado para match_player(): además
    del nombre completo, indexa por (equipo, apellido) y por apellido a
    secas, para poder cruzar con los nombres cortos que Futmondo usa a
    menudo para jugadores conocidos (ver docstring de match_player()).

    "Apellido" aquí es simplemente la última palabra del nombre completo
    normalizado — una aproximación razonable (falla con apellidos
    compuestos tipo "Van Dijk" o "De Jong", que quedarían como "dijk"/
    "jong"), pero es exactamente el mismo criterio que ya usa Futmondo para
    mostrar el nombre corto en esos casos en la práctica.
    """
    players = league_data.get("players", [])
    by_full_name: dict[str, dict] = {}
    by_team_and_surname: dict[tuple[str, str], list[dict]] = {}
    by_surname: dict[str, list[dict]] = {}

    for p in players:
        full_name = _normalize(p.get("player_name", ""))
        if not full_name:
            continue
        by_full_name.setdefault(full_name, p)

        surname = full_name.split(" ")[-1]
        team = _normalize(p.get("team_title", ""))
        by_team_and_surname.setdefault((team, surname), []).append(p)
        by_surname.setdefault(surname, []).append(p)

    return {"by_full_name": by_full_name, "by_team_and_surname": by_team_and_surname, "by_surname": by_surname, "all": players}


def match_player(name: str, team: str, index: dict, fuzzy_threshold: float = 0.75) -> tuple[dict | None, str]:
    """
    Busca el jugador de Understat que mejor corresponde a `name`/`team` (los
    que devuelve Futmondo — ver jobs/sync_data.py), probando estrategias de
    MÁS a MENOS fiable y parando en la primera que dé un resultado
    inequívoco. Nunca "adivina" si hay ambigüedad real: mejor dejar a un
    jugador sin cruzar (sus columnas de Understat quedan NULL esa sync) que
    cruzarlo mal y contaminar su score con las stats de otro jugador.

    Devuelve `(jugador_o_None, estrategia)` — la estrategia es solo para
    poder auditar/loggear de qué nivel de confianza salió cada cruce (ver
    el resumen de jobs/sync_data.py). Niveles, de más a menos fiable:

      1. "exact": el nombre de Futmondo, normalizado, coincide tal cual con
         el nombre completo de un jugador de Understat (Futmondo muestra el
         nombre completo, ej. "Robert Lewandowski").
      2. "surname+team": el nombre de Futmondo coincide con el APELLIDO
         (última palabra del nombre completo) de un jugador de Understat Y
         ambos juegan en el mismo equipo (nombre de equipo normalizado) —
         cubre el caso más habitual: Futmondo mostrando solo el apellido de
         un jugador conocido (ej. "Cairney" -> "Tom Cairney" del Fulham).
      3. "surname_unique": igual que el anterior pero sin poder confirmar
         equipo (los nombres de equipo de las dos fuentes no coinciden en
         texto, o Futmondo no trae equipo) — solo se acepta si ese apellido
         es único en TODA la liga, para no arriesgarse a mezclar a dos
         jugadores homónimos de equipos distintos.
      4. "fuzzy": similitud de texto (`difflib.SequenceMatcher`) contra los
         jugadores del MISMO equipo — aceptado solo si el mejor candidato
         supera `fuzzy_threshold` Y saca claramente más nota que el segundo
         mejor candidato (margen >= 0.15), para no "adivinar" entre dos
         apellidos parecidos del mismo equipo (ej. dos defensas con
         apellido similar). Pensado para variantes menores de transcripción
         (guiones, apóstrofes, orden nombre/apellido) que las estrategias
         anteriores no cubren.

    Si ninguna estrategia da un resultado inequívoco, devuelve
    `(None, "sin_match")`.
    """
    import difflib

    normalized_name = _normalize(name)
    normalized_team = _normalize(team or "")
    if not normalized_name:
        return None, "sin_nombre"

    exact = index["by_full_name"].get(normalized_name)
    if exact:
        return exact, "exact"

    same_team_same_surname = index["by_team_and_surname"].get((normalized_team, normalized_name), [])
    if len(same_team_same_surname) == 1:
        return same_team_same_surname[0], "surname+team"

    if not same_team_same_surname:
        same_surname_anywhere = index["by_surname"].get(normalized_name, [])
        if len(same_surname_anywhere) == 1:
            return same_surname_anywhere[0], "surname_unique"

    if normalized_team:
        same_team_players = [p for p in index["all"] if _normalize(p.get("team_title", "")) == normalized_team]
        if same_team_players:
            def ratio(p: dict) -> float:
                return difflib.SequenceMatcher(None, normalized_name, _normalize(p.get("player_name", ""))).ratio()

            scored = sorted(same_team_players, key=ratio, reverse=True)
            best_ratio = ratio(scored[0])
            runner_up_ratio = ratio(scored[1]) if len(scored) > 1 else 0.0
            if best_ratio >= fuzzy_threshold and (best_ratio - runner_up_ratio) >= 0.15:
                return scored[0], "fuzzy"

    return None, "sin_match"


def team_fixture_difficulty(league_data: dict, team_title: str, upcoming_only: bool = True) -> list[dict]:
    """
    Devuelve la lista de partidos de `team_title` con la probabilidad de
    derrota/empate/victoria (`forecast`) como proxy de dificultad del rival.

    Pensado para alimentar la "dificultad del rival" en
    engine/lineup_optimizer.py vía next_match_difficulty() (más abajo).
    """
    matches = [
        m
        for m in league_data.get("dates", [])
        if m["h"]["title"] == team_title or m["a"]["title"] == team_title
    ]
    if upcoming_only:
        matches = [m for m in matches if not m.get("isResult")]
    return matches


def next_match_difficulty(league_data: dict, team_title: str) -> float | None:
    """
    Dificultad del próximo partido de `team_title`, como probabilidad de NO
    ganar (empate + derrota) según el `forecast` de Understat: 0 = victoria
    segura, 1 = derrota segura.

    `forecast` viene SIEMPRE en perspectiva del equipo LOCAL (verificado
    contra resultados reales: forecast.w alto correlaciona con victoria
    local, forecast.l alto con derrota local) — hay que voltearlo si
    `team_title` juega fuera.

    Devuelve None si no hay próximo partido conocido en el calendario
    (temporada recién empezada sin fixtures cargados, o equipo ya sin
    partidos pendientes en los datos disponibles).
    """
    upcoming = team_fixture_difficulty(league_data, team_title, upcoming_only=True)
    if not upcoming:
        return None

    match = min(upcoming, key=lambda m: m["datetime"])
    forecast = match.get("forecast") or {}
    is_home = match["h"]["title"] == team_title
    win_prob = float(forecast.get("w", 0)) if is_home else float(forecast.get("l", 0))
    return 1 - win_prob
