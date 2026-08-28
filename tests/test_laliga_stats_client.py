"""
Tests de laliga_stats_client.py. Todo mockeado (sin red real) salvo
test_get_league_data_real_network, marcado @pytest.mark.network y
excluido por defecto (ver pytest.ini) -- confirma que el endpoint sigue
funcionando de verdad, pero no debe correr en cada `pytest` normal.
"""
from unittest.mock import MagicMock, patch

import pytest

from clients.laliga_stats_client import (
    build_player_index,
    current_season,
    get_league_data,
    get_league_data_with_fallback,
    index_players_by_name,
    match_player,
    next_match_difficulty,
    team_fixture_difficulty,
)


def test_current_season_before_july_is_previous_year():
    # current_season() hace "from datetime import datetime" DENTRO de la
    # función -- hay que parchear la clase global, no un nombre de módulo.
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.year = 2026
        mock_dt.now.return_value.month = 3
        assert current_season() == "2025"


def test_current_season_from_july_is_current_year():
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.year = 2026
        mock_dt.now.return_value.month = 8
        assert current_season() == "2026"


def _fake_get_league_data(players_by_season, teams_by_season=None):
    """
    `teams_by_season`, si se pasa, fija explícitamente el registro de
    equipos conocidos de esa temporada (simula el listado de equipos que
    Understat ya tiene aunque aún no les haya procesado ningún partido).
    Si no se pasa para una temporada dada, se derivan los equipos de sus
    propios jugadores -- suficiente para los tests que no necesitan
    distinguir "equipo sin datos" de "equipo inexistente".
    """
    def fake(league="La_liga", season=None):
        players = players_by_season.get(season, [])
        teams = (teams_by_season or {}).get(season)
        if teams is None:
            teams = {p["team_title"]: {"title": p["team_title"]} for p in players if "team_title" in p}
        return {"players": players, "teams": teams, "dates": []}

    return fake


def test_get_league_data_with_fallback_uses_current_season_when_available():
    with patch(
        "clients.laliga_stats_client.get_league_data",
        side_effect=_fake_get_league_data({"2025": [{"player_name": "X", "team_title": "Equipo"}]}),
    ), patch("clients.laliga_stats_client.current_season", return_value="2025"):
        data, season, fallback_by_team = get_league_data_with_fallback()
    assert season == "2025"
    assert fallback_by_team == {}
    assert len(data["players"]) == 1


def test_get_league_data_with_fallback_falls_back_when_current_season_empty():
    """
    Regresión del caso real visto en producción (2026-08-16): la temporada
    recién empezada todavía no tiene datos en Understat -- debe caer a la
    anterior en vez de dejar el sync sin ninguna stat externa. Aquí ni
    siquiera hay listado de equipos para la temporada actual (fallback
    total, no se puede diferenciar por equipo).
    """
    players_by_season = {"2026": [], "2025": [{"player_name": "Mbappe", "team_title": "Equipo"}] * 600}
    with patch(
        "clients.laliga_stats_client.get_league_data",
        side_effect=_fake_get_league_data(players_by_season, teams_by_season={"2026": {}}),
    ), patch("clients.laliga_stats_client.current_season", return_value="2026"):
        data, season, fallback_by_team = get_league_data_with_fallback()
    assert season == "2026"  # la temporada real sigue siendo la actual, no la de fallback
    assert fallback_by_team == {"Equipo": "2025"}
    assert len(data["players"]) == 600
    assert all(p["_source_season"] == "2025" for p in data["players"])


def test_get_league_data_with_fallback_fills_missing_teams_only():
    """
    Caso real detectado en producción (2026-08-17, jornada 1 de LaLiga):
    la temporada actual ya tiene datos para algunos equipos pero otros
    todavía no han sido procesados por Understat -- solo esos equipos
    deben completarse con la temporada anterior, sin tocar los que ya
    están al día (y sin colar datos viejos de un equipo que ya tiene
    datos frescos).
    """
    current_players = [{"player_name": "Jugador Sevilla", "team_title": "Sevilla"}]
    previous_players = [
        {"player_name": "Jugador Athletic", "team_title": "Athletic Club"},
        {"player_name": "Jugador Sevilla Viejo", "team_title": "Sevilla"},  # equipo ya al día -- no debe colarse
    ]
    teams_by_season = {"2026": {"1": {"title": "Sevilla"}, "2": {"title": "Athletic Club"}}}
    with patch(
        "clients.laliga_stats_client.get_league_data",
        side_effect=_fake_get_league_data(
            {"2026": current_players, "2025": previous_players}, teams_by_season=teams_by_season
        ),
    ), patch("clients.laliga_stats_client.current_season", return_value="2026"):
        data, season, fallback_by_team = get_league_data_with_fallback()

    assert season == "2026"
    assert fallback_by_team == {"Athletic Club": "2025"}
    names = {p["player_name"] for p in data["players"]}
    assert names == {"Jugador Sevilla", "Jugador Athletic"}  # NO "Jugador Sevilla Viejo"

    sevilla_player = next(p for p in data["players"] if p["team_title"] == "Sevilla")
    assert sevilla_player["_source_season"] == "2026"
    athletic_player = next(p for p in data["players"] if p["team_title"] == "Athletic Club")
    assert athletic_player["_source_season"] == "2025"


