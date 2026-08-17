"""
Job: actualiza stats externas (Understat) + estado de Futmondo en la base
de datos. Pensado para correr por cron (GitHub Actions) con más frecuencia
que los jobs de mercado/alineación, ya que alimenta a ambos.

Mapeo Futmondo -> BD confirmado por fetch autenticado real 2026-08-17 (ver
clients/futmondo_client.py para el detalle completo de cada campo).

También reconcilia el estado de nuestras propias pujas (tabla `bids`): nada
más las marca -- ninguna otra parte del bot actualiza 'placed' a 'won'/
'lost' después de colocarlas (ver README, es solo un registro de
auditoría, no la fuente de verdad de si una puja se ganó o no). A
diferencia de Comunio, Futmondo no expone un endpoint con la lista de
pujas propias pendientes (ver clients/futmondo_client.py), así que la
reconciliación aquí es puramente por pertenencia: si el jugador apareció
en la plantilla, la puja se da por ganada; si ya no está ni en plantilla
ni en el mercado, se da por perdida (aunque en realidad pudiera ser que el
propio manager rival ganó, o que expiró sin comprador, no se puede
distinguir con los datos disponibles).
"""
from datetime import datetime, timezone

from clients.futmondo_client import FutmondoClient, FUTMONDO_POSITION_MAP
from clients.laliga_stats_client import get_league_data_with_fallback, index_players_by_name
from db.models import init_db, get_connection, get_open_bids, update_bid_status, get_open_sales, update_sale_status
from notifier import notify


def _reconcile_sales(roster_player_ids: set) -> int:
    """
    Para cada venta que seguimos creyendo listada (status='listed'),
    comprueba si el jugador ya no está en la plantilla — si no está, es
    que alguien completó la compra, se marca 'sold'. No hay forma de
    distinguir con los datos de la API una venta real de una retirada
    manual del mercado sin vender.

    Devuelve cuántas se han marcado 'sold' (para la notificación).
    """
    sold = 0
    for sale in get_open_sales():
        if sale["player_id"] in roster_player_ids:
            continue  # sigue en la plantilla, la venta sigue listada
        update_sale_status(sale["id"], "sold")
        sold += 1
    return sold


def _reconcile_bids(roster_player_ids: set, market_player_ids: set) -> dict:
    """
    Para cada puja que seguimos creyendo pendiente (status='placed' en
    nuestra BD): ganada si el jugador ya está en la plantilla; perdida si
    ya no está ni en plantilla ni en el mercado actual (el listado
    expiró/se resolvió sin nosotros); si sigue en el mercado y no en la
    plantilla, se asume que la puja sigue abierta y no se toca todavía.

    Devuelve {'won': n, 'lost': n} para poder resumirlo en la notificación.
    """
    counts = {"won": 0, "lost": 0}
    for bid in get_open_bids():
        if bid["player_id"] in roster_player_ids:
            update_bid_status(bid["id"], "won")
            counts["won"] += 1
        elif bid["player_id"] not in market_player_ids:
            update_bid_status(bid["id"], "lost")
            counts["lost"] += 1
    return counts


def _parse_int(value) -> int | None:
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
    `player` es un item tal cual lo devuelve Futmondo, ya sea de roster o
    de market (ver docstring de clients/futmondo_client.py: mismo shape en
    ambos, salvo campos exclusivos de cada uno — "buyPrice"/"market" solo
    en roster, "price"/"numberOfBids"/"expirationDate" solo en market).

    `on_market`: si se pasa explícito, manda sobre `player.get("market")`.
    Necesario porque los jugadores que vienen de get_market() (el listado
    de fichajes) no traen ese campo — igual que pasaba con "onMarket" en
    Comunio (ver README, bug real corregido a raíz de esto), se mantiene
    la misma cautela aquí aunque no se haya repetido el bug.
    """
    player_id = str(player["id"])
    position = FUTMONDO_POSITION_MAP.get(player.get("role"), player.get("role"))
    average = player.get("average") or {}
    # "fitness" parece ser la puntuación de los últimos partidos, pero el
    # orden cronológico no se ha podido confirmar (liga de prueba en
    # pretemporada, siempre vacío) — ver TODO en clients/futmondo_client.py.
    fitness = average.get("fitness") or []
    last_points = fitness[-1] if fitness else None

    conn.execute(
        """
        INSERT INTO players (id, name, team, position, understat_id, updated_at)
        VALUES (?, ?, ?, ?, NULL, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, team=excluded.team, position=excluded.position, updated_at=excluded.updated_at
        """,
        (player_id, player.get("name"), player.get("team"), position, now),
    )

    conn.execute(
        """
        INSERT INTO futmondo_snapshots
            (player_id, price, buy_price, points, last_points, average_points, on_market, status, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            player_id,
            player.get("value"),
            player.get("buyPrice"),
            _parse_int(player.get("points")),
            _parse_int(last_points),
            _parse_float(average.get("average")),
            1 if (on_market if on_market is not None else player.get("market")) else 0,
            player.get("status") or None,
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

    client = FutmondoClient()

    roster = client.get_roster()
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

    roster_players = roster.get("answer", [])
    market_players = market.get("answer", [])

    matched = 0
    with get_connection() as conn:
        for player in roster_players:
            _upsert_player_and_snapshot(conn, player, now)  # on_market: usa el propio player["market"]

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

    # Reconciliar el estado de nuestras propias pujas (ver docstring de
    # _reconcile_bids): ninguna otra parte del bot actualiza 'placed' a
    # 'won'/'lost' después de colocarlas.
    roster_player_ids = {str(p["id"]) for p in roster_players}
    market_player_ids = {str(p["id"]) for p in market_players}
    reconciled = _reconcile_bids(roster_player_ids, market_player_ids)
    sold = _reconcile_sales(roster_player_ids)

    total = len(roster_players) + len(market_players)
    message = [
        f"sync_data: {len(roster_players)} en plantilla, {len(market_players)} en mercado, "
        f"{matched}/{total} cruzados con Understat."
    ]
    if reconciled["won"] or reconciled["lost"]:
        message.append(f"Pujas resueltas desde el último sync: {reconciled['won']} ganada(s), {reconciled['lost']} perdida(s).")
    if sold:
        message.append(f"Ventas completadas desde el último sync: {sold}.")
    notify(" ".join(message))


if __name__ == "__main__":
    run()
