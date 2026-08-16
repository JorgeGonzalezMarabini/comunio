# Comunio Liga Bot

Bot de gestión automática de un equipo en Comunio (liga privada): fichajes/pujas,
evaluación de jugadores (Comunio + stats externas), alineaciones automáticas y
notificación de cada acción. Coste 0: sin APIs de pago ni VPS de pago.

## Estado actual

- [x] Captura de endpoints reales de Comunio — hecha el 2026-08-15 con Chrome DevTools sobre una liga de prueba real
- [x] Cliente de Comunio (`clients/comunio_client.py`) — login (esquema `Bearer` **confirmado** con petición real autenticada) y lectura (standings/squad/market/offers/lineup) contra endpoints reales; **pujar, guardar alineación, retirar puja, poner en venta y quitar de la venta confirmados los cinco al 100%** con pruebas reales (verbo y body exactos, ver abajo) — no queda ninguna escritura basada solo en inferencia
- [x] Cliente de stats externas (`clients/laliga_stats_client.py`) — Understat implementado contra su endpoint JSON real (`getLeagueData`); **FBref descartado** (bloquea con Cloudflare, ver abajo)
- [x] Esquema de base de datos (`db/models.py`) — campos alineados con Understat Y con el JSON real de Comunio (squad/market), incluyendo normalización de posición y del "-" de puntos en pretemporada
- [x] Motor de evaluación (`engine/evaluator.py`) — conectado a datos reales: `normalize_pool()`/`evaluate_players()` parten de `db.models.get_player_features()` (SQL con último snapshot de Comunio + Understat por jugador) y normalizan cada feature 0..1 dentro del pool; pesos configurables en `config.py`, sin calibrar todavía contra resultados reales de liga
- [x] Estrategia de pujas (`engine/bidding_strategy.py`) — el importe se ancla al VM/precio real del jugador + una prima que escala con el score, siempre topado por los límites de seguridad de `config.py`; **dos bugs reales corregidos**: (1) el cap de seguridad podía recortar el importe por debajo del precio del jugador y Comunio rechazaba la puja — `decide_bid()` ya no puja si el cap no llega al precio; (2) **el más grave** — el bot no restaba las ofertas de compra pendientes sin resolver de días anteriores, pudiendo comprometer más saldo del real y dejarlo en negativo (ver sección dedicada más abajo: saldo negativo = **0 puntos toda la jornada**, regla oficial de Comunio); `decide_bids_for_market()` decide varias pujas de una tacada respetando el riesgo acumulado en la misma pasada, ahora también priorizando posiciones en riesgo de plantilla (`apply_position_priority()`)
- [x] Optimizador de alineaciones (`engine/lineup_optimizer.py`) — formaciones básicas (formato humano "4-4-2", con `to_api_tactic()` para convertir al "442" real de la API); **dificultad del rival ya incorporada** vía `apply_fixture_difficulty()` + `laliga_stats_client.next_match_difficulty()` (forecast de Understat, verificado que siempre es en perspectiva del equipo local)
- [x] `jobs/sync_data.py` — mapeo real Comunio+Understat -> `db/models.py`, corrido de verdad en producción vía el cron. También **reconcilia el estado de pujas y ventas** (`bids.status`/`sales.status`: 'placed'/'listed' -> 'won'/'lost'/'sold') comparando `get_offers()` y la plantilla actual — nada más lo hacía, así que sin esto la auditoría se quedaba congelada para siempre
- [x] `jobs/run_market.py` — pipeline completo: candidatos de mercado -> evaluator -> bidding_strategy -> `place_bid()` real, con presupuesto (`credit`) y riesgo ya comprometido hoy leídos de Comunio/BD; cada intento fallido (HTTP o rechazo de negocio con HTTP 200, `ComunioOfferError`) se audita sin tumbar los demás. **Corrido de verdad en producción** (2026-08-16, cron real vía GitHub Actions): pujó por 4 jugadores reales tras el fix del bug de `on_market`
- [x] `jobs/set_lineup.py` — pipeline completo: plantilla -> evaluator (con **pesos distintos a los de puja**, ver nota abajo) -> dificultad de rival -> `pick_lineup()` -> `build_lineup_slots()`/`pick_substitutes()` -> `set_lineup()` real. La decisión SIEMPRE se audita en `lineup_decisions`; el envío real a Comunio está **activado por defecto** (`config.ENABLE_LINEUP_AUTO_SUBMIT=true`, con opción de desactivarlo en `.env`) ahora que el mapeo de slots está confirmado al 100%. Ahora también avisa de **riesgo de plantilla** (ver `engine/squad_risk.py` más abajo)
- [x] `jobs/run_sales.py` (nuevo) — identifica jugadores comprados por el bot con plusvalía suficiente (`engine/selling_strategy.py`, sobre `purchaseInfo.price` real de squad) y los pone en venta (`list_for_sale()`); único ingreso real del bot (ver sección dedicada más abajo)
- [~] Jobs y scheduler en GitHub Actions — `schedule:` activado en los 4 YAMLs (incluido el nuevo `run_sales.yml`) + persistencia de `db/comunio.db` entre ejecuciones (commit automático); **pendiente**: configurar Secrets/Variables del nuevo job en GitHub (mismos que los demás, ver sección "Activar el cron")
- [x] Notificaciones por Telegram (`notifier.py`)

