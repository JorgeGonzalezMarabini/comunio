from datetime import datetime, timedelta, timezone

from engine.selling_strategy import apply_revaluation_premium, compute_revaluation_premium_pct, decide_sales

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)


def _price(days_ago, price):
    date = (NOW - timedelta(days=days_ago)).isoformat()
    return {"date": date, "price": price}

# _full_442_squad(): índices fijos para que los tests puedan referenciar
# jugadores concretos sin ambigüedad.
#   [0]        portero
#   [1..4]     defensas (Defensa0..3)
#   [5..8]     centrocampistas (Medio0..3)
#   [9..10]    delanteros (Delantero0..1)


def _full_442_squad():
    """
    Plantilla 4-4-2 base (todos sanos, ninguno en `bought_by_bot` -> ningún
    candidato de venta por sí sola, ver TODO.md #4/engine/selling_strategy.py:
    solo son candidatos los jugadores presentes en `bought_by_bot`, el
    registro local de pujas ganadas por el bot -- ya no se mira `buyPrice`
    de Futmondo, que no distingue "comprado por el bot" de "plantilla
    inicial") sobre la que cada test modifica sus propios casos.
    """
    squad = [{"id": 1, "name": "Portero", "role": "portero", "status": "", "value": 500_000}]
    squad += [
        {"id": 10 + i, "name": f"Defensa{i}", "role": "defensa", "status": "", "value": 500_000}
        for i in range(4)
    ]
    squad += [
        {"id": 20 + i, "name": f"Medio{i}", "role": "centrocampista", "status": "", "value": 500_000}
        for i in range(4)
    ]
    squad += [
        {"id": 30 + i, "name": f"Delantero{i}", "role": "delantero", "status": "", "value": 500_000}
        for i in range(2)
    ]
    return squad


def test_decide_sales_ignores_players_not_bought_by_bot():
    squad = _full_442_squad()
    assert decide_sales(squad, formation="4-4-2") == []


def test_decide_sales_ignores_buy_price_field_entirely():
    """
    Núcleo del arreglo del TODO #4: un `buyPrice > 0` en el roster (como lo
    tendría un jugador de la plantilla inicial, ver clients/futmondo_client.py)
    ya NO basta para ser candidato -- solo cuenta `bought_by_bot`.
    """
    squad = _full_442_squad()
    squad[5]["buyPrice"] = 300_000  # Medio0: buyPrice>0 pero nunca lo compró el bot
    squad[5]["value"] = 900_000  # +200% si se mirara buyPrice
    assert decide_sales(squad, formation="4-4-2") == []


def test_decide_sales_below_profit_threshold_is_ignored():
    squad = _full_442_squad()
    bought_by_bot = {"20": 500_000}  # Medio0 (id=20)
    squad[5]["value"] = 520_000  # +4%, bajo el 10% por defecto
    assert (
        decide_sales(squad, formation="4-4-2", min_profit_pct=0.10, bought_by_bot=bought_by_bot) == []
    )


def test_decide_sales_blocks_sale_that_would_leave_position_uncovered():
    """
    Regresión del fallo real detectado: vender es tan capaz de dejar una
    posición sin cobertura (-4 puntos) como una cláusula de rescisión.
    El portero es el único de su posición -> NUNCA debe venderse aunque
    tenga mucha plusvalía.
    """
    squad = _full_442_squad()
    bought_by_bot = {"1": 300_000}  # portero (id=1) comprado barato, ahora vale 500k (+66%)

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot)
    assert decisions == []


def test_decide_sales_allows_sale_when_position_has_spare_bench():
    squad = _full_442_squad()
    bought_by_bot = {"20": 700_000}  # Medio0 (id=20), MED tiene sobra (4 para 4)... añado un 5º abajo
    squad[5]["value"] = 900_000  # +28.5%
    squad.append({"id": 25, "name": "Medio Extra", "role": "centrocampista", "status": "", "value": 500_000})
    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 20
    assert decisions[0]["purchase_price"] == 700_000
    assert decisions[0]["profit"] == 200_000


