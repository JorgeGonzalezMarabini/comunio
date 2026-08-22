"""
Estrategia de pujas: decide a quién pujar y cuánto, dado el score de
engine/evaluator.py y el presupuesto restante.

Estos límites de seguridad son innegociables: ninguna decisión del modelo
puede saltárselos, sea cual sea el score calculado. El objetivo es que un
fallo del modelo (bug, datos corruptos, score mal calculado) nunca pueda
fundir el equipo.
"""
from __future__ import annotations

from datetime import timedelta

import config


class BudgetExceededError(Exception):
    """Una decisión intentó saltarse un límite de seguridad configurado."""


def _weighted_average(values_weights: list[tuple[float, float]]) -> float:
    """
    Media ponderada de `[(valor, peso), ...]`. Si la suma de pesos es 0
    (todos los pesos son 0, p. ej. ningún candidato con score positivo) cae
    a la media simple en vez de dividir por cero. Lista vacía -> 0.0.
    """
    if not values_weights:
        return 0.0
    weight_sum = sum(w for _, w in values_weights)
    if weight_sum <= 0:
        return sum(v for v, _ in values_weights) / len(values_weights)
    return sum(v * w for v, w in values_weights) / weight_sum


def dynamic_player_cap(
    remaining_budget: int,
    squad: list[dict],
    market_candidates: list[dict],
    weights: dict = None,
) -> int:
    """
    Tope dinámico por jugador — sustituye al antiguo tope FIJO de 15M
    (`config.BIDDING_SAFETY_LIMITS["max_spend_per_player_floor"]`, que
    ahora es solo el suelo, ver docstring en config.py). Un número fijo en
    euros se queda obsoleto con el tiempo: el valor de los jugadores en
    Futmondo sube con el rendimiento a lo largo de la temporada, así que un
    tope fijo bloquea cada vez a más jugadores (los mejores, normalmente)
    aunque el presupuesto disponible también haya crecido — `decide_bid()`
    nunca puja por debajo del precio real, así que un jugador con precio
    por encima del tope queda excluido de pujas para siempre, sin importar
    su score.

    Combina tres señales (`config.BIDDING_DYNAMIC_CAP_WEIGHTS`, deben sumar
    1.0), cada una capturando una noción distinta de "qué es razonable
    pujar por UN jugador ahora mismo":

      - squad_value: precio medio de TU plantilla actual (`squad`, espera
        "price" por jugador) — escala con el crecimiento real de tu
        equipo; estable porque no depende de qué haya a la venta hoy.
      - market_value: precio medio de `market_candidates` PONDERADO por
        `score` (no precio simple, espera "price" y "score" por
        candidato) — sigue la inflación general del mercado sin dejar que
        un jugador carísimo con score bajo (mal rendimiento, lesión, a la
        venta por casualidad un día concreto) descalibre el tope; los
        candidatos realmente atractivos (score alto) pesan más en la
        media. Scores negativos (penalización por lesión) se tratan como 0
        de peso, no restan.
      - budget_pct: `remaining_budget * max_pct_of_budget_per_player` — %
        del saldo disponible ahora, ligado a lo que de verdad se puede
        permitir hoy.

    El resultado nunca baja del suelo de seguridad
    (`max_spend_per_player_floor`) — protege casos degenerados (plantilla
    o mercado vacíos/muy baratos, típico al empezar la temporada) sin
    abrir una vía para gastar de más: `max_biddable_amount()` sigue
    aplicando DESPUÉS los topes de jornada y saldo usable, que no cambian.
    """
    weights = weights or config.BIDDING_DYNAMIC_CAP_WEIGHTS
    limits = config.BIDDING_SAFETY_LIMITS

    squad_prices = [p["price"] for p in squad if p.get("price")]
    avg_squad_value = sum(squad_prices) / len(squad_prices) if squad_prices else 0.0

    market_priced = [
        (p["price"], max(p.get("score", 0.0), 0.0)) for p in market_candidates if p.get("price")
    ]
    avg_market_value = _weighted_average(market_priced)

    budget_component = max(0, remaining_budget) * limits["max_pct_of_budget_per_player"]

    blended = (
        weights["squad_value"] * avg_squad_value
        + weights["market_value"] * avg_market_value
        + weights["budget_pct"] * budget_component
    )
    return max(limits["max_spend_per_player_floor"], int(blended))


