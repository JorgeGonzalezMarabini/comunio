"""
Job: actualiza stats externas (Understat) + estado de Comunio en la base de
datos. Pensado para correr por cron (GitHub Actions) con más frecuencia que
los jobs de mercado/alineación, ya que alimenta a ambos.

Ver clients/comunio_client.py y clients/laliga_stats_client.py para el
detalle de qué está confirmado con captura real y qué sigue pendiente
(sobre todo: nombres de campo exactos del JSON de squad/exchangemarket de
Comunio, que aún no se han podido inspeccionar sin exponer el token).
"""
from datetime import datetime, timezone

from clients.comunio_client import ComunioClient
from clients.laliga_stats_client import get_league_data, index_players_by_name
from db.models import init_db, get_connection
from notifier import notify


def run():
    init_db()

    client = ComunioClient()
    client.login()

    # TODO: una vez confirmados los nombres de campo reales del JSON de
    # Comunio, mapear squad/market a la tabla `players` + `comunio_snapshots`.
    squad = client.get_squad()
    market = client.get_market()

    league_data = get_league_data()
    if not league_data.get("players"):
        # Ver current_season() en laliga_stats_client.py: normal justo al
        # arrancar temporada, Understat puede tardar días en publicar datos.
        notify("sync_data: Understat sin datos todavía para la temporada actual, se reintentará en el próximo sync.")
        return
    understat_by_name = index_players_by_name(league_data)

    now = datetime.now(timezone.utc).isoformat()
    # TODO: iterar squad+market, cruzar cada jugador con understat_by_name
    # por nombre normalizado, y persistir en players/comunio_snapshots/external_stats.

    notify(
        f"sync_data: Understat OK ({len(league_data['players'])} jugadores). "
        f"Falta mapear el JSON real de Comunio (squad/market) a la base de datos."
    )


if __name__ == "__main__":
    run()