def test_decide_sales_prioritizes_most_profitable_when_bench_is_scarce():
    """Dos candidatos rentables en la misma posición, pero sin margen -> ninguno se vende; con 1 de margen, gana el más rentable."""
    squad = _full_442_squad()
    bought_by_bot = {
        "10": 1_000_000,  # Defensa0: comprado a 1M
        "11": 1_000_000,  # Defensa1: comprado a 1M
    }
    squad[1]["value"] = 2_000_000  # Defensa0, ahora 2M, +100% -- el más rentable
    squad[2]["value"] = 1_200_000  # Defensa1, ahora 1.2M, +20% -- menos rentable

    # DEF: 4 disponibles para 4 titulares -> bench=0, SIN margen -- ningún defensa debe venderse
    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot)
    assert decisions == []

    # Añado un 5º defensa de sobra -> ahora hay bench=1, solo cabe 1 venta
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 10  # Defensa0, el más rentable (+100%), gana el único hueco


def test_decide_sales_allows_selling_injured_profitable_player_freely():
    """Un lesionado no contaba como 'disponible' para cubrir su posición -> venderlo no empeora nada."""
    squad = _full_442_squad()
    squad[9]["status"] = "injured"  # Delantero0
    bought_by_bot = {str(squad[9]["id"]): 300_000}
    squad[9]["value"] = 500_000  # +66%, pero lesionado

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]


# --- Corte de pérdidas: un único umbral para TODOS los estados (a
# petición del usuario, 2026-08-22 -- tercera vuelta de este mismo cambio:
# primero solo lesión confirmada, luego genérico + un umbral más bajo
# exclusivo de lesión confirmada, ahora unificado del todo, ver docstring
# del módulo) ---
#
# En todos estos tests: min_profit_pct=0.10, max_loss_pct=0.10 -- el mismo
# umbral, sea cual sea el estado del jugador (sano, duda o lesión
# confirmada).


def test_decide_sales_ignores_loss_below_the_unified_threshold_regardless_of_status():
    """Una pérdida pequeña (-4%, por debajo del umbral único del 10%) no activa el corte -- aquí, para un jugador sano."""
    squad = _full_442_squad()
    bought_by_bot = {str(squad[9]["id"]): 500_000}
    squad[9]["value"] = 480_000  # -4%: ni +10% de rentabilidad ni -10% de corte

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.10, max_loss_pct=0.10)
    assert decisions == []


def test_decide_sales_ignores_small_loss_below_any_threshold():
    """
    Una pérdida pequeña (por debajo del umbral de corte) no activa el
    corte de pérdidas -- pero como sigue siendo lesión CONFIRMADA, se
    vende igualmente por la vía de venta incondicional (ver más abajo).
    """
    squad = _full_442_squad()
    squad[9]["status"] = "injured2"  # Delantero0, lesión confirmada
    bought_by_bot = {str(squad[9]["id"]): 500_000}
    squad[9]["value"] = 480_000  # -4%: ni +10% de rentabilidad ni -10% de corte

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.10, max_loss_pct=0.10)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    assert "se vende siempre" in decisions[0]["reason"]


def test_decide_sales_loss_cut_threshold_is_now_the_same_aggressive_value_for_healthy_player():
    """
    A petición del usuario (2026-08-22, tras la venta incondicional de
    lesión confirmada): el corte de pérdidas se UNIFICA para todos los
    estados en el mismo umbral, antes exclusivo de lesión confirmada (10%,
    la mitad del genérico de antes, 20%) -- una pérdida del 12% ahora
    corta también a un jugador SANO, cosa que con el umbral genérico
    anterior no habría hecho.
    """
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%: por encima del umbral unificado (10%)

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.10, max_loss_pct=0.10)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    assert decisions[0]["profit"] == -120_000
    assert "falacia del coste hundido" in decisions[0]["reason"]
    assert "corte de pérdidas" in decisions[0]["reason"]


def test_decide_sales_doubt_status_now_uses_the_same_unified_threshold_too():
    """
    "doubt" ya no tiene un umbral distinto -- con la unificación, la misma
    pérdida del 12% que antes (umbral genérico 20%) NO activaba nada para
    "doubt" ahora sí lo hace (umbral único 10%).
    """
    squad = _full_442_squad()
    squad[9]["status"] = "doubt"  # Delantero0, solo duda
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%: por encima del umbral unificado (10%)

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.10, max_loss_pct=0.10)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]


