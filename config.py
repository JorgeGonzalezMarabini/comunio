"""
Configuración centralizada del bot.

Todo lo que sea "número mágico" o credencial vive aquí (o en .env), nunca
hardcodeado dentro de engine/ o clients/. Así se puede ajustar el
comportamiento del bot sin tocar lógica.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# --- Comunio ---
# Capturado con Chrome DevTools el 2026-08-15 (login real + navegación autenticada).
# La web (www.comunio.es, Next.js) y la API REST (api.comunio.es) son dominios
# distintos: el bot habla siempre con api.comunio.es.
COMUNIO_EMAIL = os.getenv("COMUNIO_EMAIL")
COMUNIO_PASSWORD = os.getenv("COMUNIO_PASSWORD")
COMUNIO_BASE_URL = os.getenv("COMUNIO_BASE_URL", "https://api.comunio.es")
COMUNIO_AUTH_HEADER = os.getenv("COMUNIO_AUTH_HEADER", "Authorization")
# Esquema asumido por convención (access_token/refresh_token en localStorage,
# header "Authorization" confirmado por HAR). No se ha podido confirmar el
# valor literal del prefijo sin exponer el token real; si el cliente da 401
# con "Bearer", probar sin prefijo o con otro esquema.
COMUNIO_AUTH_SCHEME = os.getenv("COMUNIO_AUTH_SCHEME", "Bearer")

# Una liga privada = una "community". Se obtiene tras el login (a confirmar
# el campo exacto de la respuesta) o inspeccionando la URL de la app una vez
# dentro de la liga. De momento configurable a mano.
COMUNIO_COMMUNITY_ID = os.getenv("COMUNIO_COMMUNITY_ID")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Base de datos ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "db/comunio.db")

# --- Pesos del evaluador (engine/evaluator.py) ---
# Configurables para poder ajustarlos con el tiempo sin tocar código.
EVALUATOR_WEIGHTS = {
    "comunio_points_per_price": 0.35,   # rendimiento Comunio relativo al precio
    "comunio_trend": 0.15,              # tendencia de puntuación reciente
    "xg": 0.25,                         # expected goals (Understat)
    "minutes_played": 0.15,             # continuidad / peso en su equipo (FBref)
    "injury_penalty": 0.10,             # penalización si lesionado/duda
}

# --- Límites de seguridad de pujas (engine/bidding_strategy.py) ---
# Ninguno de estos límites se debe saltar nunca, pase lo que pase el modelo.
BIDDING_SAFETY_LIMITS = {
    "max_spend_per_player": 15_000_000,      # tope absoluto por jugador
    "max_budget_risk_per_matchday_pct": 0.30,  # % máx. del presupuesto restante jugable en una jornada
    "min_budget_reserve": 2_000_000,          # colchón que nunca se toca
}

# --- Alineación (engine/lineup_optimizer.py) ---
# Formato real del sitio (confirmado por captura): sin el "1-" del portero.
DEFAULT_FORMATION = os.getenv("DEFAULT_FORMATION", "4-4-2")

# --- Logging / auditoría ---
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
