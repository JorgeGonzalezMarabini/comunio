"""
Cliente de alineaciones REALES oficiales: API-Football (api-sports.io),
plan gratuito (100 peticiones/día).

Por qué esta fuente y no otra: decisión del usuario (2026-08-17, ver
conversación) tras valorar tres opciones -- SofaScore (API no oficial, sin
key, pero sin contrato documentado, con el precedente real en este mismo
proyecto de que una fuente sin documentar puede romperse sin aviso, ver
FBref/Cloudflare en clients/laliga_stats_client.py) y un mecanismo
puramente manual (coherente con que las sustituciones ya se gestionan a
mano, ver jobs/manage_substitutes.py, pero sin nada automático). Se eligió
API-Football por tener una API pública documentada y estable
(https://www.api-football.com/documentation-v3), a cambio de gestionar una
API key gratuita (el README asumía "coste 0: sin APIs de pago" -- esta key
es gratuita, no de pago, pero SÍ es una cuenta/rate limit nuevos a
mantener, aceptado explícitamente por el usuario).

**BLOQUEADO -- CONFIRMADO el 2026-08-17 con una API key real que el plan
GRATUITO no sirve para este caso de uso**: `GET /teams` y `GET /fixtures`
con `season` = temporada en curso (2026) devuelven `results: 0` y
`"errors": {"plan": "Free plans do not have access to this season, try
from 2022 to 2024."}` -- el plan gratuito de API-Football solo da acceso a
temporadas HISTÓRICAS (2022-2024), no a la temporada en curso. Sin acceso a
la temporada en curso, `/fixtures` nunca encuentra el partido de hoy y
`/fixtures/lineups` nunca tiene nada que consultar -- **este cliente no
puede funcionar en producción con un plan gratuito**, no es un límite de
volumen (100 peticiones/día) sino un bloqueo total de acceso a los datos
que hacen falta.

Confirmado también, con `season=2023` (dentro del rango permitido): el
league id de LaLiga (config.API_FOOTBALL_LALIGA_LEAGUE_ID) SÍ es 140 --
`GET /teams?league=140&season=2023` devolvió los 20 equipos reales de esa
temporada (incluido Barcelona, id 529) sin error.

TODO real antes de poder usar esto en producción -- decisión pendiente del
usuario, ver conversación 2026-08-17 tras este hallazgo:
  1. Pasar a un plan de pago de API-Football (Pro, ~$19/mes según su
     página de precios en el momento de este hallazgo, con acceso a la
     temporada en curso y 7500 peticiones/día) -- el resto del diseño de
     este módulo (caché, cascada de nombres) sigue siendo válido tal cual,
     solo falta la key de pago.
  2. Cambiar de fuente (ej. SofaScore, descartado inicialmente por no tener
     una API documentada -- ver conversación 2026-08-17 sobre las opciones
     valoradas).
  3. Volver al mecanismo manual (descartado inicialmente por el mismo
     motivo).

Con el plan gratuito activado tal cual (`ENABLE_REAL_LINEUP_CHECK=true`
pero sin plan de pago), `find_players_confirmed_out_of_real_lineup()` no
lanza (el error de plan queda dentro de una respuesta 200 válida, `_get()`
no lo detecta como fallo HTTP) pero tampoco encuentra nunca ningún partido
-- se comporta como si ningún equipo jugara nunca, sin avisar de que en
realidad es un problema de plan. Ver TODO en find_players_confirmed_out_of_real_lineup()
más abajo.

Puntos menores todavía sin confirmar (secundarios al bloqueo de arriba):
  - Que GET /fixtures/lineups devuelva `response: []` de verdad hasta que
    la alineación se hace pública (según la documentación de API-Football,
    normalmente ~1h antes del partido) y no algún otro shape para
    "pendiente" -- no se ha podido probar contra un fixture de la
    temporada en curso.
  - Consumo real de peticiones/día una vez haya un plan que sí dé acceso a
    la temporada en curso -- ver find_players_confirmed_out_of_real_lineup()
    para el diseño de caché pensado para no agotarlo, sin medir todavía.

Endpoints usados (documentación v3, acceso directo sin RapidAPI):

    GET {API_FOOTBALL_BASE_URL}/teams
        ?league={league_id}&season={season}
        headers: {"x-apisports-key": API_FOOTBALL_KEY}
        -> {"response": [{"team": {"id": ..., "name": ...}, "venue": {...}}, ...]}
        TODA la liga en una sola llamada -- por eso se cachea en memoria del
        proceso (ver get_team_ids()), los ids no cambian durante la temporada.

    GET {API_FOOTBALL_BASE_URL}/fixtures
        ?team={team_id}&date={YYYY-MM-DD}
        -> {"response": [{"fixture": {"id": ..., "date": ..., "timestamp": ...,
                                        "status": {"short": ...}},
                           "teams": {"home": {...}, "away": {...}}}, ...]}
        Lista vacía si ese equipo no juega ese día.

    GET {API_FOOTBALL_BASE_URL}/fixtures/lineups
        ?fixture={fixture_id}
        -> {"response": [{"team": {"id": ..., "name": ...}, "formation": ...,
                           "startXI": [{"player": {"id": ..., "name": ...,
                                                     "number": ..., "pos": ...}}],
                           "substitutes": [...], "coach": {...}}, ...]}
        `response` vacío ([]) hasta que la alineación se confirma
        (normalmente ~1h antes del partido, según la propia documentación de
        API-Football) -- se interpreta como "todavía no hay alineación real
        publicada", NO como error.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

import requests

import config
from clients.laliga_stats_client import build_player_index, match_player

# Cache en memoria del PROCESO (no persiste entre ejecuciones del job, a
# diferencia de real_lineup_checks en BD): los team ids no cambian durante
# la temporada, así que basta con no repetir la llamada dentro de una misma
# ejecución de jobs/manage_substitutes.py.
_team_ids_cache: dict[str, int] | None = None


class ApiFootballPlanError(Exception):
    """
    La API devolvió HTTP 200 con el campo `errors` relleno -- API-Football
    usa esto para errores de PLAN (ej. "Free plans do not have access to
    this season"), no solo `response.raise_for_status()` como el resto de
    fallos HTTP. CONFIRMADO en vivo el 2026-08-17: con el plan gratuito,
    GET /teams y GET /fixtures con la temporada en curso devuelven
    `results: 0` y este error dentro de una respuesta 200 -- sin esta
    comprobación, _get() lo trataría como "sin datos" (p.ej. "ese equipo no
    juega hoy") en vez de como el fallo de plan que realmente es, y
    find_players_confirmed_out_of_real_lineup() se quedaría sin sustituir a
    nadie SIEMPRE, sin avisar nunca de la causa real. Ver docstring del
    módulo para el hallazgo completo y las opciones para resolverlo.
    """


def _normalize(text: str | None) -> str:
    """Minúsculas, sin acentos/diacríticos -- mismo criterio que clients/laliga_stats_client.py."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def _headers() -> dict:
    return {"x-apisports-key": config.API_FOOTBALL_KEY}


def _get(path: str, params: dict) -> dict:
    response = requests.get(f"{config.API_FOOTBALL_BASE_URL}{path}", headers=_headers(), params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    errors = data.get("errors")
    # `errors` viene como dict {"campo": "mensaje"} cuando hay error, lista
    # vacía [] cuando no lo hay -- ambas formas vistas en respuestas reales.
    if errors:
        raise ApiFootballPlanError(f"{path} devolvió errors: {errors}")
    return data


def get_team_ids(season: str) -> dict[str, int]:
    """
    -> {nombre_equipo_normalizado: team_id_api_football} para TODA
    LaLiga (config.API_FOOTBALL_LALIGA_LEAGUE_ID), de una sola llamada a
    GET /teams -- se cachea en memoria del proceso (_team_ids_cache) porque
    los ids no cambian durante la temporada, y repetirla consumiría
    presupuesto igual que cualquier otra contra el límite de 100/día.

    TODO (optimización no hecha todavía, presupuesto ajustado): esta caché
    es solo de PROCESO -- jobs/manage_substitutes.py corre como proceso
    nuevo en cada tick de cron (ver manage_substitutes.yml, 12 veces/día de
    viernes a lunes), así que hoy esto gasta 1 petición/ejecución igualmente
    (12/día solo para esto). Persistirla en BD (como real_lineup_checks, ver
    db/models.py) la reduciría a 1 petición para toda la temporada -- se dejó
    así por simplicidad de una primera versión, sin API key todavía con la
    que medir si de verdad hace falta.
    """
    global _team_ids_cache
    if _team_ids_cache is not None:
        return _team_ids_cache
    data = _get("/teams", {"league": config.API_FOOTBALL_LALIGA_LEAGUE_ID, "season": season})
    _team_ids_cache = {_normalize(item["team"]["name"]): item["team"]["id"] for item in data.get("response", [])}
    return _team_ids_cache


def get_todays_fixture(team_id: int, date: str = None) -> dict | None:
    """
    -> el fixture (dict crudo de la API) de `team_id` en `date` (YYYY-MM-DD,
    UTC; por defecto hoy), o None si ese equipo no tiene partido ese día.
    """
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _get("/fixtures", {"team": team_id, "date": date})
    fixtures = data.get("response", [])
    return fixtures[0] if fixtures else None


def get_lineup_response(fixture_id: int) -> list[dict]:
    """
    -> `response` crudo de GET /fixtures/lineups?fixture=... -- lista VACÍA
    si la alineación real todavía no se ha publicado (normal hasta ~1h antes
    del partido según la documentación de API-Football), no un error.
    """
    data = _get("/fixtures/lineups", {"fixture": fixture_id})
    return data.get("response", [])


def real_starters_for_team(lineup_response: list[dict], team_id: int) -> list[dict] | None:
    """
    Extrae, de `lineup_response` (ver get_lineup_response()), el startXI real
    de `team_id`, reformado a la forma que esperan build_player_index()/
    match_player() de clients.laliga_stats_client (reutilizados tal cual
    para no duplicar la cascada de cruce de nombres ya confirmada con
    Understat -- ver su docstring): {"player_name": ..., "team_title": ...}.

    Devuelve None si `team_id` no aparece en `lineup_response` -- indica que
    la alineación real de ESE equipo todavía no está publicada (el caso más
    normal es que `lineup_response` esté vacío del todo, ver
    get_lineup_response(), pero se comprueba por equipo por si la API
    publicara un lado antes que el otro).
    """
    for team_entry in lineup_response:
        if team_entry.get("team", {}).get("id") == team_id:
            team_name = team_entry["team"].get("name", "")
            return [{"player_name": p["player"]["name"], "team_title": team_name} for p in team_entry.get("startXI", [])]
    return None


def find_players_confirmed_out_of_real_lineup(players_by_id: dict, season: str, today: str = None) -> set[str]:
    """
    Para cada equipo presente en `players_by_id` (normalmente solo los
    equipos de nuestros titulares ACTUALES, ver jobs/manage_substitutes.py --
    no hace falta consultar toda la plantilla), determina si ese equipo
    juega hoy y, si la alineación real ya se publicó, compara el once real
    contra nuestros jugadores de ese equipo -- devuelve el conjunto de ids
    (Futmondo, nuestros) de quienes NO aparecen en el once real.

    Esto es DELIBERADAMENTE independiente de is_injury_status(): cubre
    rotación/decisión táctica del entrenador REAL (el caso más frecuente en
    la práctica), no solo lesión/duda -- ver README, sección "TODO sin
    resolver -- 'no juega' es más amplio que 'lesionado'".

    Cachea en `real_lineup_checks` (ver db.models.get_real_lineup_check()/
    save_real_lineup_check()) por (equipo, `today`) para no repetir llamadas
    dentro del mismo día -- imprescindible dado el límite de 100
    peticiones/día del plan gratuito: sin esta caché, cada pasada de
    manage_substitutes.py (varias veces al día, ver manage_substitutes.yml)
    volvería a gastar presupuesto por cada equipo, agotándolo mucho antes de
    que la alineación real se publique de verdad (normalmente ~1h antes del
    partido).

    `players_by_id`: {id: {..., "team", "position"}} -- solo se usan
    "team"/"position"/"name" de cada jugador.
    `today` (opcional, YYYY-MM-DD UTC): para poder testear con una fecha
    fija: por defecto, hoy.

    No recibe una conexión de BD: cada acceso a `real_lineup_checks` abre la
    suya propia vía db.models.get_connection(), igual que el resto de
    funciones de db/models.py (ver p.ej. get_open_bids()/update_bid_status()).
    """
    from db.models import get_real_lineup_check, save_real_lineup_check

    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    teams = {p["team"] for p in players_by_id.values() if p.get("team")}
    if not teams:
        return set()

    team_ids = get_team_ids(season)
    confirmed_out: set[str] = set()

    for team in teams:
        cached = get_real_lineup_check(team, today)
        if cached is not None and cached["lineup_published"]:
            # Ya se resolvió hoy para este equipo -- reutiliza sin gastar presupuesto.
            import json

            real_starter_ids = set(json.loads(cached["starting_player_ids"] or "[]"))
            team_player_ids = {pid for pid, p in players_by_id.items() if p.get("team") == team}
            confirmed_out |= team_player_ids - real_starter_ids
            continue
        if cached is not None and cached["fixture_id"] is None:
            continue  # ya sabemos que este equipo no juega hoy -- no repetir la llamada

        team_id = team_ids.get(_normalize(team))
        if team_id is None:
            continue  # nombre de equipo sin cruzar contra API-Football esta pasada -- no adivinar

        fixture = get_todays_fixture(team_id) if cached is None else None
        if fixture is None and cached is None:
            save_real_lineup_check(team, today, None, False, [], now_iso)
            continue

        fixture_id = fixture["fixture"]["id"] if fixture else cached["fixture_id"]
        lineup_response = get_lineup_response(fixture_id)
        real_starters = real_starters_for_team(lineup_response, team_id)
        if real_starters is None:
            save_real_lineup_check(team, today, fixture_id, False, [], now_iso)
            continue  # alineación real todavía no publicada -- reintentar en una pasada posterior

        index = build_player_index({"players": real_starters})
        team_players = {pid: p for pid, p in players_by_id.items() if p.get("team") == team}
        real_starter_ids = set()
        for pid, p in team_players.items():
            match, _strategy = match_player(p["name"], team, index, position=p.get("position"))
            if match is not None:
                real_starter_ids.add(pid)

        save_real_lineup_check(team, today, fixture_id, True, sorted(real_starter_ids), now_iso)
        confirmed_out |= set(team_players) - real_starter_ids

    return confirmed_out
