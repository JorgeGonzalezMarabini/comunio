from datetime import datetime, timedelta, timezone

from engine.selling_strategy import (
    apply_revaluation_premium,
    compute_revaluation_premium_pct,
    confirm_loss_is_sustained,
    decide_sales,
    effective_loss_cut_threshold,
    effective_profit_threshold,
    price_momentum_pct,
)

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)


def _price(days_ago, price):
    date = (NOW - timedelta(days=days_ago)).isoformat()
    return {"date": date, "price": price}


def _history_point(days_ago, price):
    return {"recorded_at": (NOW - timedelta(days=days_ago)).isoformat(), "price": price}

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


def test_decide_sales_ignores_player_already_on_market():
    """
    Regresión del incidente en vivo 2026-08-22 (jugador Dieng): un jugador
    con `market: true` (ya puesto en venta en una pasada anterior -- ver
    docstring de FutmondoClient.get_roster) nunca vuelve a ser candidato,
    aunque cumpla de sobra el umbral de rentabilidad -- si no, cada pasada
    lo redecide y `FutmondoClient.list_for_sale()` falla con
    `api.error.not_found` al reintentar un listado que ya existe.
    """
    squad = _full_442_squad()
    bought_by_bot = {"20": 500_000}  # Medio0 (id=20)
    squad[5]["value"] = 900_000  # +80%, de sobra por encima del umbral por defecto
    squad[5]["market"] = True
    assert decide_sales(squad, formation="4-4-2", bought_by_bot=bought_by_bot) == []


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


# --- Refinamientos de "oportunidad de mercado" (2026-08-23) ---
# Caso real que los motivó: el mismo día se vendieron a la vez Raba+Brugué
# por DEL y Camavinga+Dieng por MED, cada pareja justificada por un ÚNICO
# mejor candidato de mercado en su posición -- de ese candidato solo se
# puede fichar uno.

WEEKDAY_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)  # miércoles
SATURDAY_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)  # sábado


def test_decide_sales_market_upgrade_caps_at_one_sale_per_position_by_default():
    """
    Dos suplentes DEF cualifican por oportunidad de mercado contra el
    MISMO mejor candidato -- de ese candidato solo se puede fichar uno, así
    que solo se vende el de mayor margen (peor suplente relativo, id=15),
    no los dos (config.SELLING_UPGRADE_MAX_SALES_PER_POSITION=1 por
    defecto).
    """
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra1", "role": "defensa", "status": "", "value": 500_000})
    squad.append({"id": 16, "name": "Defensa Extra2", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"15": 500_000, "16": 500_000}

    own_squad_features = [
        _feature_row(15, "DEF", xg=0.0, minutes_played=0),  # peor suplente -> mayor margen
        _feature_row(16, "DEF", xg=3.0, minutes_played=600),  # algo mejor -> margen más pequeño
    ]
    market_candidates = [_feature_row("mercado1", "DEF", xg=10.0, minutes_played=900)]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )

    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 15


def test_decide_sales_market_upgrade_blocks_expensive_candidate_with_poor_price_efficiency():
    """
    "No podemos comparar la calidad de un jugador de 4 millones con uno de
    50": aunque el margen de score bruto (0.70) supere de sobra
    `upgrade_available_min_margin`, si el candidato objetivo cuesta 49.5M
    más que el propio jugador, el margen de score por millón extra
    (0.014) no llega a `SELLING_UPGRADE_MIN_SCORE_PER_EXTRA_MILLION`
    (0.03 por defecto) -- no se vende.
    """
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"15": 500_000}

    own_squad_features = [_feature_row(15, "DEF", xg=0.0, minutes_played=0)]
    market_candidates = [_feature_row("mercado1", "DEF", price=50_000_000, xg=10.0, minutes_played=900)]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        budget=100_000_000,  # de sobra para pagarlo -- aísla el filtro de eficiencia del de asequibilidad
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )
    assert decisions == []


def test_decide_sales_market_upgrade_allows_pricier_candidate_with_good_efficiency():
    """Mismo margen que el test anterior, pero el candidato solo cuesta 1.5M más -- eficiencia 0.47, sí compensa."""
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"15": 500_000}

    own_squad_features = [_feature_row(15, "DEF", xg=0.0, minutes_played=0)]
    market_candidates = [_feature_row("mercado1", "DEF", price=2_000_000, xg=10.0, minutes_played=900)]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        budget=2_000_000,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 15
    assert "precio objetivo 2000000" in decisions[0]["reason"]


def test_decide_sales_market_upgrade_blocked_when_not_affordable_even_with_sale_proceeds():
    """El candidato objetivo (2M) no cabe ni sumando lo que liberaría la venta (0.5M) al presupuesto (0) -- no se vende."""
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"15": 500_000}

    own_squad_features = [_feature_row(15, "DEF", xg=0.0, minutes_played=0)]
    market_candidates = [_feature_row("mercado1", "DEF", price=2_000_000, xg=10.0, minutes_played=900)]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        budget=0,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )
    assert decisions == []


def test_decide_sales_market_upgrade_shares_budget_across_positions_prioritizing_larger_margin():
    """
    DEF (margen 0.70) y MED (margen 0.35) cualifican a la vez, cada uno
    contra un candidato de 2M, pero el presupuesto compartido (2M) más lo
    que libera cada venta (0.5M) solo llega para UNO de los dos -- gana el
    de mayor margen (DEF), MED se descarta esta vez (a petición del
    usuario: "llevar la cuenta del presupuesto sacrificado").
    """
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    squad.append({"id": 25, "name": "Medio Extra", "role": "centrocampista", "status": "", "value": 500_000})
    bought_by_bot = {"15": 500_000, "25": 500_000}

    own_squad_features = [
        _feature_row(15, "DEF", xg=0.0, minutes_played=0),  # margen 0.70 frente al mejor DEF
        _feature_row(25, "MED", xg=5.0, minutes_played=450),  # margen 0.35 frente al mejor MED
    ]
    market_candidates = [
        _feature_row("def_best", "DEF", price=2_000_000, xg=10.0, minutes_played=900),
        _feature_row("med_best", "MED", price=2_000_000, xg=10.0, minutes_played=900),
        # candidato MED flojo, solo para dar rango de normalización al grupo MED (no es el "mejor")
        _feature_row("med_floor", "MED", price=500_000, xg=0.0, minutes_played=0),
    ]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        budget=2_000_000,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
    )

    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 15  # DEF, mayor margen, se queda el presupuesto compartido