def test_get_league_data_with_fallback_no_data_anywhere():
    with patch("clients.laliga_stats_client.get_league_data", side_effect=_fake_get_league_data({})), \
         patch("clients.laliga_stats_client.current_season", return_value="2026"):
        data, season, fallback_by_team = get_league_data_with_fallback()
    assert data["players"] == []
    assert fallback_by_team == {}  # intentó caer a la anterior, pero tampoco tenía nada que ofrecer


def test_index_players_by_name_normalizes_accents_and_case():
    league_data = {"players": [{"player_name": "Ángel Recio", "id": "1"}, {"player_name": "Kylian Mbappe-Lottin", "id": "2"}]}
    index = index_players_by_name(league_data)
    assert "angel recio" in index
    assert "kylian mbappe-lottin" in index


# --- match_player(): cruce robusto Futmondo <-> Understat ---
#
# Futmondo muestra a menudo solo el apellido de un jugador conocido
# ("Cairney" en vez de "Tom Cairney") -- index_players_by_name() por sí
# solo (cruce exacto de nombre completo) no basta para ese caso, que es el
# más habitual en la práctica (ver captura real documentada en el README).

_LEAGUE_DATA = {
    "players": [
        {"player_name": "Tom Cairney", "team_title": "Fulham", "id": "u1", "position": "M"},
        {"player_name": "Sergio Ramos", "team_title": "Sevilla", "id": "u2", "position": "D"},
        {"player_name": "Diego Garcia", "team_title": "Betis", "id": "u3"},  # apellido "Garcia" repetido en otro equipo
        {"player_name": "Luis Garcia", "team_title": "Alaves", "id": "u4"},  # para forzar ambigüedad de apellido sin equipo
        {"player_name": "Kylian Mbappe-Lottin", "team_title": "Real Madrid", "id": "u5", "position": "F"},
        {"player_name": "Marc Bola", "team_title": "Watford", "id": "u6", "position": "D"},  # apellido único en la liga, para probar el filtro de posición
        {"player_name": "Iker Solano", "team_title": "Getafe", "id": "u7"},  # apellido único, sin "position" -- para probar que el filtro no bloquea sin dato
        {"player_name": "Robin Le Normand", "team_title": "Atletico Madrid", "id": "u8", "position": "D"},  # apellido compuesto, dos palabras
        {"player_name": "Alex Baena", "team_title": "Atletico Madrid", "id": "u9", "position": "S"},  # posición SOLO "S" (suplente, sin F/M/D/GK) -- ver test de _position_is_compatible
        {"player_name": "Trent Alexander-Arnold", "team_title": "Real Madrid", "id": "u10", "position": "D"},  # apodo = nombre de pila, no apellido
        {"player_name": "Trent Otro", "team_title": "Sevilla", "id": "u11"},  # mismo nombre de pila "Trent" en otro equipo -- para forzar ambigüedad sin equipo
        {"player_name": "Vinicius Junior", "team_title": "Real Madrid", "id": "u12", "position": "F"},  # nombre de pila único en la liga
    ]
}


def test_match_player_exact_full_name():
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Sergio Ramos", "Sevilla", index)
    assert strategy == "exact"
    assert player["id"] == "u2"


def test_match_player_surname_with_matching_team():
    """Caso más habitual: Futmondo muestra solo el apellido de un jugador conocido."""
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Cairney", "Fulham", index)
    assert strategy == "surname+team"
    assert player["id"] == "u1"


def test_match_player_surname_unique_when_team_names_dont_align():
    """
    Si el nombre de equipo de Futmondo no coincide en texto con el de
    Understat (p.ej. abreviaturas distintas), pero el apellido es único en
    toda la liga, se acepta igualmente -- sin equipo con el que
    desambiguar, un apellido único en toda la liga sigue siendo una señal
    fiable.
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Cairney", "Fulham FC", index)  # nombre de equipo no coincide
    assert strategy == "surname_unique"
    assert player["id"] == "u1"


def test_match_player_ambiguous_surname_without_team_match_gives_up():
    """
    Dos jugadores distintos comparten apellido ("Garcia") en equipos
    distintos -- sin poder confirmar equipo, no hay forma fiable de saber
    cuál es. Mejor no cruzar que cruzar mal.
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Garcia", "Equipo Desconocido", index)
    assert player is None
    assert strategy == "sin_match"


