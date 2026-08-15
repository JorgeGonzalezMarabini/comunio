"""
Notificaciones vía Telegram Bot API (gratis).

Uso típico al final de cada job:
    from notifier import notify
    notify("✅ run_market: puja de 3.2M en Jugador X (score 0.81)")
"""
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
