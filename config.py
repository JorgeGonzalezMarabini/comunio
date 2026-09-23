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
# REBALANCEADO (2026-09-07, a petición del usuario -- ver conversación:
# sensación de comprar/alinear jugadores de poco valor mientras el
# presupuesto crece sin parar). Antes "futmondo_points_per_price" pesaba
# 0.35, el más alto de los cuatro -- al ser una RATIO (puntos por millón
# gastado, ver evaluator.normalize_pool), y dado que el precio de un
# jugador sube más que proporcionalmente respecto a sus puntos según sube
# de nivel, ese peso favorecía SISTEMÁTICAMENTE a jugadores baratos con
# buena ratio frente a jugadores caros de rendimiento absoluto muy
# superior, sea cual sea el presupuesto disponible -- confirmado con datos
# reales de producción (16 días, 2026-08-22 a 2026-09-07): 76% de los
# fichajes fueron al precio suelo del juego (1.0-1.6M) y la mayoría
# acumulaban 0 goles/asistencias y xG≈0, mientras el presupuesto crecía
# +29M netos por vender más de lo que se reinvertía. El propio código ya
# reconocía este sesgo y lo corregía para ELEGIR ALINEACIÓN
# (LINEUP_EVALUATOR_WEIGHTS pone este peso a 0, ver docstring de abajo),
# pero nunca se aplicó la misma corrección a la fase de COMPRA. Bajado a
# 0.10 (deja de ser el factor dominante, pero no desaparece del todo --
# a diferencia de la alineación, en compra el precio SÍ es dinero real
# todavía sin gastar) y redistribuido hacia las tres señales de
# rendimiento que no dependen del precio (xg, trend, minutes_played) --
# la suma de pesos positivos se mantiene en 0.90, igual que antes (ver
# rango típico documentado en engine/evaluator.score_player). Sin
# calibrar todavía con resultados reales tras el cambio.
# "clean_sheet_rate" (2026-09-11, análisis a petición del usuario: Futmondo
# da +1 punto por jugar >60' -- ya se cuela de forma opaca dentro de
# "average_points"/"last_points", que Futmondo calcula con ese bonus ya
# aplicado, así que no lleva peso propio -- y puntos extra por portería a
# cero, que NO tenía ninguna señal en el score pese a que el propio
# docstring de evaluator.normalize_pool ya señalaba el problema). Solo
# cuenta para POR/DEF (ver engine.evaluator.score_player) -- para
# MED/DEL el peso es irrelevante, así que no hace falta bajarles nada al
# resto de pesos para dejarle sitio. Sin calibrar todavía con resultados
# reales, igual que el resto de EVALUATOR_WEIGHTS.
# "xg" excluido para POR (2026-09-21, revisión a petición del usuario tras
# 14 días con los pesos rebalanceados de arriba): Understat no traquea xG
# de porteros (su xg crudo es siempre 0), así que normalize_pool() les da
# a TODOS el mismo 0.5 normalizado (rango 0 dentro del grupo, ver
# _minmax_normalize) -- una señal SIN NINGUNA información que solo inflaba
# el score de POR frente al resto de posiciones al comparar candidatos
# entre sí (jobs/run_market.py compara el score de todas las posiciones en
# la misma escala para umbral/prioridad/swap). Confirmado con datos reales
# de producción: con "xg" peso 0.40 el score medio de POR (0.392) superaba
# al de MED (0.218) pese a que Futmondo puntúa a los porteros sobre todo
# por portería a cero -- señal que SÍ aporta información real y sigue
# aplicando a POR vía "clean_sheet_rate" (ver engine.evaluator.score_player).
# Ver también EVALUATOR_XG90_MIN_MINUTES_RATIO más abajo (mismo análisis, xG con
# pocos minutos jugados).
EVALUATOR_WEIGHTS = {
    "futmondo_points_per_price": 0.10,  # rendimiento Futmondo relativo al precio
    "futmondo_trend": 0.20,             # tendencia de puntuación reciente
    "xg": 0.40,                         # expected goals (Understat) -- no aplica a POR, ver comentario de arriba
    "minutes_played": 0.20,             # continuidad / peso en su equipo
    "clean_sheet_rate": 0.15,           # portería a cero del equipo -- solo aplica a POR/DEF
    "doubt_penalty": 0.20,              # penalización si en duda (no lesión confirmada, esa se descarta antes)
}

# % de los minutos que el EQUIPO del jugador lleva disputados esta
# temporada (`team_games * 90`, mismo dato que minutes_played_ratio/
# clean_sheet_rate -- ver `jobs/sync_data._team_games_by_title()`) que el
# jugador necesita haber jugado ÉL para confiar por completo en su xG/90
# CRUDO (ver engine.evaluator.normalize_pool) -- por debajo, se encoge
# HACIA 0 (no hacia la media del grupo: con pools pequeños, como el propio
# mercado de candidatos que evalúa jobs/run_market.py -- unos 20 jugadores
# repartidos en 4 posiciones --, esa media puede estar dominada por un
# único jugador con minutos reales, "contagiando" su tasa a un compañero
# de 0 minutos en vez de neutralizarlo), en proporción lineal a cuántos
# minutos reales respaldan el dato (0 minutos -> factor 0; el umbral o más
# -> factor 1, valor crudo intacto).
#
# DELIBERADAMENTE UN % Y NO UN NÚMERO FIJO DE MINUTOS (a petición del
# usuario, 2026-09-21, corrigiendo el diseño anterior de este mismo día):
# 270 minutos fijos (3 partidos) no representan lo mismo en la jornada 6
# (el 50% de los 540 minutos que ha podido disputar el equipo) que en la
# jornada 20 (solo el 15% de sus 1800 minutos) -- un umbral fijo se vuelve
# cada vez más laxo según avanza la temporada, justo cuando hay MÁS
# partidos con los que se podría exigir una muestra más representativa.
# Con un %, el suelo escala con el calendario: en la jornada 6 sigue
# pidiendo ~270 minutos (0.5 * 6 * 90 = 270, el mismo punto de partida ya
# confirmado con datos reales de producción -- ver más abajo), pero en la
# jornada 20 pide 900. Punto de partida: 50%, razonado como "que el
# jugador haya participado en al menos la mitad de los minutos que el
# equipo lleva jugados esta temporada" antes de tratar su xG/90
# extrapolado como representativo. `team_games` ausente (0/None, Understat
# todavía no publica el `history` del equipo) cae al mismo fallback que el
# resto de features de normalize_pool: `games` del propio jugador.
# `team_games`/`games` ambos en 0 (antes de la primera jornada) no se
# puede juzgar nada -- se deja el valor crudo intacto (en ese caso
# `minutes_played` también suele ser 0, así que el xG/90 crudo ya es 0 de
# por sí). Sin el suelo (versión original, ya corregida el mismo día): un
# jugador con 1-10 minutos jugados y un solo remate podía salir con xG/90
# varias veces mayor que el mejor delantero de la liga con minutos reales
# -- confirmado con datos de producción (2026-09-21): un jugador con 1
# minuto jugado y 0.08 xG salía con xG/90=6.88 (vs. máximo real de un
# titular ~1.5), fijando el 1.0 normalizado de su grupo entero y
# aplastando a cualquier delantero con rendimiento genuino a lo largo de
# la temporada. Sin calibrar todavía con resultados reales tras el cambio.
EVALUATOR_XG90_MIN_MINUTES_RATIO = float(os.getenv("EVALUATOR_XG90_MIN_MINUTES_RATIO", "0.5"))

