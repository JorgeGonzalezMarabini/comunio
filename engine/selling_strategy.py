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

Corte por reversión desde máximo / trailing-stop (a petición del usuario,
evaluación del trigger de venta 2026-09-11): el corte de pérdidas de arriba
solo mira la plusvalía frente al precio de COMPRA. Un jugador comprado a
10 que subió a 20 y ha caído a 15 sigue con +50% frente a la compra y no
dispara nada, aunque haya perdido una cuarta parte de su pico -- se deja
evaporar buena parte de una plusvalía ya generada antes de reaccionar. Por
eso, si `purchase_baselines` (mismo formato que
`db.models.get_purchase_baselines()`) viene informado y trae "peak_price"
para el jugador, también se pone en venta si ha caído
`trailing_stop_max_drawdown_pct` (config.SELLING_TRAILING_STOP_MAX_DRAWDOWN_PCT,
por defecto 20%, deliberadamente más laxo que `max_loss_pct` -- aquí no se
corta una mala operación, se protege una buena) desde ese máximo, AUNQUE
siga en positivo frente al precio de compra. Sin `purchase_baselines` (o
sin "peak_price" para ese jugador -- recién comprado, sin snapshot todavía
desde entonces), esta vía queda desactivada sin más para él esta pasada.
No necesita una confirmación de tendencia propia (a diferencia del corte
de pérdidas, ver abajo): al compararse contra un máximo HISTÓRICO ya exige
una caída sostenida por construcción.

Confirmación de tendencia del corte de pérdidas (a petición del usuario,
misma evaluación 2026-09-11): `cutting_losses` mira el ÚLTIMO valor
conocido -- un dato puntual volátil que cruce `-max_loss_pct` un día y se
corrija al siguiente dispararía la venta igualmente. Por eso, cuando el
corte de pérdidas sería el ÚNICO motivo de venta de un candidato (igual
que el patrón de `only_market_reason` de la vía 5), y `recent_price_history`
(mismo formato que `db.models.get_recent_price_history()`) trae histórico
para ese jugador, se llama a `confirm_loss_is_sustained()` (ver su
docstring) antes de confirmar el corte -- si detecta que el valor ya
repuntó desde el mínimo reciente más de lo tolerado, se pospone el corte
esta pasada (se reevalúa en la siguiente, no se descarta para siempre).
Con menos histórico del mínimo exigido, o sin `recent_price_history` en
absoluto, esta confirmación queda desactivada y el corte se dispara igual
que hoy (falla ABIERTO: la falta de datos nunca debe bloquear un corte de
pérdidas real).

Corte de pérdidas frente al VM de compra, con multiplicadores (a petición
del usuario, 2026-09-23, "me preocupa la alta rotación de fichajes"): el
corte se medía contra lo PAGADO en la puja, que de media fue un ~11% más
que el VM del jugador -- con -15% de umbral, el corte saltaba con una
caída real de apenas unos puntos y vendía, sobre todo, esa prima (ver
números en config.SELLING_MAX_LOSS_PCT). La prima ya es coste hundido,
así que ahora la pérdida se mide contra "value_at_purchase" de
`purchase_baselines` (VM del último snapshot previo a la puja; lo pagado
si no hay) y el umbral se relaja con `effective_loss_cut_threshold()`:
más margen cuanto más reciente es el fichaje (x2 el día de la compra,
x1 a las dos semanas) y cuanto mejor puntúa (`average.average` del
roster, hasta x1.5), con un tope absoluto (40% por defecto). La
plusvalía de la vía 1 sigue midiéndose contra lo pagado. Por lo mismo,
la vía 5 (oportunidad de mercado) exige una antigüedad mínima
(config.SELLING_UPGRADE_MIN_HOLD_DAYS): un recién fichado no tiene aún
estadísticas propias y su score de alineación sale artificialmente bajo.