def test_decide_sales_market_upgrade_blocks_when_target_listing_expires_too_soon():
    """
    Al candidato objetivo solo le quedan 2h de mercado -- menos de las 24h
    que se asume que tardará en resolverse nuestra propia venta
    (config.SELLING_ASSUMED_SALE_RESOLUTION_HOURS) -- no tiene sentido
    vender para intentar comprarlo, no se vende.
    """
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"15": 500_000}

    own_squad_features = [_feature_row(15, "DEF", xg=0.0, minutes_played=0)]
    market_candidates = [_feature_row("mercado1", "DEF", xg=10.0, minutes_played=900)]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
        market_listing_expirations={"mercado1": "2026-08-26T14:00:00+00:00"},  # +2h
        now=WEEKDAY_NOW,
    )
    assert decisions == []


def test_decide_sales_market_upgrade_allows_when_target_listing_has_enough_time():
    """Mismo caso, pero al candidato le quedan 72h -- de sobra para resolver nuestra venta antes -- sí se vende."""
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"15": 500_000}

    own_squad_features = [_feature_row(15, "DEF", xg=0.0, minutes_played=0)]
    market_candidates = [_feature_row("mercado1", "DEF", xg=10.0, minutes_played=900)]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
        market_listing_expirations={"mercado1": "2026-08-29T12:00:00+00:00"},  # +72h
        now=WEEKDAY_NOW,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 15


def test_decide_sales_market_upgrade_ignores_timing_filter_without_expiration_data():
    """Sin `market_listing_expirations` (o sin entrada para ese candidato), el filtro de tiempo queda desactivado."""
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    bought_by_bot = {"15": 500_000}

    own_squad_features = [_feature_row(15, "DEF", xg=0.0, minutes_played=0)]
    market_candidates = [_feature_row("mercado1", "DEF", xg=10.0, minutes_played=900)]

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        own_squad_features=own_squad_features,
        market_candidates=market_candidates,
        now=WEEKDAY_NOW,
    )
    assert len(decisions) == 1


def test_decide_sales_weekend_guard_blocks_any_reason_for_a_starter():
    """
    Bloqueo defensivo (config.ENABLE_SELLING_WEEKEND_LINEUP_GUARD): en fin
    de semana, un titular guardado no se vende NI SIQUIERA por lesión
    confirmada (la vía incondicional) -- ninguna vía queda exenta.
    """
    squad = _full_442_squad()
    squad[1]["status"] = "injured2"  # Defensa0 (id=10)
    bought_by_bot = {"10": 500_000}

    decisions = decide_sales(
        squad,
        bought_by_bot=bought_by_bot,
        own_lineup_player_ids={"10"},
        now=SATURDAY_NOW,
    )
    assert decisions == []


def test_decide_sales_weekend_guard_does_not_apply_on_a_weekday():
    """Mismo caso que el anterior, pero en día de partido normal (miércoles) -- sí se vende."""
    squad = _full_442_squad()
    squad[1]["status"] = "injured2"
    bought_by_bot = {"10": 500_000}

    decisions = decide_sales(
        squad,
        bought_by_bot=bought_by_bot,
        own_lineup_player_ids={"10"},
        now=WEEKDAY_NOW,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 10


def test_decide_sales_weekend_guard_disabled_without_own_lineup_data():
    """Sin `own_lineup_player_ids`, el bloqueo de fin de semana queda desactivado -- comportamiento idéntico al de antes."""
    squad = _full_442_squad()
    squad[1]["status"] = "injured2"
    bought_by_bot = {"10": 500_000}

    decisions = decide_sales(squad, bought_by_bot=bought_by_bot, now=SATURDAY_NOW)
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 10


def test_decide_sales_weekend_guard_can_be_disabled_explicitly():
    """`enable_weekend_lineup_guard=False` desactiva el bloqueo aunque haya `own_lineup_player_ids` y sea fin de semana."""
    squad = _full_442_squad()
    squad[1]["status"] = "injured2"
    bought_by_bot = {"10": 500_000}

    decisions = decide_sales(
        squad,
        bought_by_bot=bought_by_bot,
        own_lineup_player_ids={"10"},
        now=SATURDAY_NOW,
        enable_weekend_lineup_guard=False,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 10


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


# --- confirm_loss_is_sustained (evaluación del trigger de venta, 2026-09-11) ---


def test_confirm_loss_is_sustained_fails_open_with_too_few_data_points():
    """Sin histórico suficiente, se confirma el corte igual que si esta función no existiera."""
    history = [_history_point(1, 900_000)]
    assert confirm_loss_is_sustained(history, min_data_points=2, now=NOW) == (True, None)


def test_confirm_loss_is_sustained_fails_open_without_any_history():
    assert confirm_loss_is_sustained([], min_data_points=2, now=NOW) == (True, None)


def test_confirm_loss_is_sustained_confirms_a_clean_sustained_drop():
    """Serie que solo baja, sin repunte -- se confirma el corte."""
    history = [_history_point(2, 1_000_000), _history_point(1, 950_000), _history_point(0, 900_000)]
    confirmed, note = confirm_loss_is_sustained(history, min_data_points=2, max_rebound_pct=0.05, now=NOW)
    assert (confirmed, note) == (True, None)


def test_confirm_loss_is_sustained_postpones_on_a_clear_rebound_from_the_bottom():
    """Repuntó +10% desde el mínimo reciente (por encima del 5% tolerado) -- se pospone el corte."""
    history = [_history_point(2, 1_000_000), _history_point(1, 800_000), _history_point(0, 880_000)]
    confirmed, note = confirm_loss_is_sustained(history, min_data_points=2, max_rebound_pct=0.05, now=NOW)
    assert confirmed is False
    assert note is not None and "repuntó" in note


def test_confirm_loss_is_sustained_tolerates_a_small_rebound_within_the_margin():
    """Un repunte pequeño (2%, por debajo del 5% tolerado) no invalida la confirmación."""
    history = [_history_point(2, 1_000_000), _history_point(1, 800_000), _history_point(0, 816_000)]
    confirmed, _ = confirm_loss_is_sustained(history, min_data_points=2, max_rebound_pct=0.05, now=NOW)
    assert confirmed is True


def test_confirm_loss_is_sustained_ignores_entries_outside_the_lookback_window():
    history = [_history_point(30, 2_000_000), _history_point(1, 1_000_000), _history_point(0, 900_000)]
    confirmed, _ = confirm_loss_is_sustained(history, lookback_days=7, min_data_points=2, now=NOW)
    assert confirmed is True  # el punto de hace 30 días no cuenta ni para el mínimo ni para el repunte


def test_confirm_loss_is_sustained_ignores_unparseable_or_non_positive_entries():
    history = [
        {"recorded_at": None, "price": 1_000_000},
        {"recorded_at": _history_point(1, 0)["recorded_at"], "price": 0},
        _history_point(1, 1_000_000),
        _history_point(0, 900_000),
    ]
    confirmed, _ = confirm_loss_is_sustained(history, min_data_points=2, max_rebound_pct=0.05, now=NOW)
    assert confirmed is True  # solo los 2 puntos válidos cuentan, sin repunte entre ellos


# --- decide_sales: trailing-stop / corte por reversión desde máximo (2026-09-11) ---


def test_decide_sales_trailing_stop_triggers_even_while_still_profitable_vs_purchase():
    """
    Comprado a 10M, subió a 20M de pico y ha caído a 16M: +60% frente a la
    compra (no activaría ni rentabilidad -- por debajo de un umbral muy
    alto -- ni corte de pérdidas), pero -20% desde el pico sí activa el
    trailing-stop (umbral 15%).
    """
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 10_000_000}
    squad[9]["value"] = 16_000_000  # +60% vs compra, -20% vs pico de 20M
    purchase_baselines = {str(squad[9]["id"]): {"peak_price": 20_000_000, "points_at_purchase": None}}

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        min_profit_pct=999,  # nunca se alcanza por esta vía en este test
        max_loss_pct=999,  # idem
        purchase_baselines=purchase_baselines,
        trailing_stop_max_drawdown_pct=0.15,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    assert "reversión desde máximo" in decisions[0]["reason"]
    assert "20000000" in decisions[0]["reason"].replace(".", "").replace(",", "")


def test_decide_sales_trailing_stop_not_triggered_below_the_drawdown_threshold():
    squad = _full_442_squad()
    bought_by_bot = {str(squad[9]["id"]): 10_000_000}
    squad[9]["value"] = 19_000_000  # -5% desde el pico, por debajo del umbral 15%
    purchase_baselines = {str(squad[9]["id"]): {"peak_price": 20_000_000, "points_at_purchase": None}}

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        min_profit_pct=999,
        max_loss_pct=999,
        purchase_baselines=purchase_baselines,
        trailing_stop_max_drawdown_pct=0.15,
    )
    assert decisions == []


