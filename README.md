# Comunio Liga Bot

Bot de gestión automática de un equipo en Comunio (liga privada): fichajes/pujas,
evaluación de jugadores (Comunio + stats externas), alineaciones automáticas y
notificación de cada acción. Coste 0: sin APIs de pago ni VPS de pago.

## Estado actual

- [x] Captura de endpoints reales de Comunio — hecha el 2026-08-15 con Chrome DevTools sobre una liga de prueba real
- [x] Cliente de Comunio (`clients/comunio_client.py`) — login (esquema `Bearer` **confirmado** con petición real autenticada) y lectura (standings/squad/market/offers/lineup) contra endpoints reales; **guardar alineación confirmado al 100%** (verbo, body exacto y numeración de los 11 slots, ver abajo); pujar/retirar puja con path confirmado (por captura + `_links` HATEOAS), verbo/body por convención razonable sin confirmar al 100%
- [x] Cliente de stats externas (`clients/laliga_stats_client.py`) — Understat implementado contra su endpoint JSON real (`getLeagueData`); **FBref descartado** (bloquea con Cloudflare, ver abajo)
- [x] Esquema de base de datos (`db/models.py`) — campos alineados con Understat Y con el JSON real de Comunio (squad/market), incluyendo normalización de posición y del "-" de puntos en pretemporada
- [x] Motor de evaluación (`engine/evaluator.py`) — conectado a datos reales: `normalize_pool()`/`evaluate_players()` parten de `db.models.get_player_features()` (SQL con último snapshot de Comunio + Understat por jugador) y normalizan cada feature 0..1 dentro del pool; pesos configurables en `config.py`, sin calibrar todavía contra resultados reales de liga
- [x] Estrategia de pujas (`engine/bidding_strategy.py`) — el importe ahora se ancla al VM/precio real del jugador (antes era una fracción arbitraria del presupuesto, sin relación con lo que costaba de verdad) + una prima que escala con el score, siempre topado por los límites de seguridad de `config.py`; `decide_bids_for_market()` decide varias pujas de una tacada respetando el riesgo acumulado en la misma pasada
- [x] Optimizador de alineaciones (`engine/lineup_optimizer.py`) — formaciones básicas (formato humano "4-4-2", con `to_api_tactic()` para convertir al "442" real de la API); **dificultad del rival ya incorporada** vía `apply_fixture_difficulty()` + `laliga_stats_client.next_match_difficulty()` (forecast de Understat, verificado que siempre es en perspectiva del equipo local)
- [x] `jobs/sync_data.py` — mapeo real Comunio+Understat -> `db/models.py` implementado y probado con datos sintéticos que replican las formas reales capturadas (login real pendiente de probar en este entorno, no hay credenciales cargadas)
- [x] `jobs/run_market.py` — pipeline completo: candidatos de mercado -> evaluator -> bidding_strategy -> `place_bid()` real, con presupuesto (`credit`) y riesgo ya comprometido hoy leídos de Comunio/BD; cada intento fallido se audita sin tumbar los demás. Probado de punta a punta con un `ComunioClient` simulado
- [x] `jobs/set_lineup.py` — pipeline completo: plantilla -> evaluator (con **pesos distintos a los de puja**, ver nota abajo) -> dificultad de rival -> `pick_lineup()` -> `build_lineup_slots()`/`pick_substitutes()` -> `set_lineup()` real. La decisión SIEMPRE se audita en `lineup_decisions`; el envío real a Comunio está **activado por defecto** (`config.ENABLE_LINEUP_AUTO_SUBMIT=true`, con opción de desactivarlo en `.env`) ahora que el mapeo de slots está confirmado al 100%
- [x] Jobs y scheduler en GitHub Actions — YAMLs listos en modo manual (`workflow_dispatch`), cron comentado hasta tener credenciales
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

**Pujar**: verbo HTTP exacto (POST) y nombres de campo del body siguen
implementados por convención REST razonable, **sin confirmación al 100%**
(a diferencia de la alineación, no se ha repetido el submit interceptando
la llamada real para no generar pujas de más). Si `place_bid` devuelve 4xx,
revisar esto primero — el patrón de la alineación (body real distinto del
inferido: `lineup` en la raíz, no anidado bajo `items`, y faltaba
`substitutes`) es un buen recordatorio de que las inferencias por
convención pueden fallar en detalles no obvios.

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

Nota de temporada: `current_season()` en `laliga_stats_client.py` calcula la
temporada vigente por fecha, pero justo al arrancar una temporada nueva
Understat puede tardar unos días en publicar datos (`players: []`) — el job
debe manejarlo sin romper, no asumir que siempre habrá datos.

**Lesiones/dudas**: resuelto directamente con campos reales de Comunio, sin
depender de una tercera fuente. Cada jugador de `squad`/`market` trae
`status` (`"ACTIVE"` | `"WEAKENED"` | `"INJURED"`, no se ha visto un valor
de sanción en esta muestra) + `statusInfo` en texto libre (ej. "Lesión
muscular", "Fractura de peroné") — ya mapeado en `db/models.py`
(`comunio_snapshots.status`/`status_info`).

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

## Estructura

Ver el detalle de cada módulo en su propio docstring. Resumen:

```
clients/    -> integraciones externas (Comunio, Understat)
db/         -> esquema SQLite + conexión
engine/     -> evaluator, bidding_strategy, lineup_optimizer
jobs/       -> entrypoints ejecutados por cron (sync_data, run_market, set_lineup)
notifier.py -> resumen por Telegram tras cada job
logs/       -> auditoría de decisiones
```