def test_match_player_fuzzy_catches_minor_spelling_variation():
    """
    Variante menor de transcripción (sin guion) del mismo jugador, en el
    mismo equipo -- ninguna estrategia exacta la coge, pero la similitud de
    texto sí, sin ambigüedad real (es el único jugador de ese equipo).
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Kylian Mbappe Lottin", "Real Madrid", index)
    assert strategy == "fuzzy"
    assert player["id"] == "u5"


def test_match_player_surname_unique_accepts_when_position_compatible():
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Bola", "Watford FC", index, position="DEF")  # Understat: "D" -> DEF
    assert strategy == "surname_unique"
    assert player["id"] == "u6"


def test_match_player_surname_unique_rejects_when_position_conflicts():
    """
    Apellido único en la liga, pero Futmondo lo da como delantero y
    Understat lo tiene fichado como defensa -- demasiada discrepancia para
    fiarse sin equipo con el que confirmar. Mejor no cruzar.
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Bola", "Watford FC", index, position="DEL")
    assert player is None
    assert strategy == "sin_match"


def test_match_player_fuzzy_accepts_when_position_compatible():
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Kylian Mbappe Lottin", "Real Madrid", index, position="DEL")  # Understat: "F" -> DEL
    assert strategy == "fuzzy"
    assert player["id"] == "u5"


def test_match_player_fuzzy_rejects_when_position_conflicts():
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Kylian Mbappe Lottin", "Real Madrid", index, position="POR")
    assert player is None
    assert strategy == "sin_match"


def test_match_player_compound_surname_matches_via_last_word():
    """
    Caso real detectado en producción (2026-08-17): Futmondo muestra el
    apellido compuesto completo ("Le Normand"), pero build_player_index()
    solo indexa por la ÚLTIMA palabra del nombre completo de Understat
    ("normand") -- sin el reintento por última palabra, este jugador nunca
    cruzaría aunque sí esté en el índice. Aquí el nombre de equipo tampoco
    coincide en texto ("Atleti", abreviatura coloquial no cubierta por
    _TEAM_ALIASES, vs "Atletico Madrid"), así que debe resolverse por
    "surname_unique", no por "surname+team".
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Le Normand", "Atleti", index, position="DEF")
    assert strategy == "surname_unique"
    assert player["id"] == "u8"


def test_match_player_team_alias_normalizes_known_name_variants():
    """
    Caso real detectado en producción (temporada 2026-27, jornada 2):
    Futmondo y Understat nombran a "Atlético de Madrid" de forma distinta
    ("Atlético de Madrid" vs "Atletico Madrid") -- sin canonicalizar ambos
    lados vía _normalize_team()/_TEAM_ALIASES, "surname+team" nunca
    disparaba para ninguno de los 6 equipos afectados (ver
    _TEAM_ALIASES), degradando el cruce a "surname_unique" (o a
    "sin_match" si el apellido no era único en toda la liga).
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Baena", "Atlético de Madrid", index, position="MED")
    assert strategy == "surname+team"
    assert player["id"] == "u9"


def test_match_player_surname_unique_accepts_unrecognized_position_code():
    """
    Caso real detectado en producción (temporada 2026-27, jornada 2):
    Understat a veces solo etiqueta a un jugador con "S" (suplente, sin
    F/M/D/GK) en sus primeras jornadas con minutos -- ninguno de esos
    tokens está en _UNDERSTAT_POSITION_TO_SHORT. Antes del fix,
    `_position_is_compatible` convertía eso en `{None}` y rechazaba
    SIEMPRE, aunque el docstring diga que sin dato de posición no debe
    bloquear el match. Aquí, sin equipo con el que confirmar (nombre de
    equipo sin alias), debe seguir resolviéndose por "surname_unique".
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Baena", "Atleti", index, position="MED")
    assert strategy == "surname_unique"
    assert player["id"] == "u9"


def test_match_player_firstname_with_matching_team():
    """
    Caso real (jugadores conocidos por su nombre de pila, no su apellido):
    "Trent" no coincide como apellido con nada ("alexander-arnold" es la
    última palabra de "Trent Alexander-Arnold"), pero sí como NOMBRE DE
    PILA de un jugador del mismo equipo -- ambigüedad resuelta por equipo,
    como "surname+team" pero para nombre de pila.
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Trent", "Real Madrid", index)
    assert strategy == "firstname+team"
    assert player["id"] == "u10"