def test_decide_sales_trailing_stop_disabled_without_purchase_baselines():
    """Sin `purchase_baselines`, la vía queda desactivada -- comportamiento idéntico al de antes de esta feature."""
    squad = _full_442_squad()
    bought_by_bot = {str(squad[9]["id"]): 10_000_000}
    squad[9]["value"] = 16_000_000  # -20% desde un pico que aquí no se informa

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=999, max_loss_pct=999
    )
    assert decisions == []


# --- decide_sales: confirmación de tendencia del corte de pérdidas (2026-09-11) ---


def test_decide_sales_loss_cut_postponed_when_price_already_rebounded():
    """Cruza el umbral de corte (-12%), pero el histórico reciente muestra un repunte claro -- se pospone."""
    squad = _full_442_squad()
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%, por encima del umbral de corte (10%)
    recent_price_history = {
        str(squad[9]["id"]): [
            _history_point(2, 800_000),
            _history_point(1, 850_000),
            _history_point(0, 880_000),  # repuntó +10% desde el mínimo de la ventana
        ]
    }

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        min_profit_pct=0.10,
        max_loss_pct=0.10,
        recent_price_history=recent_price_history,
        loss_confirmation_min_data_points=2,
        loss_confirmation_max_rebound_pct=0.05,
    )
    assert decisions == []


def test_decide_sales_loss_cut_confirmed_when_no_rebound():
    """Cruza el umbral y el histórico reciente confirma que la caída sigue vigente -- se vende."""
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%
    recent_price_history = {
        str(squad[9]["id"]): [
            _history_point(2, 950_000),
            _history_point(1, 910_000),
            _history_point(0, 880_000),  # sigue bajando, sin repunte
        ]
    }

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        min_profit_pct=0.10,
        max_loss_pct=0.10,
        recent_price_history=recent_price_history,
        loss_confirmation_min_data_points=2,
        loss_confirmation_max_rebound_pct=0.05,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]


def test_decide_sales_loss_cut_unaffected_without_recent_price_history():
    """Sin `recent_price_history`, el corte de pérdidas se dispara igual que antes de esta feature (falla abierto)."""
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.10, max_loss_pct=0.10
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]


def test_decide_sales_loss_confirmation_gate_does_not_block_confirmed_injury_sale():
    """
    Si además de cruzar el umbral de pérdida el jugador tiene lesión
    CONFIRMADA, esa vía incondicional manda igual -- el repunte de precio
    no pospone nada porque el corte de pérdidas ya no es el ÚNICO motivo
    (un lesionado confirmado tampoco necesita margen de banquillo, ver
    docstring del módulo).
    """
    squad = _full_442_squad()
    squad[9]["status"] = "injured2"
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%
    recent_price_history = {
        str(squad[9]["id"]): [
            _history_point(2, 800_000),
            _history_point(1, 850_000),
            _history_point(0, 880_000),  # repunte claro, que SÍ pospondría un corte de pérdidas "puro"
        ]
    }

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        min_profit_pct=0.10,
        max_loss_pct=0.10,
        recent_price_history=recent_price_history,
        loss_confirmation_min_data_points=2,
        loss_confirmation_max_rebound_pct=0.05,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    # motivo mostrado: cutting_losses sigue en True (no es el ÚNICO motivo,
    # así que la confirmación de tendencia ni se evalúa) -- misma prioridad
    # de `reason` que ya existía antes de esta feature (ver docstring de
    # decide_sales: vías 2/3 dan un motivo más específico cuando también
    # aplican junto a la 4).
    assert "corte de pérdidas (lesión confirmada)" in decisions[0]["reason"]