def test_decide_sales_cutting_losses_ignores_bench_margin_for_confirmed_injury():
    """
    El corte de pérdidas de un lesionado confirmado que es el ÚNICO de su
    posición se vende igual (un lesionado nunca contó como "disponible",
    así que no hay margen de banquillo que proteger).
    """
    squad = _full_442_squad()
    squad[0]["status"] = "injured2"  # Portero, único de su posición
    bought_by_bot = {str(squad[0]["id"]): 1_000_000}
    squad[0]["value"] = 850_000  # -15%, por encima del umbral de corte (10%)

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.10, max_loss_pct=0.10)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[0]["id"]


def test_decide_sales_loss_cut_still_blocked_by_bench_margin_for_healthy_player():
    """
    A diferencia de un lesionado, el corte de pérdidas de un jugador SANO
    sigue respetando el margen de suplentes -- si es el único de su
    posición, no se vende aunque supere el umbral de pérdida.
    """
    squad = _full_442_squad()
    bought_by_bot = {str(squad[0]["id"]): 1_000_000}  # Portero, único de su posición, sano
    squad[0]["value"] = 700_000  # -30%, muy por encima del umbral (10%)

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.10, max_loss_pct=0.10)
    assert decisions == []


# --- Concentración de capital en lesión confirmada (a petición del usuario, 2026-08-22) ---
#
# En todos estos tests: min_profit_pct=0.10, max_loss_pct=0.10,
# injury_concentration_max_pct=0.15 -- el jugador de interés vale bastante
# más que el resto de la plantilla (que se queda en los 500.000 por
# defecto de _full_442_squad()) para que represente una parte grande del
# capital total, con una plusvalía/pérdida pequeña a propósito para aislar
# la señal de concentración de las otras dos vías (rentabilidad y corte de
# pérdidas).

_CONCENTRATION_KWARGS = dict(min_profit_pct=0.10, max_loss_pct=0.10, injury_concentration_max_pct=0.15)


def test_decide_sales_sells_confirmed_injured_player_overconcentrating_capital():
    """
    Un lesionado confirmado que vale una parte grande del capital total del
    equipo se vende aunque su plusvalía/pérdida no active ninguna otra vía
    -- el problema es tener capital inmovilizado en un jugador inutilizable,
    no la rentabilidad de la operación.
    """
    squad = _full_442_squad()
    squad[9]["status"] = "injured2"  # Delantero0, lesión confirmada
    squad[9]["value"] = 2_000_000  # el resto de la plantilla sigue en 500.000 c/u
    bought_by_bot = {str(squad[9]["id"]): 1_900_000}  # +5.3%: ni rentable ni en corte de pérdidas

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, **_CONCENTRATION_KWARGS)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    assert "concentración de capital" in decisions[0]["reason"]


def test_decide_sales_sells_confirmed_injured_player_below_concentration_threshold_too():
    """
    Un lesionado confirmado que NO concentra suficiente capital (y sin
    plusvalía/pérdida relevante) se vende igualmente -- la vía de
    concentración ya no es la única que cubre la lesión confirmada, la
    venta incondicional (ver docstring del módulo) aplica siempre.
    """
    squad = _full_442_squad()
    squad[9]["status"] = "injured2"
    # Sube un poco de valor, pero no lo bastante para superar el 15% del capital total.
    squad[9]["value"] = 600_000
    bought_by_bot = {str(squad[9]["id"]): 580_000}  # +3.4%

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, **_CONCENTRATION_KWARGS)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    assert "concentración de capital" not in decisions[0]["reason"]  # no llegó a activar esa vía
    assert "se vende siempre" in decisions[0]["reason"]


def test_decide_sales_concentration_rule_does_not_apply_to_doubt_status():
    """
    "doubt" queda fuera de la concentración de capital, igual que del corte
    de pérdidas agresivo -- todavía puede llegar a jugar.
    """
    squad = _full_442_squad()
    squad[9]["status"] = "doubt"
    squad[9]["value"] = 2_000_000  # misma concentración que el test de lesión confirmada
    bought_by_bot = {str(squad[9]["id"]): 1_900_000}

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, **_CONCENTRATION_KWARGS)
    assert decisions == []