def max_biddable_amount(
    remaining_budget: int,
    already_risked_this_matchday: int,
    pending_committed: int = 0,
    player_cap: int = None,
) -> int:
    """
    Calcula el máximo que se puede pujar ahora mismo respetando:
      - el tope por jugador (dinámico, ver dynamic_player_cap() — o su
        suelo fijo si no se pasa `player_cap`)
      - el % máximo de presupuesto arriesgable en la jornada
      - la reserva mínima que nunca se toca
      - el dinero YA comprometido en ofertas pendientes sin resolver

    `player_cap`: tope por jugador ya calculado (ver dynamic_player_cap()).
    Si no se pasa, cae a `config.BIDDING_SAFETY_LIMITS
    ["max_spend_per_player_floor"]` — mismo comportamiento que el antiguo
    tope fijo, para no romper llamadas/tests que no calculan el dinámico.

    `pending_committed`: aproximación a TODAS tus pujas de compra todavía
    pendientes (sin resolver), sea de hoy o de días anteriores — ver
    db.models.get_pending_bid_amount() cruzado con clients.futmondo_client.
    real_pending_bid_amount() (TODO.md #3, resuelto: el segundo SÍ viene
    confirmado del propio Futmondo, vía el campo "bid" de get_market()).
    En Comunio esto estaba confirmado por su propia FAQ oficial (saldo
    negativo al cierre de jornada = 0 puntos esa jornada entera); en
    Futmondo no se ha encontrado esa regla exacta de penalización por
    saldo negativo en una fuente oficial — se mantiene la misma resta
    defensiva por precaución: si el saldo (`budget`/`information`) tampoco
    se descuenta hasta resolver el mercado (razonable asumirlo, visto que
    el `budget` que devuelve `/2/userteam/changeplayer` no cambia al
    pujar), un bot que ignore esto podría comprometer más de lo que el
    saldo real soporta.
    """
    limits = config.BIDDING_SAFETY_LIMITS
    if player_cap is None:
        player_cap = limits["max_spend_per_player_floor"]

    truly_available = max(0, remaining_budget - pending_committed)
    usable_budget = max(0, truly_available - limits["min_budget_reserve"])
    matchday_cap = int(usable_budget * limits["max_budget_risk_per_matchday_pct"])
    matchday_remaining = max(0, matchday_cap - already_risked_this_matchday)

    return min(player_cap, matchday_remaining, usable_budget)