## Endpoints reales de Comunio (capturados 2026-08-15)

Dos dominios distintos: `www.comunio.es` es el frontend (Next.js, no se usa
desde el bot) y `https://api.comunio.es` es la API REST real que sí se usa.

Auth: NO es cookie de sesión — `POST /login` devuelve `access_token` /
`refresh_token` que el frontend guarda en `localStorage` y reenvía en cada
llamada como header `Authorization: Bearer <token>` — **esquema confirmado**
con una petición real autenticada (200 OK), no solo una suposición.

La API es HAL/HATEOAS: casi toda respuesta trae un `_links` con URLs
completas para las acciones disponibles sobre ese recurso — mucho más
fiable que adivinar rutas.

| Acción | Endpoint |
|---|---|
| Login | `POST /login` — body `{username, password, tzoffset}` |
| Estado de sesión | `GET /login/state` |
| Plantilla | `GET /users/{userId}/squad` |
| Clasificación | `GET /communities/{communityId}/standings?period=total&wpe=true` |
| Miembros de la liga | `GET /communities/{communityId}/members` |
| Mercado (compra/venta) | `GET /communities/{communityId}/users/{userId}/exchangemarket` |
| Ofertas/pujas activas | `GET /communities/{communityId}/users/{userId}/offers?current` (el query param es obligatorio, sin él da 500) |
| Alineación actual | `GET /communities/{communityId}/users/{userId}/lineup` |
| Logout | `POST /communities/{communityId}/users/{userId}/logout` — body `{userId}` |

**Pujar**: el propio `_links["game:exchangemarket:placeoffers"]` de
`get_market()` apunta literalmente a `.../users/{userId}/offers` (el mismo
path que el GET) — confirma que es un POST ahí. El objeto resultante,
releído después con `get_offers()`, tiene esta forma real (validado con una
puja real de prueba, con permiso explícito):
```
{id, type: "PURCHASE", tradable: {id, name, ...}, user: {...},
 tradingPartner: {...}, price, datecreated, state: "PENDING",
 _links: {"game:offer:withdraw": ".../offers/{id}", "game:offer:decline": "..."}}
```
El body de creación (`{"type": "PURCHASE", "tradable": {"id": ...}, "price": ...}`)
es una inferencia razonable a partir de esa forma, no un POST confirmado
literalmente. `game:offer:withdraw` da el path para retirar una puja propia
(`withdraw_bid()`), verbo DELETE por convención sin confirmar tampoco.

**Guardar alineación — CONFIRMADO AL 100%** (2026-08-15, once completo de 11
jugadores en la liga de prueba, interceptando la llamada PUT real del
propio frontend + réplica exacta del body devolviendo 200 `{"status": "OK"}`):

```
PUT /communities/{communityId}/users/{userId}/lineup
body: {
    "userId": <user_id, int>,
    "tactic": "442",                              # SIN guiones, ver to_api_tactic()
    "lineup": {"1": "<playerId>", ..., "11": "<playerId>"},   # strings
    "substitutes": {"striker": "", "midfielder": "", "defender": "", "keeper": ""},
    "type": "default",
}
```

