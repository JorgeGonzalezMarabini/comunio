"""
Job: evalúa el mercado y ejecuta pujas automáticas, 100% autónomo (sin
confirmación manual). Cada puja (o intento fallido) se audita en la tabla
`bids` con el score y motivo que la justificó.

Asume que jobs/sync_data.py ya corrió antes en el cron (así `players`/
`futmondo_snapshots`/`external_stats` están al día) — este job solo lee de
la BD y decide, no vuelve a sincronizar stats.

Antes de evaluar el mercado, calcula dos señales de prioridad sobre la
plantilla propia (ambas se suman si coinciden en el mismo candidato, ver
engine.bidding_strategy.apply_position_priority — ninguna fuerza la puja
de un candidato realmente malo, solo dan ventaja frente a otro de score
similar):

  1. Riesgo de plantilla (engine/squad_risk.py:assess_squad_depth):
     posiciones sin ningún suplente sano, donde un "clausulazo"/lesión/
     sanción más dejaría un hueco en la alineación. Señal de CANTIDAD.

  2. Mejora del once (engine/squad_risk.py:weakest_starter_scores): el
     candidato, evaluado con los MISMOS pesos con los que se elige la
     alineación real (config.LINEUP_EVALUATOR_WEIGHTS — el precio no debe
     importar), supera en calidad al titular más flojo de su posición
     hoy. Señal de CALIDAD: aunque la posición ya tenga suplente de sobra,
     fichar a alguien mejor que el peor titular actual sigue siendo una
     mejora real del equipo que sale a jugar. Para comparar en igualdad de
     condiciones, este score de alineación se calcula sobre plantilla +
     mercado juntos en la misma llamada a evaluate_players() (normalizar
     cada uno por separado los dejaría en escalas distintas, no
     comparables entre sí — ver engine/evaluator.py:normalize_pool).

El presupuesto disponible se calcula restando TODAS las pujas pendientes
sin resolver — resuelve TODO.md #3: combina `db.models.
get_pending_bid_amount()` (auditoría local) con `clients.futmondo_client.
real_pending_bid_amount()` (confirmado del propio Futmondo, campo "bid" de
`get_market()`, ver ese docstring) tomando el MAYOR de los dos, nunca solo
uno — así una BD perdida/no reconciliada a tiempo no puede hacer que se
subestime el compromiso real. No es solo lo arriesgado hoy
(get_bids_risked_today, que sigue usándose como ritmo de gasto por
jornada, no como protección de saldo).

place_bid() está **100% confirmado** (ver clients/futmondo_client.py) —
puede fallar por HTTP (requests.RequestException) o por rechazo de negocio
con HTTP 200 (FutmondoOfferError); cada intento va en su propio try/except
para que un fallo de una puja no tumbe las demás ni la ejecución completa.

También confirmado en vivo (2026-08-18, ver `clients.futmondo_client.
real_pending_bid_amount()`): pujar dos veces sobre el mismo jugador
mientras la primera puja sigue abierta no la actualiza (Futmondo responde
"ok" pero ignora el nuevo importe) — por eso este job excluye de los
candidatos a cualquier jugador con una puja local todavía `'placed'`
(`db.models.get_open_bids()`) antes de evaluar, en vez de reintentar una
"mejora" que no tiene ningún efecto real.

Ofertas sobre jugadores de OTRO MANAGER (a petición del usuario,
2026-08-22, ver TODO.md #15, `config.ENABLE_BIDS_ON_MANAGER_LISTINGS`):
todavía sin confirmar en vivo si ganar una puja de compra requiere que el
OTRO MANAGER acepte una oferta explícitamente (como parece ser el caso al
vender, TODO.md #15) o si se resuelve sola. Mientras ese flag siga en
`False` (por defecto), este job solo evalúa/puja por candidatos puestos
en venta por el propio Futmondo (item de mercado con `"computer": True`,
ver `clients/futmondo_client.py`) y, en cada pasada, cancela cualquier
puja YA ABIERTA sobre un jugador de otro manager (`"computer": False`) —
así no queda presupuesto ni plaza de plantilla comprometidos
indefinidamente en una oferta cuya resolución no depende de nosotros.

Lesión confirmada vs. duda (a petición del usuario, 2026-08-22): "doubt"
(duda, el jugador todavía puede llegar a jugar) e "injuredN" (lesión ya
confirmada, ver `clients.futmondo_client.is_confirmed_injured_status()`)
ya NO se tratan igual aquí. "doubt" sigue evaluándose con el resto de
candidatos, solo que con una penalización de score más dura que antes
(`config.EVALUATOR_WEIGHTS["doubt_penalty"]`, ver `engine/evaluator.py`).
Una lesión confirmada, en cambio, se descarta ANTES de evaluar — igual que
los candidatos con puja local ya abierta de arriba — porque normalmente es
baja segura varias jornadas: cualquier penalización de score, por dura que
sea, siempre podría llegar a compensarse con muy buenas stats en el resto
de métricas, y aquí eso sería justo el error que se quiere evitar.

Plantilla completa (a raíz de 11 pujas fallidas en vivo, 2026-08-22): antes
de evaluar cualquier candidato se compara `len(roster)` contra
`information.answer.configuration.maxPlayersInRoster` -- si la plantilla
ya está al máximo que permite la liga, Futmondo rechaza CUALQUIER puja
nueva con `FutmondoOfferError("api.market.max_number_players_in_roster")`
sin importar posición ni presupuesto, así que se descartan aquí todos los
candidatos NUEVOS en vez de generar pujas condenadas a fallar. **Ya NO
corta la ejecución entera** (cambiado a la vez que se añadió el reajuste a
la baja, ver más abajo): cancelar+repujar más barato una puja YA abierta
no pide una plaza nueva, así que esa fase sigue evaluándose igual aunque
la plantilla esté al máximo.

**Corregido el mismo día, dos veces** (a petición del usuario, tras ver en
vivo "plantilla completa (15/15)" tratándose en realidad de una liga con
tope 18): el campo leído originalmente era `configuration.numberOfPlayers`,
que en realidad es el **número de jugadores INICIALES** de la liga (15),
no el máximo. Un primer intento de arreglo, inspeccionando el bundle
`main.dart.js` de la propia app (dart2js no minifica los literales de
string), encontró `configuration.playersInRoster` como candidato -- esa
clave sí existe (validada con `r>=11` en el propio código de la app), pero
resultó ser el nombre usado en el payload de ESCRITURA al guardar la
configuración de liga, no el campo real que devuelve `get_information()`.
**Confirmado con prueba manual real** (2026-08-22): el campo correcto en
la respuesta es `configuration.maxPlayersInRoster`. Si solo hay hueco
PARCIAL (menos plazas libres que candidatos buenos, contando también las pujas abiertas
sobre otros jugadores como plaza reservada), se limita
`decide_bids_for_market(max_bids=...)` a esas plazas -- como
`apply_position_priority` ya deja `prioritized` ordenado por
score+boosts de mayor a menor, ese corte sigue pujando primero por los
candidatos más importantes (posición en riesgo / mejora del once), no
por los primeros que se evalúen.

Cancelar+pujar mejor (TODO.md #13, `engine.bidding_strategy.
find_cancel_swap_candidates`): fase APARTE, después del loop normal de
arriba, para los candidatos buenos (score/precio válidos, ver
`is_price_worth_bidding`) que se quedaron fuera solo por presupuesto/tope,
no por calidad — se calcula incluso si el loop normal no pujó nada, que es
justo el caso más útil (presupuesto tan ajustado que nada nuevo entra).
Solo sacrifica una puja abierta si el candidato la supera en score por un
margen amplio (`config.BIDDING_CANCEL_SWAP_MIN_MARGIN`, bastante por
encima de cualquier boost de prioridad, para no cancelar por ruido) y si a
esa puja le queda tiempo de sobra antes de expirar
(`config.BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY` — si está a punto de
resolverse, mejor dejarla terminar). Nunca cancela una puja cuyo id real de
Futmondo no venga confirmado en el `get_market()` de esta misma pasada —
eso excluye automáticamente cualquier puja colocada en este mismo run (su
`market_item` es de ANTES de pujar). Como `cancel_bid()`+`place_bid()` no
es atómico, un fallo a medias (cancela pero no llega a pujar la nueva)
queda auditado en `bids` como `'cancelled'`+`'failed'`, nunca silencioso —
ver `config.BIDDING_MAX_CANCEL_SWAPS_PER_RUN` (1 por defecto, deliberado
mientras esta feature no tiene histórico real).

TOCTOU entre el snapshot de plantilla y la ejecución real de las pujas
(bug real en vivo, 2 pujas fallidas 2026-08-23 con
`api.market.max_number_players_in_roster` a pesar del guard de arriba, ver
TODO.md #18): `get_roster()`/`get_information()` se leen UNA SOLA VEZ al
principio de `run()`, y con ese único snapshot se decide TODO el lote de
pujas nuevas de esta pasada -- pero entre esa lectura y el `place_bid()`
real de cada candidato pasa el tiempo de evaluar el pool completo del
mercado (Fotmob/Understat incluidos), tiempo en el que el hueco real de
plantilla puede cambiar (otra oferta resuelta de forma asíncrona por
Futmondo, o incluso una acción manual del usuario en la app -- nada
bloquea la cuenta entre medias). Si eso pasa, el snapshot queda obsoleto
y el guard de arriba ya no protege nada: seguiría intentando pujar todo el
lote igual. Arreglado sin tener que releer `get_roster()` antes de cada
`place_bid()` (evita más llamadas de las necesarias): el bucle de pujas de
más abajo reacciona al primer rechazo REAL de Futmondo con ese código
concreto (`FutmondoOfferError.code`, ver clients/futmondo_client.py) y
aborta el resto de candidatos NUEVOS de este mismo lote sin intentarlos --
todos comparten el mismo hueco que la propia API ya confirmó que no
existe, así que seguir probando uno a uno solo generaría más pujas
condenadas a fallar.

`maxPlayersInRoster` ausente en la respuesta (mismo incidente, mismo
día): antes, si el campo venía `None` (glitch transitorio de la API, o un
cambio de forma de la respuesta -- este campo concreto ya se confundió DOS
VECES el mismo día que se introdujo, ver docstring de arriba), ambos
guards (`roster_full`/`available_roster_slots`) se desactivaban en
silencio y se volvía a pujar sin ningún tope de plazas -- justo el bug
original de las 11 pujas fallidas, pero sin ningún aviso de que el propio
guard se había saltado. Primer arreglo (mismo día): tratarlo explícitamente
como anomalía y no pujar NINGÚN candidato nuevo esa pasada -- correcto,
pero de sobra: es un valor de configuración de LIGA que el usuario fija
una vez al crearla y no cambia en la práctica, así que bloquear pujas
enteras por un glitch puntual de la API era más conservador de lo
necesario. Arreglo definitivo (a petición del usuario, mismo día, ver
TODO.md #19): se cachea en `db.models.league_settings`
(`get_league_setting()`/`save_league_setting()`) en cuanto la API lo
confirma, y si una pasada concreta no lo informa, se cae a ese último
valor confirmado en vez de bloquear o degradar a "sin límite" -- solo si
NUNCA se confirmó (ni ahora ni antes, primera vez que corre el bot contra
esta liga) se trata como anomalía de verdad.

El error real de cada puja fallida (antes solo vivía en el mensaje de
Telegram de esa pasada, nunca en la BD -- imposible diagnosticar después
de los hechos si no se guardó a mano) ahora se persiste también en
`bids.error` (ver `_persist_bid()`), no solo el `reason` del score que la
justificó.

Reajustar a la baja pujas abiertas cuyo VM ha caído (a petición del
usuario, 2026-08-22, ver engine.bidding_strategy.
find_reprice_down_candidates): `decide_bid()` ancla el importe al VM del
jugador EN EL MOMENTO de pujar — una vez colocada, la puja quedaba
"congelada" para siempre, sin importar cuánto cayera después el VM del
jugador (Futmondo no tiene endpoint confirmado para editar el importe de
una oferta abierta — modifybid/modifyrosterbid/modifyprice aparecen solo
como hallazgo sin implementar, ver clients.futmondo_client — ni pujar otra
vez la actualiza, ver docstring de arriba). Fase APARTE, después del loop
normal y del swap de arriba: para cada puja local todavía abierta con
datos frescos de hoy (mismo candidato evaluado con el VM/score actuales,
"fresh_already_bid" más abajo) se recalcula lo que decide_bid() pujaría
HOY por ese MISMO jugador; si el resultado es al menos
`config.BIDDING_REPRICE_DOWN_MIN_DROP_PCT` más bajo que lo ya pujado, se
cancela la puja vieja y se coloca una nueva por el importe recalculado.
Nunca sube una puja, ni cancela sin más una que decide_bid() ya no
recomendaría en absoluto (eso queda fuera de alcance: solo baja un
importe, no decide si seguir pujando por ese jugador). Mismas precauciones
que el swap: nunca toca una puja sin id real de Futmondo confirmado en el
`get_market()` de esta pasada, ni una a punto de expirar
(`config.BIDDING_REPRICE_DOWN_MIN_HOURS_BEFORE_EXPIRY`), ni más de
`config.BIDDING_MAX_REPRICE_DOWNS_PER_RUN` por ejecución — y un fallo a
medias (cancela pero no llega a repujar) queda auditado como
`'cancelled'`+`'failed'`, igual que el swap.

Rescate de déficit de plantilla en pujas de COMPRA (a petición del usuario:
dos clausulazos -u otra baja- sobre la misma posición sin ninguna venta
propia pendiente ahí, ver `engine.bidding_strategy.
find_deficit_rescue_swaps`): `jobs.sync_data._rescue_sales_at_risk()` ya
cancela una VENTA propia listada cuando su posición se queda con margen
NEGATIVO (`deficit>0`, ver `engine/squad_risk.py`), pero esa vía no sirve
si no había ninguna venta pendiente en esa posición -- ahí la única forma
de recuperar un cuerpo es una puja de COMPRA nueva. Fase APARTE, tras el
swap normal de arriba: para cada posición con `deficit>0` que NINGUNA puja
cubre todavía (ni de esta pasada, ni ya abierta antes, ni del swap
normal), sacrifica una puja de compra en OTRA posición (nunca otra
también en déficit) para poder pujar por el mejor candidato disponible de
la posición en déficit -- SIN exigir el margen de score del swap normal
(`config.BIDDING_CANCEL_SWAP_MIN_MARGIN`): lo urgente es recuperar un
cuerpo, no encontrar una mejora. Mismas precauciones que el resto de fases
de cancelar+pujar (nunca toca una puja sin id real de Futmondo confirmado
en el `get_market()` de esta pasada, ni una a punto de expirar
`config.BIDDING_DEFICIT_RESCUE_MIN_HOURS_BEFORE_EXPIRY`, ni más de
`config.BIDDING_MAX_DEFICIT_RESCUES_PER_RUN` por ejecución) y mismo
patrón de auditoría ante un fallo a medias (`'cancelled'`+`'failed'`).
"""
from datetime import datetime, timezone

