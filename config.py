"""
Configuración centralizada del bot.

Todo lo que sea "número mágico" o credencial vive aquí (o en .env), nunca
hardcodeado dentro de engine/ o clients/. Así se puede ajustar el
comportamiento del bot sin tocar lógica.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# --- Interruptor general del bot ---
# Con esto en false, TODOS los jobs (sync_data, run_market, run_sales,
# set_lineup, manage_substitutes) hacen return inmediato al principio de
# run() sin tocar la red ni la BD -- pensado como pausa rápida sin tener
# que entrar a desactivar el `schedule:` de cada uno de los 5 workflows de
# GitHub Actions por separado (incómodo: hay que editar 5 archivos .yml y
# volver a activarlos luego). Basta con poner/quitar la GitHub Variable
# `ENABLE_BOT=false` en un solo sitio (Settings del repo, ver README,
# "Activar el cron en GitHub Actions") -- los `schedule:` siguen
# disparándose igual (GitHub no permite pausarlos vía variable), pero cada
# ejecución termina en el acto sin hacer nada. No notifica por Telegram al
# saltarse (evitaría el propósito de silenciarlo) -- solo un print visible
# en el log de Actions si alguien lo revisa a mano.
#
# Parseo deliberadamente distinto del resto de flags ENABLE_XXX (que son
# "false" por defecto y se activan explícitamente con "true"): este es
# "true" por defecto y hay que apagarlo explícitamente, porque en GitHub
# Actions una Variable NO definida (`${{ vars.ENABLE_BOT }}` sin haberla
# creado) se resuelve como cadena VACÍA, no se omite -- así que llega
# `ENABLE_BOT=""` al proceso. `os.getenv(..., "true") == "true"` fallaría
# ahí (compara "" contra "true"), apagando el bot solo por no haber creado
# todavía la Variable, justo lo contrario de lo que se busca. Por eso se
# comprueba que NO sea explícitamente una cadena "falsy" en vez de exigir
# que sea "true": vacío/no definida -> sigue activo.
ENABLE_BOT = os.getenv("ENABLE_BOT", "true").strip().lower() not in ("false", "0", "no")

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

# Reintentos ante fallo de CONEXIÓN (no HTTP 4xx/5xx, que ya tuvo
# respuesta) en las llamadas de SOLO LECTURA de Futmondo (ver
# clients/futmondo_client.py:FutmondoClient._post()). Caso real confirmado
# en producción (GitHub Actions, 2026-08-17): la API cerró la conexión sin
# responder a mitad de jobs/run_market.py (`RemoteDisconnected`), tumbando
# el job entero sin ningún reintento. Las escrituras (place_bid,
# change_lineup...) NO usan esto — se quedan siempre en 0 reintentos
# automáticos a propósito, ver docstring de `_post`.
FUTMONDO_READ_MAX_RETRIES = int(os.getenv("FUTMONDO_READ_MAX_RETRIES", "2"))

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Base de datos ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "db/futmondo.db")

# --- Alineaciones reales (clients/football_lineups_client.py, Fotmob) ---
# Decisión del usuario (2026-08-17, ver conversación): fuente elegida para
# detectar cuándo un titular SANO no está en el once real de su equipo (no
# solo lesión/duda, ver is_injury_status()). Fotmob es la CUARTA fuente
# probada ese mismo día -- las tres anteriores (API-Football plan gratuito,
# SofaScore, TheSportsDB) se descartaron por bloqueo o mala calidad de
# datos, todo confirmado con llamadas reales, ver docstring completo de
# clients/football_lineups_client.py y README ("Banquillo/suplentes").
#
# Fotmob NO requiere API key ni cuenta -- por eso no hay nada equivalente a
# API_FOOTBALL_KEY que configurar aquí.
FOTMOB_BASE_URL = os.getenv("FOTMOB_BASE_URL", "https://www.fotmob.com/api/data")

# League id de LaLiga en Fotmob: 87, CONFIRMADO en vivo el 2026-08-17
# (GET /matches?date=... devolvió los partidos reales de LaLiga bajo ese id).
FOTMOB_LALIGA_LEAGUE_ID = int(os.getenv("FOTMOB_LALIGA_LEAGUE_ID", "87"))

# Apaga/enciende la comprobación de alineación real por completo -- False
# por defecto (más conservador todavía que ENABLE_SUBSTITUTE_AUTO_SUBMIT):
# aunque Fotmob no exige credenciales, sigue siendo una API no oficial sin
# contrato ni SLA (ver TODO en clients/football_lineups_client.py) --
# jobs/manage_substitutes.py comprueba este flag antes de importar/usar
# clients/football_lineups_client.py.
ENABLE_REAL_LINEUP_CHECK = os.getenv("ENABLE_REAL_LINEUP_CHECK", "false").lower() == "true"

# --- Pesos del evaluador (engine/evaluator.py) ---
# Configurables para poder ajustarlos con el tiempo sin tocar código.
# Para DECIDIR PUJAS: el precio importa (relación calidad/precio del
# mercado, con presupuesto limitado que repartir entre candidatos).
#
# "injury_penalty" -> "doubt_penalty" (a petición del usuario, 2026-08-22):
# aquí ya NO hace falta penalizar lesión confirmada en el score -- se
# descarta directamente el candidato ANTES de evaluar (ver
# clients.futmondo_client.is_confirmed_injured_status() y
# jobs/run_market.py), así que score_player() nunca llega a ver un
# candidato con lesión confirmada en esta ruta. Lo que sí queda por
# penalizar es "doubt" (duda, el jugador todavía puede llegar a jugar) --
# con más peso que antes (0.10 -> 0.20, el doble) porque antes esa misma
# penalización suave se repartía entre dos casos bien distintos (duda Y
# lesión confirmada) sin que ninguno de los dos estuviera bien servido.
EVALUATOR_WEIGHTS = {
    "futmondo_points_per_price": 0.35,  # rendimiento Futmondo relativo al precio
    "futmondo_trend": 0.15,             # tendencia de puntuación reciente
    "xg": 0.25,                         # expected goals (Understat)
    "minutes_played": 0.15,             # continuidad / peso en su equipo
    "doubt_penalty": 0.20,              # penalización si en duda (no lesión confirmada, esa se descarta antes)
}

# Para ELEGIR ALINEACIÓN: el precio NO debe importar — un jugador de la
# plantilla ya está comprado, su precio es coste hundido. Reutilizar
# EVALUATOR_WEIGHTS aquí penalizaría injustamente a los fichajes caros
# (ver jobs/set_lineup.py). Mismas features, sin "futmondo_points_per_price".
#
# Sigue usando "injury_penalty" (duda Y lesión confirmada por igual) sin
# tocar, a diferencia de EVALUATOR_WEIGHTS de arriba: aquí no tiene
# sentido "descartar" a un lesionado de la plantilla propia como se hace
# con un candidato de mercado -- ya es tuyo. Quién juega de verdad ya lo
# decide `engine.lineup_optimizer._rank_healthy_first()` (sanos siempre
# primero, un lesionado solo si no queda ningún sano en su posición); este
# peso solo afecta al score que ordena dentro de cada grupo.
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
    # SUELO del tope dinámico por jugador (ver
    # engine.bidding_strategy.dynamic_player_cap) -- ya NO es un tope fijo:
    # un número absoluto en euros se queda obsoleto con el tiempo porque el
    # valor de los jugadores en Futmondo sube con el rendimiento a lo largo
    # de la temporada. Detectado en datos reales (2026-08-18): con el tope
    # fijo de 15M, una puja quedó capada exactamente en 15.000.000 y CUALQUIER
    # jugador con precio > 15M queda excluido de pujas para siempre (decide_bid
    # nunca puja por debajo del precio real) sin importar score ni presupuesto
    # -- precio medio de mercado visto ~9.8M, máximo visto 47.3M, ya fuera de
    # alcance. Este valor se mantiene como SUELO (nunca techo) para casos
    # degenerados: plantilla/mercado vacíos o muy baratos al empezar la
    # temporada.
    "max_spend_per_player_floor": 15_000_000,
    "max_budget_risk_per_matchday_pct": 0.30,  # % máx. del presupuesto restante jugable en una jornada
    "min_budget_reserve": 2_000_000,          # colchón que nunca se toca
    # Cuánto por encima del precio/VM real del jugador (Futmondo) se está
    # dispuesto a pujar como máximo, escalado por el score (0..1) del
    # jugador: prima_aplicada = max_premium_over_price_pct * score. Un
    # jugador con score 1.0 se puja hasta un +20% sobre su VM; uno con
    # score 0 no se puja por encima del VM.
    "max_premium_over_price_pct": 0.20,
    # Componente "presupuesto" del tope dinámico: % del saldo usable que se
    # considera razonable poner en UN solo jugador (ver dynamic_player_cap).
    "max_pct_of_budget_per_player": 0.20,
    # Cuánto puede superar el PRECIO DE SALIDA de un listado (el importe que
    # decide quien pone al jugador en venta -- otro manager, o el "Computer"
    # -- campo "price" de get_market(), no confundir con "value"/VM real) al
    # VM real del jugador antes de descartar el candidato sin más (a
    # petición del usuario, 2026-08-22): a diferencia del mercado de
    # fichajes normal de Comunio, donde el precio siempre lo pone la
    # plataforma, en Futmondo un manager puede pedir lo que quiera por su
    # propio jugador -- sin este tope, un listado con precio de salida
    # inflado muy por encima del VM real podría acabar recibiendo una puja
    # igualmente inflada (la prima de decide_bid() se calcula sobre el VM,
    # pero nada impedía hasta ahora comparar ese VM contra lo que el
    # vendedor pide de verdad). 50% es un punto de partida razonado, sin
    # calibrar todavía con datos reales.
    "max_listing_price_over_value_pct": 0.50,
}

# Pesos del tope dinámico por jugador (engine.bidding_strategy.
# dynamic_player_cap) -- sustituye al antiguo tope fijo de 15M combinando
# tres señales que sí escalan con el tiempo, cada una capturando una noción
# distinta de "qué es razonable pujar por UN jugador ahora mismo". Deben
# sumar 1.0.
BIDDING_DYNAMIC_CAP_WEIGHTS = {
    "squad_value": 0.40,   # precio medio de TU plantilla -- estable, no depende de qué haya a la venta hoy
    "market_value": 0.35,  # precio medio del mercado ponderado por score -- inflación general sin dejarse desviar por un outlier puntual
    "budget_pct": 0.25,    # % del saldo disponible ahora -- ligado a lo que de verdad puedes permitirte hoy
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

# Cuánto sube el score de un candidato de mercado si superaría en score de
# alineación (config.LINEUP_EVALUATOR_WEIGHTS, sin precio — coste hundido)
# al titular más flojo de su posición HOY (ver
# engine.squad_risk.weakest_starter_scores). Señal distinta de
# BIDDING_POSITION_RISK_BOOST: esta es sobre CALIDAD del once real, no
# sobre CANTIDAD de suplentes sanos — un candidato puede activar una,
# ambas o ninguna. Aditivo, se suma al boost de riesgo si los dos aplican
# a la vez. Ver engine.bidding_strategy.apply_position_priority.
BIDDING_UPGRADE_BOOST = float(os.getenv("BIDDING_UPGRADE_BOOST", "0.15"))

# Cancelar una puja abierta para pujar por un candidato mejor bloqueado
# solo por presupuesto/tope (ver clients.futmondo_client.cancel_bid,
# TODO.md #13, y engine.bidding_strategy.find_cancel_swap_candidates).
# Feature nueva sin histórico de producción todavía -- valores deliberadamente
# conservadores, pensados para afinarse con datos reales.
BIDDING_CANCEL_SWAP_MIN_MARGIN = float(os.getenv("BIDDING_CANCEL_SWAP_MIN_MARGIN", "0.25"))
# Margen exigido entre el score del candidato bloqueado y el de la puja
# abierta más floja antes de sacrificarla -- deliberadamente por ENCIMA de
# BIDDING_POSITION_RISK_BOOST/BIDDING_UPGRADE_BOOST (0.15 cada uno) para que
# un boost puntual no baste por sí solo para disparar una cancelación.

# Horas mínimas hasta que expire una puja abierta para considerarla
# sacrificable -- si está a punto de resolverse, mejor dejarla terminar
# (podríamos ganarla barata) que arriesgarse a cancelar justo antes.
BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY = float(os.getenv("BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY", "6"))

# Máximo de cancelaciones-para-pujar-mejor por ejecución de run_market --
# 1 a propósito mientras no hay histórico real de esta feature; nada de
# cascadas la primera vez que corre contra la cuenta real.
BIDDING_MAX_CANCEL_SWAPS_PER_RUN = int(os.getenv("BIDDING_MAX_CANCEL_SWAPS_PER_RUN", "1"))

# --- Venta de jugadores (engine/selling_strategy.py) ---
# % mínimo de plusvalía (precio actual vs. precio de referencia,
# "buyPrice" de roster) para considerar vender un jugador. Vender es la
# ÚNICA fuente de ingresos en Futmondo, igual que en Comunio (no hay
# salario pasivo) — la estrategia documentada es comprar barato y vender
# cuando sube; 10% es un punto de partida razonable, sin calibrar todavía
# con resultados reales.
SELLING_MIN_PROFIT_PCT = float(os.getenv("SELLING_MIN_PROFIT_PCT", "0.10"))

# Umbral de PÉRDIDA (positivo, ej. 0.20 = -20%) a partir del cual
# CUALQUIER jugador (sano, en duda o lesionado) se pone en venta aunque no
# llegue a SELLING_MIN_PROFIT_PCT, incluso con pérdidas -- corte de
# pérdidas genérico para no caer en la falacia del coste hundido con
# cualquier jugador que se ha convertido en un lastre, esperando "a que
# recupere" sin más motivo que lo que costó en su día (a petición del
# usuario, 2026-08-22, versión más radical que la anterior -- antes esto
# solo aplicaba a lesión confirmada; ver docstring de
# engine/selling_strategy.py). 20% es un punto de partida razonado, sin
# calibrar todavía con resultados reales.
SELLING_MAX_LOSS_PCT = float(os.getenv("SELLING_MAX_LOSS_PCT", "0.20"))

# Igual que SELLING_MAX_LOSS_PCT pero MÁS BAJO (corta antes) y SOLO para
# lesión CONFIRMADA (no "doubt" -- ver clients.futmondo_client.
# is_confirmed_injured_status()): el valor de un jugador lesionado tiende
# a seguir bajando cuanto más tiempo pasa sin jugar, así que aquí sí hay
# motivo para cortar la pérdida más pronto que en el caso genérico de
# arriba. "doubt" sigue usando SELLING_MAX_LOSS_PCT, no este umbral --
# todavía puede llegar a jugar, no hay la misma base para asumir que solo
# va a perder valor. 10% (la mitad del umbral genérico) es un punto de
# partida razonado, sin calibrar todavía con resultados reales.
SELLING_INJURY_MAX_LOSS_PCT = float(os.getenv("SELLING_INJURY_MAX_LOSS_PCT", "0.10"))

# % máximo (positivo, ej. 0.15 = 15%) del capital TOTAL del equipo (suma
# del valor de mercado de toda la plantilla + presupuesto disponible) que
# puede estar inmovilizado en UN solo jugador con lesión CONFIRMADA (no
# "doubt" -- ver clients.futmondo_client.is_confirmed_injured_status())
# antes de ponerlo en venta, aunque ni siquiera haya llegado a
# SELLING_INJURY_MAX_LOSS_PCT de pérdida (a petición del usuario,
# 2026-08-22, ver docstring de engine/selling_strategy.py). La intención
# no es la rentabilidad de la operación sino evitar tener gran parte del
# capital inmovilizado en un jugador que no se puede usar mientras esté
# lesionado -- un problema de concentración de capital, no de plusvalía.
# 15% es un punto de partida razonado, sin calibrar todavía con
# resultados reales.
SELLING_INJURY_CONCENTRATION_MAX_PCT = float(os.getenv("SELLING_INJURY_CONCENTRATION_MAX_PCT", "0.15"))

# Prima sobre el precio de venta pedido por revalorización rápida sostenida
# (a petición del usuario, 2026-08-22, ver engine.selling_strategy.
# compute_revaluation_premium_pct/apply_revaluation_premium): usa el
# histórico diario de VM de FutmondoClient.get_player_summary() para
# detectar una subida sostenida reciente y proyectar una FRACCIÓN
# conservadora de esa subida por encima del VM, en vez de pedir el VM tal
# cual sin más (comportamiento anterior a este cambio).
#
# Activado por defecto (a petición del usuario, 2026-08-22): el formato
# real de "prices" (fecha ISO-8601, orden ascendente, "price" = VM diario
# real) ya está CONFIRMADO en vivo contra la cuenta real (Koke y Roberto
# Fernández, 7 días de histórico cada uno, ver TODO.md #14) -- ya no hay
# riesgo de aplicar una prima sobre un formato mal interpretado. Impacto
# acotado en cualquier caso: solo sube el precio PEDIDO al vender, nunca
# gasta dinero. `jobs/run_sales.py` solo llama a get_player_summary() y
# aplica la prima si este flag está a true.
ENABLE_SELLING_REVALUATION_PREMIUM = os.getenv("ENABLE_SELLING_REVALUATION_PREMIUM", "true").lower() == "true"

# Ventana de días hacia atrás sobre la que se mide la subida sostenida.
SELLING_REVALUATION_LOOKBACK_DAYS = float(os.getenv("SELLING_REVALUATION_LOOKBACK_DAYS", "7"))

# Mínimo de puntos de precio dentro de la ventana para considerar que hay
# base suficiente -- con 1-2 puntos sueltos (típico justo tras un reinicio
# de BD/liga) no hay tendencia real que proyectar.
SELLING_REVALUATION_MIN_DATA_POINTS = int(os.getenv("SELLING_REVALUATION_MIN_DATA_POINTS", "3"))

# % mínimo de subida total en la ventana para considerar que la
# revalorización es "rápida" y no ruido normal del mercado.
SELLING_REVALUATION_MIN_PCT_TO_PROJECT = float(os.getenv("SELLING_REVALUATION_MIN_PCT_TO_PROJECT", "0.15"))

# Fracción de la subida observada que se proyecta como prima -- nunca la
# subida completa, para no inventar un precio arbitrariamente optimista
# solo porque los últimos días fueron buenos.
SELLING_REVALUATION_PROJECTION_FRACTION = float(os.getenv("SELLING_REVALUATION_PROJECTION_FRACTION", "0.5"))

# Tope duro de la prima aplicable, pase lo que pase con el cálculo de arriba.
SELLING_REVALUATION_MAX_PREMIUM_PCT = float(os.getenv("SELLING_REVALUATION_MAX_PREMIUM_PCT", "0.15"))

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

# --- Sustitución manual (jobs/manage_substitutes.py) ---
# Envía a Futmondo de verdad los cambios de sustitución decididos (además
# de auditarlos siempre en `substitution_decisions`). Pensado para ligas
# donde el "entrenador automático" de Futmondo (función de pago que
# sustituye solo a un titular que no juega) NO está activado — ver
# engine.lineup_optimizer.build_substitution_changes() y README, sección
# "Banquillo/suplentes".
#
# Por defecto False, más conservador que ENABLE_LINEUP_AUTO_SUBMIT:
# aunque se apoya en una regla de la API confirmada en producción (entrar
# desde el banquillo siempre funciona), la secuencia completa de dos
# `changes` reales, uno detrás de otro, no se ha probado todavía con una
# lesión real (sin ningún caso disponible en la liga de prueba,
# pretemporada) — dejar en false hasta confirmarlo, o para revisar
# `substitution_decisions` a mano antes de dejarlo escribir solo.
ENABLE_SUBSTITUTE_AUTO_SUBMIT = os.getenv("ENABLE_SUBSTITUTE_AUTO_SUBMIT", "false").lower() == "true"

# --- Logging / auditoría ---
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