def test_decide_sales_concentration_rule_does_not_apply_to_healthy_player():
    """Un jugador SANO que concentra mucho capital no se vende por eso -- la regla es solo para lesión confirmada."""
    squad = _full_442_squad()
    squad[9]["value"] = 2_000_000  # sano, misma concentración que los tests anteriores
    bought_by_bot = {str(squad[9]["id"]): 1_900_000}

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, **_CONCENTRATION_KWARGS)
    assert decisions == []


def test_decide_sales_concentration_counts_budget_as_part_of_total_capital():
    """
    Un presupuesto grande diluye la concentración -- el mismo lesionado que
    se vendía por concentración sin presupuesto (test de arriba) deja de
    superar ESE umbral si se añade bastante `budget` al capital total. Pero
    sigue siendo lesión CONFIRMADA, así que se vende igual por la vía
    incondicional (ver más abajo) -- solo cambia el motivo mostrado.
    """
    squad = _full_442_squad()
    squad[9]["status"] = "injured2"
    squad[9]["value"] = 2_000_000
    bought_by_bot = {str(squad[9]["id"]): 1_900_000}

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot, budget=20_000_000, **_CONCENTRATION_KWARGS
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    assert "concentración de capital" not in decisions[0]["reason"]  # diluida por el budget, no fue esta vía
    assert "se vende siempre" in decisions[0]["reason"]


def test_decide_sales_concentration_rule_ignores_bench_margin_like_any_confirmed_injury():
    """La venta forzada por concentración de capital tampoco respeta margen de banquillo -- puede ser el único de su posición."""
    squad = _full_442_squad()
    squad[0]["status"] = "injured2"  # Portero, único de su posición
    squad[0]["value"] = 2_000_000
    bought_by_bot = {str(squad[0]["id"]): 1_900_000}

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot, **_CONCENTRATION_KWARGS)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[0]["id"]


# --- Venta SIEMPRE para lesión confirmada (a petición del usuario,
# 2026-08-22, caso real: Mendy) ---
#
# Generalización de las dos vías de lesión de arriba: un lesionado
# CONFIRMADO se vende sin condiciones, aunque ni pierda valor suficiente
# para el corte de pérdidas NI concentre suficiente capital -- justo el
# hueco que dejaban esas dos vías.


def test_decide_sales_force_sells_confirmed_injury_with_zero_profit_and_no_concentration():
    """
    Caso real (Mendy, 2026-08-22): lesión confirmada, profit_pct=0% (recién
    backfilleado, sin plusvalía ni pérdida) y sin concentrar capital
    suficiente -- ninguna de las otras vías se activa, pero se vende
    igualmente por ser lesión confirmada.
    """
    squad = _full_442_squad()
    squad[9]["status"] = "injured2"  # Delantero0, lesión confirmada
    bought_by_bot = {str(squad[9]["id"]): 500_000}
    squad[9]["value"] = 500_000  # 0% -- ni rentable, ni pérdida, ni concentración

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.10, injury_concentration_max_pct=0.15,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    assert decisions[0]["profit_pct"] == 0.0
    assert "se vende siempre" in decisions[0]["reason"]


def test_decide_sales_does_not_force_sell_doubt_status():
    """"doubt" (no confirmada) queda fuera de la venta incondicional -- todavía puede llegar a jugar."""
    squad = _full_442_squad()
    squad[9]["status"] = "doubt"
    bought_by_bot = {str(squad[9]["id"]): 500_000}
    squad[9]["value"] = 500_000  # 0%, mismo caso que el test anterior pero solo "doubt"

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.10, injury_concentration_max_pct=0.15,
    )
    assert decisions == []


def test_decide_sales_force_injury_sale_ignores_bench_margin_even_as_only_player_in_position():
    """La venta incondicional por lesión confirmada tampoco respeta margen de banquillo -- puede ser el único de su posición."""
    squad = _full_442_squad()
    squad[0]["status"] = "injured2"  # Portero, único de su posición
    bought_by_bot = {str(squad[0]["id"]): 500_000}
    squad[0]["value"] = 500_000  # 0%

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.10, injury_concentration_max_pct=0.15,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[0]["id"]


