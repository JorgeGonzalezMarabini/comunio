"""
Notificaciones vía Telegram Bot API (gratis).

Uso típico al final de cada job:
    from notifier import notify
    notify("✅ run_market: puja de 3.200.000 en Jugador X (score 0.81)")
"""
import contextlib
import time
from datetime import datetime, timezone

import requests

import config
from db.models import record_job_run


def format_number(value) -> str:
    """
    Formatea una cantidad al estilo español para que se lea de un vistazo
    en el móvil: punto como separador de miles, coma como separador
    decimal (p. ej. 1234567 -> "1.234.567", 1234.5 -> "1.234,5"). Sin esto
    los importes salían en crudo (p. ej. "1234567"), muy difíciles de
    interpretar a golpe de vista en la notificación de Telegram.

    Los decimales de los `float` se recortan a 2 posiciones y los ceros
    finales se eliminan (1234.0 -> "1.234", no "1.234,00").
    """
    if isinstance(value, float):
        s = f"{value:,.2f}"
        integer_part, _, decimal_part = s.partition(".")
        decimal_part = decimal_part.rstrip("0")
        s = integer_part if not decimal_part else f"{integer_part}.{decimal_part}"
    else:
        s = f"{value:,}"
    # f"{:,}" usa "," para miles y "." para decimales (estilo EN) -- se
    # intercambian con un marcador intermedio para no chocar entre sí.
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def notify(message: str) -> bool:
    """
    Envía `message` al chat configurado. Devuelve True si se envió bien,
    False si falla (nunca lanza excepción: un fallo de notificación no debe
    tumbar un job).
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print(f"[notifier] Telegram no configurado, mensaje no enviado: {message}")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[notifier] Error enviando notificación: {e}")
        return False


@contextlib.contextmanager
def track_job_run(job_name: str):
    """
    Para envolver el entry point de un job (`if __name__ == "__main__":`,
    ver jobs/*.py). Dos responsabilidades (antes solo la primera, bajo el
    nombre `notify_on_crash` -- renombrado al añadir la segunda):

    1. Si `run()` deja escapar CUALQUIER excepción no controlada más
       abajo, la notifica por Telegram antes de dejarla propagar tal cual
       (GitHub Actions sigue marcando el job en rojo igual -- esto no la
       traga, solo añade el aviso).

       Motivación — caso real (2026-08-17): `jobs/run_market.py` cayó
       entero en GitHub Actions por un `RemoteDisconnected` al llamar a
       Futmondo (ver `clients/futmondo_client.py._post`, ahora con
       reintento para ese caso concreto, pero cualquier otro fallo no
       controlado tenía el mismo problema) sin que llegara ningún aviso —
       el usuario solo se enteró revisando los logs de Actions a mano.
       Cada job ya notifica siempre sus fallos "esperados" (rechazo de una
       puja, envío parcial de la alineación...) vía `notify()` al final de
       `run()`; este wrapper cubre el hueco de los fallos que ni siquiera
       llegan a esa última línea.

    2. Mide cuánto tarda `run()` (con `time.perf_counter()`, inmune a
       ajustes del reloj del sistema) y lo persiste en la tabla `job_runs`
       (ver db.models.record_job_run()), tanto si termina bien como si
       revienta -- antes no había ninguna forma de saber cuánto tarda cada
       job sin abrir a mano cada ejecución de GitHub Actions. También se
       imprime por stdout (visible en el log del step de Actions sin
       necesidad de consultar la BD).

       `record_job_run()` va protegido con su propio try/except (igual que
       `notify()` nunca lanza por un fallo de Telegram): un fallo al
       escribir en la BD (p. ej. bloqueada) no debe enmascarar la
       excepción original de `run()` ni impedir el aviso por Telegram del
       punto 1.

    Uso:
        if __name__ == "__main__":
            with track_job_run("run_market"):
                run()
    """

    def _record(status: str, elapsed: float, error: str = None) -> None:
        try:
            record_job_run(job_name, status, elapsed, started_at, error=error)
        except Exception as e:
            print(f"[notifier] Error persistiendo job_runs: {e}")

    started_at = datetime.now(timezone.utc)
    start = time.perf_counter()
    try:
        yield
    except Exception as e:
        elapsed = time.perf_counter() - start
        _record("error", elapsed, error=f"{type(e).__name__}: {e}")
        print(f"[{job_name}] duración: {elapsed:.1f}s (fallo: {type(e).__name__})")
        notify(f"🔴 {job_name}: fallo no controlado tras {elapsed:.1f}s, el job se detuvo sin completar ({type(e).__name__}: {e}).")
        raise
    else:
        elapsed = time.perf_counter() - start
        _record("ok", elapsed)
        print(f"[{job_name}] duración: {elapsed:.1f}s")
