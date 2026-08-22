"""
Job: evalúa el mercado y ejecuta pujas automáticas, 100% autónomo (sin
confirmación manual). Cada puja (o intento fallido) se audita en la tabla
`bids` con el score y motivo que la justificó.

Asume que jobs/sync_data.py ya corrió antes en el cron (así `players`/
`futmondo_snapshots`/`external_stats` están al día) — este job solo lee de
la BD y decide, no vuelve a sincronizar stats.

Antes de evaluar el mercado, calcula dos señales de prioridad sobre la
plantilla propia (ambas se suman si coinciden en el mismo candidato, ver
engine.bidding_strategy.apply_position_priority — ninguna fuerza la puja
de un candidato realmente malo, solo dan ventaja frente a otro de score
similar):

  1. Riesgo de plantilla (engine/squad_risk.py:assess_squad_depth):
     posiciones sin ningún suplente sano, donde un "clausulazo"/lesión/
     sanción más dejaría un hueco en la alineación. Señal de CANTIDAD.

  2. Mejora del once (engine/squad_risk.py:weakest_starter_scores): el
     candidato, evaluado con los MISMOS pesos con los que se elige la
     alineación real (config.LINEUP_EVALUATOR_WEIGHTS — el precio no debe
     importar), supera en calidad al titular más flojo de su posición
     hoy. Señal de CALIDAD: aunque la posición ya tenga suplente de sobra,
     fichar a alguien mejor que el peor titular actual sigue siendo una
     mejora real del equipo que sale a jugar. Para comparar en igualdad de
     condiciones, este score de alineación se calcula sobre plantilla +
     mercado juntos en la misma llamada a evaluate_players() (normalizar
     cada uno por separado los dejaría en escalas distintas, no
     comparables entre sí — ver engine/evaluator.py:normalize_pool).

El presupuesto disponible se calcula restando TODAS las pujas pendientes
sin resolver — resuelve TODO.md #3: combina `db.models.
get_pending_bid_amount()` (auditoría local) con `clients.futmondo_client.
real_pending_bid_amount()` (confirmado del propio Futmondo, campo "bid" de
`get_market()`, ver ese docstring) tomando el MAYOR de los dos, nunca solo
uno — así una BD perdida/no reconciliada a tiempo no puede hacer que se
subestime el compromiso real. No es solo lo arriesgado hoy
(get_bids_risked_today, que sigue usándose como ritmo de gasto por
jornada, no como protección de saldo).

place_bid() está **100% confirmado** (ver clients/futmondo_client.py) —
puede fallar por HTTP (requests.RequestException) o por rechazo de negocio
con HTTP 200 (FutmondoOfferError); cada intento va en su propio try/except
para que un fallo de una puja no tumbe las demás ni la ejecución completa.

También confirmado en vivo (2026-08-18, ver `clients.futmondo_client.
real_pending_bid_amount()`): pujar dos veces sobre el mismo jugador
mientras la primera puja sigue abierta no la actualiza (Futmondo responde
"ok" pero ignora el nuevo importe) — por eso este job excluye de los
candidatos a cualquier jugador con una puja local todavía `'placed'`
(`db.models.get_open_bids()`) antes de evaluar, en vez de reintentar una
"mejora" que no tiene ningún efecto real.

Lesión confirmada vs. duda (a petición del usuario, 2026-08-22): "doubt"
(duda, el jugador todavía puede llegar a jugar) e "injuredN" (lesión ya
confirmada, ver `clients.futmondo_client.is_confirmed_injured_status()`)
ya NO se tratan igual aquí. "doubt" sigue evaluándose con el resto de
candidatos, solo que con una penalización de score más dura que antes
(`config.EVALUATOR_WEIGHTS["doubt_penalty"]`, ver `engine/evaluator.py`).
Una lesión confirmada, en cambio, se descarta ANTES de evaluar — igual que
los candidatos con puja local ya abierta de arriba — porque normalmente es
baja segura varias jornadas: cualquier penalización de score, por dura que
sea, siempre podría llegar a compensarse con muy buenas stats en el resto
de métricas, y aquí eso sería justo el error que se quiere evitar.

Plantilla completa (a raíz de 11 pujas fallidas en vivo, 2026-08-22): antes
de evaluar cualquier candidato se compara `len(roster)` contra
`information.answer.configuration.numberOfPlayers` -- si la plantilla ya
está al máximo que permite la liga, Futmondo rechaza CUALQUIER puja nueva
con `FutmondoOfferError("api.market.max_number_players_in_roster")` sin
importar posición ni presupuesto, así que el job corta aquí en vez de
generar pujas condenadas a fallar.

Cancelar+pujar mejor (TODO.md #13, `engine.bidding_strategy.
find_cancel_swap_candidates`): fase APARTE, después del loop normal de
arriba, para los candidatos buenos (score/precio válidos, ver
`is_price_worth_bidding`) que se quedaron fuera solo por presupuesto/tope,
no por calidad — se calcula incluso si el loop normal no pujó nada, que es
justo el caso más útil (presupuesto tan ajustado que nada nuevo entra).
Solo sacrifica una puja abierta si el candidato la supera en score por un
margen amplio (`config.BIDDING_CANCEL_SWAP_MIN_MARGIN`, bastante por
encima de cualquier boost de prioridad, para no cancelar por ruido) y si a
esa puja le queda tiempo de sobra antes de expirar
(`config.BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY` — si está a punto de
resolverse, mejor dejarla terminar). Nunca cancela una puja cuyo id real de
Futmondo no venga confirmado en el `get_market()` de esta misma pasada —
eso excluye automáticamente cualquier puja colocada en este mismo run (su
`market_item` es de ANTES de pujar). Como `cancel_bid()`+`place_bid()` no
es atómico, un fallo a medias (cancela pero no llega a pujar la nueva)
queda auditado en `bids` como `'cancelled'`+`'failed'`, nunca silencioso —
ver `config.BIDDING_MAX_CANCEL_SWAPS_PER_RUN` (1 por defecto, deliberado
mientras esta feature no tiene histórico real).
"""
from datetime import datetime, timezone