# --- decide_sales: nota informativa de puntos ya extraídos (2026-09-11) ---


def test_decide_sales_loss_cut_reason_includes_points_earned_context():
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%
    squad[9]["points"] = 45
    purchase_baselines = {str(squad[9]["id"]): {"peak_price": None, "points_at_purchase": 10}}

    decisions = decide_sales(
        squad,
        formation="4-4-2",
        bought_by_bot=bought_by_bot,
        min_profit_pct=0.10,
        max_loss_pct=0.10,
        purchase_baselines=purchase_baselines,
    )
    assert len(decisions) == 1
    assert "sumó 35 punto(s) de liga" in decisions[0]["reason"]
    assert "no afecta a la decisión" in decisions[0]["reason"]


def test_decide_sales_reason_has_no_points_note_without_points_at_purchase():
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%
    squad[9]["points"] = 45

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.10, max_loss_pct=0.10
    )
    assert len(decisions) == 1
    assert "para contexto" not in decisions[0]["reason"]


# --- Corte de pérdidas frente al VM de compra, con multiplicadores por
# antigüedad y media de puntos (a petición del usuario, 2026-09-23, ver
# docstring del módulo) ---

_MULT_KWARGS = dict(
    loss_time_mult_max=2.0,
    loss_time_decay_days=14,
    loss_avg_points_ref=3.0,
    loss_avg_points_max_mult=1.5,
    loss_max_effective_pct=0.40,
    top_players_require_replacement=False,  # estos tests miden los multiplicadores; la regla del top tiene los suyos
)


def _days_ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def test_effective_loss_cut_threshold_without_data_is_the_base():
    assert effective_loss_cut_threshold(0.15, time_mult_max=2.0, time_decay_days=14) == 0.15


def test_effective_loss_cut_threshold_time_multiplier_decays_linearly():
    kw = dict(time_mult_max=2.0, time_decay_days=14, max_effective_pct=1.0)
    assert effective_loss_cut_threshold(0.15, days_held=0, **kw) == 0.30
    assert abs(effective_loss_cut_threshold(0.15, days_held=7, **kw) - 0.225) < 1e-9
    assert effective_loss_cut_threshold(0.15, days_held=14, **kw) == 0.15
    assert effective_loss_cut_threshold(0.15, days_held=60, **kw) == 0.15


def test_effective_loss_cut_threshold_average_multiplier_is_clamped():
    kw = dict(avg_points_ref=3.0, avg_points_max_mult=1.5, max_effective_pct=1.0)
    assert effective_loss_cut_threshold(0.15, average_points=1.0, **kw) == 0.15  # nunca por debajo del base
    assert abs(effective_loss_cut_threshold(0.15, average_points=4.0, **kw) - 0.20) < 1e-9
    assert abs(effective_loss_cut_threshold(0.15, average_points=9.0, **kw) - 0.225) < 1e-9  # tope x1.5


def test_effective_loss_cut_threshold_is_capped():
    assert effective_loss_cut_threshold(
        0.15, days_held=0, average_points=9.0, time_mult_max=2.0, time_decay_days=14,
        avg_points_ref=3.0, avg_points_max_mult=1.5, max_effective_pct=0.40,
    ) == 0.40


def test_decide_sales_loss_cut_ignores_the_auction_premium():
    """
    Pagado 1.15M por un jugador que valía 1.0M: a 0.95M es -17% frente a lo
    pagado (antes cortaba), pero solo -5% frente al VM de compra -- no se vende.
    """
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 1_150_000}
    squad[9]["value"] = 950_000
    baselines = {str(squad[9]["id"]): {"peak_price": 1_000_000, "value_at_purchase": 1_000_000,
                                         "purchased_at": _days_ago(30)}}

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.15, max_loss_pct=0.15,
        purchase_baselines=baselines, trailing_stop_max_drawdown_pct=999, now=NOW, **_MULT_KWARGS,
    )
    assert decisions == []


def test_decide_sales_loss_cut_against_purchase_value_after_the_grace_decay():
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 1_150_000}
    squad[9]["value"] = 820_000  # -18% frente al VM de compra, fichado hace 30 días -> umbral base 15%
    baselines = {str(squad[9]["id"]): {"peak_price": 1_000_000, "value_at_purchase": 1_000_000,
                                         "purchased_at": _days_ago(30)}}

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.15, max_loss_pct=0.15,
        purchase_baselines=baselines, trailing_stop_max_drawdown_pct=999, now=NOW, **_MULT_KWARGS,
    )
    assert len(decisions) == 1
    assert "corte de pérdidas" in decisions[0]["reason"]
    assert "VM en la compra 1000000" in decisions[0]["reason"]
    assert "-18.0% frente al VM de compra" in decisions[0]["reason"]


def test_decide_sales_recent_signing_gets_more_room_before_loss_cut():
    """La misma caída del 18% a los 2 días de ficharlo no corta (umbral ~27.9%)."""
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 820_000
    baselines = {str(squad[9]["id"]): {"peak_price": 1_000_000, "value_at_purchase": 1_000_000,
                                         "purchased_at": _days_ago(2)}}

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot, min_profit_pct=0.15, max_loss_pct=0.15,
        purchase_baselines=baselines, trailing_stop_max_drawdown_pct=999, now=NOW, **_MULT_KWARGS,
    )
    assert decisions == []


def test_decide_sales_good_average_gets_more_room_before_loss_cut():
    """Media 4.5 (x1.5): una caída del 18% ya veterana no corta (umbral 22.5%); con media 2 sí."""
    def run(average):
        squad = _full_442_squad()
        squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
        squad[9]["value"] = 820_000
        squad[9]["average"] = {"average": average}
        baselines = {str(squad[9]["id"]): {"peak_price": 1_000_000, "value_at_purchase": 1_000_000,
                                             "purchased_at": _days_ago(30)}}
        return decide_sales(
            squad, formation="4-4-2", bought_by_bot={str(squad[9]["id"]): 1_000_000},
            min_profit_pct=0.15, max_loss_pct=0.15, purchase_baselines=baselines,
            trailing_stop_max_drawdown_pct=999, now=NOW, **_MULT_KWARGS,
        )

    assert run(4.5) == []
    assert len(run(2.0)) == 1