# Forma ponderada por recencia (a petición del usuario, 2026-09-23: "de
# nada vale que hiciese mucha puntuación hace muchas jornadas y muy poco en
# las últimas" -- la media de temporada pesa igual la jornada 1 que la
# actual). Se usa en lugar de la media plana en el evaluador (pujas y
# alineación), en el filtro BIDDING_MIN_AVERAGE_POINTS y en los
# multiplicadores por media de las ventas (SELLING_PROFIT_*/SELLING_LOSS_*).
#
# Puntos: media ponderada de `average.fitness` (últimas 5 jornadas del
# equipo, 0 si no jugó) con peso DECAY^k para la jornada de hace k (la
# última pesa 1, la anterior 0.7, luego 0.49...). Las jornadas anteriores
# a esas 5 entran a través de la media de temporada, con el peso que les
# correspondería en la misma serie geométrica (DECAY^5 / (1 - DECAY)):
# con 0.7, las 5 últimas suman el 83% del peso. Sin `fitness`, se usa la
# media de temporada tal cual. Sin calibrar todavía: con solo ~30 jornadas
# reconstruibles del histórico, ni la media plana ni la ponderada predicen
# mejor la jornada siguiente (error medio 1.3-1.4 puntos en ambos casos);
# el array completo se guarda desde ahora (futmondo_snapshots.recent_points)
# para poder calibrarlo.
EVALUATOR_RECENT_POINTS_DECAY = float(os.getenv("EVALUATOR_RECENT_POINTS_DECAY", "0.7"))

# Minutos: el ratio de minutos se mezcla entre el de las últimas
# EVALUATOR_RECENT_MINUTES_WINDOW_GAMES jornadas del equipo (reconstruido
# del histórico local de external_stats) y el de temporada, con peso
# EVALUATOR_RECENT_MINUTES_WEIGHT para el reciente. Sin histórico local
# suficiente para ese jugador (p. ej. un candidato que aparece por primera
# vez en el mercado), solo el de temporada, como antes.
EVALUATOR_RECENT_MINUTES_WINDOW_GAMES = int(os.getenv("EVALUATOR_RECENT_MINUTES_WINDOW_GAMES", "3"))
EVALUATOR_RECENT_MINUTES_WEIGHT = float(os.getenv("EVALUATOR_RECENT_MINUTES_WEIGHT", "0.6"))

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
#
# "futmondo_form" (a petición del usuario, 2026-09-23): hasta ahora este
# score NO incluía el NIVEL de puntos de Futmondo -- solo su tendencia
# (forma - media), xG y minutos --, así que con datos reales ponía a
# Sannadi (media 1.3) por delante de Jutglà (4.6) y a Szczesny (-2) por
# delante de Cárdenas (1.7). Ahora suma la forma ponderada por recencia de
# los puntos (engine.evaluator.form_points, normalizada por posición),
# redistribuyendo el resto para que la suma de pesos positivos siga en 1.0
# y los umbrales que se miden en esta escala (SELLING_UPGRADE_AVAILABLE_MIN_
# MARGIN, BIDDING_CANCEL_SWAP_MIN_MARGIN, BIDDING_UPGRADE_BOOST...) sigan
# significando lo mismo. Sin calibrar todavía con resultados reales.
LINEUP_EVALUATOR_WEIGHTS = {
    "futmondo_points_per_price": 0.0,
    "futmondo_form": 0.35,
    "futmondo_trend": 0.15,
    "xg": 0.25,
    "minutes_played": 0.25,
    "clean_sheet_rate": 0.20,  # solo aplica a POR/DEF, ver EVALUATOR_WEIGHTS arriba
    "injury_penalty": 0.10,
}

# Pujar por un jugador puesto en venta por OTRO MANAGER (a petición del
# usuario, 2026-08-22, ver TODO.md #15): no está confirmado si eso
# funciona igual que pujar por uno puesto en venta por "el Computer" del
# propio juego (market item `"computer": True`, ver
# clients/futmondo_client.py) -- si el mecanismo normal de venta requiere
# que el VENDEDOR acepte una oferta explícitamente (como parece ser el
# caso, TODO.md #15, todavía sin confirmar en vivo), una puja sobre un
# jugador de otro manager podría quedarse pendiente indefinidamente sin
# resolverse nunca, comprometiendo presupuesto y una plaza de plantilla
# escasa sin ningún control sobre cuándo (o si) se resuelve. Por eso,
# DESACTIVADO por defecto: `jobs/run_market.py` solo considera candidatos
# puestos en venta por el propio Futmondo (`computer=True`) mientras este
# flag siga en False, y además cancela en cada pasada cualquier puja YA
# ABIERTA sobre un jugador de otro manager -- hasta que TODO.md #15 se
# confirme en vivo y se pueda activar con seguridad.
ENABLE_BIDS_ON_MANAGER_LISTINGS = os.getenv("ENABLE_BIDS_ON_MANAGER_LISTINGS", "false").lower() == "true"

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
    "market_value": 0.35,  # precio medio del mercado ponderado por score, ver BIDDING_DYNAMIC_CAP_MARKET_OUTLIER_MULTIPLIER más abajo
    "budget_pct": 0.25,    # % del saldo disponible ahora -- ligado a lo que de verdad puedes permitirte hoy
}

