"""
Job: fija la alineación antes del cierre de jornada.

TODO: implementar en cuanto comunio_client.py tenga get_squad/set_lineup
reales.
"""
from notifier import notify


def run():
    # TODO:
    #   1. client = ComunioClient(); client.login()
    #   2. squad = client.get_squad()
    #   3. expected_scores = <combinar evaluator + forma reciente + rival>
    #   4. lineup = lineup_optimizer.pick_lineup(squad)
    #   5. client.set_lineup(lineup["formation"], lineup["starters"])
    #   6. Persistir en `lineup_decisions` con motivo
    #   7. notify(resumen del once elegido y motivo)
    notify("set_lineup: pendiente de implementar (bloqueado por captura de HAR de Comunio)")


if __name__ == "__main__":
    run()
