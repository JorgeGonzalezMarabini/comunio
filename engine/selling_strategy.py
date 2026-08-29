"""
Estrategia de ventas: identifica jugadores de la plantilla que conviene
poner en venta ahora mismo.

En Futmondo, igual que en Comunio (ver README, "Economía"), vender
jugadores es la vía principal para generar dinero: se compra
barato/infravalorado y se vende cuando el valor sube.

Resuelve TODO.md #4 (`buyPrice` no distingue "comprado por el bot" de
"plantilla inicial"): en Comunio, `purchaseInfo == null` distinguía sin
ambigüedad ambos casos. En Futmondo no se encontró un campo equivalente
confiable — `buyPrice` aparece tanto en plantilla inicial (a veces 0, a
veces no) como en compras reales, sin poder distinguir el origen por ese
campo (ver clients/futmondo_client.py). En vez de eso, `decide_sales()`
recibe `bought_by_bot` (ver `db.models.get_won_bid_prices()`): el registro
LOCAL de pujas que el propio bot colocó y ganó (tabla `bids`,
reconciliada en `jobs/sync_data.py`), independiente de `buyPrice`. Un
jugador solo es candidato a venta si aparece ahí, y el precio de
referencia usado es el importe realmente pagado en esa puja (más fiable
que `buyPrice`, que ni siquiera se necesita ya para esta decisión) — misma
semántica que `purchaseInfo != null` en Comunio, alcanzada sin depender de
un campo de Futmondo sin confirmar.

Riesgo de plantilla (ver engine/squad_risk.py): vender es una acción tan
capaz de dejarte sin cobertura en una posición como que te "clausulen" un
jugador — la diferencia es que esta la causa el propio bot, así que es
100% evitable. Por eso decide_sales() NUNCA vende un jugador si eso deja
esa posición sin margen de suplentes sanos (bench <= 0 tras la venta),
por muy rentable que sea la operación — a diferencia de la prioridad de
puja (un empujón blando), esto es un bloqueo duro: no hay ninguna
plusvalía que compense quedarte con una posición vacía.

Corte de pérdidas, un único umbral para TODOS los estados (a petición del
usuario, 2026-08-22 -- tercera vuelta de este mismo cambio, ver historial
de commits: primero solo lesión confirmada, luego genérico con un umbral
más bajo exclusivo para lesión confirmada, ahora unificado del todo):
hasta ahora un jugador solo era candidato a venta si superaba
`min_profit_pct` — sin ninguna otra vía, esperando indefinidamente a que
"recupere" plusvalía por muy mal que fuera la operación. Eso es la
falacia del coste hundido en estado puro: aferrarse a cuánto se pagó en
el pasado en vez de decidir por lo que el jugador es AHORA.

Por eso, además de la vía normal (`min_profit_pct`), CUALQUIER jugador
(sano, en duda o lesionado) que haya perdido más de `max_loss_pct`
(config.SELLING_MAX_LOSS_PCT) se pone en venta igualmente, aunque sea con
pérdidas — cortar una pérdida grande no debería depender de por qué bajó,
del mismo modo que empezar a subir de valor ya bastaba para vender (vía
normal) sin mirar el estado del jugador. Un único umbral para todos: antes
había un umbral genérico (0.20) para sano/duda y otro más agresivo (0.10)
solo para lesión confirmada -- razonado porque un lesionado tiende a
seguir perdiendo valor cuanto más tiempo pasa sin jugar. Esa distinción ya
no hace falta: la lesión confirmada tiene su propia vía incondicional más
abajo (se vende siempre, pase lo que pase con su pérdida), así que el
umbral de corte de pérdidas pasa a ser el mismo (el antes exclusivo de
lesión, más agresivo) para cualquier jugador, sano o en duda incluidos —
si un jugador empieza a caer de valor, no hay motivo para esperar más a
cortarlo que para el que empieza a subir.

Concentración de capital en lesión CONFIRMADA (a petición del usuario,
2026-08-22): las dos vías de arriba solo miran RENTABILIDAD (plusvalía o
pérdida de LA OPERACIÓN). Pero un jugador lesionado es un problema
también si simplemente representa una parte demasiado grande del capital
del equipo (plantilla + presupuesto), aunque no esté ni cerca de
`max_loss_pct` — mientras esté lesionado no se puede usar, así que
tener mucho capital inmovilizado ahí es un coste de oportunidad real
(ese dinero no puede fichar a nadie más). Por eso, un jugador con lesión
CONFIRMADA cuyo valor supera `injury_concentration_max_pct`
(config.SELLING_INJURY_CONCENTRATION_MAX_PCT) del capital total
(suma del valor de TODA la plantilla + `budget`) también se pone en
venta, sin mirar plusvalía/pérdida en absoluto. Como con las dos vías
anteriores, "doubt" queda fuera — todavía puede llegar a jugar.

Venta SIEMPRE para lesión CONFIRMADA (a petición del usuario, 2026-08-22,
generalización de la vía anterior -- caso real detectado: Mendy, lesión
confirmada, sin pérdida ni concentración de capital suficiente para
activar ninguna de las dos vías de arriba, se quedaba sin vender
indefinidamente): un jugador con lesión CONFIRMADA es sencillamente
INSERVIBLE mientras dure -- no puede jugar ni puntuar -- y ocupa una plaza
de plantilla que podría usarse para fichar a otro que sí sume. Las dos
vías anteriores (corte de pérdidas agravado, concentración de capital) ya
apuntaban a este mismo problema pero solo lo resolvían en los casos donde
además cruzaba un umbral de pérdida o de tamaño; esta vía es la
generalización sin condiciones: se vende siempre, sin mirar
rentabilidad/pérdida/concentración en absoluto. Como con las dos vías de
lesión anteriores, "doubt" queda fuera — todavía puede llegar a jugar, así
que su valor actual sigue siendo información útil y no se le fuerza nada.

Oportunidad de mercado / plaza escasa (a petición del usuario, 2026-08-22,
tras el límite de plantilla de `jobs/run_market.py`): las tres vías de
arriba solo miran RENTABILIDAD de la operación o concentración de
capital — ninguna mira si el jugador en sí es bueno. Con plazas cada vez
más escasas, un suplente mediocre que ni gana ni pierde puede ocupar un
hueco valioso indefinidamente, aunque el mercado ofrezca constantemente
sustitutos mejores para su posición. Por eso, si `own_squad_features`/
`market_candidates` (features crudas, mismo formato que
`db.models.get_player_features()`) vienen informados, se calcula el score
de alineación (`config.LINEUP_EVALUATOR_WEIGHTS`, calidad pura, sin
precio — igual que `engine.squad_risk.weakest_starter_scores`, mismo
principio aplicado en sentido contrario) de la plantilla propia y del
mercado EN LA MISMA llamada a `evaluate_players()` (para que sean
comparables, ver `engine.evaluator.normalize_pool`). Un jugador (sano, no
"doubt"/lesionado — su calidad actual no es representativa mientras no
pueda jugar) se pone en venta igualmente si el MEJOR candidato disponible
en el mercado en su misma posición supera su score de alineación en
`upgrade_available_min_margin` (config.SELLING_UPGRADE_AVAILABLE_MIN_MARGIN),
aunque profit_pct no llegue a ningún otro umbral. Ambos parámetros son
opcionales (`None`/vacíos por defecto): si el llamador no los pasa, esta
vía queda desactivada sin más, backward-compatible con el resto de usos
de `decide_sales()`.

Refinamientos de "oportunidad de mercado" (a petición del usuario,
2026-08-23, caso real: el mismo día se vendieron a la vez Raba+Brugué por
DEL y Camavinga+Dieng por MED, cada pareja justificada por un ÚNICO mejor
candidato de mercado en su posición -- de ese candidato solo se puede
fichar uno, así que vender a los dos no tenía sentido). Los cuatro se
aplican SOLO a un candidato que cualifica ÚNICAMENTE por esta vía (si
también cualifica por plusvalía/pérdida/lesión, esas vías mandan y estos
filtros no aplican -- ver `reason` más abajo):

  a) Como mucho `max_upgrade_sales_per_position`
     (config.SELLING_UPGRADE_MAX_SALES_PER_POSITION, por defecto 1) venta
     por posición y ejecución vía esta vía -- si varios candidatos propios
     de la misma posición cualifican, se prioriza el de mayor margen de
     score (peor suplente relativo); el resto se descarta esta vez, igual
     que ya ocurre con el margen de banquillo.

  b) Eficiencia marginal de precio
     (`upgrade_min_score_per_extra_million`, config.
     SELLING_UPGRADE_MIN_SCORE_PER_EXTRA_MILLION): si el candidato
     objetivo es más caro que el propio jugador, el margen de score por
     MILLÓN EXTRA de precio debe superar este umbral -- evita comparar
     directamente la calidad de un jugador de 4M con uno de 50M solo
     porque el margen de score bruto ya superaba
     `upgrade_available_min_margin` (a petición del usuario: "no podemos
     comparar la calidad de un jugador de 4 millones con uno de 50"). Si
     el candidato es igual o más barato, no se exige nada extra aquí.

  c) Asequibilidad, con presupuesto COMPARTIDO entre posiciones: los
     candidatos que sobreviven (a) y (b) se procesan en orden de mayor
     margen de score primero (a petición del usuario, para que la
     oportunidad más clara se quede el presupuesto si varias compiten a
     la vez), acumulando cuánto se iría "gastando" (precio del candidato
     objetivo) sobre `budget` + lo que liberaría cada venta ya aprobada en
     esta misma pasada. Si no alcanza para el precio del candidato
     objetivo, esa posición se descarta esta vez (no compite más por el
     presupuesto restante) -- evita autorizar dos ventas cuyos objetivos
     juntos no se podrían pagar en la misma jornada, aunque cada una por
     separado sí pareciera asequible.

  d) Ventana de tiempo del listado objetivo
     (`assumed_sale_resolution_hours`, config.
     SELLING_ASSUMED_SALE_RESOLUTION_HOURS, y `market_listing_expirations`
     con el `expirationDate` real del candidato, de
     `FutmondoClient.get_market()`): si al candidato objetivo le queda
     menos tiempo en el mercado del que se asume que tardará en resolverse
     nuestra propia venta, no tiene sentido vender con ese objetivo
     concreto -- para cuando tengamos el dinero, el candidato ya no estará
     listado. Sin `expirationDate` del candidato (dato no disponible o
     candidato sin listado real detrás), este filtro queda desactivado
     para ese candidato, no bloquea por defecto.

Los cuatro son opcionales/con default de config -- si el llamador no pasa
`market_listing_expirations`, (d) simplemente no bloquea a nadie; (a),
(b) y (c) siempre están activos (tienen default de config, no se pueden
desactivar por completo, a diferencia de la vía entera que sí depende de
`own_squad_features`/`market_candidates`).

Bloqueo de alineado en fin de semana (config.
ENABLE_SELLING_WEEKEND_LINEUP_GUARD, activado por defecto, a petición del
usuario 2026-08-23): si `own_lineup_player_ids` viene informado (ids de
`FutmondoClient.get_lineup()["answer"]["players"]`, los TITULARES
guardados, no el banquillo) y hoy es sábado/domingo, ningún jugador de esa
lista se pone en venta esta pasada, sea cual sea el motivo (ninguna de las
cinco vías queda exenta) -- no está confirmado si Futmondo penaliza vender
a un titular con la jornada en juego, así que es puramente preventivo.
Sin `own_lineup_player_ids`, este bloqueo queda desactivado (no se puede
aplicar sin saber quién está alineado).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config
from clients.futmondo_client import FUTMONDO_POSITION_MAP, is_confirmed_injured_status, is_injury_status
from engine.evaluator import evaluate_players
from engine.squad_risk import assess_squad_depth


def score_market_upgrade_candidates(
    own_squad_features: list[dict], market_candidates: list[dict]
) -> tuple[dict[str, float], dict[str, dict]]:
    """
    Score de alineación (`config.LINEUP_EVALUATOR_WEIGHTS`, calidad pura,
    sin precio) de la plantilla propia y del mercado abierto, en la MISMA
    llamada a `evaluate_players()` (`engine.evaluator.normalize_pool`
    normaliza dentro del pool que se le pasa, así que hace falta la misma
    llamada para que sean comparables entre sí) -- extraído de
    `decide_sales()` ("oportunidad de mercado / plaza escasa", ver
    docstring del módulo) para poder reutilizarlo también al ACEPTAR una
    oferta de venta (`jobs.run_sales._resolve_swap_target()`, ver
    config.SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT): si el candidato que
    motivó un swap ya no está disponible, hace falta repetir esta misma
    comparación para buscar un equivalente.

    Devuelve (`lineup_score_by_id`, `best_market_candidate_by_position`):
      - `lineup_score_by_id`: {id (str): score} de TODOS los jugadores
        pasados (propios y de mercado).
      - `best_market_candidate_by_position`: {position: {"id", "score",
        "price"}} -- el candidato de MERCADO (nunca uno propio) con mejor
        score por posición; hace falta su precio real y su id para los
        refinamientos de la vía 5 de `decide_sales()` (eficiencia de
        precio, asequibilidad, tiempo del listado) y para el retargeteo de
        swaps.

    Ambos vacíos si falta `own_squad_features` o `market_candidates` -- la
    vía/comparación queda desactivada sin más para quien llame.
    """
    lineup_score_by_id: dict[str, float] = {}
    best_market_candidate_by_position: dict[str, dict] = {}
    if not own_squad_features or not market_candidates:
        return lineup_score_by_id, best_market_candidate_by_position

    lineup_scored = evaluate_players(
        list(own_squad_features) + list(market_candidates), weights=config.LINEUP_EVALUATOR_WEIGHTS
    )
    own_ids = {str(p["id"]) for p in own_squad_features}
    for p in lineup_scored:
        lineup_score_by_id[str(p["id"])] = p["score"]
        if str(p["id"]) in own_ids:
            continue  # es plantilla propia, no un candidato de mercado -- no cuenta como "disponible"
        position_key = p.get("position")
        if position_key is None:
            continue
        existing = best_market_candidate_by_position.get(position_key)
        if existing is None or p["score"] > existing["score"]:
            best_market_candidate_by_position[position_key] = {
                "id": str(p["id"]),
                "score": p["score"],
                "price": p.get("price") or 0,
            }
    return lineup_score_by_id, best_market_candidate_by_position


def decide_sales(
    squad: list[dict],
    min_profit_pct: float = None,
    formation: str = None,
    bought_by_bot: dict[str, int] = None,
    max_loss_pct: float = None,
    budget: int = 0,
    injury_concentration_max_pct: float = None,
    own_squad_features: list[dict] = None,
    market_candidates: list[dict] = None,
    upgrade_available_min_margin: float = None,
    max_upgrade_sales_per_position: int = None,
    upgrade_min_score_per_extra_million: float = None,
    market_listing_expirations: dict[str, str] = None,
    assumed_sale_resolution_hours: float = None,
    own_lineup_player_ids=None,
    enable_weekend_lineup_guard: bool = None,
    now: datetime = None,
) -> list[dict]:
    """
    `squad`: items reales de FutmondoClient.get_roster()["answer"] (necesita
    "id", "name", "role", "status", "value") tal cual, sin normalizar
    antes; aquí dentro se traduce la posición (FUTMONDO_POSITION_MAP) para
    poder cruzarla con engine.squad_risk. Un jugador con `market: true` (ya
    puesto en venta en una pasada anterior -- ver docstring de
    FutmondoClient.get_roster) nunca es candidato, sin mirar las cinco vías
    de abajo: poner en venta no es instantáneo, así que sigue en la
    plantilla varias pasadas, y reintentar `list_for_sale()` sobre un
    listado que ya existe falla con `api.error.not_found` (confirmado en
    vivo 2026-08-22, jugador Dieng).

    `bought_by_bot`: {player_id: precio pagado} — normalmente
    `db.models.get_won_bid_prices()`. Solo los jugadores presentes aquí son
    candidatos a venta (equivalente a `purchaseInfo != null` en Comunio,
    ver docstring del módulo); el precio de referencia para la plusvalía es
    el importe de ese dict, no `buyPrice` de Futmondo. Si se omite (o llega
    vacío/None), no hay ningún candidato — nunca se recurre a `buyPrice`
    como fallback silencioso, para no reintroducir la ambigüedad original.

    `max_loss_pct`: por defecto config.SELLING_MAX_LOSS_PCT — umbral de
    PÉRDIDA (positivo, ej. 0.10 = -10%) a partir del cual CUALQUIER jugador
    (sano, en duda o lesionado, el mismo umbral para todos -- ver docstring
    del módulo) se pone en venta aunque no llegue a `min_profit_pct`,
    incluso con pérdidas (corte de pérdidas para no caer en la falacia del
    coste hundido).

    `budget`: presupuesto disponible ahora mismo (normalmente
    `FutmondoClient.get_information()["answer"]["budget"]`) — se suma al
    valor de mercado de toda la plantilla para calcular el capital TOTAL
    del equipo, usado por `injury_concentration_max_pct` (ver abajo). Por
    defecto 0 (solo cuenta el valor de la plantilla) si el llamador no lo
    tiene a mano.

    `injury_concentration_max_pct`: por defecto
    config.SELLING_INJURY_CONCENTRATION_MAX_PCT — % máximo del capital
    total (plantilla + `budget`) que puede estar inmovilizado en UN jugador
    con lesión CONFIRMADA antes de ponerlo en venta, sin mirar
    rentabilidad en absoluto (ver docstring del módulo: no es un problema
    de plusvalía, es de concentración de capital en un jugador que no se
    puede usar). No aplica a "doubt".

    `own_squad_features`/`market_candidates`: features CRUDAS (mismo
    formato que devuelve `db.models.get_player_features()` — "price",
    "points", "xg", "position" POR/DEF/MED/DEL ya normalizada, etc., NO
    los items de `squad`/`get_roster()`) de la plantilla propia y del
    mercado abierto ahora mismo, respectivamente. Habilitan la 4ª vía de
    venta (ver docstring del módulo, "Oportunidad de mercado / plaza
    escasa") -- si CUALQUIERA de los dos viene vacío/None (el valor por
    defecto), esa vía queda desactivada sin más, ningún candidato se ve
    afectado por ella.

    `upgrade_available_min_margin`: por defecto
    config.SELLING_UPGRADE_AVAILABLE_MIN_MARGIN — margen mínimo (en score
    de alineación, `config.LINEUP_EVALUATOR_WEIGHTS`) que el MEJOR
    candidato de mercado en la misma posición debe superar al score de
    alineación propio del jugador antes de venderlo solo por esto.

    `max_upgrade_sales_per_position`, `upgrade_min_score_per_extra_million`,
    `market_listing_expirations`, `assumed_sale_resolution_hours`: los
    cuatro refinamientos de la vía 5 (ver docstring del módulo,
    "Refinamientos de oportunidad de mercado") — límite por posición,
    eficiencia marginal de precio, ventana de tiempo del listado objetivo
    y asequibilidad con presupuesto compartido entre posiciones. Todos por
    defecto de config salvo `market_listing_expirations` ({} si se omite,
    desactiva solo el filtro de tiempo).

    `own_lineup_player_ids`: ids (cualquier tipo, se normalizan a string)
    de los TITULARES guardados ahora mismo (`FutmondoClient.get_lineup()
    ["answer"]["players"]`, no el banquillo). Si se omite, el bloqueo de
    fin de semana (ver abajo) queda desactivado sin más.

    `enable_weekend_lineup_guard`: por defecto
    config.ENABLE_SELLING_WEEKEND_LINEUP_GUARD — si está activo Y hoy es
    sábado/domingo (`now`), ningún jugador presente en
    `own_lineup_player_ids` se pone en venta esta pasada, sea cual sea el
    motivo (ver docstring del módulo).

    `now`: por defecto `datetime.now(timezone.utc)` — inyectable para
    tests deterministas (afecta al bloqueo de fin de semana y a la
    comparación de tiempo del listado objetivo).

    Devuelve una decisión por cada jugador que cumpla CUALQUIERA de estas
    cinco condiciones (Y cuya posición siga teniendo margen de suplentes
    sanos después de la venta, salvo que ya esté lesionado/en duda —
    ver más abajo):
      1. Su revalorización (`value` vs. el precio pagado en
         `bought_by_bot`) supera `min_profit_pct` (por defecto
         config.SELLING_MIN_PROFIT_PCT) — vía normal, para todos.
      2. Ha perdido más de `max_loss_pct` — corte de pérdidas, mismo
         umbral para cualquier estado (sano, en duda o lesionado).
      3. Tiene lesión CONFIRMADA y su valor supera
         `injury_concentration_max_pct` del capital total — concentración
         de capital, solo para lesión confirmada (no "doubt"), sin mirar
         plusvalía/pérdida.
      4. Tiene lesión CONFIRMADA (no "doubt") — se vende SIEMPRE, sin
         mirar rentabilidad/pérdida/concentración en absoluto: es
         inservible mientras dure y ocupa una plaza que podría liberarse
         para fichar a otro (ver docstring del módulo). Esta vía por sí
         sola ya cubre a cualquier lesionado confirmado; las vías 2 y 3
         siguen aquí porque dan un motivo más específico en el `reason`
         cuando también aplican.
      5. No está lesionado/en duda, y el mejor candidato de mercado en su
         misma posición supera su score de alineación en
         `upgrade_available_min_margin` — oportunidad de mercado, solo si
         `own_squad_features`/`market_candidates` vienen informados. Si
         esta es la ÚNICA vía que aplica, además tiene que sobrevivir los
         cuatro refinamientos de arriba (tope por posición, eficiencia de
         precio, ventana de tiempo, asequibilidad compartida).

    Ningún jugador presente en `own_lineup_player_ids` se vende en fin de
    semana, sea cual sea la vía (ver `enable_weekend_lineup_guard`) — esto
    se comprueba ANTES de evaluar las cinco vías de arriba.

    Cada decisión:
        {"player_id", "asking_price", "purchase_price", "profit",
         "profit_pct", "reason"}
    listo para persistir en la tabla `sales` (auditoría) y pasar a
    FutmondoClient.list_for_sale().

    Si hay varios candidatos rentables en la misma posición pero no hay
    margen para vender a todos, se prioriza al de mayor plusvalía (orden
    descendente) — el resto se descarta esta vez, no se difiere ni se
    fuerza.

    El precio de venta pedido (`asking_price`) es el valor de mercado
    actual (`value`) — pedir el valor de mercado tal cual es la opción más
    simple y segura (en Comunio se vio que pedir más no ayudaba porque el
    mercado ajustaba solo; no se ha confirmado si Futmondo hace algo
    parecido, pero no hay motivo para pedir un precio distinto al VM).
    """
    min_profit_pct = config.SELLING_MIN_PROFIT_PCT if min_profit_pct is None else min_profit_pct
    max_loss_pct = config.SELLING_MAX_LOSS_PCT if max_loss_pct is None else max_loss_pct
    injury_concentration_max_pct = (
        config.SELLING_INJURY_CONCENTRATION_MAX_PCT
        if injury_concentration_max_pct is None
        else injury_concentration_max_pct
    )
    upgrade_available_min_margin = (
        config.SELLING_UPGRADE_AVAILABLE_MIN_MARGIN
        if upgrade_available_min_margin is None
        else upgrade_available_min_margin
    )
    max_upgrade_sales_per_position = (
        config.SELLING_UPGRADE_MAX_SALES_PER_POSITION
        if max_upgrade_sales_per_position is None
        else max_upgrade_sales_per_position
    )
    upgrade_min_score_per_extra_million = (
        config.SELLING_UPGRADE_MIN_SCORE_PER_EXTRA_MILLION
        if upgrade_min_score_per_extra_million is None
        else upgrade_min_score_per_extra_million
    )
    assumed_sale_resolution_hours = (
        config.SELLING_ASSUMED_SALE_RESOLUTION_HOURS
        if assumed_sale_resolution_hours is None
        else assumed_sale_resolution_hours
    )
    enable_weekend_lineup_guard = (
        config.ENABLE_SELLING_WEEKEND_LINEUP_GUARD
        if enable_weekend_lineup_guard is None
        else enable_weekend_lineup_guard
    )
    now = now or datetime.now(timezone.utc)
    market_listing_expirations = market_listing_expirations or {}
    own_lineup_ids = {str(i) for i in (own_lineup_player_ids or [])}
    # Aproximación por día de la semana (sábado=5, domingo=6) -- no
    # distingue la hora exacta de los partidos, ver docstring del módulo.
    is_weekend_now = now.weekday() >= 5
    bought_by_bot = bought_by_bot or {}

    # Capital total del equipo (plantilla + presupuesto disponible) --
    # denominador de injury_concentration_max_pct, ver docstring. Se
    # calcula sobre TODA la plantilla (no solo los candidatos), es el
    # patrimonio real del equipo en este momento.
    total_capital = sum(p.get("value", 0) or 0 for p in squad) + max(0, budget)

    # Oportunidad de mercado (ver docstring): score de alineación de
    # plantilla propia y mercado, y mejor candidato por posición -- ver
    # score_market_upgrade_candidates(). Si falta cualquiera de los dos,
    # esta vía queda desactivada (diccionarios vacíos -> ningún candidato
    # la activa más abajo).
    lineup_score_by_id, best_market_candidate_by_position = score_market_upgrade_candidates(
        own_squad_features, market_candidates
    )

    candidates = []
    for player in squad:
        purchase_price = bought_by_bot.get(str(player["id"])) or 0
        current_price = player.get("value", 0)
        if purchase_price <= 0 or current_price <= 0:
            continue  # no comprado por el bot (o sin precio de referencia): no es candidato

        if player.get("market"):
            # Ya puesto en venta en una pasada anterior (`market: true` en
            # el item de roster -- ver docstring de FutmondoClient.get_roster,
            # "solo en roster: buyPrice, market"). Poner en venta no es
            # instantáneo (ver docstring de jobs/run_sales.py), así que
            # sigue en la plantilla varias pasadas hasta que alguien lo
            # compre. Sin este corte, cada pasada lo volvía a decidir como
            # candidato y `FutmondoClient.list_for_sale()` fallaba con
            # `api.error.not_found` al reintentar un listado que ya existe
            # (confirmado en vivo 2026-08-22, jugador Dieng).
            continue

        if enable_weekend_lineup_guard and is_weekend_now and str(player["id"]) in own_lineup_ids:
            # Bloqueo defensivo (ver docstring del módulo): titular
            # guardado y hoy es fin de semana -- no se vende, sea cual sea
            # el motivo, por si acaso el juego penaliza o complica vender
            # a alguien alineado con la jornada en juego (sin confirmar).
            continue

        profit = current_price - purchase_price
        profit_pct = profit / purchase_price

        # Corte de pérdidas: mismo umbral (max_loss_pct) para CUALQUIER
        # estado -- sano, en duda o lesión confirmada (ver docstring del
        # módulo: unificado, la lesión confirmada ya tiene su propia vía
        # incondicional más abajo). Se vende aunque no llegue a
        # min_profit_pct, incluso con pérdidas.
        is_confirmed_injured = is_confirmed_injured_status(player.get("status"))
        is_injured_or_doubtful = is_injury_status(player.get("status"))
        loss_threshold = max_loss_pct
        cutting_losses = profit_pct <= -loss_threshold

        # Concentración de capital: solo lesión CONFIRMADA, sin mirar
        # rentabilidad -- demasiado capital inmovilizado en un jugador que
        # no se puede usar es un problema en sí mismo (ver docstring).
        concentration_pct = (current_price / total_capital) if total_capital > 0 else 0.0
        overconcentrated = is_confirmed_injured and concentration_pct >= injury_concentration_max_pct

        # Venta SIEMPRE para lesión CONFIRMADA (ver docstring del módulo):
        # inservible mientras dure, ocupa una plaza -- se vende sin mirar
        # rentabilidad/pérdida/concentración. "doubt" queda fuera, igual
        # que en las dos vías de lesión de arriba.
        force_sell_confirmed_injury = is_confirmed_injured

        # Oportunidad de mercado: nunca para lesionado/en duda (ver
        # docstring del módulo, misma razón que la concentración de
        # capital de arriba solo mira lesión confirmada -- la calidad
        # actual de alguien que no puede jugar no es representativa).
        position_key = FUTMONDO_POSITION_MAP.get(player.get("role"), player.get("role"))
        own_lineup_score = lineup_score_by_id.get(str(player["id"]))
        best_market_candidate = best_market_candidate_by_position.get(position_key)
        best_available_lineup_score = best_market_candidate["score"] if best_market_candidate else None
        market_upgrade_available = (
            not is_injured_or_doubtful
            and own_lineup_score is not None
            and best_available_lineup_score is not None
            and (best_available_lineup_score - own_lineup_score) >= upgrade_available_min_margin
        )

        if (
            profit_pct < min_profit_pct
            and not cutting_losses
            and not overconcentrated
            and not force_sell_confirmed_injury
            and not market_upgrade_available
        ):
            continue

        # True si "oportunidad de mercado" es la ÚNICA vía que aplica --
        # solo estos candidatos pasan por los cuatro refinamientos
        # adicionales de la vía 5 (ver docstring del módulo); el resto
        # (rentabilidad/pérdida/concentración/lesión) no mira precio del
        # objetivo en absoluto, esas vías se venden igual.
        only_market_reason = (
            market_upgrade_available
            and profit_pct < min_profit_pct
            and not cutting_losses
            and not overconcentrated
            and not force_sell_confirmed_injury
        )

        candidates.append(
            (
                player,
                purchase_price,
                profit,
                profit_pct,
                cutting_losses,
                is_confirmed_injured,
                loss_threshold,
                overconcentrated,
                concentration_pct,
                is_injured_or_doubtful,
                market_upgrade_available,
                own_lineup_score,
                best_available_lineup_score,
                force_sell_confirmed_injury,
                only_market_reason,
                best_market_candidate,
            )
        )

    # Más rentables primero: si el margen de plantilla en una posición no
    # alcanza para vender a todos los candidatos de esa posición, se
    # prioriza el de mayor plusvalía. Los cortes de pérdidas (profit_pct
    # muy negativo) quedan naturalmente al final de este orden, pero no
    # compiten por margen de banquillo de todos modos (ver más abajo: un
    # lesionado nunca contó como "disponible", así que no descuenta bench).
    candidates.sort(key=lambda c: c[3], reverse=True)

    # Refinamientos de la vía 5 (ver docstring del módulo) -- SOLO para
    # candidatos donde "oportunidad de mercado" es la ÚNICA vía que
    # aplica. Se resuelven en un pre-paso propio, independiente del orden
    # por plusvalía de arriba (aquí manda el margen de score, a petición
    # del usuario): a) como mucho `max_upgrade_sales_per_position` por
    # posición (el de mayor margen se queda la plaza), luego b)+c)+d) en
    # ese mismo orden de mayor margen, para que la oportunidad más clara
    # se quede el presupuesto compartido si varias compiten a la vez.
    market_only_entries = [
        (c[12] - c[11], FUTMONDO_POSITION_MAP.get(c[0].get("role"), c[0].get("role")), c[0], c[15])
        for c in candidates
        if c[14]  # only_market_reason
    ]
    market_only_entries.sort(key=lambda t: t[0], reverse=True)

    count_by_position = {}
    shortlisted = []
    for margin, position_key, player, best_candidate in market_only_entries:
        if count_by_position.get(position_key, 0) >= max_upgrade_sales_per_position:
            continue  # ya se autorizó el máximo para esta posición esta vez (a)
        count_by_position[position_key] = count_by_position.get(position_key, 0) + 1
        shortlisted.append((margin, position_key, player, best_candidate))

    approved_market_only_ids = set()
    available_budget = max(0, budget)
    for margin, position_key, player, best_candidate in shortlisted:
        best_candidate = best_candidate or {}
        target_price = best_candidate.get("price") or 0
        own_price = player.get("value", 0)

        extra_cost = target_price - own_price
        if extra_cost > 0:
            efficiency = margin / (extra_cost / 1_000_000)
            if efficiency < upgrade_min_score_per_extra_million:
                continue  # (b) el margen de score no compensa lo mucho más caro que es el objetivo

        target_expiration = parse_iso_datetime(market_listing_expirations.get(best_candidate.get("id")))
        if target_expiration is not None:
            time_left = target_expiration - now
            if time_left < timedelta(hours=assumed_sale_resolution_hours):
                continue  # (d) el listado objetivo cerraría antes de que nuestra venta se resuelva

        prospective_budget = available_budget + own_price
        if target_price > prospective_budget:
            continue  # (c) no llegaríamos a cubrir el precio del candidato objetivo con lo disponible

        available_budget = prospective_budget - target_price  # (c) se "reserva" para el resto de esta pasada
        approved_market_only_ids.add(str(player["id"]))

    # Margen de suplentes sanos por posición ANTES de vender nada (ver
    # engine.squad_risk.assess_squad_depth) — se va descontando según se
    # aceptan ventas de esta misma pasada, para no autorizar de golpe dos
    # ventas que juntas sí dejarían la posición sin cubrir.
    normalized_squad = [{**p, "position": FUTMONDO_POSITION_MAP.get(p.get("role"), p.get("role"))} for p in squad]
    depth = assess_squad_depth(normalized_squad, formation=formation)
    bench_remaining = {position: info["bench"] for position, info in depth.items()}

    decisions = []
    for (
        player,
        purchase_price,
        profit,
        profit_pct,
        cutting_losses,
        is_confirmed_injured,
        loss_threshold,
        overconcentrated,
        concentration_pct,
        is_injured_or_doubtful,
        market_upgrade_available,
        own_lineup_score,
        best_available_lineup_score,
        force_sell_confirmed_injury,
        only_market_reason,
        best_market_candidate,
    ) in candidates:
        if only_market_reason and str(player["id"]) not in approved_market_only_ids:
            continue  # no superó los refinamientos adicionales de la vía 5 (a/b/c/d, ver arriba)

        position = FUTMONDO_POSITION_MAP.get(player.get("role"), player.get("role"))

        # Un jugador ya lesionado/sancionado no contaba como "disponible"
        # en assess_squad_depth, así que venderlo no empeora la cobertura
        # real de la posición -- no hace falta descontar margen por él.
        if not is_injured_or_doubtful:
            if bench_remaining.get(position, 0) <= 0:
                continue  # vender aquí dejaría la posición sin cubrir -- no se vende, por rentable que sea
            bench_remaining[position] -= 1

        # Prioridad del motivo mostrado (no son excluyentes entre sí, un
        # candidato puede cumplir varios a la vez): rentabilidad normal
        # primero (el caso más informativo/común), luego corte de
        # pérdidas, luego concentración de capital, luego oportunidad de
        # mercado (las dos últimas ni siquiera miran profit_pct).
        if profit_pct >= min_profit_pct:
            reason = (
                f"pagado por el bot {purchase_price}, ahora {player.get('value', 0)} "
                f"({profit_pct:+.1%}) >= umbral {min_profit_pct:.1%}; posición {position} con margen suficiente"
            )
        elif cutting_losses:
            motivo = "corte de pérdidas (lesión confirmada)" if is_confirmed_injured else "corte de pérdidas"
            reason = (
                f"{motivo}: pagado por el bot {purchase_price}, ahora {player.get('value', 0)} "
                f"({profit_pct:+.1%}) -- pérdida >= umbral de corte {loss_threshold:.1%} (mismo umbral para "
                "cualquier estado); se vende aunque no llegue al umbral de rentabilidad, para no caer en la "
                "falacia del coste hundido"
            )
        elif overconcentrated:
            reason = (
                f"lesión confirmada, concentración de capital: vale {player.get('value', 0)} "
                f"({concentration_pct:.1%} del capital total del equipo) >= umbral "
                f"{injury_concentration_max_pct:.1%}; se vende sin mirar plusvalía/pérdida ({profit_pct:+.1%}), "
                "para no tener capital inmovilizado en un jugador que no se puede usar"
            )
        elif force_sell_confirmed_injury:
            reason = (
                f"lesión confirmada: jugador inservible mientras no pueda jugar ni puntuar (vale "
                f"{player.get('value', 0)}, {profit_pct:+.1%} respecto al precio de referencia {purchase_price}); "
                "se vende siempre, sin mirar rentabilidad/pérdida/concentración, para liberar la plaza y poder "
                "fichar a otro en su lugar"
            )
        else:
            target_price = (best_market_candidate or {}).get("price") or 0
            extra_cost = target_price - player.get("value", 0)
            precio_nota = (
                f", precio objetivo {target_price} (+{extra_cost} sobre el propio, ya comprobado eficiencia "
                "de precio/asequibilidad/tiempo de listado)"
                if extra_cost > 0
                else f", precio objetivo {target_price} (no más caro que el propio)"
            )
            reason = (
                f"oportunidad de mercado: sin plusvalía suficiente ({profit_pct:+.1%}), pero el mejor candidato "
                f"de mercado en {position} tiene score de alineación {best_available_lineup_score:.3f} frente a "
                f"{own_lineup_score:.3f} propio (>= margen {upgrade_available_min_margin}){precio_nota}; se "
                "libera la plaza de cara a esa oportunidad, aunque hoy no compense económicamente"
            )

        # Candidato "swap" (a petición del usuario, 2026-08-29, ver
        # config.SELLING_SWAP_MIN_HOURS_BEFORE_ACCEPT): solo se persiste
        # para un candidato que cualifica ÚNICAMENTE por oportunidad de
        # mercado (`only_market_reason`) -- las otras cuatro vías no tienen
        # un reemplazo concreto identificado, así que no hay swap que
        # registrar. None en el resto de casos; jobs/run_sales.py solo
        # guarda el swap en `db.models.sale_swap_targets` si viene relleno.
        decisions.append(
            {
                "player_id": player["id"],
                "asking_price": player.get("value", 0),
                "purchase_price": purchase_price,
                "profit": profit,
                "profit_pct": profit_pct,
                "reason": reason,
                "swap_target_player_id": (best_market_candidate or {}).get("id") if only_market_reason else None,
                "swap_target_price": (best_market_candidate or {}).get("price") if only_market_reason else None,
            }
        )
    return decisions


def parse_iso_datetime(value) -> datetime | None:
    """
    Parsea una fecha ISO-8601 con milisegundos y `Z` -- formato CONFIRMADO
    en vivo tanto para `FutmondoClient.get_player_summary()["answer"]
    ["prices"][].date` (2026-08-22, ver TODO.md #14) como para
    `expirationDate`/`creationDate` de listados de mercado (ver
    `clients/futmondo_client.py`) -- de ahí que esta función, pese al
    nombre histórico, sirva para ambos usos dentro de este módulo (ver
    `decide_sales()`, ventana de tiempo del candidato objetivo). Se
    soporta también epoch numérico (segundos o milisegundos) como
    fallback defensivo, nunca visto en la práctica pero sin coste
    mantenerlo. Cualquier valor que no encaje en ninguno de los dos ->
    None, y quien llama debe tratarlo como "sin dato", nunca reventar por
    esto.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def compute_revaluation_premium_pct(
    prices: list[dict],
    lookback_days: float = None,
    min_data_points: int = None,
    min_pct_to_project: float = None,
    projection_fraction: float = None,
    max_premium_pct: float = None,
    now: datetime = None,
) -> tuple[float, str | None]:
    """
    A petición del usuario (2026-08-22): además de pedir el VM tal cual
    (ver docstring de `decide_sales()`), detecta si un jugador lleva
    SUBIENDO de forma sostenida en los últimos días y, si es así, proyecta
    una FRACCIÓN conservadora (nunca la subida completa) de esa tendencia
    como prima sobre el precio pedido -- el VM oficial de Futmondo puede
    tardar en reflejar del todo una revalorización muy reciente.

    `prices`: histórico tal cual devuelve `FutmondoClient.get_player_summary()
    ["answer"]["prices"]` (lista de `{"date", "price", ...}`; formato
    CONFIRMADO en vivo 2026-08-22, ver TODO.md #14 y docstring de
    `get_player_summary()` -- `date` ISO-8601, `price` es el VM diario
    real). Esta función se mantiene igualmente defensiva más allá de esa
    confirmación (nunca revienta con datos inesperados, ver
    `parse_iso_datetime()`): entradas sin "date"/"price" parseables (o con
    precio <= 0) se descartan sin más, no cuentan como dato.

    Condiciones, TODAS necesarias para proponer una prima > 0 (si falla
    cualquiera, devuelve `(0.0, None)` -- ninguna prima, se pide el VM tal
    cual, igual que hasta ahora):
      1. Al menos `min_data_points` (config.SELLING_REVALUATION_MIN_DATA_POINTS)
         puntos dentro de los últimos `lookback_days`
         (config.SELLING_REVALUATION_LOOKBACK_DAYS) -- justo después de un
         reinicio de la BD/liga no hay historial suficiente todavía, y no
         hay base para proyectar nada con 1-2 puntos sueltos.
      2. La serie dentro de esa ventana es NO DECRECIENTE en cada paso
         (ningún día baja respecto al anterior) -- un solo día de bajada
         descarta la prima entera: esto busca una tendencia sostenida, no
         un pico puntual seguido de una corrección.
      3. La subida total en la ventana (`pct_change`, del primer al último
         punto) alcanza `min_pct_to_project`
         (config.SELLING_REVALUATION_MIN_PCT_TO_PROJECT) -- por debajo de
         eso no se considera "revalorización rápida", es ruido normal.

    Si las tres se cumplen, la prima propuesta es
    `min(max_premium_pct, pct_change * projection_fraction)` — se proyecta
    solo una FRACCIÓN (config.SELLING_REVALUATION_PROJECTION_FRACTION) de
    la subida observada, nunca toda, y siempre topada por
    `max_premium_pct` (config.SELLING_REVALUATION_MAX_PREMIUM_PCT):
    ninguna decisión de este módulo debe poder inventar un precio
    arbitrariamente alto solo porque unos pocos días de historial subieron
    mucho.

    Devuelve `(premium_pct, nota_auditable)` -- `nota_auditable` es None si
    `premium_pct` es 0.0, listo para añadirse al "reason" de la decisión.
    """
    lookback_days = config.SELLING_REVALUATION_LOOKBACK_DAYS if lookback_days is None else lookback_days
    min_data_points = config.SELLING_REVALUATION_MIN_DATA_POINTS if min_data_points is None else min_data_points
    min_pct_to_project = (
        config.SELLING_REVALUATION_MIN_PCT_TO_PROJECT if min_pct_to_project is None else min_pct_to_project
    )
    projection_fraction = (
        config.SELLING_REVALUATION_PROJECTION_FRACTION if projection_fraction is None else projection_fraction
    )
    max_premium_pct = config.SELLING_REVALUATION_MAX_PREMIUM_PCT if max_premium_pct is None else max_premium_pct
    now = now or datetime.now(timezone.utc)

    parsed = []
    for entry in prices or []:
        date = parse_iso_datetime(entry.get("date"))
        price = entry.get("price")
        if date is None or not isinstance(price, (int, float)) or price <= 0:
            continue
        parsed.append((date, price))
    parsed.sort(key=lambda t: t[0])

    cutoff = now - timedelta(days=lookback_days)
    window = [(d, p) for d, p in parsed if d >= cutoff]
    if len(window) < min_data_points:
        return 0.0, None

    for (_, prev_price), (_, curr_price) in zip(window, window[1:]):
        if curr_price < prev_price:
            return 0.0, None  # al menos un día bajó -- no es una tendencia sostenida

    first_price, last_price = window[0][1], window[-1][1]
    pct_change = (last_price - first_price) / first_price
    if pct_change < min_pct_to_project:
        return 0.0, None

    premium_pct = min(max_premium_pct, pct_change * projection_fraction)
    note = (
        f"revalorización sostenida +{pct_change:.1%} en {len(window)} dato(s) de los últimos "
        f"{lookback_days:.0f} días -> prima proyectada +{premium_pct:.1%} (tope {max_premium_pct:.1%})"
    )
    return premium_pct, note


def apply_revaluation_premium(decision: dict, prices: list[dict], now: datetime = None) -> dict:
    """
    Post-procesa UNA decisión de `decide_sales()` subiendo `asking_price`
    por encima del VM si `compute_revaluation_premium_pct()` detecta
    revalorización rápida y sostenida (ver esa función para las
    condiciones y por qué es conservadora).

    Función pura (no llama a Futmondo): `jobs/run_sales.py` obtiene
    `prices` (de `FutmondoClient.get_player_summary()`) por cada decisión y
    decide si llamar a esto -- controlado por
    `config.ENABLE_SELLING_REVALUATION_PREMIUM` (True por defecto desde que
    se confirmó en vivo el formato real de `prices`, ver TODO.md #14).

    Si no hay prima que aplicar, devuelve `decision` TAL CUAL (ni siquiera
    una copia) -- comportamiento idéntico al de antes de esta feature.
    """
    premium_pct, note = compute_revaluation_premium_pct(prices, now=now)
    if premium_pct <= 0:
        return decision
    return {
        **decision,
        "asking_price": int(decision["asking_price"] * (1 + premium_pct)),
        "reason": f"{decision['reason']}; {note}",
    }