# "market_value" (arriba) pondera el precio de cada candidato del mercado
# por su score -- pensado para que un candidato carísimo con score BAJO
# (mal rendimiento, a la venta por casualidad) no desvíe la media, ya que
# pesa casi 0. Pero NO protege el caso contrario, real y confirmado en
# producción (2026-09-21, al revisar si el tope de precio necesitaba
# subir tras exigir más calidad en la puja -- ver BIDDING_MIN_AVERAGE_
# POINTS/EVALUATOR_XG90_MIN_MINUTES_RATIO): un jugador carísimo con score
# ALTO (una estrella real, listada un día concreto a un precio que ningún
# presupuesto de fantasy puede permitirse igual) pesa casi 1 en la media
# ponderada -- un solo listado así puede disparar `avg_market_value`
# varias veces (confirmado: de 4.6M a 16.1M en el mercado real de ese día,
# solo por un candidato). El tope por jugador de TODOS los demás
# candidatos ese día queda inflado por un precio que nunca se va a pagar.
# Este multiplicador acota el precio de cada candidato ANTES de la media
# ponderada a `avg_squad_value * este_valor` (nunca el score, que sigue
# intacto) -- ningún jugador individual debería pesar en "cuánto vale el
# mercado para TU equipo" con un precio varias veces el de tu propia
# plantilla media; a partir de ahí ya no es una referencia realista, es un
# caso aislado. `avg_squad_value <= 0` (plantilla vacía, caso degenerado)
# no aplica ningún recorte -- sin plantilla propia no hay referencia
# contra la que acotar. Punto de partida: 5x, sin calibrar todavía con
# resultados reales.
BIDDING_DYNAMIC_CAP_MARKET_OUTLIER_MULTIPLIER = float(
    os.getenv("BIDDING_DYNAMIC_CAP_MARKET_OUTLIER_MULTIPLIER", "5.0")
)

# Score mínimo (ver engine/evaluator.score_player) para considerar pujar por
# un jugador. Punto de partida sin calibrar con datos reales todavía.
BIDDING_MIN_SCORE_THRESHOLD = float(os.getenv("BIDDING_MIN_SCORE_THRESHOLD", "0.15"))

# Media de puntos por partido mínima (a petición del usuario, 2026-09-21,
# ver engine.bidding_strategy.decide_bid/is_price_worth_bidding) para
# considerar pujar por un jugador -- filtro DURO, independiente del score:
# ni el score combinado (ver EVALUATOR_WEIGHTS) ni sus boosts de
# prioridad/mejora (BIDDING_POSITION_RISK_BOOST/BIDDING_UPGRADE_BOOST)
# distinguen "pocos datos todavía" de "cuando ha jugado, ha rendido mal de
# verdad" -- un jugador con `average_points` NEGATIVO o cercano a 0 puede
# superar igualmente el umbral de score por trend/xg/minutos, sobre todo
# con los boosts de riesgo de plantilla sumados. Confirmado con casos
# reales de producción (2026-09-21): se pujó por un portero con
# average_points=-2.0 (score=0.525, muy por encima del umbral de 0.15) y
# por un defensa con average_points=0.0 (score=0.857) -- ninguno de los
# dos tenía lesión/duda que lo descartase antes, y su rendimiento real ya
# demostrado (no una hipótesis de xG/tendencia) era malo o nulo.
# `player.get("average_points")` ausente (None, sin snapshot todavía) se
# trata como 0 -- mismo criterio conservador que el resto de features sin
# dato en engine.evaluator.normalize_pool -- así que un jugador sin
# ninguna jornada registrada tampoco pasa este filtro hasta tener datos.
# Aplica también al rescate de déficit de plantilla (find_deficit_rescue_
# swaps usa is_price_worth_bidding() sin overridear esto) -- incluso con
# urgencia real, no tiene sentido fichar a alguien con rendimiento
# demostrado nulo o negativo solo por rellenar un hueco. Valor 3.0 (a
# petición explícita del usuario, 2026-09-21) -- más estricto que el punto
# de partida original (1.0, que solo excluía la cola de rendimiento
# negativo o cero): con datos reales de producción, este umbral excluye
# aprox. el 58% del pool completo de jugadores (la mayoría, suplentes con
# apenas participación), no solo los casos extremos. Sin calibrar todavía
# con resultados reales tras el cambio.
BIDDING_MIN_AVERAGE_POINTS = float(os.getenv("BIDDING_MIN_AVERAGE_POINTS", "3.0"))

# --- "Presupuesto objetivo" (a petición del usuario, 2026-09-07) ---
# Hasta ahora el presupuesto disponible solo subía un TECHO pasivo (cuánto
# se puede llegar a pujar por un jugador, ver dynamic_player_cap) -- nunca
# exigía más calidad: BIDDING_MIN_SCORE_THRESHOLD era fijo pasase lo que
# pasase con la caja acumulada. Combinado con el sesgo de EVALUATOR_WEIGHTS
# hacia "puntos por precio" (ver comentario de arriba, corregido el mismo
# día), esto dejaba pasar fichajes de relleno con la primera puja que
# superase el umbral, aunque el presupuesto ya estuviera muy por encima de
# lo que la plantilla necesitaba para funcionar -- exactamente el patrón
# visto en producción (caja neta +29M en 16 días sin que el umbral de
# compra se moviera ni un punto). Ver
# engine.bidding_strategy.dynamic_min_score_threshold, usado por
# jobs/run_market.py en vez del umbral fijo a secas.
#
# % del capital total del equipo (presupuesto + valor de mercado de toda la
# plantilla) que se considera una reserva de caja SANA -- por debajo de
# esto no sube nada el umbral, tener colchón es normal y deseable (ver
# BIDDING_SAFETY_LIMITS["min_budget_reserve"]). Sin calibrar todavía con
# resultados reales.
BIDDING_IDLE_CASH_TARGET_PCT = float(os.getenv("BIDDING_IDLE_CASH_TARGET_PCT", "0.35"))

# Cuánto puede llegar a subir BIDDING_MIN_SCORE_THRESHOLD (aditivo) cuando
# TODO el capital del equipo es presupuesto sin invertir (caso extremo,
# squad_value=0) -- escala LINEALMENTE entre BIDDING_IDLE_CASH_TARGET_PCT
# (boost 0) y 100% caja (boost máximo), ver dynamic_min_score_threshold.
# Con el suelo de 0.15 y este máximo, el umbral puede llegar como mucho a
# 0.35 -- todavía por debajo del score de un jugador sano en buena forma
# (ver test_score_player_weights_and_injury_penalty, máximo positivo
# 0.90), así que nunca bloquea un fichaje realmente bueno, solo relleno
# mediocre que hoy pasaría el umbral fijo solo porque hay caja de sobra.
# Sin calibrar todavía con resultados reales.
BIDDING_IDLE_CASH_MAX_THRESHOLD_BOOST = float(os.getenv("BIDDING_IDLE_CASH_MAX_THRESHOLD_BOOST", "0.20"))

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
# empezó en 1 a propósito mientras no había histórico real de esta
# feature. Subido a 3 (2026-08-22, a petición del usuario) al confirmar
# que el límite de plantilla (ver jobs/run_market.py) hace que "candidato
# bueno bloqueado" pase de ser un caso raro (solo por presupuesto/tope) a
# ser la situación NORMAL en cuanto la plantilla está casi llena -- con 1
# swap por pasada, el resto de candidatos igual de buenos se quedaban
# esperando al siguiente cron, con riesgo de que otro manager se
# adelantara. Sigue acotado (no ilimitado): cada swap individual sigue
# exigiendo el margen de score (BIDDING_CANCEL_SWAP_MIN_MARGIN) y el
# colchón antes de expirar (BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY).
BIDDING_MAX_CANCEL_SWAPS_PER_RUN = int(os.getenv("BIDDING_MAX_CANCEL_SWAPS_PER_RUN", "3"))