def apply_position_priority(
    candidates: list[dict],
    at_risk_positions: set,
    boost: float = None,
    upgrade_thresholds: dict = None,
    upgrade_boost: float = None,
) -> list[dict]:
    """
    Da prioridad a los candidatos de mercado por dos señales distintas,
    que pueden coincidir en el mismo candidato y sumarse:

    1. Riesgo de plantilla (ver engine.squad_risk.assess_squad_depth):
       posiciones sin ningún suplente sano, donde perder un titular más
       dejaría un hueco en la alineación y -4 puntos (ver README). Sube el
       score `boost` (config.BIDDING_POSITION_RISK_BOOST). Señal de
       CANTIDAD: da igual lo bueno o malo que sea el titular actual, lo
       que falta es un cuerpo de más en el banquillo.

    2. Mejora del once (ver engine.squad_risk.weakest_starter_scores):
       `upgrade_thresholds` es {position: score|None} con el score (con
       config.LINEUP_EVALUATOR_WEIGHTS) del titular más flojo de cada
       posición HOY. Cada candidato necesita también un campo
       "lineup_score" — ese mismo candidato evaluado con esos MISMOS
       pesos (no el "score" de puja que ya trae, que valora precio y no es
       comparable: mezclar unidades distintas daría una comparación sin
       sentido). Si `lineup_score` supera el listón (o el listón es None —
       plantilla sin nadie todavía en esa posición), sube el score
       `upgrade_boost` (config.BIDDING_UPGRADE_BOOST). Señal de CALIDAD:
       el titular actual ya existe, pero fichar a este candidato y
       sentarlo en el banquillo mejoraría el once real, no solo el margen
       de suplentes.

    Ninguna de las dos fuerza la puja de un candidato realmente malo (los
    boosts son aditivos, no multiplican ni ignoran el umbral mínimo de
    score) — solo dan ventaja frente a otro candidato de score similar en
    una posición sin ese problema.

    Guarda el score original en "base_score" (para la auditoría) y marca
    "position_at_risk"/"would_upgrade_lineup": bool en cada candidato.
    Devuelve la lista reordenada por score ajustado (mayor primero) —
    decide_bids_for_market espera la lista ya en este orden.
    """
    boost = config.BIDDING_POSITION_RISK_BOOST if boost is None else boost
    upgrade_boost = config.BIDDING_UPGRADE_BOOST if upgrade_boost is None else upgrade_boost
    upgrade_thresholds = upgrade_thresholds or {}

    adjusted = []
    for c in candidates:
        base_score = c.get("score", 0.0)
        is_at_risk = c.get("position") in at_risk_positions

        would_upgrade = False
        if c.get("position") in upgrade_thresholds:
            threshold = upgrade_thresholds[c["position"]]
            would_upgrade = threshold is None or c.get("lineup_score", base_score) > threshold

        score = base_score
        if is_at_risk:
            score += boost
        if would_upgrade:
            score += upgrade_boost

        adjusted.append(
            {
                **c,
                "base_score": base_score,
                "score": score,
                "position_at_risk": is_at_risk,
                "would_upgrade_lineup": would_upgrade,
            }
        )
    return sorted(adjusted, key=lambda p: p["score"], reverse=True)


