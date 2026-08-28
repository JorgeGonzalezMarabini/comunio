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

Además, cada sync hace un backfill idempotente de la plantilla inicial (ver
`_backfill_initial_squad_bids`): a los jugadores que llegaron con el equipo
(no comprados por el bot) se les registra una puja 'won' sintética por su VM
actual, porque ese VM sí se descontó del presupuesto inicial -- sin esto,
`engine.selling_strategy.decide_sales()` los descarta como candidatos a
venta sin mirar su lesión/pérdida de valor (no aparecían en
`get_won_bid_prices()`).

Rescate de ventas por riesgo de plantilla (ver `_rescue_sales_at_risk` /
`engine.squad_risk.sales_to_cancel`): si mientras una venta propia sigue
listada, otro jugador de esa misma posición desaparece de la plantilla
(cláusula pagada por otro manager, venta aceptada, lesión...) y eso deja la
posición con margen NEGATIVO (`assess_squad_depth().deficit > 0` -- ni
siquiera contando de vuelta al jugador en venta llegaríamos a los
titulares requeridos), este sync cancela esa venta (`cancel_sale()`) para
recuperar el cuerpo antes de que se cierre sola y nos deje cortos. Vive
aquí (corre cada hora) y no en jobs/run_sales.py (cada 2h) para reaccionar
lo antes posible -- cancelar tarde no deshace el riesgo si la venta ya se
resolvió sola para entonces.
"""
from datetime import datetime, timezone

import requests

import config
from clients.futmondo_client import FutmondoClient, FutmondoOfferError, FUTMONDO_POSITION_MAP
from clients.laliga_stats_client import build_player_index, get_league_data_with_fallback, match_player
from db.models import init_db, get_connection, get_open_bids, update_bid_status, get_open_sales, update_sale_status
from engine.squad_risk import assess_squad_depth, sales_to_cancel
from notifier import notify, track_job_run


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


def _rescue_sales_at_risk(client: FutmondoClient, roster_players: list) -> list[dict]:
    """
    Cancela ventas propias YA LISTADAS (`sales.status='listed'`) cuya
    posición se ha quedado con margen NEGATIVO desde que se listaron (ver
    `engine.squad_risk.sales_to_cancel`) -- típicamente porque OTRO
    jugador de esa misma posición desapareció de la plantilla entre medias
    (cláusula pagada por otro manager, venta aceptada, lesión...; Futmondo
    no distingue la causa, ver docstring del módulo).

    Deliberadamente NO usa `at_risk` (margen cero) para decidir, solo
    `deficit` (margen negativo) -- con `at_risk` a secas, CUALQUIER venta
    recién listada se cancelaría en la primera pasada (listar ya resta uno
    de "disponible" por diseño, ver docstring de `assess_squad_depth`).

    Un fallo al cancelar una venta concreta (red, o el listado ya no
    existe porque se vendió/canceló mientras tanto -- Futmondo no
    documenta qué código devuelve en ese caso, ver docstring de
    `FutmondoClient.cancel_sale`) no aborta el resto: se ignora sin más y
    se reintentará solo si sigue detectado como déficit en el próximo
    sync.

    Devuelve las ventas canceladas con éxito (id, player_id, position)
    para la notificación.
    """
    open_sales = get_open_sales()
    if not open_sales:
        return []

    normalized_squad = [
        {**p, "position": FUTMONDO_POSITION_MAP.get(p.get("role"), p.get("role"))} for p in roster_players
    ]
    depth = assess_squad_depth(normalized_squad, formation=config.DEFAULT_FORMATION)
    position_by_player_id = {str(p["id"]): p["position"] for p in normalized_squad}

    rescued = []
    for sale in sales_to_cancel(depth, open_sales, position_by_player_id):
        try:
            client.cancel_sale(str(sale["player_id"]))
        except (requests.RequestException, FutmondoOfferError):
            continue
        update_sale_status(sale["id"], "delisted")
        rescued.append({**sale, "position": position_by_player_id.get(str(sale["player_id"]))})
    return rescued


def _backfill_initial_squad_bids(conn, roster_players: list, now: str) -> int:
    """
    Los jugadores de la plantilla INICIAL (asignados al crear el equipo, no
    comprados por el bot vía puja) no tienen ninguna fila 'won' en `bids` --
    por eso `engine.selling_strategy.decide_sales()` los descarta como
    candidatos a venta sin siquiera mirar su lesión/pérdida de valor (ver
    `purchase_price = bought_by_bot.get(...) or 0` -> `continue`). Su VM SÍ
    se descontó del presupuesto inicial del equipo al repartir la plantilla,
    así que aquí se registra una puja 'won' sintética por su VM actual (no
    tenemos guardado el VM exacto del momento del reparto, es la mejor
    aproximación disponible) para que sí entren a evaluación de venta, a
    petición del usuario (2026-08-22, ver Mendy: lesionado y nunca evaluado
    por este motivo).

    Se ejecuta en cada sync, pero es idempotente por diseño: en cuanto un
    jugador tiene alguna puja 'won' (esta sintética o una real ganada más
    adelante), deja de tocarse.

    Devuelve cuántas pujas sintéticas se han añadido (para la notificación).
    """
    added = 0
    for player in roster_players:
        value = player.get("value")
        if not value or value <= 0:
            continue  # sin VM fiable, no se puede fijar un precio de referencia
        player_id = str(player["id"])
        already_won = conn.execute(
            "SELECT 1 FROM bids WHERE player_id = ? AND status = 'won' LIMIT 1", (player_id,)
        ).fetchone()
        if already_won:
            continue
        conn.execute(
            """
            INSERT INTO bids (player_id, amount, status, score, reason, created_at)
            VALUES (?, ?, 'won', NULL, ?, ?)
            """,
            (
                player_id,
                value,
                "Plantilla inicial: VM descontado del presupuesto al crear el equipo (backfill automático de sync_data)",
                now,
            ),
        )
        added += 1
    return added


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

    Guarda también, por separado del VM ("value" -> columna `price`), el
    precio de SALIDA del listado ("price" del propio `player`, columna
    `listing_price`) y si es cláusula (`isClause` -> `is_clause`) — a
    petición del usuario (2026-08-22): a diferencia del VM, que siempre lo
    calcula Futmondo, el precio de salida lo elige quien pone al jugador en
    venta (otro manager, o el "Computer"), así que hace falta guardarlo
    aparte para poder compararlo contra el VM real en
    engine.bidding_strategy.decide_bid(). Ambos campos son exclusivos de
    market (ver docstring de clients/futmondo_client.py) -- quedan NULL
    para jugadores de roster.
    """
    player_id = str(player["id"])
    position = FUTMONDO_POSITION_MAP.get(player.get("role"), player.get("role"))
    average = player.get("average") or {}
    # "fitness" parece ser la puntuación de los últimos partidos. Ya no
    # viene siempre vacío (jornada 1 en curso, confirmado 2026-08-18), pero
    # con longitud máxima 1 vista hasta ahora el orden cronológico sigue
    # sin poder confirmarse — ver TODO.md #7 / docstring en
    # clients/futmondo_client.py.
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

    is_clause = player.get("isClause")

    conn.execute(
        """
        INSERT INTO futmondo_snapshots
            (player_id, price, buy_price, points, last_points, average_points, on_market, status,
             listing_price, is_clause, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            player.get("price"),  # precio de SALIDA del listado, distinto del VM -- ver docstring arriba
            None if is_clause is None else (1 if is_clause else 0),
            now,
        ),
    )


def _upsert_external_stats(conn, player_id: str, understat_player: dict, season: str, now: str, team_games: int = None) -> None:
    """
    `team_games`: partidos ya jugados por el equipo del jugador EN LA
    TEMPORADA ACTUAL (ver `_team_games_by_title()`). Se pasa `None` cuando
    esta fila viene del fallback de temporada anterior (ver
    get_league_data_with_fallback()) -- `games`/`minutes_played` de esa fila
    son de una temporada completa distinta, mezclarlos con el team_games de
    la temporada actual daría un ratio sin sentido (ver TODO.md #12).
    """
    conn.execute(
        """
        INSERT INTO external_stats
            (player_id, season, games, minutes_played, goals, non_penalty_goals, assists,
             xg, non_penalty_xg, xa, xg_chain, xg_buildup, yellow_cards, red_cards,
             understat_position, team_games, source, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'understat', ?)
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
            team_games,
            now,
        ),
    )