Numeración de slots CONFIRMADA (patrón fijo, no depende del jugador): se
numeran 1..11 agrupando por posición en ESTE orden fijo — **delanteros ->
centrocampistas -> defensas -> portero** (portero SIEMPRE el último slot).
En un 4-4-2: slots 1-2 delanteros, 3-6 centrocampistas, 7-10 defensas, 11
portero. Ver `engine.lineup_optimizer.build_lineup_slots()`. `substitutes`
es UN suplente por categoría de posición (no una lista, solo 4 slots de
banquillo fijos en la UI) — ver `pick_substitutes()`.

**Pujar — CONFIRMADO AL 100%** (2026-08-15, interceptando la llamada POST
real del frontend al pujar por un jugador nuevo + réplica exacta con un
`offerid` real devuelto):

```
POST /communities/{communityId}/users/{userId}/offers
body: {"offers": [{"price": <amount>, "tradableid": <playerId>, "type": "NEW"}]}
```

Distinto de lo inferido inicialmente (`"tradable": {"id": ...}` anidado,
`"type": "PURCHASE"`) — el body real usa `"tradableid"` plano y `"type":
"NEW"` al crear (`"PURCHASE"` es el tipo que aparece luego en la oferta ya
creada al releerla, un concepto distinto). Además, `"offers"` es una
**lista**: la API admite pujar por varios jugadores en una sola llamada
(no usado todavía, ver TODO en `place_bid`).

**Importante**: la respuesta es HTTP 200 incluso si la oferta se rechaza a
nivel de negocio (ej. el jugador ya no está en el mercado, o **pujar por
debajo del precio/VM del jugador** — visto en producción el 2026-08-16) —
hay que mirar `response["response"][i]["status"]`, no solo el código HTTP.
`place_bid()` ya lo comprueba y lanza `ComunioOfferError` si no es `"OK"`.

Ese rechazo por precio bajo destapó un bug real en `bidding_strategy.py`:
el cap de seguridad de jornada podía recortar el importe calculado por
debajo del precio del jugador (p.ej. con el presupuesto de jornada casi
agotado por pujas anteriores en la misma pasada), y `decide_bid()` no lo
comprobaba antes de enviar — mandaba una puja condenada a ser rechazada.
Corregido: si el cap no llega al precio, no se puja por ese jugador en
este momento (mejor no pujar que fallar).

Este es el segundo caso (después de la alineación) en que el body real
difería de una inferencia razonable por convención en detalles no obvios
— buen recordatorio de que "parece razonable" no sustituye a probarlo.

**Retirar puja — CONFIRMADO AL 100%** (2026-08-15, interceptando la llamada
real del frontend al pulsar "Retirar oferta" + réplica exacta sobre otra
oferta real, comprobando después que desaparece de `get_offers()`):

```
PUT /communities/{communityId}/users/{userId}/offers/{offerId}
body: {}
```

Un tercer caso de inferencia incorrecta: se asumía **DELETE** por
convención REST (`game:offer:withdraw` sonaba a "borra este recurso"), pero
el verbo real es **PUT con body vacío** — el path ya identifica la oferta,
el verbo+URL es toda la instrucción que hace falta. Con esto, **ninguna de
las tres escrituras del bot depende ya de una convención sin probar**.

**Nota de privacidad de la captura:** el valor real del `access_token` nunca
se expuso a mí ni se registró en ningún sitio — se leyó únicamente dentro
del propio navegador (`localStorage.getItem(...)`) para construir el header
de peticiones de prueba, y el resultado que se me devolvió fue solo la
forma/valores de los datos de negocio (plantilla, mercado, ofertas — nada
sensible), nunca el token en sí. La extensión de captura además bloquea
activamente la lectura de cookies/tokens en otros contextos, y esa
protección se respetó tal cual en vez de intentar sortearla.

## Stats externas: Understat sí, FBref no

FBref bloquea cualquier petición simple con un reto Cloudflare (`403 Just a
moment...`) — no es viable desde `requests`/GitHub Actions sin meter un
navegador headless completo, así que se descartó (decisión del usuario,
2026-08-15).

Understat, en cambio, expone un endpoint JSON real que la propia web usa
por AJAX — nada de parsear HTML ni `<script>` embebidos:

```
GET https://understat.com/getLeagueData/{league}/{season}
-> {"teams": {...}, "players": [...], "dates": [...]}
```

Verificado contra `La_liga/2025`: 600 jugadores, 20 equipos, con xG, xA,
minutos, goles, asistencias, tarjetas y posición por jugador — cubre casi
todo lo que iba a aportar FBref. El calendario (`dates[].forecast`) da
además una señal directa de dificultad del próximo rival para
`lineup_optimizer.py`.