def decide_bid(
    player: dict,
    remaining_budget: int,
    already_risked_this_matchday: int,
    min_score_threshold: float = None,
    pending_committed: int = 0,
    player_cap: int = None,
) -> dict | None:
    """
    Decide si pujar por `player` (debe incluir "id", "score" de
    engine/evaluator.py, y "price" — el VM/precio real de Futmondo, ver
    db.models.get_player_features) y cuánto, respetando los límites de
    seguridad.

    El importe se ancla al precio/VM REAL del jugador, no a una fracción
    arbitraria del presupuesto: se puja el VM + una prima que crece con el
    score (config.BIDDING_SAFETY_LIMITS["max_premium_over_price_pct"]),
    topado siempre por max_biddable_amount(). Un jugador con score 0 no se
    puja por encima de su VM; uno con score 1.0 se puja hasta el máximo de
    prima configurado.

    `player["listing_price"]` (a petición del usuario, 2026-08-22): a
    diferencia del VM, que siempre lo calcula Futmondo, el precio de SALIDA
    de un listado lo elige quien pone al jugador en venta -- otro manager,
    o el "Computer" (ver db.models/jobs.sync_data) -- así que nada garantiza
    que se acerque al VM real. Si viene informado (candidato de mercado) y
    supera el VM en más de
    config.BIDDING_SAFETY_LIMITS["max_listing_price_over_value_pct"], se
    descarta el candidato sin más: perseguir un precio de salida inflado no
    tiene sentido aunque la prima calculada sobre el VM real diera para
    cubrirlo. Ausente (None, roster propio) o por debajo de ese tope no
    cambia nada del cálculo de abajo.

    Si `player` viene de apply_position_priority() (tiene "position_at_risk"/
    "would_upgrade_lineup" y "base_score"), la razón auditada deja
    constancia de qué boost(s) ya incluye el score — para poder revisar
    después por qué se pujó por ese jugador en concreto.

    `pending_committed`: ver max_biddable_amount() — dinero ya comprometido
    en pujas pendientes sin resolver (protección defensiva de saldo, ver
    README).

    `player_cap`: ver max_biddable_amount()/dynamic_player_cap() — tope
    dinámico por jugador ya calculado para esta pasada del mercado.

    Devuelve None si no se debe pujar, o un dict:
        {"player_id": ..., "amount": ..., "score": ..., "reason": "..."}
    listo para persistir en la tabla `bids` (auditoría).
    """
    min_score_threshold = config.BIDDING_MIN_SCORE_THRESHOLD if min_score_threshold is None else min_score_threshold

    score = player.get("score", 0)
    if score < min_score_threshold:
        return None

    price = player.get("price") or 0
    if price <= 0:
        # Sin precio de referencia real no hay base segura para calcular
        # una puja — mejor no pujar que inventar un importe a ciegas.
        return None

    listing_price = player.get("listing_price") or 0
    if listing_price > price * (1 + config.BIDDING_SAFETY_LIMITS["max_listing_price_over_value_pct"]):
        # El vendedor (otro manager, o el "Computer") pide muy por encima
        # del VM real -- no perseguir un precio de salida inflado, ver
        # docstring de arriba.
        return None

    premium_pct = max(0.0, score) * config.BIDDING_SAFETY_LIMITS["max_premium_over_price_pct"]
    desired_amount = int(price * (1 + premium_pct))

    cap = max_biddable_amount(remaining_budget, already_risked_this_matchday, pending_committed, player_cap=player_cap)
    if cap <= 0:
        return None

    amount = min(desired_amount, cap)

    # Bug real detectado en producción con Comunio (2026-08-15, ver
    # historial de commits): el cap de seguridad puede recortar `amount`
    # por debajo del precio/VM real del jugador (p.ej. si el presupuesto
    # de jornada ya está casi agotado por pujas anteriores en la misma
    # pasada) — Comunio rechazaba esas pujas por ir por debajo del precio;
    # de Futmondo no se ha confirmado el mismo rechazo exacto, pero mejor
    # no pujar que mandar una oferta con toda probabilidad condenada a
    # fallar: si el cap no llega ni al precio base, no hay margen seguro
    # para pujar por este jugador en este momento.
    if amount < price:
        return None

    boosts_applied = []
    if player.get("position_at_risk"):
        boosts_applied.append("prioridad riesgo de plantilla")
    if player.get("would_upgrade_lineup"):
        boosts_applied.append("mejora el once titular")

    if boosts_applied:
        score_note = f"score={score:.3f} (base={player.get('base_score', score):.3f} + " + " + ".join(boosts_applied) + ")"
    else:
        score_note = f"score={score:.3f}"

    return {
        "player_id": player["id"],
        "amount": amount,
        "score": score,
        "position_at_risk": bool(player.get("position_at_risk")),
        "would_upgrade_lineup": bool(player.get("would_upgrade_lineup")),
        "reason": (
            f"{score_note} >= umbral={min_score_threshold}; "
            f"precio_base={price}" + (f" (precio_salida={listing_price})" if listing_price else "") + ", "
            f"prima={premium_pct:.1%} -> deseado={desired_amount}; "
            f"cap_seguro={cap} -> puja_final={amount}"
        ),
    }