def test_decide_sales_market_upgrade_blocked_for_a_recent_signing():
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    own_squad_features = [_feature_row(10, "DEF", xg=0.0, minutes_played=10)]
    market_candidates = [_feature_row("mercado1", "DEF", xg=5.0, minutes_played=900)]

    def run(days_held):
        baselines = {"10": {"peak_price": 500_000, "value_at_purchase": 500_000, "purchased_at": _days_ago(days_held)}}
        return decide_sales(
            squad, formation="4-4-2", bought_by_bot={"10": 500_000},
            own_squad_features=own_squad_features, market_candidates=market_candidates,
            purchase_baselines=baselines, upgrade_min_hold_days=7, now=NOW,
        )

    assert run(1) == []
    decisions = run(10)
    assert len(decisions) == 1
    assert "oportunidad de mercado" in decisions[0]["reason"]


# --- Venta por plusvalía ponderada por media, titularidad y tendencia (a
# petición del usuario, 2026-09-23, caso Koski, ver docstring del módulo) ---

_PROFIT_KWARGS = dict(
    min_profit_pct=0.15,
    profit_avg_points_ref=3.0,
    profit_avg_points_max_mult=2.5,
    profit_starter_mult=1.5,
    profit_momentum_lookback_days=3,
    profit_momentum_min_pct=0.03,
    profit_momentum_min_data_points=2,
    trailing_stop_max_drawdown_pct=999,
    enable_weekend_lineup_guard=False,
    protect_top_players_from_profit=False,  # estos tests miden los multiplicadores; la protección tiene los suyos
    top_players_require_replacement=False,
    now=NOW,
)


def test_effective_profit_threshold_multipliers():
    kw = dict(avg_points_ref=3.0, avg_points_max_mult=2.5, starter_mult=1.5)
    assert effective_profit_threshold(0.15, **kw) == 0.15
    assert effective_profit_threshold(0.15, average_points=2.0, **kw) == 0.15  # nunca por debajo del base
    assert abs(effective_profit_threshold(0.15, average_points=4.5, **kw) - 0.225) < 1e-9
    assert abs(effective_profit_threshold(0.15, average_points=12, **kw) - 0.375) < 1e-9  # tope x2.5
    assert abs(effective_profit_threshold(0.15, average_points=4.5, is_starter=True, **kw) - 0.3375) < 1e-9


def test_price_momentum_pct_uses_first_point_in_window():
    history = [_history_point(5, 500_000), _history_point(2.5, 1_000_000), _history_point(1, 1_050_000)]
    assert abs(price_momentum_pct(history, 1_100_000, lookback_days=3, min_data_points=2, now=NOW) - 0.10) < 1e-9


def test_price_momentum_pct_none_without_enough_data():
    assert price_momentum_pct([_history_point(1, 1_000_000)], 1_100_000, lookback_days=3, min_data_points=2, now=NOW) is None
    assert price_momentum_pct(None, 1_100_000, now=NOW) is None


def _profit_squad(value=1_200_000, average=None):
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    squad[9]["value"] = value
    if average is not None:
        squad[9]["average"] = {"average": average}
    return squad, {str(squad[9]["id"]): 1_000_000}


def test_decide_sales_profit_sale_unchanged_for_low_average_bench_player_with_flat_price():
    squad, bought = _profit_squad(1_200_000, average=2.0)
    history = {str(squad[9]["id"]): [_history_point(2, 1_190_000), _history_point(1, 1_200_000)]}

    decisions = decide_sales(squad, formation="4-4-2", bought_by_bot=bought, recent_price_history=history, **_PROFIT_KWARGS)
    assert len(decisions) == 1
    assert "umbral 15.0%" in decisions[0]["reason"]


def test_decide_sales_high_average_raises_the_profit_threshold():
    squad, bought = _profit_squad(1_200_000, average=5.0)  # +20% < 25% (x1.67)

    assert decide_sales(squad, formation="4-4-2", bought_by_bot=bought, **_PROFIT_KWARGS) == []


def test_decide_sales_starter_raises_the_profit_threshold():
    squad, bought = _profit_squad(1_200_000)  # +20% < 22.5% (titular x1.5)

    assert decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought, own_lineup_player_ids={squad[9]["id"]}, **_PROFIT_KWARGS
    ) == []


def test_decide_sales_profit_sale_postponed_while_price_still_rising():
    squad, bought = _profit_squad(1_300_000)  # +30%, muy por encima del 15%...
    history = {str(squad[9]["id"]): [_history_point(2.5, 1_150_000), _history_point(1, 1_250_000)]}  # ...pero +13% en 3d

    assert decide_sales(squad, formation="4-4-2", bought_by_bot=bought, recent_price_history=history, **_PROFIT_KWARGS) == []


def test_decide_sales_koski_like_case_is_kept():
    """Mejor media del equipo, titular, +16% frente a lo pagado y subiendo ~+7% diario -> no se vende."""
    squad, bought = _profit_squad(1_160_000, average=5.14)
    history = {str(squad[9]["id"]): [_history_point(2, 1_010_000), _history_point(1, 1_080_000)]}

    assert decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought, own_lineup_player_ids={squad[9]["id"]},
        recent_price_history=history, **_PROFIT_KWARGS,
    ) == []


def test_decide_sales_profit_multiplier_uses_recency_weighted_form():
    """Media de temporada alta pero apagado en las últimas jornadas: ya no protege la plusvalía."""
    squad, bought = _profit_squad(1_200_000)  # +20%
    squad[9]["average"] = {"average": 5.0, "fitness": [12, 10, 0, 0, 0]}
    assert len(decide_sales(squad, formation="4-4-2", bought_by_bot=bought, **_PROFIT_KWARGS)) == 1

    squad, bought = _profit_squad(1_200_000)
    squad[9]["average"] = {"average": 2.0, "fitness": [0, 0, 4, 7, 9]}  # en forma ahora -> umbral más alto
    assert decide_sales(squad, formation="4-4-2", bought_by_bot=bought, **_PROFIT_KWARGS) == []


# --- Protección de los mejores y rotación sobre los peores, por posición (a
# petición del usuario, 2026-09-23, ver docstring del módulo) ---


