"""
Tests de laliga_stats_client.py. Todo mockeado (sin red real) salvo
test_get_league_data_real_network, marcado @pytest.mark.network y
excluido por defecto (ver pytest.ini) -- confirma que el endpoint sigue
funcionando de verdad, pero no debe correr en cada `pytest` normal.
"""
from unittest.mock import MagicMock, patch

import pytest

from clients.laliga_stats_client import (
    current_season,
    get_league_data,
    get_league_data_with_fallback,
    index_players_by_name,
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


def _fake_get_league_data(players_by_season):
    def fake(league="La_liga", season=None):
        return {"players": players_by_season.get(season, []), "teams": {}, "dates": []}

    return fake


def test_get_league_data_with_fallback_uses_current_season_when_available():
    with patch("clients.laliga_stats_client.get_league_data", side_effect=_fake_get_league_data({"2025": [{"player_name": "X"}]})), \
         patch("clients.laliga_stats_client.current_season", return_value="2025"):
        data, season, used_fallback = get_league_data_with_fallback()
    assert season == "2025"
    assert used_fallback is False
    assert len(data["players"]) == 1


def test_get_league_data_with_fallback_falls_back_when_current_season_empty():
    """
    Regresión del caso real visto en producción (2026-08-16): la temporada
    recién empezada todavía no tiene datos en Understat -- debe caer a la
    anterior en vez de dejar el sync sin ninguna stat externa.
    """
    players_by_season = {"2026": [], "2025": [{"player_name": "Mbappe"}] * 600}
    with patch("clients.laliga_stats_client.get_league_data", side_effect=_fake_get_league_data(players_by_season)), \
         patch("clients.laliga_stats_client.current_season", return_value="2026"):
        data, season, used_fallback = get_league_data_with_fallback()
    assert season == "2025"
    assert used_fallback is True
    assert len(data["players"]) == 600


def test_get_league_data_with_fallback_no_data_anywhere():
    with patch("clients.laliga_stats_client.get_league_data", side_effect=_fake_get_league_data({})), \
         patch("clients.laliga_stats_client.current_season", return_value="2026"):
        data, season, used_fallback = get_league_data_with_fallback()
    assert data["players"] == []
    assert used_fallback is True  # sí intentó caer a la anterior, aunque tampoco tuviera datos


def test_index_players_by_name_normalizes_accents_and_case():
    league_data = {"players": [{"player_name": "Ángel Recio", "id": "1"}, {"player_name": "Kylian Mbappe-Lottin", "id": "2"}]}
    index = index_players_by_name(league_data)
    assert "angel recio" in index
    assert "kylian mbappe-lottin" in index


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
