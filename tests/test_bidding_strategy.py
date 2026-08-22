from datetime import datetime, timedelta, timezone

from engine.bidding_strategy import (
    apply_position_priority,
    decide_bid,
    decide_bids_for_market,
    dynamic_player_cap,
    find_cancel_swap_candidates,
    is_price_worth_bidding,
    max_biddable_amount,
)


def test_max_biddable_amount_respects_all_limits(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "BIDDING_SAFETY_LIMITS",
        {
            "max_spend_per_player_floor": 15_000_000,
            "max_budget_risk_per_matchday_pct": 0.30,
            "min_budget_reserve": 2_000_000,
            "max_premium_over_price_pct": 0.20,
            "max_pct_of_budget_per_player": 0.20,
        },
    )
    # 20M - 2M reserva = 18M usable; 30% de jornada = 5.4M
    assert max_biddable_amount(20_000_000, already_risked_this_matchday=0) == 5_400_000


def test_max_biddable_amount_uses_player_cap_when_lower_than_floor(monkeypatch):
    """Sin player_cap explícito, cae al suelo fijo (comportamiento del antiguo tope)."""
    assert max_biddable_amount(1_000_000_000, already_risked_this_matchday=0, player_cap=3_000_000) == 3_000_000


def test_max_biddable_amount_subtracts_pending_committed_before_anything_else():
    """
    Regresión del bug de saldo negativo (ver README, "Economía de
    Comunio"): pending_committed (ofertas pendientes sin resolver, la
    fuente de verdad real de Comunio) debe restarse ANTES que el resto de
    límites, no ser "uno más" -- si no, el bot podía comprometer más
    dinero del que el saldo real soportaba.
    """
    credit = 20_000_000
    pending = 17_500_000  # casi todo el saldo ya comprometido en ofertas de días anteriores

    cap = max_biddable_amount(credit, already_risked_this_matchday=0, pending_committed=pending)
    peor_caso = credit - pending - cap
    assert peor_caso >= 0, "el peor caso (todo lo pendiente + esta puja ejecutándose a la vez) nunca debe ser negativo"


def test_decide_bid_never_bids_below_player_price():
    """
    Regresión del bug real detectado en producción: el cap de seguridad
    podía recortar el importe por debajo del precio del jugador, y Comunio
    rechazaba la puja. decide_bid() debe abstenerse en vez de mandar una
    oferta condenada a fallar.
    """
    player = {"id": "1", "score": 0.5, "price": 5_000_000}
    # already_risked casi agota el cap de jornada -> cap resultante < price
    decision = decide_bid(player, remaining_budget=20_000_000, already_risked_this_matchday=5_000_000)
    assert decision is None


def test_decide_bid_anchors_amount_to_real_price_plus_premium():
    player = {"id": "1", "score": 0.5, "price": 1_000_000}
    decision = decide_bid(player, remaining_budget=20_000_000, already_risked_this_matchday=0)
    assert decision is not None
    assert decision["amount"] >= player["price"]
    # prima = 0.5 * max_premium_over_price_pct (0.20 por defecto) = 10%
    assert decision["amount"] == int(player["price"] * 1.10)


def test_decide_bid_below_score_threshold_returns_none():
    player = {"id": "1", "score": 0.05, "price": 1_000_000}
    assert decide_bid(player, remaining_budget=20_000_000, already_risked_this_matchday=0, min_score_threshold=0.15) is None


def test_decide_bid_without_real_price_returns_none():
    player = {"id": "1", "score": 0.9, "price": 0}
    assert decide_bid(player, remaining_budget=20_000_000, already_risked_this_matchday=0) is None


def test_decide_bid_rejects_listing_price_way_above_real_value():
    """
    A petición del usuario (2026-08-22): a diferencia del VM (siempre lo
    calcula Futmondo), el precio de salida de un listado lo elige el
    manager vendedor -- un listado pedido muy por encima del VM real (aquí
    +60%, por encima del 50% de config.BIDDING_SAFETY_LIMITS
    ["max_listing_price_over_value_pct"]) debe descartarse sin más, aunque
    la prima calculada sobre el VM diera para pujar.
    """
    player = {"id": "1", "score": 1.0, "price": 1_000_000, "listing_price": 1_600_000}
    assert decide_bid(player, remaining_budget=20_000_000, already_risked_this_matchday=0) is None


def test_decide_bid_allows_listing_price_within_margin_over_real_value():
    """Un precio de salida algo por encima del VM (dentro del margen configurado) no bloquea la puja."""
    player = {"id": "1", "score": 0.5, "price": 1_000_000, "listing_price": 1_300_000}
    decision = decide_bid(player, remaining_budget=20_000_000, already_risked_this_matchday=0)
    assert decision is not None
    assert decision["amount"] == int(player["price"] * 1.10)  # el cálculo sigue anclado al VM, no al precio de salida


