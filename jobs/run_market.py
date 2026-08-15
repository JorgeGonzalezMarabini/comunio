"""
Job: evalúa el mercado y ejecuta pujas automáticas, 100% autónomo (sin
confirmación manual). Cada puja se audita en la tabla `bids` con el score
y motivo que la justificó.

TODO: implementar en cuanto comunio_client.py tenga get_market/place_bid
reales.
"""
from notifier import notify


def run():
    # TODO:
    #   1. client = ComunioClient(); client.login()
    #   2. market = client.get_market()
    #   3. stats = <leer de db para cada jugador del mercado>
    #   4. ranked = evaluator.rank_players(stats)
    #   5. Para cada candidato: decision = bidding_strategy.decide_bid(...)
    #   6. Si hay decision: client.place_bid(...); persistir en `bids`
    #   7. notify(resumen legible de todas las pujas hechas esta ejecución)
    notify("run_market: pendiente de implementar (bloqueado por captura de HAR de Comunio)")


if __name__ == "__main__":
    run()
