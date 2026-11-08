"""
Clausulazos: pagar la cláusula de jugadores de otros managers de la liga.

A petición del usuario (2026-11-08): el equipo va último y en el mercado
del Computer casi nunca salen jugadores buenos, mientras que los rivales
tienen jugadores mucho mejores con cláusulas asumibles para el saldo que
el bot va acumulando.

Reglas de la liga (indicadas por el usuario, 2026-11-08): como mucho 4
clausulazos por semana (desde el lunes 00:00), solo de lunes a viernes y el
viernes solo hasta las 18:00 hora de Madrid -- ver
scheduling.is_clause_window()/start_of_local_week() y
config.CLAUSE_MAX_PER_WEEK. Fuera de eso el job sale sin hacer nada. Ojo:
el cupo solo cuenta los clausulazos pagados por el bot (`clause_payments`);
uno pagado a mano en la app no se ve aquí -- si con él se pasara el límite,
Futmondo debería rechazar el pago y quedaría auditado como 'failed'.

Flujo (una pasada al día de lunes a viernes, ver .github/workflows/run_clauses.yml):
1. Lee la plantilla de TODOS los managers (`get_championship_players()`)
   y guarda en BD la foto de los jugadores rivales (mismo upsert que
   jobs/sync_data.py) para poder evaluarlos con engine.evaluator igual que
   a los de mercado. Futmondo no da el nombre del equipo en esa lista,
   solo `teamId`: se resuelve con un mapa sacado de la propia plantilla,
   el mercado y los jugadores ya conocidos en BD.
2. Elige candidatos con engine.clause_strategy.select_clause_targets()
   (mejoran el once o tapan un hueco, cláusula <= CLAUSE_MAX_BUDGET_PCT del
   saldo disponible, mismos filtros duros que las pujas).
3. Sin config.ENABLE_CLAUSE_AUTO_PAY (por defecto): solo notifica las
   mejores propuestas por Telegram y las audita como 'proposed'.
   Con el flag: paga como mucho UNA por pasada, relee la plantilla para confirmar que el jugador ha llegado
   (`playerMoveInDays` = 0 en la liga, el traspaso debería ser inmediato)
   y registra el importe como puja 'won' en `bids` -- así
   db.models.get_won_bid_prices() y la lógica de ventas conocen el coste
   real del jugador, no solo su VM.
"""
from datetime import datetime, timezone

import requests

import config
from clients.futmondo_client import FutmondoClient, FutmondoOfferError, real_pending_bid_amount
from db.models import (
    count_clause_payments_since,
    get_connection,
    get_league_setting,
    get_open_bids,
    get_pending_bid_amount,
    get_player_features,
    get_standby_sales,
    init_db,
    record_clause_payment,
)
from engine.clause_strategy import select_clause_targets
from engine.evaluator import evaluate_players
from engine.squad_risk import assess_squad_depth, weakest_starter_scores
from jobs.sync_data import _upsert_player_and_snapshot
from notifier import format_number, notify, track_job_run
from scheduling import CLAUSE_FRIDAY_DEADLINE, is_clause_window, start_of_local_week


def _rival_players(championship_players: list[dict], own_userteam_id: str, now: datetime) -> list[dict]:
    """
    Jugadores con dueño distinto de nosotros y cláusula pagable ya: con
    `clause.price`, sin traspaso en curso (`transferred`) y con
    `clause.date` ya pasada. El significado exacto de `date` no está
    confirmado (en los datos reales coincide con el día en que el jugador
    llegó a su equipo actual, p. ej. Iñaki Williams el 2026-11-06); si
    alguna vez viene en el futuro se trata como "todavía protegido" por
    prudencia.
    """
    rivals = []
    for p in championship_players:
        clause = p.get("clause") or {}
        owner = p.get("userteamId")
        if not owner or str(owner) == str(own_userteam_id) or not clause.get("price") or clause.get("transferred"):
            continue
        try:
            clause_date = datetime.fromisoformat(clause["date"].replace("Z", "+00:00")) if clause.get("date") else None
        except ValueError:
            clause_date = None
        if clause_date is not None and clause_date > now:
            continue
        rivals.append(p)
    return rivals