def test_decide_bid_ignores_missing_listing_price():
    """Roster/mercado sin `listing_price` informado (None) no debe activar el rechazo."""
    player = {"id": "1", "score": 0.5, "price": 1_000_000}
    assert decide_bid(player, remaining_budget=20_000_000, already_risked_this_matchday=0) is not None


def test_apply_position_priority_boosts_at_risk_position_above_higher_base_score():
    candidates = [
        {"id": "delantero_en_riesgo", "position": "DEL", "score": 0.30},
        {"id": "medio_normal", "position": "MED", "score": 0.35},
    ]
    prioritized = apply_position_priority(candidates, at_risk_positions={"DEL"}, boost=0.15)
    assert prioritized[0]["id"] == "delantero_en_riesgo"
    assert prioritized[0]["position_at_risk"] is True
    assert prioritized[0]["base_score"] == 0.30
    assert prioritized[1]["position_at_risk"] is False


def test_decide_bid_reason_mentions_priority_when_boosted():
    player = {"id": "1", "score": 0.5, "base_score": 0.35, "position_at_risk": True, "price": 1_000_000}
    decision = decide_bid(player, remaining_budget=20_000_000, already_risked_this_matchday=0)
    assert decision["position_at_risk"] is True
    assert "prioridad" in decision["reason"]


def test_apply_position_priority_boosts_candidate_that_would_upgrade_lineup():
    """
    Un candidato que superaría en score de alineación al peor titular
    actual de su posición debe marcarse would_upgrade_lineup y subir de
    score, aunque esa posición no tenga ningún riesgo de plantilla (no está
    en at_risk_positions) -- señal de CALIDAD, distinta de la de CANTIDAD.
    """
    candidates = [
        {"id": "delantero_mejora", "position": "DEL", "score": 0.30, "lineup_score": 0.80},
        {"id": "medio_normal", "position": "MED", "score": 0.35, "lineup_score": 0.10},
    ]
    prioritized = apply_position_priority(
        candidates,
        at_risk_positions=set(),  # ninguna posición en riesgo de cantidad
        upgrade_thresholds={"DEL": 0.5, "MED": 0.5},
        upgrade_boost=0.15,
    )
    assert prioritized[0]["id"] == "delantero_mejora"
    assert prioritized[0]["would_upgrade_lineup"] is True
    assert prioritized[0]["position_at_risk"] is False
    assert prioritized[0]["score"] == 0.30 + 0.15
    assert prioritized[1]["would_upgrade_lineup"] is False


def test_apply_position_priority_treats_none_threshold_as_automatic_upgrade():
    """threshold None (posición sin ningún jugador todavía en plantilla) -> cualquier candidato es mejora."""
    candidates = [{"id": "1", "position": "POR", "score": 0.1, "lineup_score": 0.0}]
    prioritized = apply_position_priority(candidates, at_risk_positions=set(), upgrade_thresholds={"POR": None})
    assert prioritized[0]["would_upgrade_lineup"] is True


def test_apply_position_priority_sums_both_boosts_when_candidate_hits_both():
    candidates = [{"id": "1", "position": "DEF", "score": 0.2, "lineup_score": 0.9}]
    prioritized = apply_position_priority(
        candidates,
        at_risk_positions={"DEF"},
        upgrade_thresholds={"DEF": 0.5},
        boost=0.15,
        upgrade_boost=0.15,
    )
    assert prioritized[0]["position_at_risk"] is True
    assert prioritized[0]["would_upgrade_lineup"] is True
    assert prioritized[0]["score"] == 0.2 + 0.15 + 0.15


def test_decide_bid_reason_mentions_lineup_upgrade_when_boosted():
    player = {
        "id": "1",
        "score": 0.5,
        "base_score": 0.35,
        "would_upgrade_lineup": True,
        "position_at_risk": False,
        "price": 1_000_000,
    }
    decision = decide_bid(player, remaining_budget=20_000_000, already_risked_this_matchday=0)
    assert decision["would_upgrade_lineup"] is True
    assert "mejora el once titular" in decision["reason"]


def test_decide_bids_for_market_accumulates_risk_across_pass():
    """
    Reproduce el comportamiento visto en producción: una primera puja
    (perfectamente pagable por sí sola) puede agotar el cap de jornada,
    dejando sin margen a un segundo candidato que también habría sido
    pagable de haberse evaluado solo, con presupuesto de sobra en total.

    remaining_budget=20M -> usable 18M -> cap de jornada 5.4M.
    Candidato 1 (4M, score 0.5): pide 4.4M, cabe en el cap -> se acepta,
    quedan 5.4M-4.4M=1.0M de margen de jornada.
    Candidato 2 (2M, score 0.4): pediría 2.16M, pero el margen que queda
    (1.0M) ya no llega ni al precio base (2M) -> se descarta, aunque a
    solas SÍ habría cabido de sobra en el cap original de 5.4M.
    """
    ranked = [
        {"id": "primero", "score": 0.5, "price": 4_000_000},
        {"id": "segundo", "score": 0.4, "price": 2_000_000},
    ]
    decisions = decide_bids_for_market(ranked, remaining_budget=20_000_000)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == "primero"