def _del_squad_with_averages(averages):
    """4-4-2 + delanteros extra; `averages` = medias de TODOS los delanteros (ids 30, 31, 35, 36...)."""
    squad = _full_442_squad()
    for extra_id in (35, 36)[: max(0, len(averages) - 2)]:
        squad.append({"id": extra_id, "name": f"Delantero{extra_id}", "role": "delantero", "status": "", "value": 500_000})
    delanteros = [p for p in squad if p["role"] == "delantero"]
    for p, avg in zip(delanteros, averages):
        p["average"] = {"average": avg}
    return squad


def test_decide_sales_top_players_are_protected_from_profit_sale():
    """4 delanteros, 2 titulares en 4-4-2: el mejor (media 6) no se vende por plusvalía; el peor sí."""
    squad = _del_squad_with_averages([6.0, 5.0, 2.0, 1.0])
    squad[9]["value"] = 1_300_000   # id 30, el mejor, +30%
    squad[-1]["value"] = 1_300_000  # id 36, el peor, +30%
    bought = {"30": 1_000_000, "36": 1_000_000}

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought, min_profit_pct=0.15, profit_avg_points_max_mult=1.0,
        protect_top_players_from_profit=True, enable_weekend_lineup_guard=False, now=NOW,
    )
    assert [d["player_id"] for d in decisions] == [36]


def test_decide_sales_protection_can_be_disabled():
    squad = _del_squad_with_averages([6.0, 5.0, 2.0, 1.0])
    squad[9]["value"] = 1_300_000
    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot={"30": 1_000_000}, min_profit_pct=0.15, profit_avg_points_max_mult=1.0,
        protect_top_players_from_profit=False, top_players_require_replacement=False,
        enable_weekend_lineup_guard=False, now=NOW,
    )
    assert [d["player_id"] for d in decisions] == [30]


def _market_row(player_id, position, price, average, status="", listing_price=None):
    return {"id": player_id, "position": position, "price": price, "average_points": average,
            "status": status, "listing_price": listing_price}


def _top_trailing_stop_run(market, budget=10_000_000, status="", **kwargs):
    """Delantero id 30 (el mejor, media 6, vale 1.2M) cae -25% desde su pico -> trailing-stop."""
    squad = _del_squad_with_averages([6.0, 5.0, 2.0, 1.0])
    squad[9]["value"] = 1_200_000
    squad[9]["status"] = status
    return decide_sales(
        squad, formation="4-4-2", bought_by_bot={"30": 1_000_000}, min_profit_pct=0.15, budget=budget,
        purchase_baselines={"30": {"peak_price": 1_600_000}}, trailing_stop_max_drawdown_pct=0.20,
        market_candidates=market, replacement_min_form_ratio=1.0, replacement_min_listing_hours=6,
        protect_top_players_from_profit=True, top_players_require_replacement=True,
        enable_weekend_lineup_guard=False, now=NOW, **kwargs,
    )


def test_decide_sales_top_player_is_kept_without_market_replacement():
    """Top de su posición: el trailing-stop ya no basta, hace falta sustituto en mercado."""
    assert _top_trailing_stop_run(market=[]) == []


def test_decide_sales_top_player_listed_with_replacement_to_buy_first():
    market = [_market_row("m1", "DEL", 1_000_000, 7.0)]  # más forma y 143k/punto < 200k/punto propio
    decisions = _top_trailing_stop_run(market)
    assert len(decisions) == 1
    d = decisions[0]
    assert d["player_id"] == 30
    assert d["replacement_target_player_id"] == "m1"
    assert d["replacement_target_price"] == 1_000_000
    assert d["swap_target_player_id"] is None
    assert "tras fichar al sustituto m1" in d["reason"]


def test_decide_sales_top_replacement_must_not_lower_form():
    """Más barato por punto pero con menos forma: bajaría la media de la posición -> no vale."""
    assert _top_trailing_stop_run([_market_row("m1", "DEL", 500_000, 5.0)]) == []


def test_decide_sales_top_replacement_must_have_better_price_per_point():
    """Más forma pero más caro por punto (2.4M / 8 = 300k > 200k) -> no vale."""
    assert _top_trailing_stop_run([_market_row("m1", "DEL", 2_400_000, 8.0)]) == []


def test_decide_sales_top_replacement_cost_uses_listing_price_when_higher():
    """VM barato pero precio de salida de 2M: 2M / 7 = 286k/punto > 200k -> no vale."""
    assert _top_trailing_stop_run([_market_row("m1", "DEL", 1_000_000, 7.0, listing_price=2_000_000)]) == []


def test_decide_sales_top_replacement_must_be_affordable_before_selling():
    """Se compra ANTES de vender: el presupuesto actual tiene que cubrirlo sin contar la venta."""
    market = [_market_row("m1", "DEL", 1_000_000, 7.0)]
    assert _top_trailing_stop_run(market, budget=900_000) == []
    assert len(_top_trailing_stop_run(market, budget=900_000, replacement_budget=1_000_000)) == 1


def test_decide_sales_top_replacement_ignores_other_positions_injured_and_expiring_listings():
    market = [
        _market_row("m1", "MED", 500_000, 9.0),
        _market_row("m2", "DEL", 500_000, 9.0, status="injured1"),
        _market_row("m3", "DEL", 500_000, 9.0),
    ]
    expirations = {"m3": (NOW + timedelta(hours=2)).isoformat()}
    assert _top_trailing_stop_run(market, market_listing_expirations=expirations) == []


def test_decide_sales_top_replacement_respects_max_purchases():
    market = [_market_row("m1", "DEL", 1_000_000, 7.0)]
    assert _top_trailing_stop_run(market, max_replacement_purchases=0) == []


def test_decide_sales_doubtful_top_player_still_requires_replacement():
    """"doubt" no es excepción: un buen jugador en duda no se pierde por un corte sin sustituto."""
    assert _top_trailing_stop_run(market=[], status="doubt") == []


def test_decide_sales_confirmed_injured_top_player_is_sold_without_replacement():
    """La lesión confirmada es la única excepción."""
    decisions = _top_trailing_stop_run(market=[], status="injured2")
    assert len(decisions) == 1
    assert decisions[0]["replacement_target_player_id"] is None


def test_decide_sales_non_top_player_needs_no_replacement():
    """El peor delantero (media 1) sale por trailing-stop sin necesidad de sustituto."""
    squad = _del_squad_with_averages([6.0, 5.0, 2.0, 1.0])
    squad[-1]["value"] = 1_200_000
    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot={"36": 1_000_000}, min_profit_pct=0.15,
        purchase_baselines={"36": {"peak_price": 1_600_000}}, trailing_stop_max_drawdown_pct=0.20,
        top_players_require_replacement=True, enable_weekend_lineup_guard=False, now=NOW,
    )
    assert [d["player_id"] for d in decisions] == [36]
    assert decisions[0]["replacement_target_player_id"] is None