Nota de temporada: `current_season()` calcula la temporada vigente por
fecha, pero justo al arrancar una temporada nueva Understat puede tardar
unos días en publicar datos (`players: []`) — **confirmado en producción**
el primer día de la temporada 2026/27 (0 jugadores en "2026" vs 600 en la
"2025" recién terminada). `get_league_data_with_fallback()` cae
automáticamente a la temporada anterior en ese caso (aproximación
temporal, `jobs/sync_data.py` notifica cuándo lo está haciendo) — ojo:
un fichaje nuevo en La Liga esta temporada no va a cruzar por nombre
contra datos de la temporada pasada, así que la tasa de cruce con
Understat baja temporalmente hasta que Understat publique la actual.

**Lesiones/dudas**: resuelto directamente con campos reales de Comunio, sin
depender de una tercera fuente. Cada jugador de `squad`/`market` trae
`status` (`"ACTIVE"` | `"WEAKENED"` | `"INJURED"`, no se ha visto un valor
de sanción en esta muestra) + `statusInfo` en texto libre (ej. "Lesión
muscular", "Fractura de peroné") — ya mapeado en `db/models.py`
(`comunio_snapshots.status`/`status_info`).

## Economía de Comunio: sin ingreso pasivo, y saldo negativo = 0 puntos

Investigado (2026-08-16, con fuentes) al preguntarnos cuál es la
estrategia ganadora en Comunio, porque afecta directamente al diseño de
seguridad del bot:

- **No hay ingreso pasivo**: la única forma de ganar dinero es vendiendo
  jugadores ([FAQ oficial](https://magazine.comunio.es/faq-comunio-10-dudas-muy-frecuentes-entre-los-managers/)).
  La estrategia clásica es especular con el valor de mercado (que fluctúa
  como una bolsa: oferta/demanda + rendimiento reciente, máx. ±15%/día o
  ±250k si el jugador vale <1,6M — [ComunioMagazine](https://magazine.comunio.es/los-valores-de-mercado-en-comunio-como-funcionan/)):
  comprar barato/infravalorado, esperar a que suba, vender con beneficio
  ([Comuniate](https://www.comuniate.com/noticias/251/como-funcionan-las-variaciones-de-precio-en-comunio-incluye-video-explicativo)).
  **Ya implementado** en `engine/selling_strategy.py` + `jobs/run_sales.py`
  (ver sección dedicada más abajo).
- **Regla crítica de seguridad**: según la misma FAQ oficial, **si tu
  saldo está en negativo al cerrar una jornada, no puntúas esa jornada
  entera (0 puntos)**, sea cual sea tu alineación. Esto es más grave que
  cualquier otro límite de seguridad ya implementado.

**Bug real corregido a raíz de esto**: Comunio no descuenta el saldo
(`credit`) al colocar una oferta de compra, solo cuando se EJECUTA al
cerrar el periodo de transferencias — que puede durar más de un día (visto
en la UI: "Desde 15.08 · Hasta 16.08"). El bot solo restaba
`get_bids_risked_today()` (lo arriesgado HOY según nuestra propia BD) del
presupuesto disponible, así que una oferta pendiente de un día anterior
sin resolver todavía no se tenía en cuenta — el bot podía comprometer más
dinero del que el saldo real soportaba si varias ofertas de días distintos
se ejecutaban a la vez, dejando el saldo en negativo.

Corregido con `clients.comunio_client.total_pending_purchase_amount()`,
que suma TODAS las ofertas de compra pendientes sin resolver directamente
desde `get_offers()` (la fuente de verdad real de Comunio, no una
aproximación local) y se resta del presupuesto ANTES que cualquier otro
límite, en `engine.bidding_strategy.max_biddable_amount()`. Demostrado con
un escenario límite (17,5M ya comprometidos de 20M de saldo): sin el fix,
el peor caso dejaba el saldo en -2,9M; con el fix, se queda en +2,35M pase
lo que pase. `get_bids_risked_today()` sigue existiendo, pero ahora solo
como ritmo de gasto por jornada (autoimpuesto), no como protección de
saldo — esa responsabilidad es de `total_pending_purchase_amount()`.

## Vender jugadores (`engine/selling_strategy.py`, `jobs/run_sales.py`)

**Poner en venta y quitar de la venta — CONFIRMADO AL 100%** (2026-08-16,
interceptando las llamadas reales del frontend en la pestaña "Ventas" +
réplica exacta):

```
POST /communities/{communityId}/users/{userId}/exchangemarket/addplayer
body: {"items": [{"tradableId": <playerId>, "price": <asking_price>}]}
respuesta real: {"status": "OK", "notPlaced": [], "purchasePrices": {...}, "remaining": <int>}

POST /communities/{communityId}/users/{userId}/exchangemarket/removeplayer
body: {"tradableIds": [<playerId>]}
```

**Importante — NO es venta instantánea**: poner en venta solo hace al
jugador visible para que alguien (otro manager o el "Computer") lo compre
después — el saldo no cambia al listar. `jobs/sync_data.py` reconcilia el
resultado comparando la plantilla en cada sync (si el jugador ya no está,
se marca `sales.status = 'sold'`) — misma limitación que con las pujas: no
se puede distinguir con los datos de la API una venta real de una
retirada manual sin vender.

`"remaining"` en la respuesta parece un límite diario de acciones de
mercado (añadir/quitar), sin confirmar el número exacto ni qué pasa al
agotarlo. `"purchasePrices"` trajo un valor que no coincidía con el precio
pedido en la prueba real (180.000 pedido -> 199.500 en la respuesta) —
sin confirmar qué representa, no se usa todavía para nada.

**Qué vender**: `get_squad()` trae el precio real de compra en
`purchaseInfo.price` (confirmado por captura real; `null` si el jugador es
de la plantilla inicial, nunca comprado por el bot). `decide_sales()`
compara ese precio contra el valor de mercado actual (`quotedprice`) y
decide vender si la plusvalía supera `config.SELLING_MIN_PROFIT_PCT`
(10% por defecto, sin calibrar todavía) — nunca se fuerza la venta de un
jugador sin precio de compra real conocido.

**Bloqueo duro de riesgo de plantilla** (encontrado al preguntarnos si la
venta tenía en cuenta quedarte sin cubrir una posición — no lo tenía):
vender es tan capaz de dejarte una posición sin cobertura (-4 puntos) como
que te "clausulen" a alguien, con la diferencia de que esta la causa el
propio bot y es 100% evitable. `decide_sales()` reutiliza
`engine.squad_risk.assess_squad_depth()` y **nunca vende** un jugador si
eso deja su posición sin margen de suplentes sanos, por rentable que sea
la operación — a diferencia del empujón blando de `apply_position_priority()`
en las pujas, aquí es un bloqueo duro: ninguna plusvalía compensa quedarte
con un hueco en la alineación. Si hay varios candidatos rentables en la
misma posición y no hay margen para vender a todos, se prioriza al de
mayor plusvalía. Un jugador lesionado/sancionado rentable sí se puede
vender sin restricción (no contaba como "disponible" para cubrir la
posición de todos modos). Probado en los 4 casos: venta bloqueada (único
sano de su posición), prioridad entre dos candidatos por la misma
posición con margen para solo uno, venta libre en posición con sobra, y
venta de un lesionado rentable.

## Cláusula de rescisión y riesgo de plantilla (`engine/squad_risk.py`)

Comunio permite (si la liga lo activa) que **cualquier manager fiche un
jugador de tu plantilla sin tu aprobación**, pagando un múltiplo de su
valor de mercado (x1,2 a x5, configurable por la liga). Si eso te deja sin
jugadores suficientes para cubrir una posición de tu alineación, Comunio
te penaliza con **-4 puntos por esa posición vacía** esa jornada
([FAQ oficial](https://classic.comunio.es/faq.phtml)). La liga de pruebas
usada en toda la sesión NO la tiene activada — es una función de pago
(Comunio Plus/Pro Player, visto en `Ajustes → Administrar liga → Reglas
del mercado → Cláusula de rescisión`, con badge "PRO" y el toggle inerte
sin la suscripción) fuera de alcance por la restricción de coste 0 del
proyecto. **La liga real del usuario sí la tendrá activada.**

`engine/squad_risk.py` vigila el riesgo estructural: para cada posición,
cuántos jugadores disponibles (sin lesión/sanción) hay por encima de los
titulares necesarios en `config.DEFAULT_FORMATION`. Si una posición se
queda sin ningún suplente sano, perder a su único titular por cualquier
motivo (cláusula, lesión, sanción) deja un hueco automático.
`jobs/set_lineup.py` lo comprueba en cada ejecución y lo incluye en la
notificación de Telegram.

**Ya conectado a las pujas**: `jobs/run_market.py` calcula el mismo riesgo
sobre tu plantilla antes de evaluar el mercado y usa
`engine.bidding_strategy.apply_position_priority()` para subir el score de
los candidatos en posiciones en riesgo (`config.BIDDING_POSITION_RISK_BOOST`,
aditivo — no fuerza la puja de un candidato malo, solo le da ventaja frente
a otro de score similar en una posición ya cubierta). La auditoría en
`bids.reason` deja constancia de cuándo una puja vino priorizada así.

TODO cuando se active en la liga real: si la API expone el importe exacto
de la cláusula por jugador (probable — el campo `hasAcceptedBuyoutClauseOffer`
ya aparece en el JSON de squad incluso con la función desactivada, así que
la estructura de datos ya existe), se podría afinar el riesgo con el coste
real de "salvar" cada posición, no solo un aviso binario.

## Pujas vs. alineación: pesos distintos a propósito

`engine/evaluator.py` acepta un `weights` opcional en `score_player()`/
`rank_players()`/`evaluate_players()` porque **el mismo score no vale para
las dos decisiones**:

- **Pujar** (`config.EVALUATOR_WEIGHTS`): el precio importa — es relación
  calidad/precio con presupuesto limitado que repartir entre candidatos del
  mercado.
- **Elegir alineación** (`config.LINEUP_EVALUATOR_WEIGHTS`, sin
  `comunio_points_per_price`): el precio NO debe importar — un jugador de
  tu plantilla ya está comprado, es coste hundido. Se detectó probando
  `jobs/set_lineup.py` con datos sintéticos: reutilizar los pesos de puja
  hacía que un delantero caro y muy productivo (Mbappe, en la prueba)
  saliera peor puntuado que suplentes baratos solo por ser caro — un sesgo
  real que habría llevado a bancar al mejor jugador de la plantilla.

## Setup local

Requiere Python 3.11+ (el `venv/` de este repo, gestionado por PyCharm, usa 3.14).

```bash
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y rellenar credenciales
python -m db.models    # crea db/comunio.db con el esquema
```

## Activar el cron en GitHub Actions

`.env` es SOLO para ejecuciones locales — GitHub Actions no lo lee. El cron
necesita esto en el repo de GitHub (`Settings` del repo, no en el código):

1. **Secrets** (`Settings → Secrets and variables → Actions → Secrets`,
   cifrados, nunca visibles en logs): `COMUNIO_EMAIL`, `COMUNIO_PASSWORD`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
2. **Variables** (misma sección, pestaña `Variables` — no son secretas, solo
   IDs): `COMUNIO_COMMUNITY_ID`, `COMUNIO_USER_ID`. Para la liga de pruebas:
   `5243734` / `21161679` (capturados durante la sesión).
3. Hacer `git push` de este repo a `origin` (el agente que escribió este
   código no tiene acceso de push desde este entorno — hace falta hacerlo
   manualmente o darle acceso).
4. Los 4 workflows (`sync_data` cada hora, `run_market` y `run_sales`
   2x/día, `set_lineup` viernes 18:00 UTC) ya tienen el `schedule:`
   activado — correrán solos en cuanto 1-3 estén hechos. Cada uno comitea
   `db/comunio.db`/`logs/` de vuelta al repo al terminar (si no, cada
   ejecución perdería lo sincronizado en la anterior).

**Antes de apuntar esto a una liga real** (no la de pruebas): revisar unos
días de ejecución en la de pruebas primero, y tener en cuenta que
`ENABLE_LINEUP_AUTO_SUBMIT=true` por defecto — el bot escribirá de verdad,
sin confirmación manual, en cuanto el cron esté activo.

## Estructura

Ver el detalle de cada módulo en su propio docstring. Resumen:

```
clients/    -> integraciones externas (Comunio, Understat)
db/         -> esquema SQLite + conexión
engine/     -> evaluator, bidding_strategy, lineup_optimizer, squad_risk, selling_strategy
jobs/       -> entrypoints ejecutados por cron (sync_data, run_market, run_sales, set_lineup)
notifier.py -> resumen por Telegram tras cada job
logs/       -> auditoría de decisiones
```