# Reajustar a la baja una puja abierta si el VM del jugador ha caído desde
# que se pujó (a petición del usuario, 2026-08-22, ver engine.
# bidding_strategy.find_reprice_down_candidates y jobs/run_market.py):
# Futmondo no tiene endpoint confirmado para "editar" el importe de una
# oferta ya abierta (modifybid/modifyrosterbid/modifyprice aparecen solo
# como hallazgo sin implementar en el bundle de la app, ver
# clients.futmondo_client) ni actualizarlo pujando otra vez sobre el mismo
# jugador (real_pending_bid_amount()) -- la única forma real es cancelar la
# puja vieja y colocar una nueva más barata, mismo mecanismo que el swap de
# arriba (TODO.md #13). Sin histórico real todavía -- valores
# deliberadamente conservadores, pensados para afinarse con datos reales.
BIDDING_REPRICE_DOWN_MIN_DROP_PCT = float(os.getenv("BIDDING_REPRICE_DOWN_MIN_DROP_PCT", "0.10"))
# % mínimo de caída entre lo pujado y lo que decide_bid() pujaría HOY con
# el VM/score actuales del jugador antes de molestarse en reajustar --
# evita cancelar+repujar por ruido/fluctuaciones mínimas del VM día a día.

BIDDING_REPRICE_DOWN_MIN_HOURS_BEFORE_EXPIRY = float(
    os.getenv("BIDDING_REPRICE_DOWN_MIN_HOURS_BEFORE_EXPIRY", "6")
)
# Igual razonamiento que BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY -- si
# la puja está a punto de resolverse, mejor dejarla terminar (podríamos
# ganarla barata) que arriesgarse a cancelarla justo antes.

BIDDING_MAX_REPRICE_DOWNS_PER_RUN = int(os.getenv("BIDDING_MAX_REPRICE_DOWNS_PER_RUN", "3"))
# Máximo de reajustes a la baja por ejecución de run_market -- mismo
# razonamiento conservador que BIDDING_MAX_CANCEL_SWAPS_PER_RUN.

# Rescate de déficit de plantilla en pujas de COMPRA (a petición del
# usuario: 2 clausulazos sobre la misma posición sin ninguna venta
# pendiente que jobs.sync_data._rescue_sales_at_risk() pueda cancelar --
# esa vía solo actúa sobre VENTAS propias ya listadas, ver README). Ver
# engine.bidding_strategy.find_deficit_rescue_swaps: a diferencia del swap
# normal de arriba (candidato MEJOR bloqueado por presupuesto/tope), aquí
# el gatillo es una posición con margen NEGATIVO de verdad (`deficit > 0`,
# ver engine.squad_risk.assess_squad_depth) que ninguna puja ya en marcha
# cubre todavía -- se sacrifica una puja de compra en OTRA posición (nunca
# una también en déficit) sin exigir ningún margen de score: lo urgente es
# recuperar un cuerpo en la posición, no encontrar una mejora. Sin
# histórico real todavía -- valores deliberadamente conservadores.
BIDDING_DEFICIT_RESCUE_MIN_HOURS_BEFORE_EXPIRY = float(
    os.getenv("BIDDING_DEFICIT_RESCUE_MIN_HOURS_BEFORE_EXPIRY", "6")
)
# Igual razonamiento que BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY -- si
# la puja sacrificable está a punto de resolverse, mejor dejarla terminar.

BIDDING_MAX_DEFICIT_RESCUES_PER_RUN = int(os.getenv("BIDDING_MAX_DEFICIT_RESCUES_PER_RUN", "1"))
# Máximo de rescates de déficit por ejecución de run_market -- 1 a
# propósito (igual que empezó BIDDING_MAX_CANCEL_SWAPS_PER_RUN) mientras
# esta feature no tiene histórico real: sin margen de score que la acote,
# conviene subirlo con más cautela que los otros dos swaps.

# --- Venta de jugadores (engine/selling_strategy.py) ---
# % mínimo de plusvalía (precio actual vs. precio de referencia,
# "buyPrice" de roster) para considerar vender un jugador. Vender es la
# ÚNICA fuente de ingresos en Futmondo, igual que en Comunio (no hay
# salario pasivo) — la estrategia documentada es comprar barato y vender
# cuando sube.
#
# SUBIDO 0.10 -> 0.15 (a petición del usuario, 2026-09-21, revisión de
# umbrales de venta tras un mes de datos reales de producción -- ver
# también SELLING_MAX_LOSS_PCT más abajo, mismo análisis): de las 30
# ventas por esta vía en el primer mes (tabla `sales`, reason "pagado por
# el bot..."), 8 jugadores distintos, la mitad (4) vendidos con una
# plusvalía de apenas 10-15% -- el mismo patrón de "vender justo al rozar
# el umbral" que en el corte de pérdidas, aunque menos extremo. 0.10
# dejaba salir plusvalías todavía modestas que un margen algo mayor
# habría dejado seguir creciendo, alimentando parte de la rotación de
# plantilla observada.
SELLING_MIN_PROFIT_PCT = float(os.getenv("SELLING_MIN_PROFIT_PCT", "0.15"))

