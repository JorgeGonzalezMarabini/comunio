"""
Tests de clients/football_lineups_client.py. Todo mockeado (sin red real,
sin API key) -- este cliente no tiene todavía un test @pytest.mark.network
como laliga_stats_client.py porque no hay una API_FOOTBALL_KEY real con la
que probarlo en vivo (ver TODO en el docstring del propio módulo).
"""
from unittest.mock import patch

import config
import clients.football_lineups_client as football_lineups_client
from clients.football_lineups_client import real_starters_for_team


def setup_function(_):
    # get_team_ids() cachea en un global de módulo -- limpiar entre tests
    # para que uno no contamine al siguiente.
    football_lineups_client._team_ids_cache = None


def test_get_team_ids_indexes_by_normalized_name():
    fake_response = {"response": [
        {"team": {"id": 1, "name": "Athletic Club"}},
        {"team": {"id": 2, "name": "Atlético Madrid"}},
    ]}
    with patch("clients.football_lineups_client._get", return_value=fake_response) as mock_get:
        ids = football_lineups_client.get_team_ids("2025")
        assert ids == {"athletic club": 1, "atletico madrid": 2}
        # Segunda llamada no debe repetir la petición (cache en memoria del proceso).
        football_lineups_client.get_team_ids("2025")
        mock_get.assert_called_once()


def test_get_todays_fixture_returns_none_when_no_match_today():
    with patch("clients.football_lineups_client._get", return_value={"response": []}):
        assert football_lineups_client.get_todays_fixture(1, date="2026-08-17") is None


def test_get_todays_fixture_returns_first_match():
    fake_response = {"response": [{"fixture": {"id": 999}}]}
    with patch("clients.football_lineups_client._get", return_value=fake_response):
        fixture = football_lineups_client.get_todays_fixture(1, date="2026-08-17")
        assert fixture["fixture"]["id"] == 999


def test_real_starters_for_team_returns_none_if_lineup_not_published():
    """response=[] (o sin el equipo pedido) -- alineación real todavía no publicada, no un error."""
    assert real_starters_for_team([], team_id=1) is None


def test_real_starters_for_team_extracts_start_xi_for_the_right_team():
    lineup_response = [
        {"team": {"id": 1, "name": "Athletic Club"}, "startXI": [
            {"player": {"id": 10, "name": "Unai Simón", "number": 1, "pos": "G"}},
            {"player": {"id": 11, "name": "Yeray", "number": 4, "pos": "D"}},
        ]},
        {"team": {"id": 2, "name": "Real Madrid"}, "startXI": [{"player": {"id": 20, "name": "Courtois"}}]},
    ]
    starters = real_starters_for_team(lineup_response, team_id=1)
    assert starters == [
        {"player_name": "Unai Simón", "team_title": "Athletic Club"},
        {"player_name": "Yeray", "team_title": "Athletic Club"},
    ]


def test_find_players_confirmed_out_marks_healthy_starter_missing_from_real_lineup(tmp_db):
    """
    Caso central de la funcionalidad: un jugador SANO (sin status de
    lesión) que Futmondo listaría como titular pero que el entrenador REAL
    no incluye en el once de hoy -- debe salir en el set devuelto.
    """
    players_by_id = {
        "p1": {"name": "Yeray", "team": "Athletic Club", "position": "DEF"},
        "p2": {"name": "Muniain", "team": "Athletic Club", "position": "MED"},
    }
    lineup_response = [
        {"team": {"id": 1, "name": "Athletic Club"}, "startXI": [
            {"player": {"id": 10, "name": "Yeray"}},
            # Muniain NO aparece -- confirmado fuera del once real de hoy, sin estar lesionado.
        ]},
    ]
    with patch("clients.football_lineups_client.get_team_ids", return_value={"athletic club": 1}), \
         patch("clients.football_lineups_client.get_todays_fixture", return_value={"fixture": {"id": 555}}), \
         patch("clients.football_lineups_client.get_lineup_response", return_value=lineup_response):
        confirmed_out = football_lineups_client.find_players_confirmed_out_of_real_lineup(
            players_by_id, season="2025", today="2026-08-17"
        )
    assert confirmed_out == {"p2"}


def test_find_players_confirmed_out_returns_empty_when_team_has_no_match_today(tmp_db):
    players_by_id = {"p1": {"name": "Yeray", "team": "Athletic Club", "position": "DEF"}}
    with patch("clients.football_lineups_client.get_team_ids", return_value={"athletic club": 1}), \
         patch("clients.football_lineups_client.get_todays_fixture", return_value=None):
        confirmed_out = football_lineups_client.find_players_confirmed_out_of_real_lineup(
            players_by_id, season="2025", today="2026-08-17"
        )
    assert confirmed_out == set()


def test_find_players_confirmed_out_uses_cache_second_time_without_hitting_lineups_endpoint(tmp_db):
    """
    Regresión del motivo de ser de real_lineup_checks: una vez publicada y
    cacheada la alineación real de un equipo para hoy, una segunda llamada
    el mismo día no debe volver a golpear /fixtures/lineups (presupuesto de
    100/día del plan gratuito).
    """
    players_by_id = {
        "p1": {"name": "Yeray", "team": "Athletic Club", "position": "DEF"},
        "p2": {"name": "Muniain", "team": "Athletic Club", "position": "MED"},
    }
    lineup_response = [
        {"team": {"id": 1, "name": "Athletic Club"}, "startXI": [{"player": {"id": 10, "name": "Yeray"}}]},
    ]
    with patch("clients.football_lineups_client.get_team_ids", return_value={"athletic club": 1}), \
         patch("clients.football_lineups_client.get_todays_fixture", return_value={"fixture": {"id": 555}}), \
         patch("clients.football_lineups_client.get_lineup_response", return_value=lineup_response) as mock_lineup:
        football_lineups_client.find_players_confirmed_out_of_real_lineup(players_by_id, season="2025", today="2026-08-17")
        confirmed_out_again = football_lineups_client.find_players_confirmed_out_of_real_lineup(
            players_by_id, season="2025", today="2026-08-17"
        )
    assert confirmed_out_again == {"p2"}
    mock_lineup.assert_called_once()