def test_decide_sales_two_tops_cannot_share_the_same_replacement():
    squad = _del_squad_with_averages([6.0, 6.0, 2.0, 1.0])
    for p in squad[9:11]:
        p["value"] = 1_200_000
    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot={"30": 1_000_000, "31": 1_000_000}, min_profit_pct=0.15,
        budget=10_000_000, purchase_baselines={"30": {"peak_price": 1_600_000}, "31": {"peak_price": 1_600_000}},
        trailing_stop_max_drawdown_pct=0.20, market_candidates=[_market_row("m1", "DEL", 1_000_000, 7.0)],
        top_players_require_replacement=True, enable_weekend_lineup_guard=False, now=NOW,
    )
    assert len(decisions) == 1


def test_decide_sales_top_player_is_never_sold_for_profit_even_with_replacement():
    """El mejor delantero con +30% se queda aunque el mercado ofrezca un sustituto igual o mejor."""
    squad = _del_squad_with_averages([6.0, 5.0, 2.0, 1.0])
    squad[9]["value"] = 1_300_000
    assert decide_sales(
        squad, formation="4-4-2", bought_by_bot={"30": 1_000_000}, min_profit_pct=0.15,
        profit_avg_points_max_mult=1.0, budget=10_000_000, market_candidates=[_market_row("m1", "DEL", 1_000_000, 7.0)],
        protect_top_players_from_profit=True, top_players_require_replacement=True,
        enable_weekend_lineup_guard=False, now=NOW,
    ) == []


def test_rank_positions_protects_the_upper_half_but_never_fewer_than_starters():
    from engine.selling_strategy import rank_positions

    squad = [{"id": 30 + i, "role": "delantero", "status": ""} for i in range(6)]
    squad += [{"id": 10 + i, "role": "defensa", "status": ""} for i in range(6)]
    scores = {str(p["id"]): float(10 - (p["id"] % 10)) for p in squad}  # id más bajo = mejor
    protected, _ = rank_positions(squad, scores, formation="4-4-2", min_share=0.5)
    assert {"30", "31", "32"} <= protected and "33" not in protected  # 6 DEL: 3 (mitad) > 2 titulares
    assert {"10", "11", "12", "13"} <= protected and "14" not in protected  # 6 DEF: 4 titulares > 3 (mitad)


def test_decide_sales_third_of_six_forwards_is_now_protected():
    """6 delanteros: el 3º (media 4) ya es de la mitad superior -> su trailing-stop exige sustituto."""
    squad = _full_442_squad()
    for extra_id in (35, 36, 37, 38):
        squad.append({"id": extra_id, "name": f"Delantero{extra_id}", "role": "delantero", "status": "", "value": 500_000})
    delanteros = [p for p in squad if p["role"] == "delantero"]
    for p, avg in zip(delanteros, [6.0, 5.0, 4.0, 3.0, 2.0, 1.0]):
        p["average"] = {"average": avg}
    delanteros[2]["value"] = 1_200_000
    kwargs = dict(
        formation="4-4-2", bought_by_bot={str(delanteros[2]["id"]): 1_000_000}, min_profit_pct=0.15,
        purchase_baselines={str(delanteros[2]["id"]): {"peak_price": 1_600_000}}, trailing_stop_max_drawdown_pct=0.20,
        top_players_require_replacement=True, enable_weekend_lineup_guard=False, now=NOW,
    )
    assert decide_sales(squad, **kwargs) == []
    assert len(decide_sales(squad, **{**kwargs, "top_players_require_replacement": False})) == 1


def _worst_protected_upgrade_run(sellable_ids, market_form=10.0, market_price=400_000):
    """
    4 DEL con medias [6, 5, 2, 1] -> protegidos 30 y 31 (el peor de ellos, 31).
    Los `sellable_ids` son del bot y tienen score de alineación bajo; el
    mercado ofrece un DEL claramente mejor (y con más forma y mejor ratio).
    """
    squad = _del_squad_with_averages([6.0, 5.0, 2.0, 1.0])
    own = [_feature_row(int(pid), "DEL", xg=0.0, minutes_played=10) for pid in sellable_ids]
    own += [_feature_row(pid, "DEL", xg=2.0, minutes_played=800) for pid in (30, 31, 35, 36) if str(pid) not in sellable_ids]
    market = [_feature_row("m1", "DEL", price=market_price, points=market_form, xg=5.0, minutes_played=900)]
    return decide_sales(
        squad, formation="4-4-2", bought_by_bot={pid: 500_000 for pid in sellable_ids}, budget=10_000_000,
        own_squad_features=own, market_candidates=market, upgrade_available_min_margin=0.05,
        upgrade_only_worst_per_position=True, top_players_require_replacement=True,
        protect_top_players_from_profit=True, enable_weekend_lineup_guard=False, now=NOW,
    )


def test_decide_sales_worst_protected_can_be_upgraded_buying_first():
    decisions = _worst_protected_upgrade_run(["31"])
    assert [d["player_id"] for d in decisions] == [31]
    assert decisions[0]["replacement_target_player_id"] == "m1"
    assert decisions[0]["swap_target_player_id"] is None
    assert "oportunidad de mercado" in decisions[0]["reason"]


def test_decide_sales_worst_protected_upgrade_still_needs_a_valid_replacement():
    """El candidato mejora el score de alineación pero tiene menos forma (4 < 5): no se cambia."""
    assert _worst_protected_upgrade_run(["31"], market_form=4.0) == []


def test_decide_sales_best_protected_is_not_upgradeable():
    """El 30 (el mejor) no es el peor de los protegidos: no se cambia por oportunidad de mercado."""
    assert _worst_protected_upgrade_run(["30"]) == []


def test_decide_sales_unprotected_worst_takes_priority_over_worst_protected():
    """Si el peor no protegido (36) también puede cambiarse, se vende él y el 31 espera."""
    decisions = _worst_protected_upgrade_run(["31", "36"])
    assert [d["player_id"] for d in decisions] == [36]
    assert decisions[0]["replacement_target_player_id"] is None