def _team_games_by_title(league_data: dict) -> dict:
    """
    {team_title: nº de partidos ya jugados esta temporada}, a partir de
    `league_data["teams"][id]["history"]` -- confirmado con datos reales
    (2026-08-18, jornada 1 en curso): cada entrada de `history` es un
    partido YA DISPUTADO con resultado real (`result`/`scored`/`missed`),
    así que `len(history)` es exactamente "partidos totales del equipo",
    lo que pedía TODO.md #12 en vez de `games` del jugador (partidos en los
    que ÉL jugó, que puede ser menor si se perdió alguno por lesión/sanción).
    """
    return {t["title"]: len(t.get("history") or []) for t in league_data.get("teams", {}).values() if t.get("title")}


def run():
    if not config.ENABLE_BOT:
        print("sync_data: ENABLE_BOT=false, no se ejecuta.")
        return

    init_db()

    client = FutmondoClient()

    roster = client.get_roster()
    market = client.get_market()

    league_data, season, fallback_by_team = get_league_data_with_fallback()
    if not league_data.get("players"):
        # Ni la temporada actual ni la anterior tienen datos -- caso
        # extremo, no visto en la práctica, pero no debe romper el sync.
        notify("sync_data: Understat sin datos ni para la temporada actual ni la anterior, se reintentará en el próximo sync.")
        player_index = None
    else:
        if fallback_by_team:
            equipos = ", ".join(sorted(fallback_by_team))
            temporadas = sorted(set(fallback_by_team.values()))
            temporada_txt = temporadas[0] if len(temporadas) == 1 else "/".join(temporadas)
            notify(
                f"sync_data: {len(fallback_by_team)} equipo(s) sin datos todavía en Understat para la temporada {season}, "
                f"usando temporada {temporada_txt} como aproximación temporal para: {equipos}."
            )
        player_index = build_player_index(league_data)

    # Se calcula sobre `league_data` (temporada actual), no sobre lo que
    # haya quedado tras el fallback -- el fallback solo AÑADE jugadores de
    # la temporada anterior a `league_data["players"]", no toca
    # `league_data["teams"]`, así que esto sigue siendo fiel a "partidos
    # jugados esta temporada" incluso para equipos con fallback (ver
    # `_upsert_external_stats`/TODO.md #12).
    team_games_by_title = _team_games_by_title(league_data)

    now = datetime.now(timezone.utc).isoformat()

    roster_players = roster.get("answer", [])
    market_players = market.get("answer", [])

    # Cuenta de cuántos cruces salieron de cada nivel de confianza (ver
    # clients.laliga_stats_client.match_player) — permite ver en la
    # notificación si el cruce se está apoyando demasiado en las
    # estrategias menos fiables, señal de que convendría revisarlo.
    match_counts: dict[str, int] = {}

    def _cross_with_understat(conn, player: dict) -> None:
        if player_index is None:
            return
        position = FUTMONDO_POSITION_MAP.get(player.get("role"), player.get("role"))
        understat_player, strategy = match_player(player.get("name", ""), player.get("team", ""), player_index, position=position)
        match_counts[strategy] = match_counts.get(strategy, 0) + 1
        if understat_player:
            # Con fallback parcial no todos los jugadores del índice
            # comparten temporada -- ver get_league_data_with_fallback().
            player_season = understat_player.get("_source_season", season)
            # team_games solo tiene sentido si esta fila es de la temporada
            # ACTUAL -- si viene del fallback (temporada anterior completa),
            # None (ver docstring de _upsert_external_stats).
            team_games = team_games_by_title.get(understat_player.get("team_title")) if player_season == season else None
            _upsert_external_stats(conn, str(player["id"]), understat_player, player_season, now, team_games)

    with get_connection() as conn:
        for player in roster_players:
            _upsert_player_and_snapshot(conn, player, now)  # on_market: usa el propio player["market"]
            _cross_with_understat(conn, player)

        for player in market_players:
            _upsert_player_and_snapshot(conn, player, now, on_market=True)  # siempre True: viene del listado de mercado
            _cross_with_understat(conn, player)

        # Backfill de pujas 'won' sintéticas para la plantilla inicial (ver
        # docstring de _backfill_initial_squad_bids) -- necesita que las filas
        # de `players` ya existan (FK), por eso va tras el upsert de roster.
        backfilled = _backfill_initial_squad_bids(conn, roster_players, now)

    # Reconciliar el estado de nuestras propias pujas (ver docstring de
    # _reconcile_bids): ninguna otra parte del bot actualiza 'placed' a
    # 'won'/'lost' después de colocarlas.
    roster_player_ids = {str(p["id"]) for p in roster_players}
    market_player_ids = {str(p["id"]) for p in market_players}
    reconciled = _reconcile_bids(roster_player_ids, market_player_ids)
    sold = _reconcile_sales(roster_player_ids)
    rescued = _rescue_sales_at_risk(client, roster_players)

    total = len(roster_players) + len(market_players)
    matched = total - match_counts.get("sin_match", 0) - match_counts.get("sin_nombre", 0)
    breakdown = ", ".join(f"{n} {strategy}" for strategy, n in sorted(match_counts.items()) if strategy not in ("sin_match", "sin_nombre"))
    message = [
        f"sync_data: {len(roster_players)} en plantilla, {len(market_players)} en mercado, "
        f"{matched}/{total} cruzados con Understat" + (f" ({breakdown})" if breakdown else "") + "."
    ]
    if reconciled["won"] or reconciled["lost"]:
        message.append(f"Pujas resueltas desde el último sync: {reconciled['won']} ganada(s), {reconciled['lost']} perdida(s).")
    if backfilled:
        message.append(f"Pujas 'won' sintéticas añadidas para plantilla inicial: {backfilled}.")
    if sold:
        message.append(f"Ventas completadas desde el último sync: {sold}.")
    if rescued:
        detail = ", ".join(f"{r['position']} #{r['player_id']}" for r in rescued)
        message.append(f"Venta(s) cancelada(s) por riesgo de plantilla ({detail}).")
    notify(" ".join(message))


if __name__ == "__main__":
    # Chequeo duplicado a propósito: el de dentro de run() protege a quien
    # llame a run() directamente (tests incluidos); este de aquí evita
    # además que se entre en track_job_run() -- si no, con ENABLE_BOT=false
    # igualmente se registraría una fila en `job_runs`, ese INSERT por sí
    # solo ensuciaría db/futmondo.db, y el step "Commit BD actualizada" del
    # workflow comitearía/pushearía igual aunque el bot no haga nada real.
    if not config.ENABLE_BOT:
        print("sync_data: ENABLE_BOT=false, no se ejecuta.")
    else:
        with track_job_run("sync_data"):
            run()
