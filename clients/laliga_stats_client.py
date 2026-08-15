"""
Cliente de estadísticas externas (gratis, vía scraping): Understat + FBref.

- Understat: xG embebido en un <script> como JSON dentro del HTML.
- FBref: tablas HTML (pandas.read_html / BeautifulSoup) con minutos jugados,
  disciplina, etc.

Riesgos asumidos: fragilidad ante cambios de HTML y necesidad de espaciar
peticiones para no acabar bloqueados por IP (rate limiting propio, no solo
buenas prácticas).
"""
from __future__ import annotations

import json
import re
import time

import requests
from bs4 import BeautifulSoup

UNDERSTAT_BASE_URL = "https://understat.com"
FBREF_BASE_URL = "https://fbref.com"

# Espaciado mínimo entre peticiones a la misma fuente para evitar bloqueos.
REQUEST_DELAY_SECONDS = 2.0


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return resp


def get_player_xg(understat_player_id: str) -> dict:
    """
    Extrae el JSON embebido de xG de la ficha de un jugador en Understat.

    TODO: validar el nombre exacto de la variable JS que contiene el JSON
    (suele ser algo tipo `var playersData = JSON.parse('...')`).
    """
    url = f"{UNDERSTAT_BASE_URL}/player/{understat_player_id}"
    html = _get(url).text
    soup = BeautifulSoup(html, "lxml")

    match = re.search(r"JSON\.parse\('(.+?)'\)", html)
    if not match:
        raise ValueError(f"No se encontró el bloque JSON esperado en {url}")

    raw = match.group(1).encode().decode("unicode_escape")
    return json.loads(raw)


def get_fbref_player_stats(fbref_player_url: str) -> dict:
    """
    Lee las tablas de estadísticas de un jugador en FBref (minutos, tarjetas...).

    TODO: identificar qué tabla(s) concretas hacen falta (standard stats,
    playing time, etc.) una vez se defina el esquema de db/models.py.
    """
    import pandas as pd

    tables = pd.read_html(fbref_player_url)
    raise NotImplementedError(
        "Pendiente: mapear qué tabla(s) de FBref usar y qué columnas extraer."
    )


def get_injury_status(player_name: str) -> str | None:
    """
    Estado de lesión/duda de un jugador.

    TODO: decidir fuente (FBref no siempre lo tiene claro; valorar fuente
    adicional gratuita específica de lesiones de LaLiga).
    """
    raise NotImplementedError("Pendiente: definir fuente de datos de lesiones.")