def decide_bids_for_market(
    ranked_candidates: list[dict],
    remaining_budget: int,
    already_risked_this_matchday: int = 0,
    min_score_threshold: float = None,
    max_bids: int = None,
    pending_committed: int = 0,
    player_cap: int = None,
) -> list[dict]:
    """
    Recorre `ranked_candidates` (ya ordenados por score descendente, ver
    engine.evaluator.rank_players/evaluate_players) y decide una puja por
    cada uno mientras haya margen de seguridad.

    `already_risked_this_matchday`: importe ya arriesgado ANTES de esta
    llamada dentro del ritmo de gasto por jornada (pacing, no protección de
    saldo — ver db.models.get_bids_risked_today). Se acumula además el
    riesgo de las pujas decididas en esta misma pasada, así que ni una sola
    llamada ni varias llamadas en la misma jornada pueden superar el límite
    configurado entre todas.

    `pending_committed`: la protección de saldo (ver TODO.md #3, resuelto)
    — TODAS las pujas de compra pendientes sin resolver ahora mismo,
    combinando `db.models.get_pending_bid_amount()` (auditoría local) con
    `clients.futmondo_client.real_pending_bid_amount()` (confirmado del
    propio Futmondo vía el campo "bid" de get_market()), no solo las de
    hoy. Se pasa tal cual a cada decide_bid() por si Futmondo tampoco
    descuenta el saldo hasta que una puja se resuelve (razonable asumirlo,
    sin confirmar la regla exacta de penalización por saldo negativo — ver
    README).

    `player_cap`: tope dinámico por jugador ya calculado para esta pasada
    (ver dynamic_player_cap()) — se pasa tal cual a cada decide_bid().
    """
    decisions = []
    risked = already_risked_this_matchday
    for player in ranked_candidates:
        if max_bids is not None and len(decisions) >= max_bids:
            break
        decision = decide_bid(
            player, remaining_budget, risked, min_score_threshold, pending_committed=pending_committed, player_cap=player_cap
        )
        if decision is None:
            continue
        decisions.append(decision)
        risked += decision["amount"]
    return decisions


def is_price_worth_bidding(player: dict, min_score_threshold: float = None) -> bool:
    """
    Repite SOLO los dos primeros checks de decide_bid() (score y precio),
    sin el cap de presupuesto/tope -- para distinguir un candidato
    "bueno pero bloqueado por límite" de uno simplemente malo. Ver
    find_cancel_swap_candidates(), que usa esto para decidir qué
    candidatos merece la pena considerar para un swap (cancelar una puja
    floja para poder pujar por este) en vez de descartarlos sin más.

    Deliberadamente NO calcula `cap`/`amount` — solo evalúa si el
    candidato PASARÍA esos dos filtros si hubiera presupuesto/tope
    suficiente, no si de hecho lo hay.
    """
    min_score_threshold = config.BIDDING_MIN_SCORE_THRESHOLD if min_score_threshold is None else min_score_threshold
    if player.get("score", 0) < min_score_threshold:
        return False
    return (player.get("price") or 0) > 0