Venta por plusvalía ponderada (a petición del usuario, 2026-09-23, caso
real: Koski listado por +16% siendo el de mejor media del equipo y
subiendo ~+7% diario): la vía 1 solo miraba precio, y como los jugadores
que puntúan bien son justo los que se revalorizan, vendía sobre todo a los
mejores. Ahora el umbral se multiplica por la media de puntos
(`average.average` del roster, hasta x2.5) y por titularidad
(`own_lineup_player_ids`, x1.5) -- ver `effective_profit_threshold()` --, y
la venta se aplaza mientras el precio siga subiendo (`price_momentum_pct()`
sobre `recent_price_history`, config.SELLING_PROFIT_MOMENTUM_*): vender en
plena subida deja dinero en la mesa, y si la subida se da la vuelta el
trailing-stop protege la plusvalía. Ver números en
config.SELLING_PROFIT_AVG_POINTS_REF.

Nota informativa de puntos ya extraídos (a petición del usuario, misma
evaluación 2026-09-11): cuando un corte de pérdidas o el trailing-stop
disparan una venta, si `purchase_baselines` trae "points_at_purchase" para
ese jugador, se añade al `reason` cuántos puntos de liga sumó DESDE que el
bot lo compró (`player["points"]` actual, que es un acumulado de
TEMPORADA que no se resetea al fichar, menos ese baseline) -- puramente
informativo, para que quien revise la notificación vea que la operación,
aunque perdiera valor de mercado, sí aportó puntos mientras estuvo en
plantilla. Deliberadamente NO participa en ninguna condición de venta:
usarlo para dar más margen a un jugador que ya rindió sería la misma
falacia del coste hundido con otro nombre (aferrarse a lo que YA dio, en
vez de decidir por lo que vale AHORA) -- la señal de rendimiento propio
reciente ya tiene su vía correcta, no condicionada al pasado, en
"oportunidad de mercado" (`config.LINEUP_EVALUATOR_WEIGHTS["futmondo_trend"]`
más arriba).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config
from clients.futmondo_client import FUTMONDO_POSITION_MAP, is_confirmed_injured_status, is_injury_status
from engine.evaluator import evaluate_players, weighted_recent_points
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
    purchase_baselines: dict[str, dict] = None,
    trailing_stop_max_drawdown_pct: float = None,
    recent_price_history: dict[str, list[dict]] = None,
    loss_confirmation_lookback_days: float = None,
    loss_confirmation_min_data_points: int = None,
    loss_confirmation_max_rebound_pct: float = None,
    loss_time_mult_max: float = None,
    loss_time_decay_days: float = None,
    loss_avg_points_ref: float = None,
    loss_avg_points_max_mult: float = None,
    loss_max_effective_pct: float = None,
    upgrade_min_hold_days: float = None,
    profit_avg_points_ref: float = None,
    profit_avg_points_max_mult: float = None,
    profit_starter_mult: float = None,
    profit_momentum_lookback_days: float = None,
    profit_momentum_min_pct: float = None,
    profit_momentum_min_data_points: int = None,
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

    `purchase_baselines`: mismo formato que
    `db.models.get_purchase_baselines()` — {player_id: {"peak_price",
    "points_at_purchase"}}. Habilita la 6ª vía de venta (trailing-stop,
    ver docstring del módulo) y la nota informativa de puntos ya
    extraídos. Si se omite (o falta la entrada de un jugador concreto),
    ambas quedan desactivadas sin más para él, backward-compatible con el
    resto de usos de `decide_sales()`.

    `trailing_stop_max_drawdown_pct`: por defecto
    config.SELLING_TRAILING_STOP_MAX_DRAWDOWN_PCT — % de caída desde
    "peak_price" (ver `purchase_baselines`) a partir del cual se vende
    igualmente, aunque siga en positivo frente al precio de compra.

    `recent_price_history`: mismo formato que
    `db.models.get_recent_price_history()` — {player_id: [{"recorded_at",
    "price"}, ...]}. Habilita la confirmación de tendencia del corte de
    pérdidas (ver docstring del módulo y `confirm_loss_is_sustained()`) —
    sin esto (o sin histórico suficiente para un jugador concreto), el
    corte de pérdidas se dispara igual que antes de esta feature (falla
    ABIERTO, ver docstring del módulo).

    `loss_confirmation_lookback_days`, `loss_confirmation_min_data_points`,
    `loss_confirmation_max_rebound_pct`: parámetros de
    `confirm_loss_is_sustained()`, todos por defecto de config
    (`SELLING_LOSS_CONFIRMATION_*`, ver su docstring).

    `loss_time_mult_max`, `loss_time_decay_days`, `loss_avg_points_ref`,
    `loss_avg_points_max_mult`, `loss_max_effective_pct`: multiplicadores
    del umbral de corte de pérdidas (ver `effective_loss_cut_threshold()`
    y docstring del módulo, "Corte de pérdidas frente al VM de compra"),
    todos por defecto de config (`SELLING_LOSS_*`). El de tiempo necesita
    "purchased_at" en `purchase_baselines`; el de media, `average.average`
    en el item de roster -- sin cualquiera de los dos, ese factor queda en
    x1.

    `upgrade_min_hold_days`: por defecto config.SELLING_UPGRADE_MIN_HOLD_DAYS
    — antigüedad mínima para que la vía 5 (oportunidad de mercado) pueda
    aplicar. Sin "purchased_at" para ese jugador, no bloquea.

    `profit_avg_points_ref`, `profit_avg_points_max_mult`,
    `profit_starter_mult`: multiplicadores del umbral de plusvalía (vía 1,
    ver `effective_profit_threshold()`); `profit_momentum_*`: aplazamiento
    mientras el precio siga subiendo (ver `price_momentum_pct()`, usa
    `recent_price_history`). Todos por defecto de config (`SELLING_PROFIT_*`).
    La titularidad sale de `own_lineup_player_ids`.

    `now`: por defecto `datetime.now(timezone.utc)` — inyectable para
    tests deterministas (afecta al bloqueo de fin de semana, a la
    comparación de tiempo del listado objetivo y a la confirmación de
    tendencia del corte de pérdidas).

    Devuelve una decisión por cada jugador que cumpla CUALQUIERA de estas
    seis condiciones (Y cuya posición siga teniendo margen de suplentes
    sanos después de la venta, salvo que ya esté lesionado/en duda —
    ver más abajo):
      1. Su revalorización (`value` vs. el precio pagado en
         `bought_by_bot`) supera el umbral efectivo de plusvalía
         (`min_profit_pct`, por defecto config.SELLING_MIN_PROFIT_PCT,
         multiplicado por media de puntos y titularidad, ver
         `effective_profit_threshold()`) y el precio NO sigue subiendo
         (`price_momentum_pct()`) — vía normal, para todos.
      2. Ha perdido más del umbral efectivo de corte (`max_loss_pct` con
         los multiplicadores por antigüedad y media de puntos, ver
         `effective_loss_cut_threshold()`) frente al VM en la compra
         ("value_at_purchase", o lo pagado si no se conoce) — corte de
         pérdidas, mismo umbral para cualquier estado.
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
      6. Ha caído `trailing_stop_max_drawdown_pct` desde el valor MÁS ALTO
         observado desde la compra ("peak_price" de `purchase_baselines`)
         — trailing-stop, aunque siga en positivo frente al precio de
         compra. Solo si `purchase_baselines` trae "peak_price" para ese
         jugador.

    El corte de pérdidas (vía 2) se pospone (no se descarta, se reevalúa
    en la siguiente pasada) si es la ÚNICA vía que aplica para un
    candidato y `confirm_loss_is_sustained()` detecta que el valor ya
    repuntó desde su mínimo reciente más de lo tolerado (ver
    `recent_price_history`/`loss_confirmation_*` y docstring del módulo).

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
    trailing_stop_max_drawdown_pct = (
        config.SELLING_TRAILING_STOP_MAX_DRAWDOWN_PCT
        if trailing_stop_max_drawdown_pct is None
        else trailing_stop_max_drawdown_pct
    )
    loss_confirmation_lookback_days = (
        config.SELLING_LOSS_CONFIRMATION_LOOKBACK_DAYS
        if loss_confirmation_lookback_days is None
        else loss_confirmation_lookback_days
    )
    loss_confirmation_min_data_points = (
        config.SELLING_LOSS_CONFIRMATION_MIN_DATA_POINTS
        if loss_confirmation_min_data_points is None
        else loss_confirmation_min_data_points
    )
    loss_confirmation_max_rebound_pct = (
        config.SELLING_LOSS_CONFIRMATION_MAX_REBOUND_PCT
        if loss_confirmation_max_rebound_pct is None
        else loss_confirmation_max_rebound_pct
    )
    loss_time_mult_max = config.SELLING_LOSS_TIME_MULT_MAX if loss_time_mult_max is None else loss_time_mult_max
    loss_time_decay_days = config.SELLING_LOSS_TIME_DECAY_DAYS if loss_time_decay_days is None else loss_time_decay_days
    loss_avg_points_ref = config.SELLING_LOSS_AVG_POINTS_REF if loss_avg_points_ref is None else loss_avg_points_ref
    loss_avg_points_max_mult = (
        config.SELLING_LOSS_AVG_POINTS_MAX_MULT if loss_avg_points_max_mult is None else loss_avg_points_max_mult
    )
    loss_max_effective_pct = (
        config.SELLING_LOSS_MAX_EFFECTIVE_PCT if loss_max_effective_pct is None else loss_max_effective_pct
    )
    upgrade_min_hold_days = config.SELLING_UPGRADE_MIN_HOLD_DAYS if upgrade_min_hold_days is None else upgrade_min_hold_days
    profit_momentum_lookback_days = (
        config.SELLING_PROFIT_MOMENTUM_LOOKBACK_DAYS
        if profit_momentum_lookback_days is None
        else profit_momentum_lookback_days
    )
    profit_momentum_min_pct = (
        config.SELLING_PROFIT_MOMENTUM_MIN_PCT if profit_momentum_min_pct is None else profit_momentum_min_pct
    )
    profit_momentum_min_data_points = (
        config.SELLING_PROFIT_MOMENTUM_MIN_DATA_POINTS
        if profit_momentum_min_data_points is None
        else profit_momentum_min_data_points
    )
    now = now or datetime.now(timezone.utc)
    market_listing_expirations = market_listing_expirations or {}
    own_lineup_ids = {str(i) for i in (own_lineup_player_ids or [])}
    purchase_baselines = purchase_baselines or {}
    recent_price_history = recent_price_history or {}
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
        #
        # Referencia = VM en la compra, no lo pagado, y umbral con
        # multiplicadores por antigüedad y media de puntos (ver docstring
        # del módulo, "Corte de pérdidas frente al VM de compra"): la prima
        # pagada en la puja ya es coste hundido.
        is_confirmed_injured = is_confirmed_injured_status(player.get("status"))
        is_injured_or_doubtful = is_injury_status(player.get("status"))
        baseline = purchase_baselines.get(str(player["id"])) or {}
        loss_reference_price = baseline.get("value_at_purchase") or purchase_price
        loss_pct = (current_price - loss_reference_price) / loss_reference_price
        purchased_at = parse_iso_datetime(baseline.get("purchased_at"))
        days_held = (now - purchased_at).total_seconds() / 86400 if purchased_at else None
        # Forma ponderada por recencia (config.EVALUATOR_RECENT_POINTS_DECAY)
        # a partir de `average.fitness` del roster; media de temporada si no
        # viene el array.
        average_info = player.get("average") if isinstance(player.get("average"), dict) else {}
        average_points = weighted_recent_points(average_info.get("fitness"), average_info.get("average"))
        if average_points is None:
            average_points = average_info.get("average")
        loss_threshold = effective_loss_cut_threshold(
            max_loss_pct,
            days_held=days_held,
            average_points=average_points,
            time_mult_max=loss_time_mult_max,
            time_decay_days=loss_time_decay_days,
            avg_points_ref=loss_avg_points_ref,
            avg_points_max_mult=loss_avg_points_max_mult,
            max_effective_pct=loss_max_effective_pct,
        )
        cutting_losses = loss_pct <= -loss_threshold

        # Plusvalía ponderada (ver docstring del módulo, "Venta por
        # plusvalía ponderada"): umbral más alto para quien puntúa bien y
        # para titulares, y aplazada mientras el precio siga subiendo.
        is_starter = str(player["id"]) in own_lineup_ids
        profit_threshold = effective_profit_threshold(
            min_profit_pct,
            average_points=average_points,
            is_starter=is_starter,
            avg_points_ref=profit_avg_points_ref,
            avg_points_max_mult=profit_avg_points_max_mult,
            starter_mult=profit_starter_mult,
        )
        momentum_pct = price_momentum_pct(
            recent_price_history.get(str(player["id"])),
            current_price,
            lookback_days=profit_momentum_lookback_days,
            min_data_points=profit_momentum_min_data_points,
            now=now,
        )
        still_rising = momentum_pct is not None and momentum_pct >= profit_momentum_min_pct
        taking_profit = profit_pct >= profit_threshold and not still_rising

        # Trailing-stop / corte por reversión desde máximo (ver docstring
        # del módulo): compara contra el valor MÁS ALTO observado desde la
        # compra ("peak_price" de purchase_baselines), no contra el precio
        # de compra -- puede disparar aunque profit_pct siga en positivo.
        peak_price = baseline.get("peak_price")
        points_at_purchase = baseline.get("points_at_purchase")
        drawdown_from_peak_pct = (
            (peak_price - current_price) / peak_price if peak_price else 0.0
        )
        trailing_stop_triggered = bool(peak_price) and drawdown_from_peak_pct >= trailing_stop_max_drawdown_pct

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
        # Antigüedad mínima (config.SELLING_UPGRADE_MIN_HOLD_DAYS): un
        # recién fichado aún no tiene score representativo. Sin fecha de
        # compra conocida, no bloquea.
        held_long_enough_for_upgrade = days_held is None or days_held >= upgrade_min_hold_days
        market_upgrade_available = (
            not is_injured_or_doubtful
            and held_long_enough_for_upgrade
            and own_lineup_score is not None
            and best_available_lineup_score is not None
            and (best_available_lineup_score - own_lineup_score) >= upgrade_available_min_margin
        )

        # Confirmación de tendencia del corte de pérdidas (ver docstring
        # del módulo y confirm_loss_is_sustained()) -- SOLO cuando el corte
        # de pérdidas sería el ÚNICO motivo de venta de este candidato (si
        # además aplica otra vía incondicional, esa manda igual y no hace
        # falta confirmar nada). Sin recent_price_history para este
        # jugador, cutting_losses no se toca -- falla ABIERTO, idéntico al
        # comportamiento previo a esta feature.
        only_loss_cut_reason = (
            cutting_losses
            and not overconcentrated
            and not force_sell_confirmed_injury
            and not market_upgrade_available
            and not trailing_stop_triggered
        )
        if only_loss_cut_reason:
            history = recent_price_history.get(str(player["id"]))
            if history:
                confirmed, _ = confirm_loss_is_sustained(
                    history,
                    lookback_days=loss_confirmation_lookback_days,
                    min_data_points=loss_confirmation_min_data_points,
                    max_rebound_pct=loss_confirmation_max_rebound_pct,
                    now=now,
                )
                if not confirmed:
                    cutting_losses = False  # repuntó desde el mínimo reciente -- se pospone esta pasada

        if (
            not taking_profit
            and not cutting_losses
            and not overconcentrated
            and not force_sell_confirmed_injury
            and not market_upgrade_available
            and not trailing_stop_triggered
        ):
            continue

        # True si "oportunidad de mercado" es la ÚNICA vía que aplica --
        # solo estos candidatos pasan por los cuatro refinamientos
        # adicionales de la vía 5 (ver docstring del módulo); el resto
        # (rentabilidad/pérdida/concentración/lesión) no mira precio del
        # objetivo en absoluto, esas vías se venden igual.
        only_market_reason = (
            market_upgrade_available
            and not taking_profit
            and not cutting_losses
            and not overconcentrated
            and not force_sell_confirmed_injury
            and not trailing_stop_triggered
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
                trailing_stop_triggered,
                peak_price,
                drawdown_from_peak_pct,
                points_at_purchase,
                loss_pct,
                loss_reference_price,
                taking_profit,
                profit_threshold,
                momentum_pct,
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
        trailing_stop_triggered,
        peak_price,
        drawdown_from_peak_pct,
        points_at_purchase,
        loss_pct,
        loss_reference_price,
        taking_profit,
        profit_threshold,
        momentum_pct,
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

        # Nota informativa de puntos ya extraídos (ver docstring del
        # módulo): SOLO se añade a las dos vías que "sacrifican" valor de
        # mercado (corte de pérdidas, trailing-stop) -- puramente
        # auditoría, nunca condiciona la decisión (ver docstring: usarlo
        # para dar más margen sería la misma falacia del coste hundido con
        # otro nombre). Sin points_at_purchase (o sin "points" en el
        # roster en vivo), no se añade nada.
        points_now = player.get("points")
        points_note = ""
        if points_at_purchase is not None and isinstance(points_now, (int, float)):
            points_earned = points_now - points_at_purchase
            points_note = (
                f"; para contexto: sumó {points_earned} punto(s) de liga mientras estuvo en plantilla "
                "(no afecta a la decisión, solo auditoría)"
            )

        # Prioridad del motivo mostrado (no son excluyentes entre sí, un
        # candidato puede cumplir varios a la vez): rentabilidad normal
        # primero (el caso más informativo/común), luego corte de
        # pérdidas, luego trailing-stop, luego concentración de capital,
        # luego lesión confirmada, luego oportunidad de mercado (las
        # últimas tres ni siquiera miran profit_pct).
        if taking_profit:
            tendencia = f"{momentum_pct:+.1%}" if momentum_pct is not None else "sin histórico"
            reason = (
                f"pagado por el bot {purchase_price}, ahora {player.get('value', 0)} "
                f"({profit_pct:+.1%}) >= umbral {profit_threshold:.1%} (base {min_profit_pct:.1%} con "
                f"multiplicadores por media de puntos y titularidad); tendencia reciente {tendencia} (ya no "
                f"sube); posición {position} con margen suficiente"
            )
        elif cutting_losses:
            motivo = "corte de pérdidas (lesión confirmada)" if is_confirmed_injured else "corte de pérdidas"
            reason = (
                f"{motivo}: VM en la compra {loss_reference_price} (pagado por el bot {purchase_price}), ahora "
                f"{player.get('value', 0)} ({loss_pct:+.1%} frente al VM de compra, {profit_pct:+.1%} frente a lo "
                f"pagado) -- pérdida >= umbral de corte efectivo {loss_threshold:.1%} (base {max_loss_pct:.1%} "
                "con multiplicadores por antigüedad y media de puntos); se vende aunque no llegue al umbral de "
                f"rentabilidad, para no caer en la falacia del coste hundido{points_note}"
            )
        elif trailing_stop_triggered:
            reason = (
                f"corte por reversión desde máximo: pico de {peak_price} desde la compra, ahora "
                f"{player.get('value', 0)} ({drawdown_from_peak_pct:.1%} de caída desde el pico) >= umbral "
                f"{trailing_stop_max_drawdown_pct:.1%}; se protege la plusvalía ya generada aunque siga en "
                f"positivo frente al precio de compra ({profit_pct:+.1%}){points_note}"
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


def effective_profit_threshold(
    base_pct: float,
    average_points: float | None = None,
    is_starter: bool = False,
    avg_points_ref: float = None,
    avg_points_max_mult: float = None,
    starter_mult: float = None,
) -> float:
    """
    Umbral de plusvalía (vía 1 de `decide_sales()`, ver docstring del
    módulo, "Venta por plusvalía ponderada"):

        base_pct * mult_media * mult_titular

      - mult_media: `average_points / avg_points_ref`, acotado a
        [1, `avg_points_max_mult`]. Sin media (None/no numérica/<=0) -> 1.
      - mult_titular: `starter_mult` si `is_starter`, si no 1.

    Nunca por debajo de `base_pct`. Parámetros por defecto de config
    (`SELLING_PROFIT_*`).
    """
    avg_points_ref = config.SELLING_PROFIT_AVG_POINTS_REF if avg_points_ref is None else avg_points_ref
    avg_points_max_mult = (
        config.SELLING_PROFIT_AVG_POINTS_MAX_MULT if avg_points_max_mult is None else avg_points_max_mult
    )
    starter_mult = config.SELLING_PROFIT_STARTER_MULT if starter_mult is None else starter_mult

    avg_mult = 1.0
    if isinstance(average_points, (int, float)) and average_points > 0 and avg_points_ref > 0:
        avg_mult = min(max(1.0, avg_points_max_mult), max(1.0, average_points / avg_points_ref))
    return base_pct * avg_mult * (max(1.0, starter_mult) if is_starter else 1.0)


def price_momentum_pct(
    price_points: list[dict] | None,
    current_price: float,
    lookback_days: float = None,
    min_data_points: int = None,
    now: datetime = None,
) -> float | None:
    """
    Variación del VM actual (`current_price`) frente al PRIMER dato de
    `price_points` (formato de `db.models.get_recent_price_history()`)
    dentro de los últimos `lookback_days` días. None si hay menos de
    `min_data_points` datos válidos en la ventana (sin base para juzgar
    tendencia -- `decide_sales()` no aplaza nada en ese caso).
    """
    lookback_days = config.SELLING_PROFIT_MOMENTUM_LOOKBACK_DAYS if lookback_days is None else lookback_days
    min_data_points = config.SELLING_PROFIT_MOMENTUM_MIN_DATA_POINTS if min_data_points is None else min_data_points
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    window = []
    for entry in price_points or []:
        date = parse_iso_datetime(entry.get("recorded_at"))
        price = entry.get("price")
        if date is None or date < cutoff or not isinstance(price, (int, float)) or price <= 0:
            continue
        window.append((date, price))
    if len(window) < min_data_points or not current_price or current_price <= 0:
        return None
    first_price = min(window, key=lambda t: t[0])[1]
    return (current_price - first_price) / first_price


def effective_loss_cut_threshold(
    base_pct: float,
    days_held: float | None = None,
    average_points: float | None = None,
    time_mult_max: float = None,
    time_decay_days: float = None,
    avg_points_ref: float = None,
    avg_points_max_mult: float = None,
    max_effective_pct: float = None,
) -> float:
    """
    Umbral de pérdida (positivo) a partir del cual `decide_sales()` corta
    pérdidas (vía 2), ver docstring del módulo, "Corte de pérdidas frente
    al VM de compra":

        min(max_effective_pct, base_pct * mult_tiempo * mult_media)

      - mult_tiempo: `time_mult_max` el día de la compra, decae linealmente
        hasta 1 a los `time_decay_days` días. Sin `days_held` -> 1.
      - mult_media: `average_points / avg_points_ref`, acotado a
        [1, `avg_points_max_mult`]. Sin media (None/no numérica/<=0) -> 1.

    Nunca devuelve menos que `base_pct` (ambos factores >= 1) salvo que
    `max_effective_pct` sea menor que el propio base. Todos los parámetros
    salvo `base_pct` por defecto de config (`SELLING_LOSS_*`).
    """
    time_mult_max = config.SELLING_LOSS_TIME_MULT_MAX if time_mult_max is None else time_mult_max
    time_decay_days = config.SELLING_LOSS_TIME_DECAY_DAYS if time_decay_days is None else time_decay_days
    avg_points_ref = config.SELLING_LOSS_AVG_POINTS_REF if avg_points_ref is None else avg_points_ref
    avg_points_max_mult = config.SELLING_LOSS_AVG_POINTS_MAX_MULT if avg_points_max_mult is None else avg_points_max_mult
    max_effective_pct = config.SELLING_LOSS_MAX_EFFECTIVE_PCT if max_effective_pct is None else max_effective_pct

    time_mult = 1.0
    if days_held is not None and time_decay_days > 0:
        remaining = max(0.0, min(1.0, (time_decay_days - max(0.0, days_held)) / time_decay_days))
        time_mult = 1.0 + (max(1.0, time_mult_max) - 1.0) * remaining

    avg_mult = 1.0
    if isinstance(average_points, (int, float)) and average_points > 0 and avg_points_ref > 0:
        avg_mult = min(max(1.0, avg_points_max_mult), max(1.0, average_points / avg_points_ref))

    return min(max_effective_pct, base_pct * time_mult * avg_mult)


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


def confirm_loss_is_sustained(
    price_points: list[dict],
    lookback_days: float = None,
    min_data_points: int = None,
    max_rebound_pct: float = None,
    now: datetime = None,
) -> tuple[bool, str | None]:
    """
    A petición del usuario (evaluación del trigger de venta 2026-09-11):
    el corte de pérdidas de `decide_sales()` (`cutting_losses`) mira el
    ÚLTIMO valor conocido -- un dato puntual volátil que cruce
    `-max_loss_pct` un día y se corrija al siguiente dispararía la venta
    igualmente. Esta función confirma que la caída sigue vigente antes de
    aceptar el corte, usando histórico LOCAL reciente (ver
    `db.models.get_recent_price_history()` -- SIN llamada de red, a
    diferencia de `compute_revaluation_premium_pct()`, que usa el
    histórico corto de `FutmondoClient.get_player_summary()`).

    `price_points`: [{"recorded_at", "price"}, ...] tal cual devuelve
    `db.models.get_recent_price_history()` (cualquier orden, se ordena
    aquí dentro). Entradas sin "recorded_at"/"price" parseables (o con
    precio <= 0) se descartan, no cuentan como dato -- misma defensiva que
    `compute_revaluation_premium_pct()`.

    Con menos de `min_data_points` (config.
    SELLING_LOSS_CONFIRMATION_MIN_DATA_POINTS) dentro de los últimos
    `lookback_days` (config.SELLING_LOSS_CONFIRMATION_LOOKBACK_DAYS) --
    FALLA ABIERTO: devuelve `(True, None)`, se confirma el corte igual que
    si esta función no existiera. A diferencia de
    `compute_revaluation_premium_pct()` (que ante falta de datos falla
    hacia "sin prima", la opción conservadora para SUBIR un precio), aquí
    el sesgo de seguridad es el opuesto: la falta de histórico NUNCA debe
    poder bloquear un corte de pérdidas real.

    Con datos suficientes: se compara el ÚLTIMO precio de la ventana
    contra el MÍNIMO de la ventana (no se exige una serie estrictamente
    no-creciente, a diferencia de la prima de revalorización -- un valor
    puede oscilar día a día incluso en una caída real, exigir monotonía
    sería demasiado frágil). Si el último precio ya repuntó
    `max_rebound_pct` (config.SELLING_LOSS_CONFIRMATION_MAX_REBOUND_PCT) o
    más desde ese mínimo, la caída puede estar revirtiendo -- devuelve
    `(False, nota)`: `decide_sales()` pospone el corte esta pasada (se
    reevalúa en la siguiente, no se descarta para siempre). Si no,
    `(True, None)`: se confirma el corte.
    """
    lookback_days = (
        config.SELLING_LOSS_CONFIRMATION_LOOKBACK_DAYS if lookback_days is None else lookback_days
    )
    min_data_points = (
        config.SELLING_LOSS_CONFIRMATION_MIN_DATA_POINTS if min_data_points is None else min_data_points
    )
    max_rebound_pct = (
        config.SELLING_LOSS_CONFIRMATION_MAX_REBOUND_PCT if max_rebound_pct is None else max_rebound_pct
    )
    now = now or datetime.now(timezone.utc)

    parsed = []
    for entry in price_points or []:
        date = parse_iso_datetime(entry.get("recorded_at"))
        price = entry.get("price")
        if date is None or not isinstance(price, (int, float)) or price <= 0:
            continue
        parsed.append((date, price))
    parsed.sort(key=lambda t: t[0])

    cutoff = now - timedelta(days=lookback_days)
    window = [(d, p) for d, p in parsed if d >= cutoff]
    if len(window) < min_data_points:
        return True, None  # sin base para juzgar tendencia -- falla ABIERTO, se corta igual

    min_price = min(p for _, p in window)
    last_price = window[-1][1]
    rebound_pct = (last_price - min_price) / min_price if min_price > 0 else 0.0
    if rebound_pct >= max_rebound_pct:
        note = (
            f"corte de pérdidas pospuesto: repuntó +{rebound_pct:.1%} desde el mínimo reciente "
            f"({min_price}) en los últimos {lookback_days:.0f} días -- puede ser ruido en reversión, "
            "se reevalúa en la siguiente pasada"
        )
        return False, note

    return True, None


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