# --- Oportunidad de mercado / plaza escasa (a petición del usuario, 2026-08-22,
# tras el límite de plantilla de jobs/run_market.py) ---


def _feature_row(player_id, position, price=500_000, points=10, xg=0.0, minutes_played=0, team_games=10, status=""):
    """Fila cruda estilo `db.models.get_player_features()` -- NO el formato de `squad`/`get_roster()`."""
    return {
        "id": player_id,
        "position": position,
        "price": price,
        "points": points,
        "last_points": points,
        "average_points": points,
        "status": status,
        "xg": xg,
        "minutes_played": minutes_played,
        "team_games": team_games,
    }


def test_decide_sales_sells_mediocre_bench_player_when_market_offers_clear_upgrade():
    """
    Un suplente que ni gana ni pierde (profit_pct=0%, por debajo de
    min_profit_pct) se pone en venta igualmente si el mercado ofrece ahora
    mismo alguien mucho mejor (score de alineación, LINEUP_EVALUATOR_WEIGHTS)
    en su misma posición -- ver docstring del módulo.
    """
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"10": 500_000}  # Defensa0 (id=10): comprado y vale igual ahora -> 0% profit

    own_squad_features = [_feature_row(10, "DEF", xg=0.0, minutes_played=10)]  # apenas juega, sin xG
    market_candidates = [_feature_row("mercado1", "DEF", xg=5.0, minutes_played=900)]  # titular indiscutible

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )

    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 10
    assert decisions[0]["profit_pct"] == 0.0
    assert "oportunidad de mercado" in decisions[0]["reason"]


def test_decide_sales_market_upgrade_disabled_without_market_data():
    """Sin `own_squad_features`/`market_candidates`, la vía queda desactivada -- comportamiento idéntico al de antes."""
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"10": 500_000}

    assert decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot) == []


def test_decide_sales_market_upgrade_ignored_when_not_clearly_better():
    """Mismo score de alineación (nadie es claramente mejor) -> no se vende solo por esto."""
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"10": 500_000}

    own_squad_features = [_feature_row(10, "DEF", xg=2.0, minutes_played=450)]
    market_candidates = [_feature_row("mercado1", "DEF", xg=2.0, minutes_played=450)]  # calidad idéntica

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )
    assert decisions == []


def test_decide_sales_market_upgrade_respects_bench_margin():
    """Igual que las otras tres vías: si vender aquí deja la posición sin cobertura, no se vende por muy claro que sea el margen de mercado."""
    squad = _full_442_squad()  # DEF sin margen de sobra (4 sanos para 4 titulares) -- sin el 5º de más
    bought_by_bot = {"10": 500_000}

    own_squad_features = [_feature_row(10, "DEF", xg=0.0, minutes_played=10)]
    market_candidates = [_feature_row("mercado1", "DEF", xg=5.0, minutes_played=900)]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )
    assert decisions == []


def test_decide_sales_market_upgrade_does_not_apply_to_confirmed_injury():
    """
    Lesión confirmada: su calidad actual no es representativa mientras no
    pueda jugar -- la vía de oportunidad de mercado nunca es el motivo para
    él. Pero al ser lesión CONFIRMADA sí se vende, por la vía incondicional
    (ver docstring del módulo) -- con un reason que lo deja claro, no el de
    oportunidad de mercado.
    """
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    squad[1]["status"] = "injured2"  # Defensa0 (id=10)
    bought_by_bot = {"10": 500_000}

    own_squad_features = [_feature_row(10, "DEF", xg=0.0, minutes_played=10, status="injured2")]
    market_candidates = [_feature_row("mercado1", "DEF", xg=5.0, minutes_played=900)]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 10
    assert "oportunidad de mercado" not in decisions[0]["reason"]
    assert "se vende siempre" in decisions[0]["reason"]


# --- compute_revaluation_premium_pct / apply_revaluation_premium (2026-08-22) ---


def test_compute_revaluation_premium_rejects_too_few_data_points():
    """1-2 puntos sueltos (típico justo tras un reinicio de BD/liga) no bastan para proyectar nada."""
    prices = [_price(1, 1_100_000), _price(0, 1_200_000)]
    assert compute_revaluation_premium_pct(prices, min_data_points=3, now=NOW) == (0.0, None)