def test_match_player_firstname_ambiguous_without_team_gives_up():
    """
    Dos jugadores distintos se llaman "Trent" (equipos distintos) -- sin
    poder confirmar equipo, no hay forma fiable de saber cuál es. Mejor no
    cruzar que cruzar mal (mismo criterio que con apellidos ambiguos).
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Trent", "Equipo Desconocido", index)
    assert player is None
    assert strategy == "sin_match"


def test_match_player_firstname_unique_when_team_names_dont_align():
    """
    "Vinícius" no es apellido de nadie ("junior" es la última palabra de
    "Vinicius Junior"), pero como nombre de pila es único en toda la liga
    -- se acepta igual que "surname_unique" acepta un apellido único
    cuando el equipo no se puede confirmar.
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Vinícius", "Real Madrid FC", index, position="DEL")  # nombre de equipo no coincide
    assert strategy == "firstname_unique"
    assert player["id"] == "u12"


def test_match_player_firstname_unique_rejects_when_position_conflicts():
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Vinícius", "Real Madrid FC", index, position="DEF")
    assert player is None
    assert strategy == "sin_match"


def test_match_player_firstname_not_tried_for_multi_word_name():
    """
    Si Futmondo ya muestra varias palabras, no tiene sentido tratar la
    primera como apodo -- ya se probó como nombre completo y como apellido
    (compuesto incluido). "Trent Desconocido" no es ningún jugador real.
    """
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Trent Desconocido", "Real Madrid", index)
    assert player is None
    assert strategy == "sin_match"


def test_match_player_position_missing_on_understat_side_does_not_block():
    """Si Understat no trae "position" para ese jugador, el filtro no debe bloquear el match -- no hay con qué comparar."""
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Solano", "Getafe FC", index, position="DEL")  # nombre de equipo no coincide -> surname_unique
    assert strategy == "surname_unique"
    assert player["id"] == "u7"


def test_match_player_no_match_returns_none():
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("Jugador Inexistente", "Equipo Inexistente", index)
    assert player is None
    assert strategy == "sin_match"


def test_match_player_empty_name_does_not_crash():
    index = build_player_index(_LEAGUE_DATA)
    player, strategy = match_player("", "Fulham", index)
    assert player is None
    assert strategy == "sin_nombre"


def test_team_fixture_difficulty_filters_by_team_and_upcoming():
    league_data = {
        "dates": [
            {"h": {"title": "Girona"}, "a": {"title": "Rayo Vallecano"}, "isResult": True, "datetime": "2025-08-15 17:00:00", "forecast": {}},
            {"h": {"title": "Girona"}, "a": {"title": "Barcelona"}, "isResult": False, "datetime": "2026-08-20 17:00:00", "forecast": {}},
            {"h": {"title": "Sevilla"}, "a": {"title": "Betis"}, "isResult": False, "datetime": "2026-08-21 17:00:00", "forecast": {}},
        ]
    }
    upcoming = team_fixture_difficulty(league_data, "Girona", upcoming_only=True)
    assert len(upcoming) == 1
    assert upcoming[0]["a"]["title"] == "Barcelona"

    all_matches = team_fixture_difficulty(league_data, "Girona", upcoming_only=False)
    assert len(all_matches) == 2


def test_next_match_difficulty_home_team_perspective():
    """
    Confirmado contra resultados reales (2026-08-15): `forecast` siempre
    está en perspectiva del equipo LOCAL. Si Girona juega en casa con
    forecast.w bajo, su dificultad (prob. de NO ganar) debe ser alta.
    """
    league_data = {
        "dates": [
            {"h": {"title": "Girona"}, "a": {"title": "Rayo Vallecano"}, "isResult": False, "datetime": "2026-08-20 17:00:00",
             "forecast": {"w": "0.012", "d": "0.0571", "l": "0.9309"}},
        ]
    }
    difficulty_local = next_match_difficulty(league_data, "Girona")
    assert difficulty_local == pytest.approx(1 - 0.012)

    # El mismo partido visto desde el rival visitante: su dificultad usa forecast.l (prob. de que gane el local)
    difficulty_visitante = next_match_difficulty(league_data, "Rayo Vallecano")
    assert difficulty_visitante == pytest.approx(1 - 0.9309)


def test_next_match_difficulty_returns_none_without_upcoming_matches():
    assert next_match_difficulty({"dates": []}, "Girona") is None


@pytest.mark.network
def test_get_league_data_real_network():
    """Confirma que el endpoint real de Understat sigue respondiendo -- requiere red, excluido por defecto."""
    data = get_league_data(season="2025")
    assert len(data["players"]) > 0
    assert "teams" in data
