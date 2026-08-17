"""
Notificaciones vía Telegram Bot API (gratis).

Uso típico al final de cada job:
    from notifier import notify
    notify("✅ run_market: puja de 3.2M en Jugador X (score 0.81)")
"""
import contextlib

import requests

import config


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
def notify_on_crash(job_name: str):
    """
    Para envolver el entry point de un job (`if __name__ == "__main__":`,
    ver jobs/*.py): si `run()` deja escapar CUALQUIER excepción no
    controlada más abajo, la notifica por Telegram antes de dejarla
    propagar tal cual (GitHub Actions sigue marcando el job en rojo igual
    -- esto no la traga, solo añade el aviso).

    Motivación — caso real (2026-08-17): `jobs/run_market.py` cayó entero
    en GitHub Actions por un `RemoteDisconnected` al llamar a Futmondo
    (ver `clients/futmondo_client.py._post`, ahora con reintento para ese
    caso concreto, pero cualquier otro fallo no controlado tenía el mismo
    problema) sin que llegara ningún aviso — el usuario solo se enteró
    revisando los logs de Actions a mano. Cada job ya notifica siempre sus
    fallos "esperados" (rechazo de una puja, envío parcial de la
    alineación...) vía `notify()` al final de `run()`; este wrapper cubre
    el hueco de los fallos que ni siquiera llegan a esa última línea.

    Uso:
        if __name__ == "__main__":
            with notify_on_crash("run_market"):
                run()
    """
    try:
        yield
    except Exception as e:
        notify(f"🔴 {job_name}: fallo no controlado, el job se detuvo sin completar ({type(e).__name__}: {e}).")
        raise