def test_decide_bids_for_market_respects_max_bids():
    ranked = [
        {"id": "1", "score": 0.5, "price": 100_000},
        {"id": "2", "score": 0.4, "price": 100_000},
        {"id": "3", "score": 0.3, "price": 100_000},
    ]
    decisions = decide_bids_for_market(ranked, remaining_budget=20_000_000, max_bids=2)
    assert len(decisions) == 2


def test_dynamic_player_cap_never_goes_below_floor():
    """Plantilla y mercado vacíos (o muy baratos) -> cae al suelo de seguridad, nunca a 0."""
    cap = dynamic_player_cap(remaining_budget=1_000_000, squad=[], market_candidates=[])
    assert cap == 15_000_000


def test_dynamic_player_cap_grows_with_squad_and_market_value():
    """
    Regresión del bug real (2026-08-18): con el tope fijo de 15M, un
    jugador de 20M+ quedaba excluido de pujas para siempre sin importar
    presupuesto ni score. Con plantilla/mercado caros y presupuesto alto,
    el tope dinámico debe superar el antiguo fijo de 15M.
    """
    squad = [{"price": 20_000_000} for _ in range(5)]
    market = [{"price": 25_000_000, "score": 0.8} for _ in range(5)]
    cap = dynamic_player_cap(remaining_budget=100_000_000, squad=squad, market_candidates=market)
    assert cap > 15_000_000


def test_dynamic_player_cap_market_component_weights_by_score_not_plain_price():
    """
    Un outlier carísimo con score bajo (mal rendimiento, a la venta por
    casualidad) no debe desviar el tope tanto como si pesara lo mismo que
    los candidatos realmente atractivos (score alto).
    """
    market_with_outlier = [
        {"price": 2_000_000, "score": 0.8},
        {"price": 2_000_000, "score": 0.8},
        {"price": 100_000_000, "score": 0.01},  # carísimo pero casi sin score
    ]
    market_without_outlier = [
        {"price": 2_000_000, "score": 0.8},
        {"price": 2_000_000, "score": 0.8},
    ]
    squad = [{"price": 2_000_000} for _ in range(3)]
    cap_with_outlier = dynamic_player_cap(remaining_budget=10_000_000, squad=squad, market_candidates=market_with_outlier)
    cap_without_outlier = dynamic_player_cap(
        remaining_budget=10_000_000, squad=squad, market_candidates=market_without_outlier
    )
    # el outlier apenas debe mover el tope (pesa ~0.01 frente a 0.8+0.8)
    assert cap_with_outlier - cap_without_outlier < 2_000_000


def test_dynamic_player_cap_budget_component_scales_with_remaining_budget():
    squad = [{"price": 1_000_000}]
    market = [{"price": 1_000_000, "score": 0.1}]
    cap_low_budget = dynamic_player_cap(remaining_budget=5_000_000, squad=squad, market_candidates=market)
    cap_high_budget = dynamic_player_cap(remaining_budget=500_000_000, squad=squad, market_candidates=market)
    assert cap_high_budget > cap_low_budget


def test_decide_bid_uses_dynamic_player_cap_to_allow_bid_above_old_fixed_cap():
    """
    El caso que el tope fijo de 15M bloqueaba para siempre: un jugador de
    20M con score alto, presupuesto de sobra -> con un player_cap dinámico
    por encima de 20M, decide_bid() SÍ debe pujar.
    """
    player = {"id": "1", "score": 0.9, "price": 20_000_000}
    decision = decide_bid(
        player,
        remaining_budget=200_000_000,
        already_risked_this_matchday=0,
        player_cap=30_000_000,
    )
    assert decision is not None
    assert decision["amount"] >= player["price"]


# --- is_price_worth_bidding / find_cancel_swap_candidates (TODO.md #13) ---


def test_is_price_worth_bidding_rejects_low_score_and_missing_price():
    assert is_price_worth_bidding({"score": 0.9, "price": 0}, min_score_threshold=0.15) is False
    assert is_price_worth_bidding({"score": 0.1, "price": 1_000_000}, min_score_threshold=0.15) is False
    assert is_price_worth_bidding({"score": 0.5, "price": 1_000_000}, min_score_threshold=0.15) is True


