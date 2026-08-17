"""
Tests de clients/football_lineups_client.py (Fotmob). Todo mockeado (sin
red real) -- este cliente no tiene todavía un test @pytest.mark.network
como laliga_stats_client.py porque Fotmob es una API no oficial sin
contrato ni SLA (ver docstring del propio módulo): fijar el shape exacto
de su respuesta en un test que sí toque la red real correría el riesgo de
fallar por un cambio de la API ajeno a este proyecto, no por una regresión
real -- se prefiere confiar en las llamadas reales ya hechas a mano (ver
conversación 2026-08-17) para dar por buena la forma usada aquí.
"""
import json
from unittest.mock import patch

import clients.football_lineups_client as football_lineups_client
from clients.football_lineups_client import find_todays_match_for_team, real_starters_for_team


def test_find_todays_match_for_team_matches_home_or_away():
    matches = [
        {"id": 1, "home": {"id": 10, "name": "Athletic Club"}, "away": {"id": 20, "name": "Real Madrid"}},
        {"id": 2, "home": {"id": 30, "name": "Barcelona"}, "away": {"id": 40, "name": "Sevilla"}},
    ]
    assert find_todays_match_for_team("Real Madrid", matches)["id"] == 1
    assert find_todays_match_for_team("Barcelona", matches)["id"] == 2
    assert find_todays_match_for_team("Villarreal", matches) is None


def test_real_starters_for_team_returns_none_if_no_lineup():
    assert real_starters_for_team(None, team_id=1) is None


def test_real_starters_for_team_returns_none_while_predicted():
    """lineupType='predicted' es una estimación de Fotmob, NO la alineación real -- no debe tratarse como confirmada."""
    lineup = {
        "lineupType": "predicted",
        "homeTeam": {"id": 1, "name": "Athletic Club", "starters": [{"id": 10, "name": "Yeray"}]},
    }
    assert real_starters_for_team(lineup, team_id=1) is None


def test_real_starters_for_team_extracts_starters_when_standard():
    lineup = {
        "lineupType": "standard",
        "homeTeam": {"id": 1, "name": "Athletic Club", "starters": [
            {"id": 10, "name": "Unai Simón"},
            {"id": 11, "name": "Yeray"},
        ]},
        "awayTeam": {"id": 2, "name": "Real Madrid", "starters": [{"id": 20, "name": "Courtois"}]},
    }
    assert real_starters_for_team(lineup, team_id=1) == [
        {"player_name": "Unai Simón", "team_title": "Athletic Club"},
        {"player_name": "Yeray", "team_title": "Athletic Club"},
    ]
    assert real_starters_for_team(lineup, team_id=2) == [{"player_name": "Courtois", "team_title": "Real Madrid"}]
    assert real_starters_for_team(lineup, team_id=999) is None


def test_find_players_confirmed_out_marks_healthy_starter_missing_from_real_lineup(tmp_db):
    """
    Caso central: un jugador SANO (sin status de lesión) que Futmondo
    listaría como titular pero que el entrenador REAL no incluye en el
    once de hoy -- debe salir en el set devuelto.
    """
    players_by_id = {
        "p1": {"name": "Yeray", "team": "Athletic Club", "position": "DEF"},
        "p2": {"name": "Muniain", "team": "Athletic Club", "position": "MED"},
    }
    matches = [{"id": 555, "home": {"id": 1, "name": "Athletic Club"}, "away": {"id": 2, "name": "Real Madrid"}}]
    lineup = {
        "lineupType": "standard",
        "homeTeam": {"id": 1, "name": "Athletic Club", "starters": [{"id": 10, "name": "Yeray"}]},
        # Muniain NO aparece -- confirmado fuera del once real de hoy, sin estar lesionado.
    }
    with patch("clients.football_lineups_client.get_todays_laliga_matches", return_value=matches), \
         patch("clients.football_lineups_client.get_match_lineup", return_value=lineup):
        confirmed_out = football_lineups_client.find_players_confirmed_out_of_real_lineup(players_by_id, today="20260817")
    assert confirmed_out == {"p2"}