import requests

import config
from clients.futmondo_client import (
    FUTMONDO_POSITION_MAP,
    FutmondoClient,
    FutmondoOfferError,
    is_confirmed_injured_status,
    real_pending_bid_amount,
)
from db.models import (
    get_connection,
    get_bids_risked_today,
    get_league_setting,
    get_open_bids,
    get_pending_bid_amount,
    get_player_features,
    save_league_setting,
    update_bid_status,
)
from engine.bidding_strategy import (
    apply_position_priority,
    decide_bid,
    decide_bids_for_market,
    dynamic_min_score_threshold,
    dynamic_player_cap,
    find_cancel_swap_candidates,
    find_deficit_rescue_swaps,
    find_reprice_down_candidates,
    is_price_worth_bidding,
)
from engine.evaluator import evaluate_players
from engine.squad_risk import assess_squad_depth, depth_warnings, weakest_starter_scores
from notifier import notify, track_job_run, format_number


_MAX_IDS_IN_SUMMARY = 20


def _format_id_list(ids: list) -> str:
    """
    Lista de ids separados por coma para el resumen de Telegram, truncada a
    `_MAX_IDS_IN_SUMMARY` -- caso real (2026-09-08): un desfase entre el
    `on_market` cacheado en BD (ver `db.models.get_player_features` /
    `jobs.sync_data`, que solo refresca ese flag para jugadores presentes
    en el roster o mercado de la pasada actual, así que uno que sale del
    mercado sin volver a aparecer en ninguna de las dos listas se queda
    "on_market=1" en BD indefinidamente) y el mercado real de Futmondo hizo
    que `skipped_manager_listed` trajera 264 candidatos en una sola
    ejecución -- esa única línea, sin truncar, medía ~7000 caracteres ella
    sola y hacía que el mensaje entero superase el límite de 4096 de
    Telegram (`api.telegram.org/.../sendMessage` respondía 400 Bad
    Request, ver notifier.py). Aparte de la protección genérica de
    `notifier._split_message()`, truncar aquí evita además un mensaje
    ilegible (264 ids en crudo no aportan nada a un vistazo en el móvil).
    """
    shown = [str(i) for i in ids[:_MAX_IDS_IN_SUMMARY]]
    rest = len(ids) - len(shown)
    suffix = f" (y {rest} más)" if rest > 0 else ""
    return ", ".join(shown) + suffix