def find_cancel_swap_candidates(
    blocked_candidates: list[dict],
    sacrificable_bids: list[dict],
    now,
    min_margin: float = None,
    min_hours_before_expiry: float = None,
    max_swaps: int = None,
) -> list[dict]:
    """
    Decide qué pujas abiertas merece la pena cancelar para poder pujar por
    un candidato mejor bloqueado por presupuesto/tope o por plazas de
    plantilla (ver TODO.md #13 y clients.futmondo_client.cancel_bid).
    Cancelar+pujar es neutro en plazas (1 puja abierta menos, 1 puja nueva
    más), así que sirve igual sea cual sea la razón concreta del bloqueo.
    Función pura: no llama a Futmondo ni toca la BD -- jobs/run_market.py
    ejecuta las propuestas.

    `blocked_candidates`: candidatos ya rankeados (score descendente, mismo
    orden que decide_bids_for_market) que `is_price_worth_bidding()` acepta
    pero que NO están en las decisiones de esta pasada -- bloqueados por
    límite (presupuesto, tope dinámico o `max_bids`/plazas de plantilla,
    ver jobs/run_market.py), no por calidad. Cada uno necesita
    "id"/"score"/"price".

    `sacrificable_bids`: pujas locales abiertas con su id REAL de oferta de
    Futmondo ya resuelto por el llamador (cruzando `db.models.get_open_bids()`
    con el campo `"bid"` de cada item de `get_market()` -- ver docstring de
    `clients.futmondo_client.real_pending_bid_amount()`). Cada una necesita
    "player_id"/"score"/"bid_id"/"expires_at" (datetime consciente de zona
    horaria, o None si se desconoce -- se trata como NO sacrificable por
    precaución, nunca se cancela sin poder confirmar cuánto le queda).

    `now`: hora actual INYECTADA (no datetime.now() aquí dentro) para que
    esto siga siendo una función pura y testeable sin reloj real.

    Nunca ofrece la misma puja para más de un candidato, ni más de
    `max_swaps` propuestas en total (config.BIDDING_MAX_CANCEL_SWAPS_PER_RUN
    por defecto — deliberadamente 1 mientras esta feature no tiene
    histórico real, ver config.py).

    Devuelve una lista de `{"candidate": ..., "sacrifice": ..., "reason": ...}`
    en el mismo orden (mejor candidato primero) -- jobs/run_market.py decide
    qué hacer si la cancelación o la puja posterior fallan a medias.
    """
    min_margin = config.BIDDING_CANCEL_SWAP_MIN_MARGIN if min_margin is None else min_margin
    min_hours_before_expiry = (
        config.BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY
        if min_hours_before_expiry is None
        else min_hours_before_expiry
    )
    max_swaps = config.BIDDING_MAX_CANCEL_SWAPS_PER_RUN if max_swaps is None else max_swaps

    min_remaining = timedelta(hours=min_hours_before_expiry)
    eligible = [
        b
        for b in sacrificable_bids
        if b.get("expires_at") is not None and (b["expires_at"] - now) >= min_remaining
    ]
    eligible.sort(key=lambda b: b["score"])  # la más floja primero

    proposals = []
    used_bid_ids = set()
    for candidate in blocked_candidates:
        if len(proposals) >= max_swaps:
            break
        for sacrifice in eligible:
            if sacrifice["bid_id"] in used_bid_ids:
                continue
            if candidate.get("score", 0) - sacrifice["score"] < min_margin:
                # `eligible` está ordenada por score ascendente: si esta ni
                # siquiera llega al margen, ninguna peor lo hará tampoco
                # para este candidato -- probamos con la siguiente puja más
                # floja igualmente por si acaso el orden de scores tiene
                # empates, pero en la práctica corta rápido.
                continue
            proposals.append(
                {
                    "candidate": candidate,
                    "sacrifice": sacrifice,
                    "reason": (
                        f"candidato {candidate.get('id')} score={candidate.get('score', 0):.3f} supera "
                        f"en >= {min_margin} a la puja abierta sobre {sacrifice['player_id']} "
                        f"(score={sacrifice['score']:.3f}); bloqueado por límite de presupuesto/tope o "
                        "de plazas en plantilla, no por calidad"
                    ),
                }
            )
            used_bid_ids.add(sacrifice["bid_id"])
            break
    return proposals


