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

# Cuánto sube el score de un candidato de mercado si su posición tiene
# riesgo de plantilla (ver engine.squad_risk.assess_squad_depth: sin
# ningún suplente sano, un "clausulazo"/lesión/sanción más dejaría un
# hueco en la alineación, -4 puntos). Aditivo, no multiplicativo — no
# fuerza la puja de un candidato realmente malo, solo le da ventaja frente
# a otro de score similar en una posición ya cubierta. Ver
# engine.bidding_strategy.apply_position_priority.
BIDDING_POSITION_RISK_BOOST = float(os.getenv("BIDDING_POSITION_RISK_BOOST", "0.15"))

# --- Venta de jugadores (engine/selling_strategy.py) ---
# % mínimo de plusvalía (precio actual vs. precio de compra real,
# "purchaseInfo.price" de squad) para considerar vender un jugador. Vender
# es la ÚNICA fuente de ingresos en Comunio (no hay salario pasivo, ver
# README) — la estrategia documentada es comprar barato y vender cuando
# sube; con un máximo de ±15%/día de fluctuación, un 10% es un punto de
# partida razonable, sin calibrar todavía con resultados reales.
SELLING_MIN_PROFIT_PCT = float(os.getenv("SELLING_MIN_PROFIT_PCT", "0.10"))

# --- Alineación (engine/lineup_optimizer.py) ---
# Formato real del sitio (confirmado por captura): sin el "1-" del portero.
DEFAULT_FORMATION = os.getenv("DEFAULT_FORMATION", "4-4-2")

# Cuánto descuenta el score de un jugador la dificultad de su próximo rival
# (0 = ignorar dificultad, 1 = un rival con 100% prob. de no perder anula
# el score por completo). Ver engine.lineup_optimizer.apply_fixture_difficulty.
LINEUP_DIFFICULTY_WEIGHT = float(os.getenv("LINEUP_DIFFICULTY_WEIGHT", "0.2"))

# Envía la alineación decidida a Comunio de verdad (además de auditarla en
# `lineup_decisions`, que pasa siempre). Activado por defecto: el mapeo de
# slots y el body están confirmados al 100% con una prueba real completa
# (once de 11 jugadores + réplica exacta del PUT devolviendo 200 OK,
# 2026-08-15 — ver clients/comunio_client.py y engine/lineup_optimizer.py).
# Poner en false en .env si se prefiere revisar manualmente antes de
# dejarlo escribir solo en una liga real.
ENABLE_LINEUP_AUTO_SUBMIT = os.getenv("ENABLE_LINEUP_AUTO_SUBMIT", "true").lower() == "true"

# --- Logging / auditoría ---
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
