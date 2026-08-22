from engine.selling_strategy import decide_sales

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


# --- Corte de pérdidas: genérico + más agresivo en lesión confirmada
# (a petición del usuario, 2026-08-22 -- versión "radical": el corte
# aplica a CUALQUIER jugador, pero es más bajo para lesión confirmada) ---
#
# En todos estos tests: min_profit_pct=0.10, max_loss_pct=0.20 (genérico),
# injury_max_loss_pct=0.10 (lesión confirmada, más bajo que el genérico).


def test_decide_sales_ignores_small_loss_below_any_threshold():
    """Una pérdida pequeña (por debajo de CUALQUIER umbral) no fuerza la venta."""
    squad = _full_442_squad()
    squad[9]["status"] = "injured2"  # Delantero0, lesión confirmada
    bought_by_bot = {str(squad[9]["id"]): 500_000}
    squad[9]["value"] = 480_000  # -4%: ni +10% de rentabilidad ni -10% de corte por lesión

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.20, injury_max_loss_pct=0.10,
    )
    assert decisions == []


def test_decide_sales_confirmed_injury_cuts_losses_at_lower_threshold_than_generic():
    """
    Un lesionado CONFIRMADO se vende con una pérdida (-12%) que NO llegaría
    a activar el corte genérico (-20%) -- su umbral es más bajo a
    propósito: tiende a seguir perdiendo valor, así que se corta antes.
    """
    squad = _full_442_squad()
    squad[9]["status"] = "injured2"  # Delantero0, lesión confirmada
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%: por debajo del -20% genérico, pero por encima del -10% de lesión

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.20, injury_max_loss_pct=0.10,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    assert decisions[0]["profit"] == -120_000
    assert "falacia del coste hundido" in decisions[0]["reason"]
    assert "lesión confirmada" in decisions[0]["reason"]


def test_decide_sales_generic_loss_cut_applies_to_healthy_player_too():
    """
    A petición del usuario: el corte de pérdidas ya NO es exclusivo de
    lesionados -- un jugador SANO con una pérdida grande (-25%, por encima
    del umbral genérico del 20%) también se vende, sin esperar a que
    "recupere" (con margen de banquillo de sobra, igual que exige
    cualquier venta de un jugador sano -- ver test de más abajo para el
    caso SIN margen).
    """
    squad = _full_442_squad()
    squad.append({"id": 35, "name": "Delantero Extra", "role": "delantero", "status": "", "value": 500_000})
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 750_000  # -25%, sano

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.20, injury_max_loss_pct=0.10,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
    assert "corte de pérdidas genérico" in decisions[0]["reason"]


def test_decide_sales_doubt_status_uses_generic_threshold_not_the_lower_injury_one():
    """
    "doubt" (duda, no lesión confirmada) usa el umbral GENÉRICO, no el más
    bajo de lesión -- con -12% (activaría el corte de lesión) NO se vende,
    porque no llega al -20% genérico.
    """
    squad = _full_442_squad()
    squad[9]["status"] = "doubt"  # Delantero0, solo duda
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 880_000  # -12%: misma pérdida que activaría el corte de lesión confirmada

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.20, injury_max_loss_pct=0.10,
    )
    assert decisions == []


def test_decide_sales_doubt_status_still_gets_generic_loss_cut_eventually():
    """"doubt" no está exento del corte de pérdidas -- con -25% sí supera el umbral genérico y se vende."""
    squad = _full_442_squad()
    squad[9]["status"] = "doubt"
    bought_by_bot = {str(squad[9]["id"]): 1_000_000}
    squad[9]["value"] = 750_000  # -25%, supera el umbral genérico del 20%

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.20, injury_max_loss_pct=0.10,
    )
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
    squad[0]["value"] = 850_000  # -15%, por encima del -10% de corte por lesión

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.20, injury_max_loss_pct=0.10,
    )
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[0]["id"]


def test_decide_sales_generic_loss_cut_still_blocked_by_bench_margin_for_healthy_player():
    """
    A diferencia de un lesionado, el corte de pérdidas de un jugador SANO
    sigue respetando el margen de suplentes -- si es el único de su
    posición, no se vende aunque supere el umbral genérico de pérdida.
    """
    squad = _full_442_squad()
    bought_by_bot = {str(squad[0]["id"]): 1_000_000}  # Portero, único de su posición, sano
    squad[0]["value"] = 700_000  # -30%, muy por encima del umbral genérico del 20%

    decisions = decide_sales(
        squad, formation="4-4-2", bought_by_bot=bought_by_bot,
        min_profit_pct=0.10, max_loss_pct=0.20, injury_max_loss_pct=0.10,
    )
    assert decisions == []
