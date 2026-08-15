"""
Job: actualiza stats externas + estado de Comunio en la base de datos.

Pensado para correr por cron (GitHub Actions) con más frecuencia que los
jobs de mercado/alineación, ya que alimenta a ambos.

TODO: implementar en cuanto comunio_client.py y laliga_stats_client.py
tengan sus métodos reales (no NotImplementedError).
"""
from db.models import init_db
from notifier import notify


def run():
    init_db()
    # TODO:
    #   1. client = ComunioClient(); client.login()
    #   2. squad = client.get_squad(); market = client.get_market()
    #   3. Para cada jugador: stats = laliga_stats_client.get_player_xg(...) etc.
    #   4. Persistir todo en price_history / external_stats
    notify("sync_data: pendiente de implementar (bloqueado por captura de HAR de Comunio)")


if __name__ == "__main__":
    run()