def test_is_price_worth_bidding_uses_config_default(monkeypatch):
    import config

    monkeypatch.setattr(config, "BIDDING_MIN_SCORE_THRESHOLD", 0.5)
    assert is_price_worth_bidding({"score": 0.4, "price": 1_000_000}) is False
    assert is_price_worth_bidding({"score": 0.6, "price": 1_000_000}) is True


def _open_bid(player_id, score, bid_id, hours_to_expiry=48, amount=1_000_000):
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    return {
        "local_row_id": 1,
        "player_id": player_id,
        "score": score,
        "amount": amount,
        "bid_id": bid_id,
        "expires_at": None if hours_to_expiry is None else now + timedelta(hours=hours_to_expiry),
    }


NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)


def test_find_cancel_swap_candidates_proposes_swap_when_margin_is_met():
    candidate = {"id": "new", "score": 0.80, "price": 5_000_000}
    worst_open_bid = _open_bid("old", score=0.40, bid_id="bid-old")

    proposals = find_cancel_swap_candidates(
        [candidate], [worst_open_bid], now=NOW, min_margin=0.25, min_hours_before_expiry=6
    )

    assert len(proposals) == 1
    assert proposals[0]["candidate"]["id"] == "new"
    assert proposals[0]["sacrifice"]["bid_id"] == "bid-old"


def test_find_cancel_swap_candidates_respects_min_margin():
    candidate = {"id": "new", "score": 0.55, "price": 5_000_000}  # solo +0.15 sobre la puja abierta
    open_bid = _open_bid("old", score=0.40, bid_id="bid-old")

    proposals = find_cancel_swap_candidates([candidate], [open_bid], now=NOW, min_margin=0.25)

    assert proposals == []


def test_find_cancel_swap_candidates_excludes_bids_expiring_soon():
    candidate = {"id": "new", "score": 0.90, "price": 5_000_000}
    about_to_expire = _open_bid("old", score=0.10, bid_id="bid-old", hours_to_expiry=2)

    proposals = find_cancel_swap_candidates(
        [candidate], [about_to_expire], now=NOW, min_margin=0.25, min_hours_before_expiry=6
    )

    assert proposals == []


def test_find_cancel_swap_candidates_excludes_bids_with_unknown_expiry():
    """Nunca se sacrifica una puja sin fecha de expiración confirmada (ver docstring)."""
    candidate = {"id": "new", "score": 0.90, "price": 5_000_000}
    unknown_expiry = _open_bid("old", score=0.10, bid_id="bid-old", hours_to_expiry=None)

    proposals = find_cancel_swap_candidates([candidate], [unknown_expiry], now=NOW, min_margin=0.25)

    assert proposals == []


def test_find_cancel_swap_candidates_picks_the_worst_open_bid_first():
    candidate = {"id": "new", "score": 0.90, "price": 5_000_000}
    ok_bid = _open_bid("mid", score=0.50, bid_id="bid-mid")
    worst_bid = _open_bid("worst", score=0.10, bid_id="bid-worst")

    proposals = find_cancel_swap_candidates([candidate], [ok_bid, worst_bid], now=NOW, min_margin=0.25)

    assert len(proposals) == 1
    assert proposals[0]["sacrifice"]["bid_id"] == "bid-worst"


def test_find_cancel_swap_candidates_respects_max_swaps_and_never_reuses_a_bid():
    candidates = [
        {"id": "a", "score": 0.95, "price": 1_000_000},
        {"id": "b", "score": 0.90, "price": 1_000_000},
        {"id": "c", "score": 0.85, "price": 1_000_000},
    ]
    open_bids = [
        _open_bid("x", score=0.10, bid_id="bid-x"),
        _open_bid("y", score=0.20, bid_id="bid-y"),
    ]

    proposals = find_cancel_swap_candidates(candidates, open_bids, now=NOW, min_margin=0.25, max_swaps=2)

    assert len(proposals) == 2
    used_bid_ids = {p["sacrifice"]["bid_id"] for p in proposals}
    assert used_bid_ids == {"bid-x", "bid-y"}  # nunca la misma puja para dos candidatos
    candidate_ids = {p["candidate"]["id"] for p in proposals}
    assert candidate_ids == {"a", "b"}  # los dos mejores candidatos, no "c"


def test_find_cancel_swap_candidates_uses_config_defaults(monkeypatch):
    import config

    monkeypatch.setattr(config, "BIDDING_CANCEL_SWAP_MIN_MARGIN", 0.5)
    monkeypatch.setattr(config, "BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY", 6)
    monkeypatch.setattr(config, "BIDDING_MAX_CANCEL_SWAPS_PER_RUN", 1)

    candidate = {"id": "new", "score": 0.60, "price": 1_000_000}  # +0.20, no llega al margen de 0.5
    open_bid = _open_bid("old", score=0.40, bid_id="bid-old")

    assert find_cancel_swap_candidates([candidate], [open_bid], now=NOW) == []