import requests

import config
from clients.futmondo_client import (
    FutmondoClient,
    FutmondoOfferError,
    is_confirmed_injured_status,
    real_pending_bid_amount,
)
from db.models import (
    get_connection,
    get_bids_risked_today,
    get_open_bids,
    get_pending_bid_amount,
    get_player_features,
    update_bid_status,
)
from engine.bidding_strategy import (
    apply_position_priority,
    decide_bid,
    decide_bids_for_market,
    dynamic_player_cap,
    find_cancel_swap_candidates,
    is_price_worth_bidding,
)
from engine.evaluator import evaluate_players
from engine.squad_risk import assess_squad_depth, depth_warnings, weakest_starter_scores
from notifier import notify, track_job_run


def _persist_bid(conn, decision: dict, status: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO bids (player_id, amount, status, score, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (str(decision["player_id"]), decision["amount"], status, decision["score"], decision["reason"], now),
    )


def run():
    if not config.ENABLE_BOT:
        print("run_market: ENABLE_BOT=false, no se ejecuta.")
        return

    client = FutmondoClient()

    raw_candidates = get_player_features(only_on_market=True)
    if not raw_candidates:
        notify("run_market: no hay candidatos en mercado en la BD (¿corrió sync_data antes?).")
        return

    # Excluye candidatos con una puja local todavía 'placed' -- confirmado
    # en vivo que pujar dos veces sobre el mismo jugador no actualiza el
    # importe (ver docstring del módulo / clients.futmondo_client.
    # real_pending_bid_amount()), así que reintentarlo no tiene efecto real
    # y solo ensucia la auditoría con filas duplicadas.
    already_bid_ids = {b["player_id"] for b in get_open_bids()}
    skipped_already_bid = [p for p in raw_candidates if p["id"] in already_bid_ids]
    raw_candidates = [p for p in raw_candidates if p["id"] not in already_bid_ids]
    if not raw_candidates:
        notify(
            f"run_market: {len(skipped_already_bid)} candidato(s) en mercado, todos con puja local ya "
            "pendiente -- nada nuevo que evaluar esta ejecución."
        )
        return

    # Descarta directamente los candidatos con lesión CONFIRMADA (ver
    # docstring del módulo) -- a diferencia de "doubt", que sigue abajo en
    # `raw_candidates` y solo se penaliza en el score
    # (config.EVALUATOR_WEIGHTS["doubt_penalty"]).
    skipped_injured = [p for p in raw_candidates if is_confirmed_injured_status(p.get("status"))]
    raw_candidates = [p for p in raw_candidates if not is_confirmed_injured_status(p.get("status"))]
    if not raw_candidates:
        notify(
            f"run_market: {len(skipped_injured)} candidato(s) en mercado, todos con lesión confirmada -- "
            "nada nuevo que evaluar esta ejecución."
        )
        return

    # Riesgo de plantilla: posiciones sin ningún suplente sano (ver
    # engine/squad_risk.py). Necesita la plantilla propia, no solo el
    # mercado -- se resuelve igual que en jobs/set_lineup.py.
    roster_response = client.get_roster()
    roster_ids = {str(p["id"]) for p in roster_response.get("answer", [])}
    all_players = get_player_features(only_on_market=False)
    squad_raw = [p for p in all_players if p["id"] in roster_ids]

    # Límite total de plantilla (confirmado en vivo 2026-08-22:
    # "api.market.max_number_players_in_roster" -- Futmondo rechaza
    # CUALQUIER puja nueva si la plantilla ya tiene el máximo de jugadores
    # que permite la liga, sin importar posición ni presupuesto/tope). Se
    # pide `get_information()` ya aquí (en vez de más abajo, donde antes
    # solo se usaba para `budget`) para cortar ANTES de evaluar candidatos
    # y así no generar pujas que van a fallar seguro.
    information = client.get_information()
    max_roster_size = information.get("answer", {}).get("configuration", {}).get("numberOfPlayers")
    if max_roster_size is not None and len(roster_ids) >= max_roster_size:
        notify(
            f"run_market: plantilla completa ({len(roster_ids)}/{max_roster_size}) -- no se puja esta "
            f"ejecución ({len(raw_candidates)} candidato(s) en mercado descartado(s))."
        )
        return

    at_risk_positions = set()
    upgrade_thresholds = {}
    lineup_score_by_id = {}
    if squad_raw:
        depth = assess_squad_depth(squad_raw, formation=config.DEFAULT_FORMATION)
        at_risk_positions = {pos for pos, info in depth.items() if info["at_risk"]}
        risk_warnings = depth_warnings(depth)

        # Score de alineación (LINEUP_EVALUATOR_WEIGHTS, sin precio) de
        # plantilla + mercado EN LA MISMA llamada, para que sean
        # comparables entre sí (ver docstring del módulo) -- de aquí sale
        # tanto el listón (squad) como el "lineup_score" de cada candidato
        # de mercado que se compara contra ese listón.
        lineup_scored = evaluate_players(squad_raw + raw_candidates, weights=config.LINEUP_EVALUATOR_WEIGHTS)
        lineup_score_by_id = {p["id"]: p["score"] for p in lineup_scored}
        squad_ids = {p["id"] for p in squad_raw}
        squad_lineup_ranked = [p for p in lineup_scored if p["id"] in squad_ids]
        upgrade_thresholds = weakest_starter_scores(squad_lineup_ranked, formation=config.DEFAULT_FORMATION)
    else:
        risk_warnings = []

    ranked = evaluate_players(raw_candidates)
    for p in ranked:
        if p["id"] in lineup_score_by_id:
            p["lineup_score"] = lineup_score_by_id[p["id"]]
    prioritized = apply_position_priority(ranked, at_risk_positions, upgrade_thresholds=upgrade_thresholds)

    remaining_budget = information.get("answer", {}).get("budget", 0)
    already_risked = get_bids_risked_today()

    # Presupuesto: se pide el mercado ya aquí (no solo más abajo para
    # pujar) para poder cruzar la auditoría local con la fuente confirmada
    # del propio Futmondo (TODO.md #3, resuelto) -- se toma el MAYOR de
    # las dos, nunca solo la local, para que una BD perdida/desincronizada
    # no pueda hacer que se subestime el compromiso real.
    market_items = client.get_market().get("answer", [])
    market_by_id = {str(p["id"]): p for p in market_items}
    pending_committed = max(get_pending_bid_amount(), real_pending_bid_amount(market_items))

    # Tope por jugador dinámico (ver engine.bidding_strategy.
    # dynamic_player_cap) en vez del antiguo tope fijo de 15M -- combina
    # valor medio de la plantilla propia (squad_raw), precio medio del
    # mercado ponderado por score (ranked, sin los boosts de prioridad de
    # apply_position_priority: ese boost es para ORDENAR candidatos entre
    # sí, no una señal de "cuánto vale el mercado ahora") y % del
    # presupuesto disponible.
    player_cap = dynamic_player_cap(remaining_budget, squad_raw, ranked)

    decisions = decide_bids_for_market(
        prioritized, remaining_budget, already_risked, pending_committed=pending_committed, player_cap=player_cap
    )

    # Candidatos buenos (no descartados por score/precio, ver
    # is_price_worth_bidding) que decide_bids_for_market NO pujó -- es
    # decir, bloqueados por presupuesto/tope, no por calidad. Se calculan
    # SIEMPRE (incluso si `decisions` está vacío) porque el caso más útil
    # del swap es justo cuando el presupuesto está tan ajustado que nada
    # nuevo entra por la vía normal -- ver TODO.md #13.
    decided_ids = {d["player_id"] for d in decisions}
    blocked_candidates = [c for c in prioritized if c["id"] not in decided_ids and is_price_worth_bidding(c)]
    swap_proposals = []
    if blocked_candidates:
        sacrificable_bids = []
        for b in get_open_bids():
            market_item = market_by_id.get(str(b["player_id"]))
            bid_info = market_item.get("bid") if market_item else None
            if not bid_info:
                # Sin id de oferta CONFIRMADO en este snapshot de mercado --
                # puede haberse resuelto ya, o ser una puja colocada en este
                # mismo run (su market_item viene del fetch de arriba,
                # HECHO ANTES de pujar nada esta pasada) -- nunca cancelar
                # sin el id real de Futmondo confirmado (ver
                # clients.futmondo_client.cancel_bid).
                continue
            expires_at = None
            raw_expiration = market_item.get("expirationDate")
            if raw_expiration:
                try:
                    expires_at = datetime.fromisoformat(raw_expiration.replace("Z", "+00:00"))
                except ValueError:
                    expires_at = None
            sacrificable_bids.append(
                {
                    "local_row_id": b["id"],
                    "player_id": b["player_id"],
                    "score": b["score"],
                    "amount": b["amount"],
                    "bid_id": bid_info["id"],
                    "expires_at": expires_at,
                }
            )
        swap_proposals = find_cancel_swap_candidates(
            blocked_candidates, sacrificable_bids, now=datetime.now(timezone.utc)
        )

    if not decisions and not swap_proposals:
        notify(
            f"run_market: sin pujas esta ejecución (saldo={remaining_budget}, "
            f"comprometido en pujas pendientes={pending_committed}, ya arriesgado hoy={already_risked}, "
            f"candidatos evaluados={len(ranked)}, tope dinámico por jugador={player_cap})."
        )
        return

    # `player_slug` no viene en `get_player_features()` (columnas de BD),
    # hace falta el candidato de mercado tal cual para pujar (place_bid lo
    # exige, ver clients/futmondo_client.py) -- se cruza por id, reusando
    # `market_by_id` ya calculado arriba (mismo fetch que para pending_committed).

    now = datetime.now(timezone.utc).isoformat()
    placed, failed = [], []

    with get_connection() as conn:
        for decision in decisions:
            market_player = market_by_id.get(str(decision["player_id"]))
            if market_player is None:
                # Ya no está en el mercado (mercado cambió entre evaluar y pujar) -- no se puede pujar.
                _persist_bid(conn, decision, "failed", now)
                failed.append((decision, "el jugador ya no está en el mercado"))
                continue
            try:
                client.place_bid(decision["player_id"], market_player["slug"], decision["amount"])
                _persist_bid(conn, decision, "placed", now)
                placed.append(decision)
            except (requests.RequestException, FutmondoOfferError) as e:
                _persist_bid(conn, decision, "failed", now)
                failed.append((decision, str(e)))

    # --- Ejecutar las propuestas de swap calculadas arriba (TODO.md #13):
    # cancelar una puja floja para pujar por un candidato mejor bloqueado
    # solo por presupuesto/tope. Fase aparte, DESPUÉS del loop normal de
    # arriba -- nunca toca ni recalcula lo ya decidido/pujado ahí.
    #
    # `pending_after_run`/`risked_after_run` sí incluyen lo pujado en el
    # loop normal de arriba (no solo el `pending_committed`/`already_risked`
    # de antes de esta pasada) -- si no, se contaría dos veces el margen que
    # esas pujas nuevas ya consumieron.
    swapped, swap_failed = [], []
    if swap_proposals:
        pending_after_run = pending_committed + sum(d["amount"] for d in decisions)
        risked_after_run = already_risked + sum(d["amount"] for d in decisions)

        with get_connection() as conn:
            for proposal in swap_proposals:
                candidate, sacrifice = proposal["candidate"], proposal["sacrifice"]
                try:
                    client.cancel_bid(sacrifice["bid_id"])
                except (requests.RequestException, FutmondoOfferError) as e:
                    swap_failed.append((candidate, sacrifice, f"cancelación fallida: {e} -- nada más tocado"))
                    continue

                # Cancelación confirmada -- a partir de aquí ya no hay
                # vuelta atrás sobre la puja vieja, pase lo que pase abajo.
                update_bid_status(sacrifice["local_row_id"], "cancelled")
                pending_after_swap = max(0, pending_after_run - sacrifice["amount"])

                new_decision = decide_bid(
                    candidate,
                    remaining_budget,
                    risked_after_run,
                    pending_committed=pending_after_swap,
                    player_cap=player_cap,
                )
                market_player = market_by_id.get(str(candidate["id"]))
                if new_decision is None or market_player is None:
                    reason = (
                        "ya no cupo dentro de los límites tras liberar presupuesto"
                        if new_decision is None
                        else "ya no está en el mercado"
                    )
                    swap_failed.append(
                        (candidate, sacrifice, f"puja cancelada pero el candidato {reason} -- swap a medias")
                    )
                    continue

                try:
                    client.place_bid(candidate["id"], market_player["slug"], new_decision["amount"])
                    _persist_bid(conn, new_decision, "placed", now)
                    swapped.append({"decision": new_decision, "sacrificed_player_id": sacrifice["player_id"]})
                except (requests.RequestException, FutmondoOfferError) as e:
                    _persist_bid(conn, new_decision, "failed", now)
                    swap_failed.append(
                        (
                            candidate,
                            sacrifice,
                            f"puja cancelada pero el place_bid posterior falló: {e} -- "
                            "queda auditado como cancelled+failed",
                        )
                    )

    summary = [f"run_market: {len(placed)} puja(s) realizada(s). (tope dinámico por jugador: {player_cap})"]
    for d in placed:
        tags = []
        if d.get("position_at_risk"):
            tags.append("prioridad: posición en riesgo")
        if d.get("would_upgrade_lineup"):
            tags.append("mejora el once titular")
        priority_tag = f" [{', '.join(tags)}]" if tags else ""
        summary.append(f"  - jugador {d['player_id']}: {d['amount']} (score={d['score']:.3f}){priority_tag}")
    if failed:
        summary.append(f"{len(failed)} puja(s) fallida(s):")
        for d, err in failed:
            summary.append(f"  - jugador {d['player_id']}: {err}")
    if swapped:
        summary.append(f"🔄 {len(swapped)} swap(s) (cancelar+pujar mejor, TODO.md #13):")
        for s in swapped:
            d = s["decision"]
            summary.append(
                f"  - jugador {d['player_id']}: {d['amount']} (score={d['score']:.3f}) "
                f"en vez de la puja cancelada sobre {s['sacrificed_player_id']}"
            )
    if swap_failed:
        summary.append(f"⚠️ {len(swap_failed)} swap(s) fallido(s) o a medias:")
        for candidate, sacrifice, err in swap_failed:
            summary.append(f"  - candidato {candidate['id']} / sacrificio {sacrifice['player_id']}: {err}")
    if risk_warnings:
        summary.append("⚠️ Riesgo de plantilla detectado (priorizado al pujar):")
        summary.extend(f"  - {w}" for w in risk_warnings)
    if skipped_injured:
        summary.append(
            f"🚑 {len(skipped_injured)} candidato(s) descartado(s) del mercado por lesión confirmada: "
            + ", ".join(str(p["id"]) for p in skipped_injured)
        )
    if pending_committed:
        summary.append(f"(comprometido en pujas pendientes sin resolver: {pending_committed})")

    notify("\n".join(summary))


if __name__ == "__main__":
    # Chequeo duplicado a propósito: el de dentro de run() protege a quien
    # llame a run() directamente (tests incluidos); este de aquí evita
    # además que se entre en track_job_run() -- si no, con ENABLE_BOT=false
    # igualmente se registraría una fila en `job_runs`, ese INSERT por sí
    # solo ensuciaría db/futmondo.db, y el step "Commit BD actualizada" del
    # workflow comitearía/pushearía igual aunque el bot no haga nada real.
    if not config.ENABLE_BOT:
        print("run_market: ENABLE_BOT=false, no se ejecuta.")
    else:
        with track_job_run("run_market"):
            run()