def _persist_bid(conn, decision: dict, status: str, now: str, error: str | None = None) -> None:
    """
    `error`: mensaje real del fallo (`str(excepción)`) cuando `status`
    es 'failed' -- antes solo se guardaba `decision["reason"]` (la
    justificación del SCORE que decidió pujar, no el motivo del fallo), así
    que un incidente real (p. ej. las 2 pujas fallidas del 2026-08-23 con
    `api.market.max_number_players_in_roster`) quedaba sin poder
    diagnosticarse desde la BD después de los hechos -- solo vivía en el
    mensaje de Telegram de esa pasada concreta. Ver TODO.md #18.
    """
    conn.execute(
        """
        INSERT INTO bids (player_id, amount, status, score, reason, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (str(decision["player_id"]), decision["amount"], status, decision["score"], decision["reason"], error, now),
    )


def run():
    if not config.ENABLE_BOT:
        print("run_market: ENABLE_BOT=false, no se ejecuta.")
        return

    client = FutmondoClient()

    # Mercado en vivo -- se pide YA aquí, antes que nada más, porque hace
    # falta para dos cosas: (a) cancelar pujas abiertas sobre jugadores de
    # OTRO MANAGER si config.ENABLE_BIDS_ON_MANAGER_LISTINGS es False (ver
    # docstring del módulo/TODO.md #15), y (b) filtrar por el mismo motivo
    # los candidatos nuevos, antes de evaluarlos. (a) se ejecuta SIEMPRE,
    # incluso si no hay ningún candidato nuevo que evaluar esta pasada
    # (por eso va antes del primer `if not raw_candidates`, no después).
    market_items = client.get_market().get("answer", [])
    market_by_id = {str(p["id"]): p for p in market_items}

    # Pujas de compra YA ABIERTAS sobre un jugador de OTRO MANAGER (a
    # petición del usuario, 2026-08-22, ver TODO.md #15/docstring del
    # módulo): mientras no se confirme que ganar esa puja funciona igual
    # que comprarle a "el Computer" del juego, no queremos presupuesto ni
    # una plaza de plantilla escasa comprometidos indefinidamente en una
    # oferta que depende de que otro manager decida aceptarla. Se ejecuta
    # en CADA pasada (no solo la primera vez) para que, si el flag pasa de
    # true a false más adelante, la siguiente ejecución limpie sola
    # cualquier puja que ya no cumpla la política.
    cancelled_manager_bids, failed_manager_bid_cancels = [], []
    if not config.ENABLE_BIDS_ON_MANAGER_LISTINGS:
        for b in get_open_bids():
            market_item = market_by_id.get(str(b["player_id"]))
            if market_item is None or market_item.get("computer", False):
                continue  # ya no está en mercado (se resolverá solo en el próximo sync), o SÍ es del Computer
            bid_info = market_item.get("bid")
            if not bid_info:
                continue  # sin id de oferta CONFIRMADO en este snapshot -- nunca cancelar a ciegas
            try:
                client.cancel_bid(bid_info["id"])
                update_bid_status(b["id"], "cancelled")
                cancelled_manager_bids.append(b["player_id"])
            except (requests.RequestException, FutmondoOfferError) as e:
                failed_manager_bid_cancels.append((b["player_id"], str(e)))
        if cancelled_manager_bids or failed_manager_bid_cancels:
            lines = [
                "run_market: revisión de pujas sobre jugadores de otro manager "
                "(config.ENABLE_BIDS_ON_MANAGER_LISTINGS=false):"
            ]
            if cancelled_manager_bids:
                lines.append(f"  - {len(cancelled_manager_bids)} cancelada(s): {', '.join(cancelled_manager_bids)}")
            if failed_manager_bid_cancels:
                lines.append(f"  - {len(failed_manager_bid_cancels)} fallida(s) al cancelar:")
                lines.extend(f"    - jugador {pid}: {err}" for pid, err in failed_manager_bid_cancels)
            notify("\n".join(lines))

    raw_candidates = get_player_features(only_on_market=True)
    if not raw_candidates:
        notify("run_market: no hay candidatos en mercado en la BD (¿corrió sync_data antes?).")
        return

    # Excluye candidatos con una puja local todavía 'placed' -- confirmado
    # en vivo que pujar dos veces sobre el mismo jugador no actualiza el
    # importe (ver docstring del módulo / clients.futmondo_client.
    # real_pending_bid_amount()), así que reintentarlo no tiene efecto real
    # y solo ensucia la auditoría con filas duplicadas.
    # OJO: ninguno de los tres filtros de abajo corta la ejecución si deja
    # `raw_candidates` vacío (a diferencia de versiones anteriores) -- un
    # mercado sin NINGÚN candidato nuevo que evaluar puede seguir teniendo
    # pujas YA ABIERTAS cuyo VM haya caído, y esa revisión (más abajo, ver
    # docstring del módulo) no depende de que haya nada nuevo que pujar.
    # Cada lista `skipped_*` se conserva para auditar en el resumen final
    # (y, `skipped_already_bid`, también como fuente de datos frescos para
    # el reajuste a la baja).
    already_bid_ids = {b["player_id"] for b in get_open_bids()}
    skipped_already_bid = [p for p in raw_candidates if p["id"] in already_bid_ids]
    raw_candidates = [p for p in raw_candidates if p["id"] not in already_bid_ids]

    # Descarta candidatos puestos en venta por OTRO MANAGER mientras
    # config.ENABLE_BIDS_ON_MANAGER_LISTINGS siga en False (ver bloque de
    # arriba/TODO.md #15) -- un candidato sin item de mercado confirmado
    # en este snapshot (desajuste puntual con sync_data) se trata como "no
    # es del Computer" por precaución, nunca se puja a ciegas.
    skipped_manager_listed = []
    if not config.ENABLE_BIDS_ON_MANAGER_LISTINGS:
        skipped_manager_listed = [
            p for p in raw_candidates if not market_by_id.get(p["id"], {}).get("computer", False)
        ]
        raw_candidates = [p for p in raw_candidates if market_by_id.get(p["id"], {}).get("computer", False)]

    # Descarta directamente los candidatos con lesión CONFIRMADA (ver
    # docstring del módulo) -- a diferencia de "doubt", que sigue abajo en
    # `raw_candidates` y solo se penaliza en el score
    # (config.EVALUATOR_WEIGHTS["doubt_penalty"]).
    skipped_injured = [p for p in raw_candidates if is_confirmed_injured_status(p.get("status"))]
    raw_candidates = [p for p in raw_candidates if not is_confirmed_injured_status(p.get("status"))]

    # Riesgo de plantilla: posiciones sin ningún suplente sano (ver
    # engine/squad_risk.py). Necesita la plantilla propia, no solo el
    # mercado -- se resuelve igual que en jobs/set_lineup.py.
    roster_response = client.get_roster()
    roster_ids = {str(p["id"]) for p in roster_response.get("answer", [])}
    all_players = get_player_features(only_on_market=False)
    squad_raw = [p for p in all_players if p["id"] in roster_ids]

    # Límite total de plantilla (confirmado en vivo 2026-08-22:
    # "api.market.max_number_players_in_roster" -- Futmondo rechaza
    # CUALQUIER puja nueva si la plantilla ya tiene el máximo de jugadores
    # que permite la liga, sin importar posición ni presupuesto/tope). Se
    # pide `get_information()` ya aquí (en vez de más abajo, donde antes
    # solo se usaba para `budget`) para cortar ANTES de evaluar candidatos
    # y así no generar pujas que van a fallar seguro.
    #
    # `maxPlayersInRoster`, NO `numberOfPlayers` ni `playersInRoster`
    # (corregido el mismo día dos veces, ver docstring del módulo):
    # `numberOfPlayers` es el número de jugadores INICIALES de la liga;
    # `playersInRoster` resultó ser el nombre del payload de ESCRITURA al
    # guardar la configuración, no el campo real de esta respuesta.
    # `maxPlayersInRoster` es el confirmado con prueba manual real contra
    # la cuenta real.
    information = client.get_information()
    max_roster_size = information.get("answer", {}).get("configuration", {}).get("maxPlayersInRoster")

    # `maxPlayersInRoster` ausente (a raíz del incidente real 2026-08-23,
    # ver TODO.md #18/#19 y docstring del módulo): este campo concreto ya
    # se confundió DOS VECES el mismo día que se introdujo el límite de
    # plantilla, así que no es descabellado que un glitch transitorio de la
    # API o un cambio de forma de la respuesta lo dejen sin informar. Es un
    # valor de configuración de LIGA que el usuario fija una vez al crearla
    # y no cambia en la práctica -- así que, si la API no lo informa esta
    # pasada, tiene mucho más sentido caer al último valor CONFIRMADO por
    # ella misma en una pasada anterior (`db.models.get_league_setting()`)
    # que degradar a "sin límite" (bug original, TODO.md #16) NI bloquear
    # pujas nuevas sin necesidad (comportamiento previo de este mismo fix,
    # TODO.md #18) -- ambos evitables si ya lo sabíamos de antes. Cuando SÍ
    # viene informado, se cachea (solo si cambió, para no escribir de más)
    # -- nunca se cachea el propio valor de fallback, para que uno viejo no
    # se reafirme a sí mismo sin ninguna confirmación real nueva.
    max_roster_size_from_cache = False
    if max_roster_size is not None:
        if get_league_setting("max_players_in_roster") != max_roster_size:
            save_league_setting("max_players_in_roster", max_roster_size, datetime.now(timezone.utc).isoformat())
    else:
        cached_max_roster_size = get_league_setting("max_players_in_roster")
        if cached_max_roster_size is not None:
            max_roster_size = cached_max_roster_size
            max_roster_size_from_cache = True

    # Solo si NUNCA se confirmó un valor real (ni esta pasada ni ninguna
    # anterior, así que tampoco hay nada cacheado) se trata como anomalía
    # de verdad: comportamiento conservador (no pujar ningún candidato
    # nuevo esta pasada, igual que plantilla llena) y notificado, en vez de
    # asumir que no hay tope.
    max_roster_size_unknown = max_roster_size is None
    roster_full = not max_roster_size_unknown and len(roster_ids) >= max_roster_size
    roster_full_discarded = len(raw_candidates) if (roster_full or max_roster_size_unknown) else 0
    if roster_full or max_roster_size_unknown:
        # Descarta los candidatos NUEVOS (Futmondo rechazaría cualquier
        # puja nueva sin importar posición/presupuesto, o no hay tope
        # confirmado ni cacheado para saber si hay hueco), pero NO corta la
        # ejecución -- el reajuste a la baja de pujas YA abiertas de más
        # abajo sigue adelante igual: cancelar+repujar más barato no pide
        # una plaza nueva, reutiliza la que esa puja ya tenía reservada.
        raw_candidates = []

    # Hueco parcial: si queda sitio pero no para todos los candidatos
    # buenos, hay que pujar solo por los `available_roster_slots` más
    # importantes -- no por todos los que pasen los demás filtros. Cuenta
    # también `already_bid_ids` (pujas abiertas sobre OTROS jugadores, ya
    # excluidas de `raw_candidates` arriba) como plaza reservada: si
    # ganan, también ocuparán un hueco de la plantilla, aunque Futmondo
    # solo compruebe `len(roster)` en el momento de pujar, no las pujas
    # pendientes. `decide_bids_for_market` recorre `prioritized` (ver
    # abajo) ya ordenado por score+boosts de mayor a menor -- pasarle
    # `max_bids` respeta ese mismo orden de importancia.
    #
    # Con `max_roster_size` sin confirmar ni cacheado, 0 en vez de "sin
    # límite" -- mismo comportamiento conservador de arriba, ver
    # `max_roster_size_unknown`.
    available_roster_slots = (
        0 if max_roster_size_unknown else max(0, max_roster_size - len(roster_ids) - len(already_bid_ids))
    )

    at_risk_positions = set()
    upgrade_thresholds = {}
    lineup_score_by_id = {}
    if squad_raw:
        depth = assess_squad_depth(squad_raw, formation=config.DEFAULT_FORMATION)
        at_risk_positions = {pos for pos, info in depth.items() if info["at_risk"]}
        risk_warnings = depth_warnings(depth)

        # Score de alineación (LINEUP_EVALUATOR_WEIGHTS, sin precio) de
        # plantilla + mercado EN LA MISMA llamada, para que sean
        # comparables entre sí (ver docstring del módulo) -- de aquí sale
        # tanto el listón (squad) como el "lineup_score" de cada candidato
        # de mercado que se compara contra ese listón. Incluye también
        # `skipped_already_bid` (candidatos con puja local ya abierta,
        # excluidos de `raw_candidates` arriba) -- si no, su "lineup_score"
        # más abajo saldría de normalizar un pool DISTINTO al de este run,
        # no comparable (ver engine.evaluator.normalize_pool).
        lineup_scored = evaluate_players(
            squad_raw + raw_candidates + skipped_already_bid, weights=config.LINEUP_EVALUATOR_WEIGHTS
        )
        lineup_score_by_id = {p["id"]: p["score"] for p in lineup_scored}
        squad_ids = {p["id"] for p in squad_raw}
        squad_lineup_ranked = [p for p in lineup_scored if p["id"] in squad_ids]
        upgrade_thresholds = weakest_starter_scores(squad_lineup_ranked, formation=config.DEFAULT_FORMATION)
    else:
        risk_warnings = []

    # Evalúa `raw_candidates` y `skipped_already_bid` EN LA MISMA llamada
    # (mismo motivo que arriba: normalize_pool normaliza dentro del pool
    # que se le pasa, así que evaluarlos por separado daría scores en
    # escalas distintas, no comparables entre sí).
    ranked_pool = evaluate_players(raw_candidates + skipped_already_bid)
    for p in ranked_pool:
        if p["id"] in lineup_score_by_id:
            p["lineup_score"] = lineup_score_by_id[p["id"]]
    ranked = [p for p in ranked_pool if p["id"] not in already_bid_ids]
    already_bid_ranked = [p for p in ranked_pool if p["id"] in already_bid_ids]
    prioritized = apply_position_priority(ranked, at_risk_positions, upgrade_thresholds=upgrade_thresholds)

    # Score "fresco" (2026-08-22, a petición del usuario) de los
    # candidatos con puja abierta -- MISMOS pesos/boosts que `prioritized`
    # (riesgo de posición / mejora del once CON LOS DATOS DE HOY), para
    # comparar swaps contra la prioridad actual de la plantilla, no contra
    # el score que ese candidato tenía congelado en `bids.score` desde el
    # momento en que se pujó (pudo quedar desactualizado si el riesgo de
    # plantilla cambió desde entonces). Ver find_cancel_swap_candidates.
    fresh_already_bid = apply_position_priority(
        already_bid_ranked, at_risk_positions, upgrade_thresholds=upgrade_thresholds
    )
    fresh_score_by_player_id = {p["id"]: p["score"] for p in fresh_already_bid}

    remaining_budget = information.get("answer", {}).get("budget", 0)
    already_risked = get_bids_risked_today()

    # Presupuesto: reusa `market_items`/`market_by_id` ya pedidos arriba
    # (mismo fetch, ahora adelantado para la política de otro manager) --
    # cruza la auditoría local con la fuente confirmada del propio
    # Futmondo (TODO.md #3, resuelto), tomando el MAYOR de las dos, nunca
    # solo la local, para que una BD perdida/desincronizada no pueda hacer
    # que se subestime el compromiso real.
    pending_committed = max(get_pending_bid_amount(), real_pending_bid_amount(market_items))

    # Tope por jugador dinámico (ver engine.bidding_strategy.
    # dynamic_player_cap) en vez del antiguo tope fijo de 15M -- combina
    # valor medio de la plantilla propia (squad_raw), precio medio del
    # mercado ponderado por score (ranked, sin los boosts de prioridad de
    # apply_position_priority: ese boost es para ORDENAR candidatos entre
    # sí, no una señal de "cuánto vale el mercado ahora") y % del
    # presupuesto disponible.
    player_cap = dynamic_player_cap(remaining_budget, squad_raw, ranked)

    # "Presupuesto objetivo" (ver engine.bidding_strategy.
    # dynamic_min_score_threshold y config.py, 2026-09-07): cuanta más caja
    # ociosa haya respecto al valor de la plantilla, más exigente el umbral
    # mínimo de score -- así el presupuesto acumulado deja de traducirse en
    # fichajes de relleno solo porque "hay dinero de sobra". `squad_raw`
    # trae "price" (VM real, ver db.models.get_player_features) igual que
    # ya consume dynamic_player_cap() arriba.
    squad_value_total = sum(p["price"] for p in squad_raw if p.get("price"))
    min_score_threshold = dynamic_min_score_threshold(remaining_budget, squad_value_total)

    decisions = decide_bids_for_market(
        prioritized,
        remaining_budget,
        already_risked,
        min_score_threshold=min_score_threshold,
        max_bids=available_roster_slots,
        pending_committed=pending_committed,
        player_cap=player_cap,
    )

    # Candidatos buenos (no descartados por score/precio, ver
    # is_price_worth_bidding) que decide_bids_for_market NO pujó -- es
    # decir, bloqueados por presupuesto/tope O por `max_bids` (plazas de
    # plantilla, ver available_roster_slots arriba), no por calidad. Se
    # calculan SIEMPRE (incluso si `decisions` está vacío) porque el caso
    # más útil del swap es justo cuando presupuesto o plazas están tan
    # ajustados que nada nuevo entra por la vía normal -- ver TODO.md #13.
    # Mismo `min_score_threshold` dinámico que decide_bids_for_market, para
    # que "bloqueado" siga significando "bloqueado por presupuesto/tope",
    # no por un umbral distinto al que de verdad se aplicó arriba.
    decided_ids = {d["player_id"] for d in decisions}
    blocked_candidates = [
        c
        for c in prioritized
        if c["id"] not in decided_ids and is_price_worth_bidding(c, min_score_threshold=min_score_threshold)
    ]

    # Pujas propias abiertas "sacrificables" -- se construye SIEMPRE (no
    # solo si hay `blocked_candidates`), porque la reusan dos fases: el
    # swap normal de abajo (candidato mejor bloqueado) y el rescate de
    # déficit de plantilla de más abajo (engine.bidding_strategy.
    # find_deficit_rescue_swaps), que puede hacer falta aunque no haya
    # ningún `blocked_candidates` todavía evaluado. "position" (nuevo aquí)
    # solo lo usa el rescate de déficit, para no sacrificar nunca una puja
    # de una posición TAMBIÉN en déficit.
    sacrificable_bids = []
    for b in get_open_bids():
        market_item = market_by_id.get(str(b["player_id"]))
        bid_info = market_item.get("bid") if market_item else None
        if not bid_info:
            # Sin id de oferta CONFIRMADO en este snapshot de mercado --
            # puede haberse resuelto ya, o ser una puja colocada en este
            # mismo run (su market_item viene del fetch de arriba,
            # HECHO ANTES de pujar nada esta pasada) -- nunca cancelar
            # sin el id real de Futmondo confirmado (ver
            # clients.futmondo_client.cancel_bid).
            continue
        expires_at = None
        raw_expiration = market_item.get("expirationDate")
        if raw_expiration:
            try:
                expires_at = datetime.fromisoformat(raw_expiration.replace("Z", "+00:00"))
            except ValueError:
                expires_at = None
        sacrificable_bids.append(
            {
                "local_row_id": b["id"],
                "player_id": b["player_id"],
                # Fresco si se pudo recalcular con los datos de hoy
                # (ver fresh_score_by_player_id arriba); si no --
                # jugador ya no en la BD como "on_market", desajuste
                # puntual con sync_data -- cae al score congelado que
                # ya traía la fila local, más seguro que descartar
                # sin más una puja sacrificable de verdad.
                "score": fresh_score_by_player_id.get(b["player_id"], b["score"]),
                "amount": b["amount"],
                "bid_id": bid_info["id"],
                "expires_at": expires_at,
                "position": FUTMONDO_POSITION_MAP.get(market_item.get("role"), market_item.get("role")),
            }
        )

    swap_proposals = (
        find_cancel_swap_candidates(blocked_candidates, sacrificable_bids, now=datetime.now(timezone.utc))
        if blocked_candidates
        else []
    )

    # Reajustar a la baja pujas ya abiertas cuyo VM ha caído (ver docstring
    # del módulo/engine.bidding_strategy.find_reprice_down_candidates). Se
    # calcula YA aquí (antes del corte de "sin pujas esta ejecución" de
    # abajo) para que una pasada sin candidatos nuevos que evaluar, pero
    # con VM caído en pujas ya abiertas, no se quede sin revisar.
    #
    # `pending_after_decisions`/`risked_after_decisions` sí incluyen lo que
    # `decisions` va a pujar en esta misma pasada (aunque todavía no se
    # haya colocado nada -- se ejecuta más abajo) para no subestimar el
    # compromiso real, mismo motivo que `pending_after_run`/
    # `risked_after_run` del swap.
    pending_after_decisions = pending_committed + sum(d["amount"] for d in decisions)
    risked_after_decisions = already_risked + sum(d["amount"] for d in decisions)

    # Nunca ofrecer para reajuste una puja que el swap de arriba ya vaya a
    # sacrificar en esta misma pasada -- ambas fases parten del mismo
    # `get_open_bids()` de antes de ejecutar nada, así que sin este cruce
    # una misma puja podría acabar propuesta para las dos cosas a la vez
    # (la segunda cancelación fallaría sobre una oferta que ya no existe).
    swap_sacrificed_bid_ids = {p["sacrifice"]["bid_id"] for p in swap_proposals}

    # --- Rescate de déficit de plantilla (ver docstring del módulo /
    # engine.bidding_strategy.find_deficit_rescue_swaps): posiciones con
    # margen NEGATIVO de verdad (`deficit>0`, no solo `at_risk` -- margen
    # CERO, ya usado arriba solo para PRIORIZAR, ver `at_risk_positions`)
    # que ninguna puja cubre todavía -- ni de esta pasada (`decisions`),
    # ni ya abierta antes de ella (`skipped_already_bid`), ni del swap
    # normal de arriba. Caso real que lo motiva: dos bajas (clausulazo u
    # otra) de la misma posición sin ninguna venta propia pendiente ahí
    # que jobs.sync_data._rescue_sales_at_risk() pueda cancelar (esa vía
    # solo actúa sobre VENTAS ya listadas, no sobre pujas de COMPRA).
    covered_positions = (
        {c.get("position") for c in prioritized if c["id"] in decided_ids}
        | {p["candidate"].get("position") for p in swap_proposals}
        | {p.get("position") for p in skipped_already_bid}
    )
    deficit_positions = (
        {pos for pos, info in depth.items() if info["deficit"] > 0} - covered_positions if squad_raw else set()
    )
    deficit_rescue_proposals = (
        find_deficit_rescue_swaps(
            deficit_positions,
            prioritized,
            decided_ids,
            [b for b in sacrificable_bids if b["bid_id"] not in swap_sacrificed_bid_ids],
            now=datetime.now(timezone.utc),
        )
        if deficit_positions
        else []
    )
    # Ninguna de las dos fases de swap debe ofrecer la misma puja abierta
    # dos veces (ni al reajuste a la baja de más abajo, ni entre sí).
    sacrificed_bid_ids_this_run = swap_sacrificed_bid_ids | {
        p["sacrifice"]["bid_id"] for p in deficit_rescue_proposals
    }

    fresh_already_bid_by_id = {p["id"]: p for p in fresh_already_bid}
    reprice_inputs = []
    for b in get_open_bids():
        candidate = fresh_already_bid_by_id.get(b["player_id"])
        if candidate is None:
            # Sin datos frescos de hoy (jugador ya no "on_market" en el
            # último sync_data, o descartado como lesión confirmada/venta
            # de otro manager más arriba) -- no reajustar sin esa base.
            continue
        market_item = market_by_id.get(str(b["player_id"]))
        bid_info = market_item.get("bid") if market_item else None
        if not bid_info:
            # Sin id de oferta CONFIRMADO en este snapshot -- mismo criterio
            # de precaución que el swap de arriba, nunca cancelar a ciegas.
            continue
        if bid_info["id"] in sacrificed_bid_ids_this_run:
            continue
        expires_at = None
        raw_expiration = market_item.get("expirationDate")
        if raw_expiration:
            try:
                expires_at = datetime.fromisoformat(raw_expiration.replace("Z", "+00:00"))
            except ValueError:
                expires_at = None
        reprice_inputs.append(
            {
                **candidate,
                "amount": b["amount"],
                "local_row_id": b["id"],
                "bid_id": bid_info["id"],
                "expires_at": expires_at,
            }
        )

    reprice_proposals = (
        find_reprice_down_candidates(
            reprice_inputs,
            remaining_budget,
            risked_after_decisions,
            now=datetime.now(timezone.utc),
            pending_committed=pending_after_decisions,
            player_cap=player_cap,
        )
        if reprice_inputs
        else []
    )

    if not decisions and not swap_proposals and not deficit_rescue_proposals and not reprice_proposals:
        # Contexto de por qué no hubo candidatos NUEVOS que evaluar (ver
        # los filtros de arriba, ninguno corta ya la ejecución por sí
        # solo) -- informativo, no cambia que aquí no hay nada que hacer.
        skip_context = []
        if skipped_already_bid:
            skip_context.append(f"{len(skipped_already_bid)} con puja local ya pendiente")
        if skipped_manager_listed:
            skip_context.append(f"{len(skipped_manager_listed)} en venta por otro manager")
        if skipped_injured:
            skip_context.append(f"{len(skipped_injured)} con lesión confirmada")
        if roster_full:
            skip_context.append(f"plantilla completa ({len(roster_ids)}/{max_roster_size})")
        if max_roster_size_unknown:
            skip_context.append("maxPlayersInRoster no informado por la API ni cacheado (anomalía, ver TODO.md #18/#19)")
        elif max_roster_size_from_cache:
            skip_context.append(f"maxPlayersInRoster no informado por la API esta pasada, usando caché ({max_roster_size})")
        skip_note = f" [{'; '.join(skip_context)}]" if skip_context else ""
        message = (
            f"run_market: sin pujas esta ejecución (saldo={format_number(remaining_budget)}, "
            f"comprometido en pujas pendientes={format_number(pending_committed)}, "
            f"ya arriesgado hoy={format_number(already_risked)}, "
            f"candidatos evaluados={len(ranked)}, tope dinámico por jugador={format_number(player_cap)})."
            f"{skip_note}"
        )
        if risk_warnings:
            # Sin esto, un riesgo de plantilla detectado (ver
            # engine/squad_risk.py) que esta pasada no llega a traducirse en
            # ninguna puja/swap/reprecio (p.ej. mercado sin candidatos de esa
            # posición, o plantilla llena) quedaba SOLO en el log interno --
            # nunca llegaba a Telegram porque este `return` corta antes de
            # alcanzar el bloque `if risk_warnings` de más abajo, que solo se
            # ejecuta en el camino de "sí hubo alguna acción".
            message += "\n⚠️ Riesgo de plantilla detectado (sin candidato con el que actuar esta pasada):"
            message += "\n" + "\n".join(f"  - {w}" for w in risk_warnings)
        notify(message)
        return

    # `player_slug` no viene en `get_player_features()` (columnas de BD),
    # hace falta el candidato de mercado tal cual para pujar (place_bid lo
    # exige, ver clients/futmondo_client.py) -- se cruza por id, reusando
    # `market_by_id` ya calculado arriba (mismo fetch que para pending_committed).

    now = datetime.now(timezone.utc).isoformat()
    placed, failed = [], []
    skipped_roster_full_mid_run = []

    with get_connection() as conn:
        for i, decision in enumerate(decisions):
            market_player = market_by_id.get(str(decision["player_id"]))
            if market_player is None:
                # Ya no está en el mercado (mercado cambió entre evaluar y pujar) -- no se puede pujar.
                _persist_bid(conn, decision, "failed", now, error="el jugador ya no está en el mercado")
                failed.append((decision, "el jugador ya no está en el mercado"))
                continue
            try:
                client.place_bid(decision["player_id"], market_player["slug"], decision["amount"])
                _persist_bid(conn, decision, "placed", now)
                placed.append(decision)
            except (requests.RequestException, FutmondoOfferError) as e:
                _persist_bid(conn, decision, "failed", now, error=str(e))
                failed.append((decision, str(e)))
                if isinstance(e, FutmondoOfferError) and e.code == "api.market.max_number_players_in_roster":
                    # TOCTOU real (2026-08-23, ver docstring del módulo/TODO.md #18): el
                    # snapshot de roster de arriba decía que había hueco para todo este
                    # lote, pero Futmondo ya rechaza este candidato con el código exacto de
                    # "plantilla llena" -- el hueco real cambió entre el snapshot y este
                    # intento. El resto de candidatos NUEVOS de `decisions` comparte ese
                    # mismo hueco, que la propia API ya confirmó que no existe: abortar en
                    # vez de seguir generando más pujas condenadas a fallar igual.
                    skipped_roster_full_mid_run = decisions[i + 1 :]
                    break

    # --- Ejecutar las propuestas de swap calculadas arriba (TODO.md #13):
    # cancelar una puja floja para pujar por un candidato mejor bloqueado
    # solo por presupuesto/tope. Fase aparte, DESPUÉS del loop normal de
    # arriba -- nunca toca ni recalcula lo ya decidido/pujado ahí.
    #
    # `pending_after_decisions`/`risked_after_decisions` (calculados arriba
    # para el reajuste a la baja) sí incluyen lo pujado en el loop normal de
    # arriba (no solo el `pending_committed`/`already_risked` de antes de
    # esta pasada) -- si no, se contaría dos veces el margen que esas pujas
    # nuevas ya consumieron.
    swapped, swap_failed = [], []
    if swap_proposals:
        with get_connection() as conn:
            for proposal in swap_proposals:
                candidate, sacrifice = proposal["candidate"], proposal["sacrifice"]
                try:
                    client.cancel_bid(sacrifice["bid_id"])
                except (requests.RequestException, FutmondoOfferError) as e:
                    swap_failed.append((candidate, sacrifice, f"cancelación fallida: {e} -- nada más tocado"))
                    continue

                # Cancelación confirmada -- a partir de aquí ya no hay
                # vuelta atrás sobre la puja vieja, pase lo que pase abajo.
                update_bid_status(sacrifice["local_row_id"], "cancelled")
                pending_after_swap = max(0, pending_after_decisions - sacrifice["amount"])

                new_decision = decide_bid(
                    candidate,
                    remaining_budget,
                    risked_after_decisions,
                    min_score_threshold=min_score_threshold,
                    pending_committed=pending_after_swap,
                    player_cap=player_cap,
                )
                market_player = market_by_id.get(str(candidate["id"]))
                if new_decision is None or market_player is None:
                    reason = (
                        "ya no cupo dentro de los límites tras liberar presupuesto"
                        if new_decision is None
                        else "ya no está en el mercado"
                    )
                    swap_failed.append(
                        (candidate, sacrifice, f"puja cancelada pero el candidato {reason} -- swap a medias")
                    )
                    continue

                try:
                    client.place_bid(candidate["id"], market_player["slug"], new_decision["amount"])
                    _persist_bid(conn, new_decision, "placed", now)
                    swapped.append({"decision": new_decision, "sacrificed_player_id": sacrifice["player_id"]})
                except (requests.RequestException, FutmondoOfferError) as e:
                    _persist_bid(conn, new_decision, "failed", now, error=str(e))
                    swap_failed.append(
                        (
                            candidate,
                            sacrifice,
                            f"puja cancelada pero el place_bid posterior falló: {e} -- "
                            "queda auditado como cancelled+failed",
                        )
                    )

    # --- Ejecutar los rescates de déficit de plantilla calculados arriba
    # (ver docstring del módulo/engine.bidding_strategy.
    # find_deficit_rescue_swaps): mismo mecanismo cancelar+pujar que el
    # swap normal de arriba, pero el gatillo es un déficit REAL de
    # plantilla (posición sin cuerpos suficientes), no un candidato mejor
    # bloqueado por presupuesto/tope -- por eso `decide_bid()` de abajo NO
    # exige ningún margen de score frente al sacrificio, a diferencia del
    # swap normal. Por el mismo motivo tampoco se le pasa el
    # `min_score_threshold` dinámico ("presupuesto objetivo", ver arriba):
    # subir el listón de calidad porque sobra caja no tiene sentido cuando
    # lo urgente es tapar un agujero real de plantilla -- se queda con el
    # umbral fijo de config.BIDDING_MIN_SCORE_THRESHOLD, igual que antes de
    # este cambio. Fase APARTE, tras el swap normal -- `deficit_rescue_
    # proposals` ya excluyó cualquier puja que ese swap fuera a sacrificar
    # en esta misma pasada (ver `swap_sacrificed_bid_ids` de arriba).
    deficit_rescued, deficit_rescue_failed = [], []
    if deficit_rescue_proposals:
        with get_connection() as conn:
            for proposal in deficit_rescue_proposals:
                candidate, sacrifice = proposal["candidate"], proposal["sacrifice"]
                try:
                    client.cancel_bid(sacrifice["bid_id"])
                except (requests.RequestException, FutmondoOfferError) as e:
                    deficit_rescue_failed.append(
                        (candidate, sacrifice, f"cancelación fallida: {e} -- nada más tocado")
                    )
                    continue

                # Cancelación confirmada -- a partir de aquí ya no hay
                # vuelta atrás sobre la puja vieja, pase lo que pase abajo.
                update_bid_status(sacrifice["local_row_id"], "cancelled")
                pending_after_rescue = max(0, pending_after_decisions - sacrifice["amount"])

                new_decision = decide_bid(
                    candidate,
                    remaining_budget,
                    risked_after_decisions,
                    pending_committed=pending_after_rescue,
                    player_cap=player_cap,
                )
                market_player = market_by_id.get(str(candidate["id"]))
                if new_decision is None or market_player is None:
                    reason = (
                        "ya no cupo dentro de los límites tras liberar presupuesto"
                        if new_decision is None
                        else "ya no está en el mercado"
                    )
                    deficit_rescue_failed.append(
                        (candidate, sacrifice, f"puja cancelada pero el candidato {reason} -- rescate a medias")
                    )
                    continue

                try:
                    client.place_bid(candidate["id"], market_player["slug"], new_decision["amount"])
                    _persist_bid(conn, new_decision, "placed", now)
                    deficit_rescued.append(
                        {"decision": new_decision, "sacrificed_player_id": sacrifice["player_id"]}
                    )
                except (requests.RequestException, FutmondoOfferError) as e:
                    _persist_bid(conn, new_decision, "failed", now, error=str(e))
                    deficit_rescue_failed.append(
                        (
                            candidate,
                            sacrifice,
                            f"puja cancelada pero el place_bid posterior falló: {e} -- "
                            "queda auditado como cancelled+failed",
                        )
                    )

    # --- Ejecutar los reajustes a la baja calculados arriba (ver docstring
    # del módulo/engine.bidding_strategy.find_reprice_down_candidates):
    # cancelar+repujar más barato una puja abierta cuyo VM ha caído. Fase
    # APARTE, tras las dos fases de swap de arriba -- `sacrificed_bid_ids_
    # this_run` ya descarta cualquier solape con cualquiera de las dos.
    reprice_downs, reprice_down_failed = [], []
    if reprice_proposals:
        with get_connection() as conn:
            for proposal in reprice_proposals:
                try:
                    client.cancel_bid(proposal["bid_id"])
                except (requests.RequestException, FutmondoOfferError) as e:
                    reprice_down_failed.append((proposal, f"cancelación fallida: {e} -- nada más tocado"))
                    continue

                # Cancelación confirmada -- a partir de aquí ya no hay
                # vuelta atrás sobre la puja vieja, pase lo que pase abajo.
                update_bid_status(proposal["local_row_id"], "cancelled")

                market_player = market_by_id.get(str(proposal["player_id"]))
                new_decision = proposal["new_decision"]
                if market_player is None:
                    reprice_down_failed.append(
                        (proposal, "puja cancelada pero el jugador ya no está en el mercado -- reajuste a medias")
                    )
                    continue

                try:
                    client.place_bid(proposal["player_id"], market_player["slug"], new_decision["amount"])
                    _persist_bid(conn, new_decision, "placed", now)
                    reprice_downs.append(proposal)
                except (requests.RequestException, FutmondoOfferError) as e:
                    _persist_bid(conn, new_decision, "failed", now, error=str(e))
                    reprice_down_failed.append(
                        (
                            proposal,
                            f"puja cancelada pero el place_bid posterior falló: {e} -- "
                            "queda auditado como cancelled+failed",
                        )
                    )

    summary = [
        f"run_market: {len(placed)} puja(s) realizada(s). "
        f"(tope dinámico por jugador: {format_number(player_cap)})"
    ]
    for d in placed:
        tags = []
        if d.get("position_at_risk"):
            tags.append("prioridad: posición en riesgo")
        if d.get("would_upgrade_lineup"):
            tags.append("mejora el once titular")
        priority_tag = f" [{', '.join(tags)}]" if tags else ""
        summary.append(
            f"  - jugador {d['player_id']}: {format_number(d['amount'])} "
            f"(score={d['score']:.3f}){priority_tag}"
        )
    if failed:
        summary.append(f"{len(failed)} puja(s) fallida(s):")
        for d, err in failed:
            summary.append(f"  - jugador {d['player_id']}: {err}")
    if skipped_roster_full_mid_run:
        summary.append(
            f"🏟️ plantilla llena detectada A MITAD de esta pasada (tras un rechazo real de Futmondo con "
            f"api.market.max_number_players_in_roster) -- {len(skipped_roster_full_mid_run)} candidato(s) "
            "más de este lote sin intentar (ver TODO.md #18): "
            + ", ".join(str(d["player_id"]) for d in skipped_roster_full_mid_run)
        )
    if swapped:
        summary.append(f"🔄 {len(swapped)} swap(s) (cancelar+pujar mejor, TODO.md #13):")
        for s in swapped:
            d = s["decision"]
            summary.append(
                f"  - jugador {d['player_id']}: {format_number(d['amount'])} (score={d['score']:.3f}) "
                f"en vez de la puja cancelada sobre {s['sacrificed_player_id']}"
            )
    if swap_failed:
        summary.append(f"⚠️ {len(swap_failed)} swap(s) fallido(s) o a medias:")
        for candidate, sacrifice, err in swap_failed:
            summary.append(f"  - candidato {candidate['id']} / sacrificio {sacrifice['player_id']}: {err}")
    if deficit_rescued:
        summary.append(f"🚨 {len(deficit_rescued)} rescate(s) de déficit de plantilla (cancelar+pujar sin margen):")
        for s in deficit_rescued:
            d = s["decision"]
            summary.append(
                f"  - jugador {d['player_id']}: {format_number(d['amount'])} (score={d['score']:.3f}) "
                f"en vez de la puja cancelada sobre {s['sacrificed_player_id']}"
            )
    if deficit_rescue_failed:
        summary.append(f"⚠️ {len(deficit_rescue_failed)} rescate(s) de déficit fallido(s) o a medias:")
        for candidate, sacrifice, err in deficit_rescue_failed:
            summary.append(f"  - candidato {candidate['id']} / sacrificio {sacrifice['player_id']}: {err}")
    if reprice_downs:
        summary.append(f"📉 {len(reprice_downs)} puja(s) reajustada(s) a la baja (VM caído):")
        for p in reprice_downs:
            d = p["new_decision"]
            summary.append(
                f"  - jugador {p['player_id']}: {format_number(p['old_amount'])} -> {format_number(d['amount'])} "
                f"(score={d['score']:.3f})"
            )
    if reprice_down_failed:
        summary.append(f"⚠️ {len(reprice_down_failed)} reajuste(s) a la baja fallido(s) o a medias:")
        for proposal, err in reprice_down_failed:
            summary.append(f"  - jugador {proposal['player_id']}: {err}")
    if risk_warnings:
        summary.append("⚠️ Riesgo de plantilla detectado (priorizado al pujar):")
        summary.extend(f"  - {w}" for w in risk_warnings)
    if skipped_injured:
        summary.append(
            f"🚑 {len(skipped_injured)} candidato(s) descartado(s) del mercado por lesión confirmada: "
            + _format_id_list([p["id"] for p in skipped_injured])
        )
    if skipped_manager_listed:
        summary.append(
            f"👤 {len(skipped_manager_listed)} candidato(s) descartado(s) por estar en venta por otro manager "
            "(config.ENABLE_BIDS_ON_MANAGER_LISTINGS=false): "
            + _format_id_list([p["id"] for p in skipped_manager_listed])
        )
    if skipped_already_bid:
        summary.append(
            f"🔁 {len(skipped_already_bid)} candidato(s) en mercado con puja local ya pendiente (sin repujar, "
            "ver reajuste a la baja arriba si el VM cayó lo bastante): "
            + _format_id_list([p["id"] for p in skipped_already_bid])
        )
    if roster_full:
        summary.append(
            f"🏟️ plantilla completa ({len(roster_ids)}/{max_roster_size}) -- "
            f"{roster_full_discarded} candidato(s) nuevo(s) descartado(s), no se pujó por ninguno"
        )
    if max_roster_size_unknown:
        summary.append(
            f"⚠️ maxPlayersInRoster no informado por la API ni cacheado esta pasada (anomalía, ver TODO.md #18/#19) -- "
            f"{roster_full_discarded} candidato(s) nuevo(s) descartado(s) por precaución, no se pujó por ninguno"
        )
    elif max_roster_size_from_cache:
        summary.append(
            f"ℹ️ maxPlayersInRoster no informado por la API esta pasada -- usado el último valor confirmado en "
            f"caché ({max_roster_size}, ver TODO.md #19)"
        )
    if pending_committed:
        summary.append(
            f"(comprometido en pujas pendientes sin resolver: {format_number(pending_committed)})"
        )

    notify("\n".join(summary))


if __name__ == "__main__":
    # Chequeo duplicado a propósito: el de dentro de run() protege a quien
    # llame a run() directamente (tests incluidos); este de aquí evita
    # además que se entre en track_job_run() -- si no, con ENABLE_BOT=false
    # igualmente se registraría una fila en `job_runs`, ese INSERT por sí
    # solo ensuciaría db/futmondo.db, y el step "Commit BD actualizada" del
    # workflow comitearía/pushearía igual aunque el bot no haga nada real.
    if not config.ENABLE_BOT:
        print("run_market: ENABLE_BOT=false, no se ejecuta.")
    else:
        with track_job_run("run_market"):
            run()
