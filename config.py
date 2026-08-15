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
# Igual que COMUNIO_COMMUNITY_ID: necesario para casi todos los endpoints
# (van scoped a /users/{userId}/...) y todavía no se ha confirmado que venga
# en la respuesta de login, así que es configurable a mano de momento.
COMUNIO_USER_ID = os.getenv("COMUNIO_USER_ID")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Base de datos ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "db/comunio.db")

# --- Pesos del evaluador (engine/evaluator.py) ---
# Configurables para poder ajustarlos con el tiempo sin tocar código.
# Para DECIDIR PUJAS: el precio importa (relación calidad/precio del
# mercado, con presupuesto limitado que repartir entre candidatos).
EVALUATOR_WEIGHTS = {
    "comunio_points_per_price": 0.35,   # rendimiento Comunio relativo al precio
    "comunio_trend": 0.15,              # tendencia de puntuación reciente
    "xg": 0.25,                         # expected goals (Understat)
    "minutes_played": 0.15,             # continuidad / peso en su equipo
    "injury_penalty": 0.10,             # penalización si lesionado/duda
}

# Para ELEGIR ALINEACIÓN: el precio NO debe importar — un jugador de la
# plantilla ya está comprado, su precio es coste hundido. Reutilizar
# EVALUATOR_WEIGHTS aquí penalizaría injustamente a los fichajes caros
# (ver jobs/set_lineup.py). Mismas features, sin "comunio_points_per_price".
LINEUP_EVALUATOR_WEIGHTS = {
    "comunio_points_per_price": 0.0,
    "comunio_trend": 0.30,
    "xg": 0.40,
    "minutes_played": 0.30,
    "injury_penalty": 0.10,
}

# --- Límites de seguridad de pujas (engine/bidding_strategy.py) ---
# Ninguno de estos límites se debe saltar nunca, pase lo que pase el modelo.
BIDDING_SAFETY_LIMITS = {
    "max_spend_per_player": 15_000_000,      # tope absoluto por jugador
    "max_budget_risk_per_matchday_pct": 0.30,  # % máx. del presupuesto restante jugable en una jornada
    "min_budget_reserve": 2_000_000,          # colchón que nunca se toca
    # Cuánto por encima del precio/VM real del jugador (Comunio) se está
    # dispuesto a pujar como máximo, escalado por el score (0..1) del
    # jugador: prima_aplicada = max_premium_over_price_pct * score. Un
    # jugador con score 1.0 se puja hasta un +20% sobre su VM; uno con
    # score 0 no se puja por encima del VM.
    "max_premium_over_price_pct": 0.20,
}

# Score mínimo (ver engine/evaluator.score_player) para considerar pujar por
# un jugador. Punto de partida sin calibrar con datos reales todavía.
BIDDING_MIN_SCORE_THRESHOLD = float(os.getenv("BIDDING_MIN_SCORE_THRESHOLD", "0.15"))

# --- Alineación (engine/lineup_optimizer.py) ---
# Formato real del sitio (confirmado por captura): sin el "1-" del portero.
DEFAULT_FORMATION = os.getenv("DEFAULT_FORMATION", "4-4-2")

# Cuánto descuenta el score de un jugador la dificultad de su próximo rival
# (0 = ignorar dificultad, 1 = un rival con 100% prob. de no perder anula
# el score por completo). Ver engine.lineup_optimizer.apply_fixture_difficulty.
LINEUP_DIFFICULTY_WEIGHT = float(os.getenv("LINEUP_DIFFICULTY_WEIGHT", "0.2"))

# Guarda de seguridad: hasta que no se confirme con una prueba real completa
# la numeración de los 11 slots de `tactic`/`items.lineup` (ver
# clients/comunio_client.py, solo 2 de 11 verificados), el job set_lineup
# calcula y audita la decisión pero NO la envía a Comunio salvo que esto
# esté a True — evita arriesgar una alineación real con un mapeo adivinado.
ENABLE_LINEUP_AUTO_SUBMIT = os.getenv("ENABLE_LINEUP_AUTO_SUBMIT", "false").lower() == "true"

# --- Logging / auditoría ---
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