# Venta por plusvalía ponderada por calidad y tendencia (a petición del
# usuario, 2026-09-23, caso real: Koski listado por +16% siendo el jugador
# con MEJOR media del equipo, 5.1, y subiendo ~+7% diario). La vía de
# plusvalía tenía un sesgo estructural: los jugadores que puntúan bien son
# justo los que se revalorizan, así que vendía sobre todo a los mejores
# (media de los vendidos por plusvalía en el backtest: 4.8). En los
# snapshots (muestra pequeña, ~1 mes): con media >= 5 el precio sube de
# media +2.7% la semana siguiente y con < 2 cae -6%; y tras subir +3/+20%
# en 3 días, sube otro +7% de media los 3 siguientes (76% de las veces).
# En dinero, el backtest no distingue variantes (-9.5%/-9.9%, solo 9
# ventas por esta vía), así que retener no cuesta y vender a un titular
# que puntúa cuesta puntos y otra prima de puja al reponer. Por eso:
#   umbral_plusvalía = SELLING_MIN_PROFIT_PCT * mult_media * mult_titular
# y además se APLAZA la venta mientras el precio siga subiendo (ver
# SELLING_PROFIT_MOMENTUM_* abajo); el trailing-stop
# (SELLING_TRAILING_STOP_MAX_DRAWDOWN_PCT) sigue protegiendo la plusvalía
# si la subida se da la vuelta.
#
# mult_media: media / SELLING_PROFIT_AVG_POINTS_REF acotado a
# [1, SELLING_PROFIT_AVG_POINTS_MAX_MULT] (media 5.1 -> x1.7; sin media o
# por debajo de la referencia -> x1, se vende igual que antes).
SELLING_PROFIT_AVG_POINTS_REF = float(os.getenv("SELLING_PROFIT_AVG_POINTS_REF", "3.0"))
SELLING_PROFIT_AVG_POINTS_MAX_MULT = float(os.getenv("SELLING_PROFIT_AVG_POINTS_MAX_MULT", "2.5"))
# mult_titular: si está en la alineación guardada (get_lineup()).
SELLING_PROFIT_STARTER_MULT = float(os.getenv("SELLING_PROFIT_STARTER_MULT", "1.5"))
# Aplazamiento por tendencia: si el VM actual supera en al menos
# SELLING_PROFIT_MOMENTUM_MIN_PCT al primer dato de los últimos
# SELLING_PROFIT_MOMENTUM_LOOKBACK_DAYS días (histórico local, mínimo
# SELLING_PROFIT_MOMENTUM_MIN_DATA_POINTS datos), la venta por plusvalía se
# pospone a la siguiente pasada. Sin histórico suficiente, no aplaza.
SELLING_PROFIT_MOMENTUM_LOOKBACK_DAYS = float(os.getenv("SELLING_PROFIT_MOMENTUM_LOOKBACK_DAYS", "3"))
SELLING_PROFIT_MOMENTUM_MIN_PCT = float(os.getenv("SELLING_PROFIT_MOMENTUM_MIN_PCT", "0.03"))
SELLING_PROFIT_MOMENTUM_MIN_DATA_POINTS = int(os.getenv("SELLING_PROFIT_MOMENTUM_MIN_DATA_POINTS", "2"))

# Protección de los mejores y rotación sobre los peores, por posición (a
# petición del usuario, 2026-09-23: "priorizar sustituir a los peores por
# mejores que cambiar a los mejores por otros mejores"). Ranking por forma
# ponderada por recencia de los PUNTOS de Futmondo (EVALUATOR_RECENT_POINTS_
# DECAY; no el score de alineación, que no incluye el nivel de puntos -- ver
# engine.selling_strategy.own_quality_scores) entre los jugadores SANOS de
# cada posición:
#   - SELLING_PROTECT_TOP_PLAYERS_FROM_PROFIT: los N mejores de cada
#     posición (N = titulares que pide la formación, p. ej. 4 DEF en 4-4-2)
#     nunca se venden por plusvalía, ni siquiera con sustituto. El
#     trailing-stop y el corte de pérdidas solo con sustituto (ver ENABLE_SELLING_TOP_PLAYERS_
#     REQUIRE_REPLACEMENT abajo); las vías de lesión siguen aplicando.
#   - SELLING_UPGRADE_ONLY_WORST_PER_POSITION: la vía "oportunidad de
#     mercado" solo puede vender al PEOR jugador sano de su posición; si ese
#     no es vendible esta pasada (recién fichado, ya en venta...), no se
#     vende a otro mejor en su lugar.
# Cuántos cuentan como "mejores" por posición: max(titulares de la
# formación, esta fracción de los jugadores rankeados de la posición,
# redondeando hacia arriba) -- a petición del usuario, 2026-09-23: "extender
# la protección a la mitad superior de cada posición" (p. ej. 6 DEL en
# 4-4-2 -> 3 protegidos en vez de 2; nunca menos que los titulares).
SELLING_TOP_PLAYERS_PROTECTED_MIN_SHARE = float(os.getenv("SELLING_TOP_PLAYERS_PROTECTED_MIN_SHARE", "0.5"))
ENABLE_SELLING_PROTECT_TOP_PLAYERS_FROM_PROFIT = (
    os.getenv("ENABLE_SELLING_PROTECT_TOP_PLAYERS_FROM_PROFIT", "true").lower() == "true"
)
ENABLE_SELLING_UPGRADE_ONLY_WORST_PER_POSITION = (
    os.getenv("ENABLE_SELLING_UPGRADE_ONLY_WORST_PER_POSITION", "true").lower() == "true"
)

# Los mejores solo se venden con sustituto fichado antes (a petición del
# usuario, 2026-09-23: "que los mejores del equipo solo se vendan si hay un
# candidato en mercado disponible para sustituirles con un ratio de
# precio/puntos mejor"). Aplica a los N mejores de cada posición (mismo
# ranking que arriba, pero contando también a los "doubt"; la lesión
# CONFIRMADA es la única excepción) en el resto de vías de venta (corte de
# pérdidas, trailing-stop, oportunidad de mercado); por plusvalía nunca. Se lista al top solo si
# hay en mercado un sustituto de su posición, sano, con:
#   - forma >= SELLING_TOP_REPLACEMENT_MIN_FORM_RATIO x la del top (no baja
#     la media de puntos de la posición),
#   - mejor ratio precio/punto (coste = max(VM, precio de salida)),
#   - coste pagable con el presupuesto ACTUAL (sin contar lo de la venta:
#     se compra antes de vender), y
#   - al menos SELLING_TOP_REPLACEMENT_MIN_LISTING_HOURS de listado.
# jobs/run_sales.py no acepta ofertas sobre ese top hasta que el sustituto
# esté en plantilla; si el sustituto sale del mercado sin ser nuestro, se
# retira la venta y se reevalúa. jobs/run_market.py puja por él con prioridad.
ENABLE_SELLING_TOP_PLAYERS_REQUIRE_REPLACEMENT = (
    os.getenv("ENABLE_SELLING_TOP_PLAYERS_REQUIRE_REPLACEMENT", "true").lower() == "true"
)
SELLING_TOP_REPLACEMENT_MIN_FORM_RATIO = float(os.getenv("SELLING_TOP_REPLACEMENT_MIN_FORM_RATIO", "1.0"))
SELLING_TOP_REPLACEMENT_MIN_LISTING_HOURS = float(os.getenv("SELLING_TOP_REPLACEMENT_MIN_LISTING_HOURS", "6"))

