from datetime import datetime, timezone

from scheduling import is_within_local_window

WINDOW = ((17, 23), (21, 43))  # misma ventana local que usa jobs/set_lineup.py


def _utc(iso):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


def test_within_window_in_cest_summer():
    # Agosto = CEST (UTC+2): 15:23-19:43 UTC == 17:23-21:43 local.
    start, end = WINDOW
    assert is_within_local_window(_utc("2026-08-21T15:23:00"), start, end)  # borde inicial
    assert is_within_local_window(_utc("2026-08-21T17:30:00"), start, end)  # dentro
    assert is_within_local_window(_utc("2026-08-21T19:43:00"), start, end)  # borde final


def test_outside_window_in_cest_summer():
    start, end = WINDOW
    assert not is_within_local_window(_utc("2026-08-21T15:22:00"), start, end)  # 1 min antes
    assert not is_within_local_window(_utc("2026-08-21T19:44:00"), start, end)  # 1 min después


def test_within_window_in_cet_winter():
    # Enero = CET (UTC+1): la MISMA ventana local (17:23-21:43) cae ahora
    # en 16:23-20:43 UTC, una hora antes que en verano -- si el cron se
    # dejara fijo en el rango de verano (15:23-19:43 UTC), en invierno
    # correspondería a 16:23-20:43 local, no a la ventana real.
    start, end = WINDOW
    assert is_within_local_window(_utc("2026-01-16T16:23:00"), start, end)  # borde inicial
    assert is_within_local_window(_utc("2026-01-16T18:30:00"), start, end)  # dentro
    assert is_within_local_window(_utc("2026-01-16T20:43:00"), start, end)  # borde final


def test_outside_window_in_cet_winter():
    start, end = WINDOW
    assert not is_within_local_window(_utc("2026-01-16T16:22:00"), start, end)
    assert not is_within_local_window(_utc("2026-01-16T20:44:00"), start, end)


def test_fixed_utc_cron_would_miss_the_window_in_winter():
    """
    Demuestra el bug que resuelve este módulo: el cron viejo de
    set_lineup.yml fijaba 15:23 UTC pensando en la ventana local de
    verano. En invierno esa misma hora UTC (15:23) cae en las 16:23 CET,
    ANTES de que empiece la ventana local real (17:23) -- sin este
    filtro, esa pasada de invierno habría corrido fuera de ventana.
    """
    start, end = WINDOW
    assert not is_within_local_window(_utc("2026-01-16T15:23:00"), start, end)
