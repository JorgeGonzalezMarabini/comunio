"""
Configuración centralizada del bot.

Todo lo que sea "número mágico" o credencial vive aquí (o en .env), nunca
hardcodeado dentro de engine/ o clients/. Así se puede ajustar el
comportamiento del bot sin tocar lógica.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# --- Futmondo ---
# Capturado con Chrome DevTools el 2026-08-17 (sesión real en app.futmondo.com,
# app Flutter Web contra un único dominio: api.futmondo.com).
#
# NO hay endpoint de login conocido (ni en captura propia ni en la
# referencia comunitaria vicenteqa/futmondo-utils) — token y userid se
# obtienen a mano iniciando sesión una vez en la web y leyendo
# localStorage["flutter.token"] / localStorage["flutter.id_user"] desde la
# consola del navegador (F12 -> Console -> `localStorage.getItem("flutter.token")`).
# Sin confirmar cuánto dura el token; si deja de funcionar (401/datos
# vacíos), hay que repetir la captura manual y actualizar el secret.
FUTMONDO_TOKEN = os.getenv("FUTMONDO_TOKEN")
FUTMONDO_USER_ID = os.getenv("FUTMONDO_USER_ID")
FUTMONDO_BASE_URL = os.getenv("FUTMONDO_BASE_URL", "https://api.futmondo.com")

# Un campeonato ("championship") es una liga privada o pública dentro de
# Futmondo; "userteamId" es el equipo de ESE usuario en ESE campeonato en
# concreto (un mismo usuario puede tener varios equipos, uno por
# campeonato). Ambos se ven en el body de cualquier petición autenticada
# mientras navegas la app (DevTools -> Network -> cualquier POST a
# api.futmondo.com -> Payload). Liga de prueba usada en la sesión de
# captura: championshipId=6a82c086b4e159a76ab3b3d8,
# userteamId=6a82c08704b95c71b37179a4 (liga descartable, solo de pruebas).
FUTMONDO_CHAMPIONSHIP_ID = os.getenv("FUTMONDO_CHAMPIONSHIP_ID")
FUTMONDO_USERTEAM_ID = os.getenv("FUTMONDO_USERTEAM_ID")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Base de datos ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "db/futmondo.db")

# --- Pesos del evaluador (engine/evaluator.py) ---
# Configurables para poder ajustarlos con el tiempo sin tocar código.
# Para DECIDIR PUJAS: el precio importa (relación calidad/precio del
# mercado, con presupuesto limitado que repartir entre candidatos).
EVALUATOR_WEIGHTS = {
    "futmondo_points_per_price": 0.35,  # rendimiento Futmondo relativo al precio
    "futmondo_trend": 0.15,             # tendencia de puntuación reciente
    "xg": 0.25,                         # expected goals (Understat)
    "minutes_played": 0.15,             # continuidad / peso en su equipo
    "injury_penalty": 0.10,             # penalización si lesionado/duda
}

# Para ELEGIR ALINEACIÓN: el precio NO debe importar — un jugador de la
# plantilla ya está comprado, su precio es coste hundido. Reutilizar
# EVALUATOR_WEIGHTS aquí penalizaría injustamente a los fichajes caros
# (ver jobs/set_lineup.py). Mismas features, sin "futmondo_points_per_price".
LINEUP_EVALUATOR_WEIGHTS = {
    "futmondo_points_per_price": 0.0,
    "futmondo_trend": 0.30,
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
    # Cuánto por encima del precio/VM real del jugador (Futmondo) se está
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
# % mínimo de plusvalía (precio actual vs. precio de referencia,
# "buyPrice" de roster) para considerar vender un jugador. Vender es la
# ÚNICA fuente de ingresos en Futmondo, igual que en Comunio (no hay
# salario pasivo) — la estrategia documentada es comprar barato y vender
# cuando sube; 10% es un punto de partida razonable, sin calibrar todavía
# con resultados reales.
SELLING_MIN_PROFIT_PCT = float(os.getenv("SELLING_MIN_PROFIT_PCT", "0.10"))

# --- Alineación (engine/lineup_optimizer.py) ---
# Formato real de Futmondo (confirmado por captura, campo "strategy" de
# /1/userteam/lineup): CON guiones, ej. "4-4-2" — a diferencia de Comunio,
# que los quitaba en su "tactic". No hace falta convertir para mandarlo.
DEFAULT_FORMATION = os.getenv("DEFAULT_FORMATION", "4-4-2")

# Cuánto descuenta el score de un jugador la dificultad de su próximo rival
# (0 = ignorar dificultad, 1 = un rival con 100% prob. de no perder anula
# el score por completo). Ver engine.lineup_optimizer.apply_fixture_difficulty.
LINEUP_DIFFICULTY_WEIGHT = float(os.getenv("LINEUP_DIFFICULTY_WEIGHT", "0.2"))

# Envía la alineación decidida a Futmondo de verdad (además de auditarla en
# `lineup_decisions`, que pasa siempre). El mapeo de slots de
# /2/userteam/changeplayer solo está confirmado al 100% para la formación
# 4-4-2 (ver TODO en engine/lineup_optimizer.py) — dejar en false en .env
# si se usa otra formación distinta de DEFAULT_FORMATION hasta confirmarla
# con una prueba real, o para revisar manualmente antes de dejarlo escribir
# solo en una liga real.
ENABLE_LINEUP_AUTO_SUBMIT = os.getenv("ENABLE_LINEUP_AUTO_SUBMIT", "true").lower() == "true"

# --- Logging / auditoría ---
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