# Umbral de PÉRDIDA (positivo, ej. 0.10 = -10%) a partir del cual
# CUALQUIER jugador (sano, en duda o lesionado) se pone en venta aunque no
# llegue a SELLING_MIN_PROFIT_PCT, incluso con pérdidas -- corte de
# pérdidas genérico para no caer en la falacia del coste hundido con
# cualquier jugador que se ha convertido en un lastre, esperando "a que
# recupere" sin más motivo que lo que costó en su día (a petición del
# usuario, 2026-08-22).
#
# UNIFICADO en un solo umbral para todos los estados (a petición del
# usuario, 2026-08-22, tras añadir la venta SIEMPRE de lesión confirmada en
# engine/selling_strategy.py): antes había dos umbrales, uno genérico
# (0.20) para sano/duda y otro más agresivo (0.10, ex-SELLING_INJURY_MAX_
# LOSS_PCT) solo para lesión confirmada, razonado porque un lesionado
# tiende a seguir perdiendo valor cuanto más tiempo pasa sin jugar. Ya no
# hace falta esa distinción para lesión confirmada -- se vende siempre
# pase lo que pase con su pérdida, con su propia vía incondicional -- así
# que el argumento de "corta antes porque va a seguir bajando" se
# generaliza sin más al resto de la plantilla: si empieza a caer, se corta
# pronto, igual que si empieza a subir conviene vender (SELLING_MIN_
# PROFIT_PCT). 10% (el valor antes exclusivo de lesión confirmada) es
# ahora el único umbral, para todos los estados.
#
# SUBIDO 0.10 -> 0.15 (a petición del usuario, 2026-09-21, revisión de
# umbrales de venta -- "creo que son demasiado sensibles y hay mucha
# rotación de plantilla" -- con un mes de datos reales de producción,
# tabla `sales`, desde que se activó esta vía el 2026-08-22): el corte de
# pérdidas es, con diferencia, la vía que más domina la actividad de venta
# (137 de 171 filas totales de `sales` en el mes, 24 jugadores distintos
# frente a 8 de rentabilidad normal y 7 de oportunidad de mercado). De
# esos 24, 15 (63%) se cortaron con una pérdida de apenas 10-15% -- justo
# rozando el umbral -- y solo 7 superaban el 20% de pérdida real. Con un
# umbral de 10% el corte estaba capturando sobre todo la oscilación normal
# de valor de Futmondo semana a semana, no manías de mala compra genuina,
# y cada corte + recompra posterior es un ciclo completo de rotación. Este
# cambio no toca `confirm_loss_is_sustained()` (SELLING_LOSS_CONFIRMATION_*
# más abajo, filtra ruido de UN día puntual): el problema aquí era la
# MAGNITUD exigida, no la persistencia -- una caída sostenida del 12% no
# tiene nada que confirmar, simplemente no debería bastar por sí sola para
# vender. 15% deja fuera esa franja de ruido y sigue cortando con margen
# cualquier pérdida que empiece a ser preocupante de verdad.
SELLING_MAX_LOSS_PCT = float(os.getenv("SELLING_MAX_LOSS_PCT", "0.15"))

# Corte de pérdidas medido contra el VALOR DE MERCADO en la compra y con
# multiplicador por antigüedad y media de puntos (a petición del usuario,
# 2026-09-23, "me preocupa la alta rotación de fichajes"). Análisis del
# primer mes real (34 ciclos compra->venta en `bids`/`sales`, mediana de
# tenencia 7 días, 10 vendidos en menos de 3): el corte de pérdidas explica
# -11.9M de los -8.4M netos de todas las ventas (16 ciclos, -12.8%), y 7.8M
# de esa pérdida es PRIMA DE PUJA -- ganamos la puja de media un +10.8% por
# encima del VM (mediana +13%), así que el -15% "frente a lo pagado"
# saltaba con apenas un -2/-5% de caída real del jugador (Cancelo, Borja
# Iglesias, Salinas, Areso, Buonanotte: cortados en 1-3 días). Esa prima
# ya está pagada -- es exactamente el coste hundido que este mismo corte
# pretende ignorar -- y cada corte obliga a reponer pagando otra prima. En
# el backtest sobre las 56 compras (`futmondo_snapshots`), el corte
# vigente dejaba un neto de -13.5% (incluido el coste de reposición) frente
# a -10.4% con estos cambios y -9.8% sin corte ninguno: en un mes de datos
# el corte no se adelanta a caídas mayores (el precio de los cortados solo
# siguió bajando un -3.9% de media tras venderlos), solo materializa la
# prima. Se mantiene el corte (con más histórico puede haber caídas largas
# que sí merezca cortar), pero bastante más laxo:
#   umbral_efectivo = min(SELLING_LOSS_MAX_EFFECTIVE_PCT,
#                         SELLING_MAX_LOSS_PCT * mult_tiempo * mult_media)
#   pérdida = (VM_actual - VM_en_la_compra) / VM_en_la_compra
# Sin VM en la compra (no hay snapshot previo), se usa lo pagado; sin
# fecha de compra, mult_tiempo = 1 (comportamiento previo). La plusvalía
# de la vía normal (SELLING_MIN_PROFIT_PCT) sigue midiéndose contra lo
# pagado: ahí lo que importa es el dinero real que se recupera.
#
# Multiplicador por antigüedad del fichaje: SELLING_LOSS_TIME_MULT_MAX el
# día de la compra, decae linealmente hasta x1 a los
# SELLING_LOSS_TIME_DECAY_DAYS días -- un fichaje reciente necesita tiempo
# para demostrar si fue mala compra o solo ruido de la primera semana.
SELLING_LOSS_TIME_MULT_MAX = float(os.getenv("SELLING_LOSS_TIME_MULT_MAX", "2.0"))
SELLING_LOSS_TIME_DECAY_DAYS = float(os.getenv("SELLING_LOSS_TIME_DECAY_DAYS", "14"))

# Multiplicador por media de puntos (`average.average` del roster):
# media / SELLING_LOSS_AVG_POINTS_REF, acotado a [1, SELLING_LOSS_AVG_POINTS_
# MAX_MULT] -- un jugador que puntúa bien aporta cada jornada aunque su VM
# oscile, así que se le da más margen; uno que puntúa por debajo de la
# referencia (o sin media todavía) se queda en x1, nunca por debajo del
# umbral base. 3.0 ~ media típica de un titular normal en los snapshots.
SELLING_LOSS_AVG_POINTS_REF = float(os.getenv("SELLING_LOSS_AVG_POINTS_REF", "3.0"))
SELLING_LOSS_AVG_POINTS_MAX_MULT = float(os.getenv("SELLING_LOSS_AVG_POINTS_MAX_MULT", "1.5"))

# Tope del umbral efectivo tras aplicar ambos multiplicadores: por mucho
# margen que den, una caída de más del 40% frente al VM de compra se corta.
SELLING_LOSS_MAX_EFFECTIVE_PCT = float(os.getenv("SELLING_LOSS_MAX_EFFECTIVE_PCT", "0.40"))