def _team_names_by_id(roster: list[dict], market: list[dict], championship_players: list[dict]) -> dict:
    """{teamId: nombre de equipo}, a partir de los items que sí traen `team`
    (plantilla y mercado) y de los jugadores ya guardados en BD con equipo."""
    names = {str(p["teamId"]): p["team"] for p in roster + market if p.get("teamId") and p.get("team")}
    with get_connection() as conn:
        known = {r["id"]: r["team"] for r in conn.execute("SELECT id, team FROM players WHERE team IS NOT NULL")}
    for p in championship_players:
        team_id = str(p.get("teamId") or "")
        if team_id and team_id not in names and known.get(str(p["id"])):
            names[team_id] = known[str(p["id"])]
    return names


def _describe(c: dict) -> str:
    reason = "tapa un hueco" if c["fills_deficit"] else f"mejora el once (+{c['lineup_gain']:.3f})"
    return (
        f"{c['name']} ({c['position']}, de {c['owner_userteam']}): cláusula {format_number(c['clause_price'])}, "
        f"VM {format_number(c.get('price') or 0)}, lineup_score={c['lineup_score']:.3f}, {reason}"
    )


def run():
    if not config.ENABLE_BOT:
        print("run_clauses: ENABLE_BOT=false, no se ejecuta.")
        return
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()

    # Reglas de la liga (ver scheduling.is_clause_window y
    # config.CLAUSE_MAX_PER_WEEK): fuera de la ventana o con el cupo semanal
    # gastado no se puede pagar, así que tampoco tiene sentido proponer.
    # Antes de tocar la red ni la BD.
    if not is_clause_window(now_dt):
        print(
            "run_clauses: fuera de la ventana de clausulazos de la liga "
            f"(lunes a viernes, viernes hasta las {CLAUSE_FRIDAY_DEADLINE[0]:02d}:{CLAUSE_FRIDAY_DEADLINE[1]:02d} Europe/Madrid)."
        )
        return
    init_db()
    paid_this_week = count_clause_payments_since(start_of_local_week(now_dt).isoformat())
    if paid_this_week >= config.CLAUSE_MAX_PER_WEEK:
        print(f"run_clauses: cupo semanal gastado ({paid_this_week}/{config.CLAUSE_MAX_PER_WEEK} desde el lunes).")
        return

    client = FutmondoClient()

    roster = client.get_roster().get("answer", [])
    market = client.get_market().get("answer", [])
    information = client.get_information().get("answer", {})
    championship_players = client.get_championship_players().get("answer", {}).get("players", [])

    roster_ids = {str(p["id"]) for p in roster}
    # Pujas de mercado todavía abiertas: las locales ('placed') y las que el
    # propio Futmondo marca con "bid" en el mercado (misma doble fuente que
    # jobs/run_market.py). Cada una puede acabar ocupando una plaza, así que
    # cuentan para el hueco libre en la plantilla.
    market_by_id = {str(p["id"]): p for p in market}
    pending_bid_ids = {str(b["player_id"]) for b in get_open_bids()} | {pid for pid, p in market_by_id.items() if "bid" in p}
    max_roster_size = information.get("configuration", {}).get("maxPlayersInRoster") or get_league_setting(
        "max_players_in_roster"
    )
    if max_roster_size is None or len(roster_ids) + len(pending_bid_ids) >= max_roster_size:
        print(
            f"run_clauses: sin plaza libre o límite desconocido ({len(roster_ids)} en plantilla + "
            f"{len(pending_bid_ids)} pujas pendientes / {max_roster_size}), no se clausula."
        )
        return

    budget = information.get("budget", 0)
    pending_committed = max(get_pending_bid_amount(), real_pending_bid_amount(market))
    available_budget = budget - pending_committed

    rivals = _rival_players(championship_players, client.userteam_id, now_dt)
    rival_by_id = {str(p["id"]): p for p in rivals}

    # Foto de los rivales en BD para evaluarlos. Los que su dueño tiene
    # ahora mismo a la venta se guardan con on_market=1, igual que haría
    # sync_data: con 0 desaparecerían de los candidatos de run_market hasta
    # el siguiente sync. Para esos se mezcla el item del mercado, que trae
    # el precio de venta ("price") y el equipo, ausentes en la lista del
    # campeonato.
    team_names = _team_names_by_id(roster, market, championship_players)
    with get_connection() as conn:
        for p in rivals:
            pid = str(p["id"])
            item = {"team": team_names.get(str(p.get("teamId"))), **p, **market_by_id.get(pid, {})}
            _upsert_player_and_snapshot(conn, item, now, on_market=pid in market_by_id)

    features = {p["id"]: p for p in get_player_features(only_on_market=False)}
    standby_ids = {str(sale["player_id"]) for sale in get_standby_sales()}
    squad_raw = [
        {**features[pid], "on_market": False} if pid in standby_ids else features[pid] for pid in roster_ids if pid in features
    ]
    rival_raw = [features[pid] for pid in rival_by_id if pid in features]
    if not squad_raw or not rival_raw:
        print("run_clauses: sin datos de plantilla o de rivales en BD, no se evalúa.")
        return

    # Mismo pool para plantilla y rivales: los scores del evaluator son
    # relativos al pool, así que solo son comparables evaluados juntos
    # (igual que hace jobs/run_market.py con los candidatos de mercado).
    lineup_scored = evaluate_players(squad_raw + rival_raw, weights=config.LINEUP_EVALUATOR_WEIGHTS)
    squad_ranked = [p for p in lineup_scored if p["id"] in roster_ids]
    upgrade_thresholds = weakest_starter_scores(squad_ranked, formation=config.DEFAULT_FORMATION)
    # Los fichajes con puja pendiente cuentan como ya en plantilla para
    # decidir si una posición tiene hueco: si run_market ya está pujando por
    # un delantero, ese hueco ya tiene quien lo tape y no hace falta pagar
    # además una cláusula. Para "mejora el once" se sigue usando el once
    # real (upgrade_thresholds), no uno con fichajes que aún no han llegado.
    pending_raw = [{**features[pid], "on_market": False} for pid in pending_bid_ids if pid in features and pid not in roster_ids]
    depth = assess_squad_depth(squad_raw + pending_raw, formation=config.DEFAULT_FORMATION)
    deficit_positions = {pos for pos, info in depth.items() if info["deficit"] > 0}

    candidates = []
    for p in lineup_scored:
        rival = rival_by_id.get(p["id"])
        if rival is None:
            continue
        candidates.append(
            {
                **p,
                "lineup_score": p["score"],
                "clause_price": rival["clause"]["price"],
                "owner_userteam": rival.get("userteam"),
                "slug": rival.get("slug"),
            }
        )

    selected = select_clause_targets(candidates, upgrade_thresholds, deficit_positions, available_budget)
    # Rivales que su dueño tiene ahora mismo a la venta: no se clausulan, se
    # avisa para hacer una oferta en la app (a petición del usuario,
    # 2026-11-08) -- run_market no puja por ventas de otros managers
    # (config.ENABLE_BIDS_ON_MANAGER_LISTINGS) y la cláusula suele costar más.
    listed = [c for c in selected if c["id"] in market_by_id]
    targets = [c for c in selected if c["id"] not in market_by_id]
    if not selected:
        print(
            f"run_clauses: ningún clausulazo cumple los criterios (saldo disponible={format_number(available_budget)}, "
            f"tope={format_number(int(available_budget * config.CLAUSE_MAX_BUDGET_PCT))}, rivales evaluados={len(candidates)})."
        )
        return

    offer_lines = []
    if listed:
        with get_connection() as conn:
            for c in listed:
                listing_price = market_by_id[c["id"]].get("price")
                reason = f"{_describe(c)}; a la venta por su dueño por {format_number(listing_price or 0)}"
                record_clause_payment(
                    conn, c["id"], c["owner_userteam"], c["clause_price"], c.get("price"), c["lineup_score"],
                    "offer_suggested", reason, now,
                )
                offer_lines.append(
                    f"  - {c['name']} ({c['position']}, de {c['owner_userteam']}) está a la venta por "
                    f"{format_number(listing_price or 0)} (cláusula {format_number(c['clause_price'])}, "
                    f"VM {format_number(c.get('price') or 0)})"
                )
        offer_lines.insert(0, "💡 Cumplen los criterios pero su dueño los tiene a la venta: mejor hacer oferta en la app que pagar la cláusula:")

    if not targets:
        notify("run_clauses:\n" + "\n".join(offer_lines))
        return

    if not config.ENABLE_CLAUSE_AUTO_PAY:
        proposals = targets[: config.CLAUSE_PROPOSALS_TO_NOTIFY]
        with get_connection() as conn:
            for c in proposals:
                record_clause_payment(
                    conn, c["id"], c["owner_userteam"], c["clause_price"], c.get("price"), c["lineup_score"],
                    "proposed", _describe(c), now,
                )
        lines = [
            f"run_clauses: {len(targets)} clausulazo(s) posibles (saldo disponible {format_number(available_budget)}, "
            f"tope por clausulazo {format_number(int(available_budget * config.CLAUSE_MAX_BUDGET_PCT))}, "
            f"cupo semanal {paid_this_week}/{config.CLAUSE_MAX_PER_WEEK} usado). "
            "NO pagados (ENABLE_CLAUSE_AUTO_PAY=false), mejores propuestas:"
        ]
        lines.extend(f"  {i}. {_describe(c)}" for i, c in enumerate(proposals, 1))
        notify("\n".join(lines + offer_lines))
        return

    target = targets[0]
    reason = _describe(target)
    try:
        client.pay_clause(target["id"], target["slug"], target["clause_price"])
    except (requests.RequestException, FutmondoOfferError) as e:
        with get_connection() as conn:
            record_clause_payment(
                conn, target["id"], target["owner_userteam"], target["clause_price"], target.get("price"),
                target["lineup_score"], "failed", reason, now, error=str(e),
            )
        notify("\n".join([f"run_clauses: ⚠️ falló el clausulazo de {reason} (error: {e})."] + offer_lines))
        return

    arrived = str(target["id"]) in {str(p["id"]) for p in client.get_roster().get("answer", [])}
    with get_connection() as conn:
        record_clause_payment(
            conn, target["id"], target["owner_userteam"], target["clause_price"], target.get("price"),
            target["lineup_score"], "paid" if arrived else "unconfirmed", reason, now,
        )
        if arrived:
            conn.execute(
                "INSERT INTO bids (player_id, amount, status, score, reason, created_at) VALUES (?, ?, 'won', ?, ?, ?)",
                (target["id"], target["clause_price"], target["lineup_score"], f"clausulazo: {reason}", now),
            )
    if arrived:
        result = f"run_clauses: ✅ clausulazo pagado y jugador ya en plantilla: {reason}."
    else:
        result = (
            f"run_clauses: ⚠️ Futmondo aceptó el clausulazo pero el jugador todavía no aparece en la plantilla: {reason}. "
            "Revisar en la app."
        )
    notify("\n".join([result] + offer_lines))

if __name__ == "__main__":
    # Mismo doble chequeo que el resto de jobs (ver jobs/sync_data.py).
    if not config.ENABLE_BOT:
        print("run_clauses: ENABLE_BOT=false, no se ejecuta.")
    else:
        with track_job_run("run_clauses"):
            run()
