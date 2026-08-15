"""
Job: actualiza stats externas (Understat) + estado de Comunio en la base de
datos. Pensado para correr por cron (GitHub Actions) con más frecuencia que
los jobs de mercado/alineación, ya que alimenta a ambos.

Mapeo Comunio -> BD confirmado por fetch autenticado real 2026-08-15 (ver
clients/comunio_client.py para el detalle completo de cada campo). Lo único
que sigue pendiente de esa fuente es dónde vienen community_id/user_id en
la respuesta de login (de momento configurables a mano).
"""
from datetime import datetime, timezone

from clients.comunio_client import ComunioClient, COMUNIO_POSITION_MAP
from clients.laliga_stats_client import get_league_data_with_fallback, index_players_by_name
from db.models import init_db, get_connection
from notifier import notify


def _parse_int(value) -> int | None:
    """Comunio devuelve "-" (string) cuando todavía no hay dato (p.ej.
    puntos en pretemporada) en vez de omitir el campo o dar 0."""
    if value is None or value == "-":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value) -> float | None:
    if value is None or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _upsert_player_and_snapshot(conn, player: dict, now: str, on_market: bool = None) -> None:
    """
    `player` es un item tal cual lo devuelve Comunio, ya sea de squad
    (campos "quotedprice"/"recommendedprice" en minúsculas) o de market
    (campos "quotedPrice"/"recommendedPrice", camelCase distinto — ver nota
    en comunio_client.py). Se leen ambas variantes para no depender de cuál
    endpoint vino el jugador.

    `on_market`: si se pasa explícito, manda sobre `player.get("onMarket")`.
    Necesario porque los jugadores que vienen de get_market() (el listado
    de fichajes) NO traen ese campo en su JSON — "onMarket" solo existe en
    los items de get_squad(), para marcar si un jugador PROPIO está puesto
    en venta. Sin este parámetro, todo jugador de mercado se guardaba con
    on_market=0 (bug real detectado: run_market() nunca encontraba
    candidatos pese a haber jugadores sincronizados).
    """
    player_id = str(player["id"])
    position = COMUNIO_POSITION_MAP.get(player.get("position"), player.get("position"))
    club = player.get("club") or {}

    conn.execute(
        """
        INSERT INTO players (id, name, team, position, understat_id, updated_at)
        VALUES (?, ?, ?, ?, NULL, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, team=excluded.team, position=excluded.position, updated_at=excluded.updated_at
        """,
        (player_id, player.get("name"), club.get("name"), position, now),
    )

    price = player.get("quotedprice", player.get("quotedPrice"))
    recommended_price = player.get("recommendedprice", player.get("recommendedPrice"))

    conn.execute(
        """
        INSERT INTO comunio_snapshots
            (player_id, price, recommended_price, points, last_points, average_points,
             on_market, status, status_info, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            player_id,
            price,
            recommended_price,
            _parse_int(player.get("points")),
            _parse_int(player.get("lastPoints")),
            _parse_float(player.get("averagePoints")),
            1 if (on_market if on_market is not None else player.get("onMarket")) else 0,
            player.get("status"),
            player.get("statusInfo") or None,
            now,
        ),
    )


def _upsert_external_stats(conn, player_id: str, understat_player: dict, season: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO external_stats
            (player_id, season, games, minutes_played, goals, non_penalty_goals, assists,
             xg, non_penalty_xg, xa, xg_chain, xg_buildup, yellow_cards, red_cards,
             understat_position, source, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'understat', ?)
        """,
        (
            player_id,
            season,
            _parse_int(understat_player.get("games")),
            _parse_int(understat_player.get("time")),
            _parse_int(understat_player.get("goals")),
            _parse_int(understat_player.get("npg")),
            _parse_int(understat_player.get("assists")),
            _parse_float(understat_player.get("xG")),
            _parse_float(understat_player.get("npxG")),
            _parse_float(understat_player.get("xA")),
            _parse_float(understat_player.get("xGChain")),
            _parse_float(understat_player.get("xGBuildup")),
            _parse_int(understat_player.get("yellow_cards")),
            _parse_int(understat_player.get("red_cards")),
            understat_player.get("position"),
            now,
        ),
    )


def _normalize_name(name: str) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def run():
    init_db()

    client = ComunioClient()
    client.login()

    squad = client.get_squad()
    market = client.get_market()

    league_data, season, used_fallback = get_league_data_with_fallback()
    if not league_data.get("players"):
        # Ni la temporada actual ni la anterior tienen datos -- caso
        # extremo, no visto en la práctica, pero no debe romper el sync.
        notify("sync_data: Understat sin datos ni para la temporada actual ni la anterior, se reintentará en el próximo sync.")
        understat_by_name = {}
    else:
        if used_fallback:
            notify(
                f"sync_data: Understat sin datos todavía para la temporada actual, "
                f"usando temporada {season} (la anterior) como aproximación temporal."
            )
        understat_by_name = index_players_by_name(league_data)

    now = datetime.now(timezone.utc).isoformat()

    squad_players = squad.get("items", [])
    market_players = [item["_embedded"]["player"] for item in market.get("items", []) if item.get("_embedded")]

    matched = 0
    with get_connection() as conn:
        for player in squad_players:
            _upsert_player_and_snapshot(conn, player, now)  # on_market: usa el propio player["onMarket"]

            understat_player = understat_by_name.get(_normalize_name(player.get("name", "")))
            if understat_player:
                _upsert_external_stats(conn, str(player["id"]), understat_player, season, now)
                matched += 1

        for player in market_players:
            _upsert_player_and_snapshot(conn, player, now, on_market=True)  # siempre True: viene del listado de mercado

            understat_player = understat_by_name.get(_normalize_name(player.get("name", "")))
            if understat_player:
                _upsert_external_stats(conn, str(player["id"]), understat_player, season, now)
                matched += 1

    total = len(squad_players) + len(market_players)
    notify(
        f"sync_data: {len(squad_players)} en plantilla, {len(market_players)} en mercado, "
        f"{matched}/{total} cruzados con Understat."
    )


if __name__ == "__main__":
    run()