# Nº mínimo de datos de precio dentro de SELLING_LOSS_CONFIRMATION_LOOKBACK_
# DAYS para poder juzgar si un corte de pérdidas (SELLING_MAX_LOSS_PCT) es
# una tendencia sostenida o ruido de un solo dato reciente (a petición del
# usuario, evaluación del trigger de venta 2026-09-11, ver docstring de
# engine.selling_strategy.confirm_loss_is_sustained()). Con menos datos que
# esto, FALLA ABIERTO -- se corta igual que hoy, nunca se bloquea un corte
# de pérdidas real solo por falta de histórico (al contrario que la prima
# de revalorización, que falla cerrado hacia "sin prima": aquí el sesgo de
# seguridad es el opuesto).
SELLING_LOSS_CONFIRMATION_MIN_DATA_POINTS = int(os.getenv("SELLING_LOSS_CONFIRMATION_MIN_DATA_POINTS", "2"))

# Ventana hacia atrás (en días) sobre la que se busca esa confirmación --
# deliberadamente más corta que SELLING_REVALUATION_LOOKBACK_DAYS (7): un
# corte de pérdidas debe reaccionar rápido, aquí solo se busca filtrar el
# ruido de un dato puntual, no exigir una tendencia de varias semanas.
SELLING_LOSS_CONFIRMATION_LOOKBACK_DAYS = float(os.getenv("SELLING_LOSS_CONFIRMATION_LOOKBACK_DAYS", "3"))

# % máximo de repunte desde el mínimo de esa ventana que se tolera antes de
# considerar que la caída ya se está revirtiendo y posponer el corte esta
# pasada (se reevalúa en la siguiente, no se descarta para siempre). No se
# exige una serie estrictamente no-creciente (a diferencia de la prima de
# revalorización): un valor puede oscilar día a día incluso en una caída
# real, así que comparar contra el mínimo de la ventana es más robusto que
# invalidar la confirmación ante cualquier repunte de un solo día.
SELLING_LOSS_CONFIRMATION_MAX_REBOUND_PCT = float(os.getenv("SELLING_LOSS_CONFIRMATION_MAX_REBOUND_PCT", "0.05"))

# % máximo de caída (positivo, ej. 0.15 = 15%) desde el valor MÁS ALTO
# observado desde que el bot compró al jugador (no desde el precio de
# compra, ver SELLING_MAX_LOSS_PCT) antes de venderlo igualmente, AUNQUE
# siga en positivo frente al precio de compra -- "corte por reversión desde
# máximo" (a petición del usuario, evaluación del trigger de venta
# 2026-09-11, ver docstring de engine/selling_strategy.py): sin esto, un
# jugador que subió mucho y luego empieza a caer no se toca hasta que la
# caída acumulada desde la COMPRA cruce SELLING_MAX_LOSS_PCT, dejando que
# se evapore buena parte de una plusvalía ya generada antes de reaccionar.
# Deliberadamente más laxo que SELLING_MAX_LOSS_PCT: el objetivo aquí
# no es cortar una mala operación sino no dejar que se esfume una buena, así
# que conviene algo más de margen frente a la oscilación normal de un
# jugador que sigue siendo rentable. No necesita una confirmación de
# tendencia propia (a diferencia de SELLING_MAX_LOSS_PCT, ver arriba): al
# compararse contra un máximo HISTÓRICO ya exige una caída sostenida por
# construcción, no un solo dato de ruido.
#
# SUBIDO 0.15 -> 0.20 (a petición del usuario, 2026-09-21, mismo análisis
# que SELLING_MAX_LOSS_PCT más arriba): al subir ese umbral de 0.10 a
# 0.15, mantener este en 0.15 lo habría dejado IGUAL de agresivo que el
# corte de pérdidas -- rompiendo la relación deliberada de "esto protege
# una plusvalía ya buena, así que debe tolerar más oscilación que un corte
# de una mala operación". Solo hubo 1 venta real por esta vía en el primer
# mes (muestra todavía pequeña para calibrar con datos propios), así que
# el ajuste aquí es para preservar el margen de +5 puntos porcentuales
# original sobre SELLING_MAX_LOSS_PCT, no una lectura directa de datos de
# esta vía en concreto.
SELLING_TRAILING_STOP_MAX_DRAWDOWN_PCT = float(os.getenv("SELLING_TRAILING_STOP_MAX_DRAWDOWN_PCT", "0.20"))

# % máximo (positivo, ej. 0.15 = 15%) del capital TOTAL del equipo (suma
# del valor de mercado de toda la plantilla + presupuesto disponible) que
# puede estar inmovilizado en UN solo jugador con lesión CONFIRMADA (no
# "doubt" -- ver clients.futmondo_client.is_confirmed_injured_status())
# antes de ponerlo en venta, aunque ni siquiera haya llegado a
# SELLING_MAX_LOSS_PCT de pérdida (a petición del usuario,
# 2026-08-22, ver docstring de engine/selling_strategy.py). La intención
# no es la rentabilidad de la operación sino evitar tener gran parte del
# capital inmovilizado en un jugador que no se puede usar mientras esté
# lesionado -- un problema de concentración de capital, no de plusvalía.
# 15% es un punto de partida razonado, sin calibrar todavía con
# resultados reales.
SELLING_INJURY_CONCENTRATION_MAX_PCT = float(os.getenv("SELLING_INJURY_CONCENTRATION_MAX_PCT", "0.15"))

# Margen mínimo de score de alineación (config.LINEUP_EVALUATOR_WEIGHTS,
# calidad pura, sin precio) que el MEJOR candidato de mercado en una
# posición debe superar al score propio de un suplente antes de venderlo
# solo por esto -- "oportunidad de mercado / plaza escasa" (a petición del
# usuario, 2026-08-22, tras el límite de plantilla de jobs/run_market.py,
# ver docstring de engine/selling_strategy.py). Deliberadamente por ENCIMA
# de BIDDING_CANCEL_SWAP_MIN_MARGIN (0.25): vender un jugador ya en
# plantilla sin motivo de rentabilidad es un cambio de comportamiento
# mayor que cancelar una puja todavía sin resolver, así que pide más
# margen de diferencia antes de disparar. Sin calibrar todavía con
# resultados reales.
SELLING_UPGRADE_AVAILABLE_MIN_MARGIN = float(os.getenv("SELLING_UPGRADE_AVAILABLE_MIN_MARGIN", "0.30"))

# Antigüedad mínima (días desde la puja ganada) para que un jugador pueda
# venderse SOLO por "oportunidad de mercado" (a petición del usuario,
# 2026-09-23, misma revisión de rotación que SELLING_LOSS_TIME_MULT_MAX):
# en el primer mes, Camavinga, Brugué e Ionuț Radu se pusieron en venta
# por esta vía a las pocas HORAS de ficharlos -- un recién llegado todavía
# no tiene estadísticas propias y su score de alineación sale bajo, así
# que casi cualquier candidato de mercado le "supera". Sin fecha de compra
# conocida, este filtro no bloquea. No afecta a las demás vías.
SELLING_UPGRADE_MIN_HOLD_DAYS = float(os.getenv("SELLING_UPGRADE_MIN_HOLD_DAYS", "7"))

