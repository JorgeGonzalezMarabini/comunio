from engine.selling_strategy import decide_sales

# _full_442_squad(): índices fijos para que los tests puedan referenciar
# jugadores concretos sin ambigüedad.
#   [0]        portero
#   [1..4]     defensas (Defensa0..3)
#   [5..8]     centrocampistas (Medio0..3)
#   [9..10]    delanteros (Delantero0..1)


def _full_442_squad():
    """
    Plantilla 4-4-2 base (todos sanos, buyPrice=0 -> ningún candidato de
    venta por sí sola, ver TODO en engine/selling_strategy.py: a diferencia
    de Comunio, aquí NO se filtra por "comprado por el bot", solo se exige
    buyPrice > 0) sobre la que cada test modifica sus propios casos.
    """
    squad = [{"id": 1, "name": "Portero", "role": "portero", "status": "", "value": 500_000, "buyPrice": 0}]
    squad += [
        {"id": 10 + i, "name": f"Defensa{i}", "role": "defensa", "status": "", "value": 500_000, "buyPrice": 0}
        for i in range(4)
    ]
    squad += [
        {"id": 20 + i, "name": f"Medio{i}", "role": "centrocampista", "status": "", "value": 500_000, "buyPrice": 0}
        for i in range(4)
    ]
    squad += [
        {"id": 30 + i, "name": f"Delantero{i}", "role": "delantero", "status": "", "value": 500_000, "buyPrice": 0}
        for i in range(2)
    ]
    return squad


def test_decide_sales_ignores_players_without_buy_price():
    squad = _full_442_squad()
    assert decide_sales(squad, formation="4-4-2") == []


def test_decide_sales_below_profit_threshold_is_ignored():
    squad = _full_442_squad()
    squad[5]["buyPrice"] = 500_000  # Medio0
    squad[5]["value"] = 520_000  # +4%, bajo el 10% por defecto
    assert decide_sales(squad, formation="4-4-2", min_profit_pct=0.10) == []


def test_decide_sales_blocks_sale_that_would_leave_position_uncovered():
    """
    Regresión del fallo real detectado: vender es tan capaz de dejar una
    posición sin cobertura (-4 puntos) como una cláusula de rescisión.
    El portero es el único de su posición -> NUNCA debe venderse aunque
    tenga mucha plusvalía.
    """
    squad = _full_442_squad()
    squad[0]["buyPrice"] = 300_000  # portero comprado barato, ahora vale 500k (+66%)

    decisions = decide_sales(squad, formation="4-4-2")
    assert decisions == []


def test_decide_sales_allows_sale_when_position_has_spare_bench():
    squad = _full_442_squad()
    squad[5]["buyPrice"] = 700_000  # Medio0, MED tiene sobra (4 para 4)... añado un 5º abajo
    squad[5]["value"] = 900_000  # +28.5%
    squad.append(
        {"id": 25, "name": "Medio Extra", "role": "centrocampista", "status": "", "value": 500_000, "buyPrice": 0}
    )
    decisions = decide_sales(squad, formation="4-4-2")
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 20
    assert decisions[0]["profit"] == 200_000


def test_decide_sales_prioritizes_most_profitable_when_bench_is_scarce():
    """Dos candidatos rentables en la misma posición, pero sin margen -> ninguno se vende; con 1 de margen, gana el más rentable."""
    squad = _full_442_squad()
    squad[1]["buyPrice"] = 1_000_000  # Defensa0: comprado a 1M
    squad[1]["value"] = 2_000_000  # ahora 2M, +100% -- el más rentable
    squad[2]["buyPrice"] = 1_000_000  # Defensa1: comprado a 1M
    squad[2]["value"] = 1_200_000  # ahora 1.2M, +20% -- menos rentable

    # DEF: 4 disponibles para 4 titulares -> bench=0, SIN margen -- ningún defensa debe venderse
    decisions = decide_sales(squad, formation="4-4-2")
    assert decisions == []

    # Añado un 5º defensa de sobra -> ahora hay bench=1, solo cabe 1 venta
    squad.append(
        {"id": 15, "name": "Defensa Extra", "role": "defensa", "status": "", "value": 500_000, "buyPrice": 0}
    )
    decisions = decide_sales(squad, formation="4-4-2")
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == 10  # Defensa0, el más rentable (+100%), gana el único hueco


def test_decide_sales_allows_selling_injured_profitable_player_freely():
    """Un lesionado no contaba como 'disponible' para cubrir su posición -> venderlo no empeora nada."""
    squad = _full_442_squad()
    squad[9]["status"] = "injured"  # Delantero0
    squad[9]["buyPrice"] = 300_000
    squad[9]["value"] = 500_000  # +66%, pero lesionado

    decisions = decide_sales(squad, formation="4-4-2")
    assert len(decisions) == 1
    assert decisions[0]["player_id"] == squad[9]["id"]