def test_decide_sales_market_upgrade_only_replaces_the_worst_of_the_position():
    """
    Dos DEF propios peores que el candidato de mercado: solo el PEOR es
    vendible por oportunidad de mercado. Si el peor está recién fichado, no
    se vende al otro en su lugar.
    """
    squad = _full_442_squad()
    squad.append({"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000})
    squad.append({"id": 16, "name": "Defensa Extra2", "role": "defensa", "status": "", "value": 500_000})
    own = [
        _feature_row(10, "DEF", xg=0.0, minutes_played=10),   # el peor
        _feature_row(15, "DEF", xg=1.0, minutes_played=300),
        _feature_row(11, "DEF", xg=2.0, minutes_played=800),
        _feature_row(12, "DEF", xg=2.0, minutes_played=800),
        _feature_row(13, "DEF", xg=2.0, minutes_played=800),
        _feature_row(16, "DEF", xg=2.0, minutes_played=800),
    ]
    market = [_feature_row("mercado1", "DEF", xg=5.0, minutes_played=900)]
    bought = {"10": 500_000, "15": 500_000}

    def run(worst_days_held):
        baselines = {
            "10": {"peak_price": 500_000, "value_at_purchase": 500_000, "purchased_at": (NOW - timedelta(days=worst_days_held)).isoformat()},
            "15": {"peak_price": 500_000, "value_at_purchase": 500_000, "purchased_at": (NOW - timedelta(days=30)).isoformat()},
        }
        return decide_sales(
            squad, formation="4-4-2", bought_by_bot=bought, own_squad_features=own, market_candidates=market,
            purchase_baselines=baselines, upgrade_available_min_margin=0.05, upgrade_min_hold_days=7,
            upgrade_only_worst_per_position=True, enable_weekend_lineup_guard=False, now=NOW,
        )

    assert [d["player_id"] for d in run(30)] == [10]
    assert run(1) == []  # el peor aún no es vendible -> no se vende al 15 en su lugar


# --- Cambiar al peor en vez de vender al top (a petición del usuario,
# 2026-09-24, ver docstring del módulo) ---


def _top_swap_run(bought_extra, market=None, squad_tweak=None, baselines_extra=None, **kwargs):
    """
    Mismo escenario que `_top_trailing_stop_run` (top 30 con trailing-stop y
    sustituto m1 en mercado), pero con más jugadores comprados por el bot
    (`bought_extra`) que pueden venderse en su lugar. DEL: 30 (6), 31 (5),
    35 (2), 36 (1).
    """
    squad = _del_squad_with_averages([6.0, 5.0, 2.0, 1.0])
    squad[9]["value"] = 1_200_000
    if squad_tweak:
        squad_tweak({str(p["id"]): p for p in squad})
    market = [_market_row("m1", "DEL", 1_000_000, 7.0)] if market is None else market
    return decide_sales(
        squad, formation="4-4-2", bought_by_bot={"30": 1_000_000, **bought_extra}, min_profit_pct=0.15,
        budget=10_000_000, purchase_baselines={"30": {"peak_price": 1_600_000}, **(baselines_extra or {})},
        trailing_stop_max_drawdown_pct=0.20, market_candidates=market, replacement_min_form_ratio=1.0,
        replacement_min_listing_hours=6, protect_top_players_from_profit=True, top_players_require_replacement=True,
        enable_weekend_lineup_guard=False, now=NOW, **kwargs,
    )


def test_decide_sales_top_with_replacement_sells_the_worst_instead():
    decisions = _top_swap_run({"36": 500_000})
    assert [d["player_id"] for d in decisions] == [36]
    d = decisions[0]
    assert d["replacement_target_player_id"] == "m1"
    assert d["replacement_target_price"] == 1_000_000
    assert d["swap_target_player_id"] is None
    assert d["asking_price"] == 500_000 and d["purchase_price"] == 500_000
    assert "en vez de vender al top 30" in d["reason"]
    assert "corte por reversión desde máximo" in d["reason"]


def test_decide_sales_top_swap_skips_worst_already_listed():
    decisions = _top_swap_run(
        {"35": 500_000, "36": 500_000}, squad_tweak=lambda by_id: by_id["36"].update(market=True)
    )
    assert [d["player_id"] for d in decisions] == [35]
    assert decisions[0]["replacement_target_player_id"] == "m1"


def test_decide_sales_top_swap_ignores_min_hold_days():
    """Un recién fichado sí puede cambiarse por el sustituto del top."""
    recent = {"36": {"purchased_at": (NOW - timedelta(days=1)).isoformat()}}
    decisions = _top_swap_run({"36": 500_000}, baselines_extra=recent)
    assert [d["player_id"] for d in decisions] == [36]


def test_decide_sales_top_swap_skips_injured_and_doubtful():
    decisions = _top_swap_run(
        {"35": 500_000, "36": 500_000},
        squad_tweak=lambda by_id: (by_id["35"].update(status="doubt"), by_id["36"].update(status="injured1")),
    )
    # Sin peor sano: se vende el top con su sustituto, como antes (el 36
    # lesionado sale igualmente por la vía de lesión, sin sustituto).
    assert [(d["player_id"], d["replacement_target_player_id"]) for d in decisions] == [(30, "m1"), (36, None)]


def test_decide_sales_top_swap_falls_back_to_top_without_sellable_worst():
    decisions = _top_swap_run({})
    assert [d["player_id"] for d in decisions] == [30]
    assert decisions[0]["replacement_target_player_id"] == "m1"


def test_decide_sales_top_swap_can_be_disabled():
    decisions = _top_swap_run({"36": 500_000}, top_swap_worst_instead=False)
    assert [d["player_id"] for d in decisions] == [30]


def test_decide_sales_top_swap_needs_a_replacement():
    assert _top_swap_run({"36": 500_000}, market=[]) == []


def test_decide_sales_top_swap_never_sells_the_same_player_twice():
    """El peor también cumple su propio trailing-stop: una sola decisión por jugador."""
    decisions = _top_swap_run(
        {"36": 500_000},
        squad_tweak=lambda by_id: by_id["36"].update(value=400_000),
        baselines_extra={"36": {"peak_price": 1_000_000}},
    )
    ids = [d["player_id"] for d in decisions]
    assert len(ids) == len(set(ids))
    assert 36 in ids