# Máximo de ventas por POSICIÓN y ejecución que puede autorizar la vía
# "oportunidad de mercado" (a petición del usuario, 2026-08-23, caso real:
# el mismo mejor candidato de DEL justificó vender a Raba Y Brugué a la
# vez, y el de MED a Camavinga Y Dieng -- pero de ese candidato solo se
# puede fichar UNO). Si varios jugadores propios de la misma posición
# cualifican, se prioriza el de mayor margen de score (peor suplente
# relativo, ver engine/selling_strategy.py); el resto se descarta esta
# vez, no se difiere ni se fuerza -- igual que ocurre ya con el margen de
# banquillo.
SELLING_UPGRADE_MAX_SALES_PER_POSITION = int(os.getenv("SELLING_UPGRADE_MAX_SALES_PER_POSITION", "1"))

# Eficiencia marginal mínima exigida (score de alineación por MILLÓN
# EXTRA de precio) cuando el mejor candidato de mercado es más caro que el
# propio jugador que se plantea vender -- a petición del usuario,
# 2026-08-23: "no podemos comparar la calidad de un jugador de 4 millones
# con uno de 50". SELLING_UPGRADE_AVAILABLE_MIN_MARGIN por sí solo no
# distingue esto (un margen de score de 0.35 es igual de "suficiente" si
# el candidato cuesta 2M más o 55M más que el propio jugador) -- este
# umbral exige que el margen de score sea proporcional al sobrecoste real:
# margen_score / (precio_candidato - precio_propio en millones) debe
# superar este valor. Solo se aplica cuando el candidato es MÁS caro (si
# es igual o más barato, el margen de score por sí solo ya basta, es
# upgrade "gratis" o barato). Sin calibrar todavía con resultados reales.
SELLING_UPGRADE_MIN_SCORE_PER_EXTRA_MILLION = float(os.getenv("SELLING_UPGRADE_MIN_SCORE_PER_EXTRA_MILLION", "0.03"))

# Horas asumidas que tarda en RESOLVERSE nuestra propia venta una vez
# listada -- a petición del usuario, 2026-08-23 ("el plazo es de 24 horas
# desde el inicio de la oferta"): Futmondo no da un `expirationDate` fijo
# para un listado nuestro antes de crearlo (lo decide el juego al listar,
# ver `FutmondoClient.list_for_sale()`), así que esto es una APROXIMACIÓN
# deliberadamente conservadora del "caso peor" (nadie puja hasta el final
# de esa ventana), usada solo para comparar contra el tiempo que le queda
# al listado del candidato objetivo (`expirationDate` real, ese sí
# confirmado, ver clients/futmondo_client.py) antes de vender "para poder
# comprar a X" -- si a X le queda menos tiempo en el mercado del que
# asumimos que tardará nuestra venta en resolverse, no tiene sentido
# vender con ese objetivo concreto (para cuando tengamos el dinero, X ya
# no estará). Sin dato exacto por puja individual todavía (ver TODO.md).
SELLING_ASSUMED_SALE_RESOLUTION_HOURS = float(os.getenv("SELLING_ASSUMED_SALE_RESOLUTION_HOURS", "24"))

# Bloqueo defensivo (a petición del usuario, 2026-08-23): en fin de semana
# (sábado/domingo, aproximación por día de la semana -- no distingue hora
# exacta de los partidos), NUNCA se pone en venta a un jugador que esté en
# la alineación TITULAR guardada del bot (`FutmondoClient.get_lineup()`),
# sea cual sea el motivo (ninguna de las cinco vías queda exenta) -- no
# está confirmado si Futmondo bloquea o penaliza vender a un titular
# mientras la jornada está en juego, así que este bloqueo es puramente
# preventivo, para no arriesgar los puntos de la jornada en curso.
ENABLE_SELLING_WEEKEND_LINEUP_GUARD = os.getenv("ENABLE_SELLING_WEEKEND_LINEUP_GUARD", "true").lower() == "true"

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

# Margen de tolerancia para aceptar una oferta recibida de Futmondo
# (jobs/run_sales.py, _process_received_offers) aunque NO llegue al precio
# de salida pedido -- a petición del usuario, 2026-08-28: una puja "muy
# cercana" al precio pedido (p.ej. a un 5% o menos por debajo) se acepta
# igual, en vez de dejar el listado esperando indefinidamente a que alguien
# iguale o supere la cifra exacta pedida. Se acepta la mejor oferta de
# Futmondo si offer_price >= listing_price * (1 - SELLING_OFFER_ACCEPTANCE_MARGIN).
SELLING_OFFER_ACCEPTANCE_MARGIN = float(os.getenv("SELLING_OFFER_ACCEPTANCE_MARGIN", "0.05"))

# Umbral MÁS ESTRICTO que el de listar, exclusivo del momento de ACEPTAR
# una oferta recibida (a petición del usuario, 2026-08-29, ver docstring
# de jobs/run_sales.py): decide_sales() permite listar hasta dejar una
# posición en bench=0 (justo los titulares que exige la alineación, sin
# margen), pero aceptar es lo que de verdad concreta la venta -- por eso
# aceptar exige bench_después >= 1 (al menos un suplente sano), salvo que
# sea un "swap" verificado en marcha (ver más abajo), el único caso en que
# se permite bajar a bench=0 al aceptar. Nunca se acepta si eso creara un
# déficit real (bench_antes <= 0), swap o no.
#
# "Swap": una venta que calificó ÚNICAMENTE por la vía "oportunidad de
# mercado" (ver engine/selling_strategy.py) ya identifica, al listar, un
# candidato de mercado concreto que la motivó -- se persiste en
# `db.models.sale_swap_targets` (ver save_swap_target()). Al aceptar, si
# ese candidato (o uno equivalente, si hubo que retargetear, ver
# `jobs.run_sales._resolve_swap_target()`) sigue realmente listado en el
# mercado AHORA MISMO con más de esta cantidad de horas antes de expirar,
# el swap se considera "en marcha" y se permite bajar a bench=0 -- da
# tiempo a que corra jobs/run_market.py y pueda pujar de verdad por él
# antes de que el listado desaparezca. Sin ese margen (o sin ningún
# candidato equivalente disponible), el swap se da por muerto y se aplica
# el umbral estricto de arriba, como cualquier otra venta.
SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT = float(os.getenv("SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT", "1"))

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
