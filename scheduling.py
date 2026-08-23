"""
Ventanas horarias en hora LOCAL (Europe/Madrid) para jobs cuyo cron de
GitHub Actions -- siempre en UTC, sin soporte de zona horaria propio --
necesita cubrir un horario que se piensa en hora española, no en UTC (ver
README, "Delay de GitHub Actions y horas críticas").

El problema real que resuelve: si el cron se deja fijo en UTC, la MISMA
hora UTC cae en una hora local distinta según haya cambio de hora o no
(CET, UTC+1, en invierno; CEST, UTC+2, en verano) -- un rango en UTC
pensado para cubrir, por ejemplo, 17:23-21:43 hora española en verano
cubre en cambio 16:23-20:43 en invierno, un margen distinto sin que nadie
haya tocado nada. Ajustar el cron a mano dos veces al año (marzo/octubre,
y encima en fechas que cambian cada año) es frágil y fácil de olvidar.

La solución de este módulo: el `.yml` del workflow deja el cron con un
rango en UTC lo bastante ancho para cubrir la ventana local deseada TANTO
en CET como en CEST (la unión de los dos rangos posibles -- ver el
cálculo en el propio `.yml`), y `is_within_local_window()` filtra, ya
dentro del job, las ejecuciones que caen fuera de la ventana local real.
Usa `zoneinfo` (stdlib desde Python 3.9, sin dependencia extra) contra la
base de datos de zonas horaria del sistema, que sabe exactamente cuándo
cambia la hora cada año -- así el filtro es correcto siempre, sin
mantenimiento.
"""
from zoneinfo import ZoneInfo

MADRID_TZ = ZoneInfo("Europe/Madrid")


def is_within_local_window(now_utc, start_hm, end_hm):
    """
    True si `now_utc` (datetime tz-aware, en UTC) cae, convertido a hora
    local de Europe/Madrid, dentro de [`start_hm`, `end_hm`] (ambos
    límites incluidos). `start_hm`/`end_hm` son tuplas (hora, minuto) en
    hora local de Madrid, p. ej. (17, 23).
    """
    local = now_utc.astimezone(MADRID_TZ)
    now_minutes = local.hour * 60 + local.minute
    start_minutes = start_hm[0] * 60 + start_hm[1]
    end_minutes = end_hm[0] * 60 + end_hm[1]
    return start_minutes <= now_minutes <= end_minutes