def test_compute_revaluation_premium_rejects_a_single_down_day():
    """Un solo día que baja respecto al anterior descarta la prima entera -- no es tendencia sostenida."""
    prices = [_price(3, 1_000_000), _price(2, 1_100_000), _price(1, 1_050_000), _price(0, 1_300_000)]
    assert compute_revaluation_premium_pct(prices, min_data_points=3, now=NOW) == (0.0, None)


def test_compute_revaluation_premium_rejects_change_below_threshold():
    """Subida sostenida pero pequeña (por debajo de min_pct_to_project) se trata como ruido normal, no como prima."""
    prices = [_price(2, 1_000_000), _price(1, 1_020_000), _price(0, 1_040_000)]  # +4% total
    premium, note = compute_revaluation_premium_pct(prices, min_data_points=3, min_pct_to_project=0.15, now=NOW)
    assert (premium, note) == (0.0, None)


def test_compute_revaluation_premium_projects_a_fraction_of_a_sustained_rise():
    prices = [_price(2, 1_000_000), _price(1, 1_100_000), _price(0, 1_200_000)]  # +20% sostenido
    premium, note = compute_revaluation_premium_pct(
        prices, min_data_points=3, min_pct_to_project=0.15, projection_fraction=0.5, max_premium_pct=0.15, now=NOW
    )
    assert premium == 0.10  # 20% * 0.5 de fracción, por debajo del tope 15%
    assert note is not None and "20.0%" in note


def test_compute_revaluation_premium_never_exceeds_the_hard_cap():
    prices = [_price(2, 1_000_000), _price(1, 1_300_000), _price(0, 1_600_000)]  # +60% sostenido
    premium, _ = compute_revaluation_premium_pct(
        prices, min_data_points=3, min_pct_to_project=0.15, projection_fraction=0.5, max_premium_pct=0.15, now=NOW
    )
    assert premium == 0.15  # 60% * 0.5 = 30%, topado a 0.15


def test_compute_revaluation_premium_ignores_entries_outside_the_lookback_window():
    """Puntos fuera de la ventana de lookback no cuentan ni para el mínimo de datos ni para el % de subida."""
    prices = [_price(30, 500_000), _price(2, 1_000_000), _price(1, 1_100_000), _price(0, 1_200_000)]
    premium, _ = compute_revaluation_premium_pct(prices, lookback_days=7, min_data_points=3, now=NOW)
    assert premium > 0.0  # se calcula sobre los 3 puntos dentro de la ventana, ignorando el de hace 30 días


def test_compute_revaluation_premium_ignores_unparseable_or_non_positive_entries():
    """Entradas sin fecha/precio parseable, o con precio <= 0, se descartan en vez de reventar."""
    prices = [
        {"date": None, "price": 1_000_000},
        {"date": _price(1, 0)["date"], "price": 0},
        _price(2, 1_000_000),
        _price(1, 1_100_000),
        _price(0, 1_200_000),
    ]
    premium, _ = compute_revaluation_premium_pct(prices, min_data_points=3, now=NOW)
    assert premium > 0.0  # las 2 entradas basura se ignoran, quedan los 3 puntos válidos


def test_apply_revaluation_premium_leaves_decision_unchanged_when_no_premium_applies():
    decision = {"player_id": "1", "asking_price": 1_000_000, "reason": "motivo original"}
    result = apply_revaluation_premium(decision, prices=[], now=NOW)
    assert result is decision  # ni siquiera se copia el dict


def test_apply_revaluation_premium_inflates_asking_price_and_extends_reason():
    decision = {"player_id": "1", "asking_price": 1_000_000, "reason": "motivo original"}
    prices = [_price(2, 1_000_000), _price(1, 1_100_000), _price(0, 1_200_000)]  # +20% sostenido
    result = apply_revaluation_premium(decision, prices, now=NOW)
    assert result["asking_price"] == 1_100_000  # 1_000_000 * 1.10
    assert result["reason"].startswith("motivo original; revalorización sostenida")
    assert decision["asking_price"] == 1_000_000  # el original no se muta
