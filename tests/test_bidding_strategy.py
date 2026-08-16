from engine.bidding_strategy import (
    apply_position_priority,
    decide_bid,
    decide_bids_for_market,
    max_biddable_amount,
)


def test_max_biddable_amount_respects_all_limits(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "BIDDING_SAFETY_LIMITS",
        {
            "max_spend_per_player": 15_000_000,
            "max_budget_risk_per_matchday_pct": 0.30,
            "min_budget_reserve": 2_000_000,
            "max_premium_over_price_pct": 0.20,
        },
    )
    # 20M - 2M reserva = 18M usable; 30% de jornada = 5.4M
    assert max_biddable_amount(20_000_000, already_risked_this_matchday=0) == 5_400_000


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