def find_reprice_down_candidates(
    open_bids: list[dict],
    remaining_budget: int,
    already_risked_this_matchday: int,
    now,
    pending_committed: int = 0,
    player_cap: int = None,
    min_drop_pct: float = None,
    min_hours_before_expiry: float = None,
    max_reprices: int = None,
) -> list[dict]:
    """
    Decide qué pujas abiertas conviene reajustar A LA BAJA porque el VM del
    jugador ha caído desde que se pujó (a petición del usuario,
    2026-08-22). `decide_bid()` ancla el importe al VM/precio real DEL
    MOMENTO en que se decide -- una vez colocada, la puja queda "congelada"
    para siempre (Futmondo no tiene endpoint confirmado para editar el
    importe de una oferta ya abierta: modifybid/modifyrosterbid/modifyprice
    aparecen solo como hallazgo sin implementar en el bundle de la app, ver
    clients.futmondo_client; y pujar otra vez sobre el mismo jugador no lo
    actualiza tampoco, ver real_pending_bid_amount()). La única forma real
    de bajar el importe es cancelar la puja vieja y colocar una nueva más
    barata -- mismo mecanismo que find_cancel_swap_candidates() (TODO.md
    #13), pero aquí el gatillo es comparar lo ya pujado contra lo que
    decide_bid() pujaría HOY por el MISMO jugador con su VM/score actuales,
    no una comparación contra otro candidato distinto. Función pura: no
    llama a Futmondo ni toca la BD -- jobs/run_market.py ejecuta las
    propuestas.

    `open_bids`: una entrada por puja local abierta, cada una el candidato
    FRESCO ya evaluado con los datos de HOY (mismo "price"/"score" -- con
    los boosts de apply_position_priority ya aplicados -- que usaría
    decide_bid() si se pujara desde cero ahora mismo, ver jobs/
    run_market.py) fusionado con "amount" (importe ya pujado),
    "local_row_id" (fila local en `bids`), "bid_id" (id REAL de oferta de
    Futmondo, confirmado en el `get_market()` de esta misma pasada -- nunca
    se cancela sin él) y "expires_at" (datetime consciente de zona horaria,
    o None si no se pudo confirmar -- se trata como NO reajustable por
    precaución, igual que en find_cancel_swap_candidates).

    `now`: hora actual INYECTADA (no datetime.now() aquí dentro) para que
    esto siga siendo una función pura y testeable sin reloj real.

    `remaining_budget`/`already_risked_this_matchday`/`pending_committed`/
    `player_cap`: se pasan tal cual a cada decide_bid() -- mismos límites
    de seguridad que rigen una puja nueva, ver max_biddable_amount().

    Nunca propone reajustar AL ALZA (si el VM ha subido, o no ha caído lo
    bastante, se deja la puja tal cual) ni cancelar sin más un candidato
    que ya no merezca la pena con los datos de hoy (decide_bid() devuelve
    None: score por debajo del umbral, o el cap de seguridad no llega ni al
    precio base) -- esto SOLO baja un importe ya pujado, nunca decide si
    seguir pujando por ese jugador en absoluto.

    Prioriza (si `max_reprices` corta antes de llegar a todas) las pujas
    más alejadas del VM actual en términos absolutos (`amount - price`),
    no el orden de `open_bids` recibido.

    Devuelve una lista de propuestas `{"player_id", "local_row_id",
    "bid_id", "old_amount", "new_decision", "reason"}` en ese mismo orden
    de prioridad -- jobs/run_market.py decide qué hacer si la cancelación o
    la puja posterior fallan a medias.
    """
    min_drop_pct = config.BIDDING_REPRICE_DOWN_MIN_DROP_PCT if min_drop_pct is None else min_drop_pct
    min_hours_before_expiry = (
        config.BIDDING_REPRICE_DOWN_MIN_HOURS_BEFORE_EXPIRY
        if min_hours_before_expiry is None
        else min_hours_before_expiry
    )
    max_reprices = config.BIDDING_MAX_REPRICE_DOWNS_PER_RUN if max_reprices is None else max_reprices

    min_remaining = timedelta(hours=min_hours_before_expiry)
    ordered = sorted(open_bids, key=lambda b: b["amount"] - (b.get("price") or 0), reverse=True)

    proposals = []
    risked = already_risked_this_matchday
    for b in ordered:
        if len(proposals) >= max_reprices:
            break
        if b.get("expires_at") is None or (b["expires_at"] - now) < min_remaining:
            # Sin fecha de expiración confirmada, o a punto de resolverse --
            # mismo criterio de precaución que find_cancel_swap_candidates.
            continue
        new_decision = decide_bid(
            b, remaining_budget, risked, pending_committed=pending_committed, player_cap=player_cap
        )
        if new_decision is None:
            continue  # ya no merece la pena pujar en absoluto -- fuera de alcance, no se toca
        if new_decision["amount"] >= b["amount"]:
            continue  # el VM no ha bajado (lo bastante) -- nunca reajustar al alza ni por ruido
        drop_pct = (b["amount"] - new_decision["amount"]) / b["amount"]
        if drop_pct < min_drop_pct:
            continue
        proposals.append(
            {
                "player_id": b["id"],
                "local_row_id": b["local_row_id"],
                "bid_id": b["bid_id"],
                "old_amount": b["amount"],
                "new_decision": new_decision,
                "reason": (
                    f"VM actual={b.get('price')}; puja vigente={b['amount']} -> recalculada hoy con "
                    f"decide_bid()={new_decision['amount']} (-{drop_pct:.1%}, umbral={min_drop_pct:.1%})"
                ),
            }
        )
        risked += new_decision["amount"]
    return proposals
