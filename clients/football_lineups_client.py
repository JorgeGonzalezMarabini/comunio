"""
Cliente de alineaciones REALES oficiales: Fotmob (www.fotmob.com), API NO
OFICIAL pero gratuita y SIN key/cuenta.

## Por qué Fotmob y no otra (las cuatro fuentes probadas en vivo el mismo día, 2026-08-17)

Objetivo: detectar cuándo un titular SANO (sin lesión/duda según Futmondo)
no está en el once REAL de su equipo hoy -- rotación/decisión táctica del
entrenador real, el motivo más frecuente en la práctica de que un titular
no puntúe, mucho más que la lesión (ver README, "Banquillo/suplentes").
Se probaron cuatro fuentes gratuitas, en este orden, cada una descartada
por un motivo real y distinto antes de llegar a Fotmob:

1. **API-Football (plan gratuito)** -- BLOQUEADO: confirmado con una API
   key real que `GET /teams` y `GET /fixtures` con la temporada en curso
   (2026) devuelven `"errors": {"plan": "Free plans do not have access to
   this season, try from 2022 to 2024."}` -- el plan gratuito solo da
   temporadas históricas, nunca la actual. El resto del diseño (league id
   140 confirmado, endpoints correctos) era válido; el bloqueo es
   exclusivamente de plan.
2. **SofaScore** -- BLOQUEADO más duro todavía: `api.sofascore.com` Y
   `www.sofascore.com` (la web entera, no solo la "API") devuelven 403 en
   TODO, incluida la ruta interna que usa el propio frontend
   (`www.sofascore.com/api/v1/...`, mismo origen). Confirmado que es una
   huella de conexión (TLS/HTTP), no un simple check de cabeceras: la
   MISMA URL exacta responde 200 desde un navegador Chrome real (probado
   con Claude in Chrome) y 403 desde curl/`requests` con cualquier
   combinación de User-Agent/Referer/cookies de sesión. Automatizarlo
   exigiría un navegador headless camuflado para pasar por humano ante un
   sistema anti-bot que activamente intenta impedirlo -- un bypass
   deliberado que este proyecto no construye, con independencia de que el
   uso final sea benigno.
3. **ESPN** (`site.api.espn.com`) -- BLOQUEADO igual (403 Akamai), ni
   siquiera se llegó a mirar si tenía alineaciones.
4. **TheSportsDB** (key de prueba pública `"3"`) -- responde SIN bloqueo,
   pero la calidad del dato es mala: la alineación de un partido real ya
   jugado (Espanyol 3-0 Levante, 2026-08-16) vino con jugadores de OTROS
   equipos/temporadas mezclados (`strTeam: "Lille"`, `"Hellas Verona"`,
   `"_Retired Soccer"` en un partido de LaLiga) -- descartada por
   integridad de datos, no por acceso: actuar sobre esto sería peor que no
   tener el dato (podría "confirmar fuera" a un titular que sí juega).

**Fotmob es la única de las cuatro que combina acceso sin bloqueo Y datos
correctos**, ambas cosas confirmadas con llamadas reales el 2026-08-17:

    GET https://www.fotmob.com/api/data/matches?date=YYYYMMDD
        -> 200 sin ninguna protección anti-bot (probado con curl + un
           User-Agent de navegador, sin key ni cookies de sesión).
        -> {"leagues": [{"id": 87, "name": "LaLiga", "ccode": "ESP",
                          "matches": [{"id": ..., "home": {"id","name"},
                                       "away": {...}, "status": {...}}]}]}
        UN día trae TODAS las ligas de golpe -- se filtra aquí por
        `config.FOTMOB_LALIGA_LEAGUE_ID` (87, CONFIRMADO: el id 87 devolvió
        partidos reales de LaLiga, ej. Deportivo A Coruña vs Elche
        2026-08-17 19:00 UTC, coincide con lo visto en paralelo en
        SofaScore vía navegador).

    GET https://www.fotmob.com/api/data/matchDetails?matchId=...
        -> content.lineup: {"lineupType": "predicted" | "standard",
            "source": "enetpulse",
            "homeTeam": {"id", "name", "starters": [{"id","name",...}]},
            "awayTeam": {...}}
        `lineupType`:
          - "predicted": estimación propia de Fotmob ANTES de la
            alineación real -- NO sirve como señal de "confirmado fuera",
            es una suposición, no un hecho.
          - "standard": alineación REAL -- CONFIRMADO comparando contra un
            partido ya jugado (Espanyol 3-0 Levante, 2026-08-16):
            `lineupType` era "standard" y los titulares listados
            (Dmitrovic, El Hilali, Riedel, Núñez... por Espanyol; Ryan,
            Nacho Pérez, Mandi... por Levante) son correctos, sin mezclar
            jugadores de otros equipos -- justo lo que falló en
            TheSportsDB.

## TODO sin confirmar todavía

  - **El momento exacto** en que `lineupType` pasa de "predicted" a
    "standard" ANTES del pitido inicial. Documentado en otras fuentes como
    ~1h antes del partido (API-Football, ver commit anterior), pero no
    observado en vivo aquí: el partido más cercano al pitido que se probó
    esta sesión seguía en "predicted" con 2h20min por delante. No es
    bloqueante -- `find_players_confirmed_out_of_real_lineup()` simplemente
    reintenta en cada pasada (ver caché) hasta que deja de ver "predicted",
    igual que se diseñó para el "response: []" de API-Football.
  - **Estabilidad a medio plazo**: es una API NO oficial y no documentada,
    igual que SofaScore -- sin contrato ni SLA, podría cambiar de forma o
    empezar a bloquear sin aviso en cualquier momento. A diferencia de
    SofaScore, HOY responde sin protección anti-bot -- si eso cambiara,
    `jobs/manage_substitutes.py` lo notifica como cualquier otro fallo de
    esta fuente (no tumba la comprobación por lesión), pero no hay forma de
    saber de antemano si va a seguir así.
  - Sin key ni cuenta, tampoco hay un límite de peticiones/día publicado
    que gestionar (a diferencia de API-Football) -- se mantiene igualmente
    la misma caché de `real_lineup_checks` por precaución y para no abusar
    de un servicio gratuito de terceros sin necesidad.
"""
from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone

import requests

import config
from clients.laliga_stats_client import build_player_index, match_player

# Fotmob no exige ninguna cabecera especial (confirmado en vivo: un
# User-Agent de navegador normal basta, sin key ni cookies de sesión) --
# a diferencia de SofaScore, que bloquea la MISMA petición pese a cabeceras
# idénticas (ver docstring del módulo). Se manda igualmente un UA de
# navegador real por si acaso, no por necesidad confirmada.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}


def _normalize(text: str | None) -> str:
    """Minúsculas, sin acentos/diacríticos -- mismo criterio que clients/laliga_stats_client.py."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def _get(path: str, params: dict) -> dict:
    response = requests.get(f"{config.FOTMOB_BASE_URL}{path}", headers=_HEADERS, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def get_todays_laliga_matches(date: str = None) -> list[dict]:
    """
    -> lista de partidos de LaLiga (config.FOTMOB_LALIGA_LEAGUE_ID) para
    `date` (YYYYMMDD, UTC; por defecto hoy), forma cruda de Fotmob
    ({"id", "home": {"id","name"}, "away": {...}, "status": {...}}).

    UNA sola llamada trae TODAS las ligas del día -- se filtra aquí, no en
    la petición (Fotmob no ofrece filtrar por liga en este endpoint).
    Lista vacía si LaLiga no tiene partidos ese día.
    """
    date = date or datetime.now(timezone.utc).strftime("%Y%m%d")
    data = _get("/matches", {"date": date})
    for league in data.get("leagues", []):
        if league.get("id") == config.FOTMOB_LALIGA_LEAGUE_ID:
            return league.get("matches", [])
    return []


def find_todays_match_for_team(team_name: str, matches: list[dict]) -> dict | None:
    """Busca en `matches` (ver get_todays_laliga_matches()) el partido de `team_name` -- None si no juega hoy."""
    normalized = _normalize(team_name)
    for m in matches:
        if _normalize(m["home"]["name"]) == normalized or _normalize(m["away"]["name"]) == normalized:
            return m
    return None


def get_match_lineup(match_id: int) -> dict | None:
    """-> `content.lineup` crudo de GET /matchDetails, o None si esa sección todavía no viene en la respuesta."""
    data = _get("/matchDetails", {"matchId": match_id})
    return data.get("content", {}).get("lineup")


def real_starters_for_team(lineup: dict | None, team_id: int) -> list[dict] | None:
    """
    Extrae el once REAL de `team_id` de `lineup` (ver get_match_lineup()),
    reformado a la forma que esperan build_player_index()/match_player() de
    clients.laliga_stats_client (reutilizados tal cual, misma cascada de
    nombres ya confirmada con Understat): {"player_name", "team_title"}.

    Devuelve None si `lineup` es None, o si `lineupType` todavía es
    "predicted" (estimación de Fotmob, NO la alineación real -- ver
    docstring del módulo) -- en cualquiera de los dos casos, "todavía no
    hay alineación real que consultar", igual de tratamiento.
    """
    if not lineup or lineup.get("lineupType") != "standard":
        return None
    for side in ("homeTeam", "awayTeam"):
        team = lineup.get(side) or {}
        if team.get("id") == team_id:
            team_name = team.get("name", "")
            return [{"player_name": p["name"], "team_title": team_name} for p in team.get("starters", [])]
    return None


def find_players_confirmed_out_of_real_lineup(players_by_id: dict, today: str = None) -> set[str]:
    """
    Para cada equipo presente en `players_by_id` (normalmente solo los
    equipos de nuestros titulares ACTUALES, ver jobs/manage_substitutes.py),
    determina si ese equipo juega hoy y, si la alineación real ya se
    publicó (`lineupType == "standard"`, ver real_starters_for_team()),
    compara el once real contra nuestros jugadores de ese equipo --
    devuelve el conjunto de ids (Futmondo, nuestros) de quienes NO
    aparecen en el once real.

    Esto es DELIBERADAMENTE independiente de is_injury_status(): cubre
    rotación/decisión táctica del entrenador REAL (el caso más frecuente en
    la práctica), no solo lesión/duda -- ver README.

    Cachea en `real_lineup_checks` (ver db.models.get_real_lineup_check()/
    save_real_lineup_check(), MISMA tabla que se diseñó para API-Football --
    el shape no cambia, "fixture_id" ahora guarda el matchId de Fotmob) por
    (equipo, `today`) para no repetir llamadas dentro del mismo día -- sin
    límite de peticiones/día publicado por Fotmob (a diferencia de
    API-Football), pero por precaución de no abusar de un servicio
    gratuito de terceros sin necesidad.

    `players_by_id`: {id: {..., "team", "position", "name"}}.
    `today` (opcional, YYYYMMDD UTC): para poder testear con una fecha
    fija; por defecto, hoy. Se guarda en caché como YYYY-MM-DD (con
    guiones) para que la clave sea consistente con el resto del proyecto
    (ver db.models.real_lineup_checks.match_date).
    """
    from db.models import get_real_lineup_check, save_real_lineup_check

    today = today or datetime.now(timezone.utc).strftime("%Y%m%d")
    cache_date = f"{today[:4]}-{today[4:6]}-{today[6:]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    teams = {p["team"] for p in players_by_id.values() if p.get("team")}
    if not teams:
        return set()

    confirmed_out: set[str] = set()
    matches = None  # perezoso: solo se pide si algún equipo no tiene ya la caché de hoy resuelta

    for team in teams:
        cached = get_real_lineup_check(team, cache_date)
        if cached is not None and cached["lineup_published"]:
            real_starter_ids = set(json.loads(cached["starting_player_ids"] or "[]"))
            team_player_ids = {pid for pid, p in players_by_id.items() if p.get("team") == team}
            confirmed_out |= team_player_ids - real_starter_ids
            continue
        if cached is not None and cached["fixture_id"] is None:
            continue  # ya sabemos que este equipo no juega hoy -- no repetir la llamada

        if matches is None:
            matches = get_todays_laliga_matches(today)

        match = find_todays_match_for_team(team, matches)
        if match is None:
            if cached is None:
                save_real_lineup_check(team, cache_date, None, False, [], now_iso)
            continue

        match_id = match["id"]
        team_id = match["home"]["id"] if _normalize(match["home"]["name"]) == _normalize(team) else match["away"]["id"]

        lineup = get_match_lineup(match_id)
        real_starters = real_starters_for_team(lineup, team_id)
        if real_starters is None:
            save_real_lineup_check(team, cache_date, match_id, False, [], now_iso)
            continue  # alineación real todavía no publicada ("predicted") -- reintentar en una pasada posterior

        index = build_player_index({"players": real_starters})
        team_players = {pid: p for pid, p in players_by_id.items() if p.get("team") == team}
        real_starter_ids = set()
        for pid, p in team_players.items():
            match_result, _strategy = match_player(p["name"], team, index, position=p.get("position"))
            if match_result is not None:
                real_starter_ids.add(pid)

        save_real_lineup_check(team, cache_date, match_id, True, sorted(real_starter_ids), now_iso)
        confirmed_out |= set(team_players) - real_starter_ids

    return confirmed_out
