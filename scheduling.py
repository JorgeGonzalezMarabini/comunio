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


# Inicio de la ventana de jobs/set_lineup.py (viernes, hora local de
# Madrid): desde la primera pasada la alineación guardada ya es la de la
# jornada. Fuente única para set_lineup y para el bloqueo de jornada.
SET_LINEUP_WINDOW_START = (17, 23)
_MONDAY = 0
_FRIDAY = 4


def is_matchday_lineup_guard_time(now_utc):
    """
    True desde el viernes a las `SET_LINEUP_WINDOW_START` (hora local de
    Madrid, primera pasada de jobs/set_lineup.py) hasta el final del
    lunes: la jornada de LaLiga empieza el viernes por la tarde y puede
    acabar con partido el lunes (a petición del usuario, 2026-10-10 --
    antes era sábado/domingo en UTC y dejaba fuera viernes y lunes). Es cuando la alineación guardada es la de la
    jornada y no debe venderse a un titular suyo (ver
    config.ENABLE_SELLING_WEEKEND_LINEUP_GUARD).
    """
    local = now_utc.astimezone(MADRID_TZ)
    if local.weekday() > _FRIDAY or local.weekday() == _MONDAY:
        return True
    return local.weekday() == _FRIDAY and (local.hour, local.minute) >= SET_LINEUP_WINDOW_START


# Reglas de la liga para clausulazos (indicadas por el usuario, 2026-11-08):
# solo de lunes a viernes, y el viernes solo hasta las 18:00 hora de Madrid.
CLAUSE_FRIDAY_DEADLINE = (18, 0)


def is_clause_window(now_utc):
    """
    True si `now_utc` cae, en hora local de Madrid, de lunes a jueves (todo
    el día) o en viernes ANTES de `CLAUSE_FRIDAY_DEADLINE` -- cuándo la liga
    permite pagar cláusulas (ver jobs/run_clauses.py).
    """
    local = now_utc.astimezone(MADRID_TZ)
    if local.weekday() < _FRIDAY:
        return True
    return local.weekday() == _FRIDAY and (local.hour, local.minute) < CLAUSE_FRIDAY_DEADLINE


def start_of_local_week(now_utc):
    """
    Lunes a las 00:00 hora de Madrid de la semana de `now_utc`, devuelto en
    UTC -- el contador semanal de clausulazos de Futmondo se reinicia el
    lunes a las 00:00 (ayuda oficial, "¿Qué son las cláusulas y cómo
    activarlas?").
    """
    from datetime import timedelta, timezone

    local = now_utc.astimezone(MADRID_TZ)
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.astimezone(timezone.utc)