def test_find_players_confirmed_out_returns_empty_when_team_has_no_match_today(tmp_db):
    players_by_id = {"p1": {"name": "Yeray", "team": "Athletic Club", "position": "DEF"}}
    with patch("clients.football_lineups_client.get_todays_laliga_matches", return_value=[]):
        confirmed_out = football_lineups_client.find_players_confirmed_out_of_real_lineup(players_by_id, today="20260817")
    assert confirmed_out == set()


def test_find_players_confirmed_out_returns_empty_while_lineup_still_predicted(tmp_db):
    players_by_id = {"p1": {"name": "Yeray", "team": "Athletic Club", "position": "DEF"}}
    matches = [{"id": 555, "home": {"id": 1, "name": "Athletic Club"}, "away": {"id": 2, "name": "Real Madrid"}}]
    lineup = {"lineupType": "predicted", "homeTeam": {"id": 1, "name": "Athletic Club", "starters": []}}
    with patch("clients.football_lineups_client.get_todays_laliga_matches", return_value=matches), \
         patch("clients.football_lineups_client.get_match_lineup", return_value=lineup):
        confirmed_out = football_lineups_client.find_players_confirmed_out_of_real_lineup(players_by_id, today="20260817")
    assert confirmed_out == set()


def test_find_players_confirmed_out_uses_cache_second_time_without_hitting_lineup_endpoint(tmp_db):
    """
    Regresión del motivo de ser de real_lineup_checks: una vez publicada y
    cacheada la alineación real de un equipo para hoy, una segunda llamada
    el mismo día no debe volver a golpear /matchDetails.
    """
    players_by_id = {
        "p1": {"name": "Yeray", "team": "Athletic Club", "position": "DEF"},
        "p2": {"name": "Muniain", "team": "Athletic Club", "position": "MED"},
    }
    matches = [{"id": 555, "home": {"id": 1, "name": "Athletic Club"}, "away": {"id": 2, "name": "Real Madrid"}}]
    lineup = {
        "lineupType": "standard",
        "homeTeam": {"id": 1, "name": "Athletic Club", "starters": [{"id": 10, "name": "Yeray"}]},
    }
    with patch("clients.football_lineups_client.get_todays_laliga_matches", return_value=matches), \
         patch("clients.football_lineups_client.get_match_lineup", return_value=lineup) as mock_lineup:
        football_lineups_client.find_players_confirmed_out_of_real_lineup(players_by_id, today="20260817")
        confirmed_out_again = football_lineups_client.find_players_confirmed_out_of_real_lineup(players_by_id, today="20260817")
    assert confirmed_out_again == {"p2"}
    mock_lineup.assert_called_once()


def test_find_players_confirmed_out_caches_no_match_today_without_repeating_matches_call(tmp_db):
    """Si ya sabemos que un equipo no juega hoy, una segunda pasada no debe volver a pedir /matches."""
    players_by_id = {"p1": {"name": "Yeray", "team": "Athletic Club", "position": "DEF"}}
    with patch("clients.football_lineups_client.get_todays_laliga_matches", return_value=[]) as mock_matches:
        football_lineups_client.find_players_confirmed_out_of_real_lineup(players_by_id, today="20260817")
        football_lineups_client.find_players_confirmed_out_of_real_lineup(players_by_id, today="20260817")
    mock_matches.assert_called_once()


def test_find_players_confirmed_out_caches_by_yyyy_mm_dd_key(tmp_db):
    """La caché usa el formato YYYY-MM-DD (con guiones), consistente con el resto del proyecto, aunque `today` llegue como YYYYMMDD."""
    from db.models import get_real_lineup_check

    players_by_id = {"p1": {"name": "Yeray", "team": "Athletic Club", "position": "DEF"}}
    with patch("clients.football_lineups_client.get_todays_laliga_matches", return_value=[]):
        football_lineups_client.find_players_confirmed_out_of_real_lineup(players_by_id, today="20260817")
    assert get_real_lineup_check("Athletic Club", "2026-08-17") is not None
