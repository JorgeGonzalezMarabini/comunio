# Comunio Liga Bot

Bot de gestión automática de un equipo en Comunio (liga privada): fichajes/pujas,
evaluación de jugadores (Comunio + stats externas), alineaciones automáticas y
notificación de cada acción. Coste 0: sin APIs de pago ni VPS de pago.

## Estado actual

- [x] Captura de endpoints reales de Comunio — hecha el 2026-08-15 con Chrome DevTools sobre una liga de prueba real
- [x] Cliente de Comunio (`clients/comunio_client.py`) — login (esquema `Bearer` **confirmado** con petición real autenticada) y lectura (standings/squad/market/offers/lineup) contra endpoints reales; pujar/guardar alineación/retirar puja con path confirmado (por captura + por los `_links` HATEOAS de la propia API), verbo/body por convención razonable sin confirmar al 100%
- [x] Cliente de stats externas (`clients/laliga_stats_client.py`) — Understat implementado contra su endpoint JSON real (`getLeagueData`); **FBref descartado** (bloquea con Cloudflare, ver abajo)
- [x] Esquema de base de datos (`db/models.py`) — campos alineados con Understat Y con el JSON real de Comunio (squad/market), incluyendo normalización de posición y del "-" de puntos en pretemporada
- [x] Motor de evaluación (`engine/evaluator.py`) — conectado a datos reales: `normalize_pool()`/`evaluate_players()` parten de `db.models.get_player_features()` (SQL con último snapshot de Comunio + Understat por jugador) y normalizan cada feature 0..1 dentro del pool; pesos configurables en `config.py`, sin calibrar todavía contra resultados reales de liga
- [x] Estrategia de pujas (`engine/bidding_strategy.py`) — el importe ahora se ancla al VM/precio real del jugador (antes era una fracción arbitraria del presupuesto, sin relación con lo que costaba de verdad) + una prima que escala con el score, siempre topado por los límites de seguridad de `config.py`; `decide_bids_for_market()` decide varias pujas de una tacada respetando el riesgo acumulado en la misma pasada
- [x] Optimizador de alineaciones (`engine/lineup_optimizer.py`) — formaciones básicas (formato humano "4-4-2", con `to_api_tactic()` para convertir al "442" real de la API), falta incorporar dificultad del rival (ya disponible vía `laliga_stats_client.team_fixture_difficulty`)
- [x] `jobs/sync_data.py` — mapeo real Comunio+Understat -> `db/models.py` implementado y probado con datos sintéticos que replican las formas reales capturadas (login real pendiente de probar en este entorno, no hay credenciales cargadas)
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

**Guardar alineación**: reutiliza el path de `get_lineup()`
(`.../users/{userId}/lineup`, patrón PUT-replace). Ojo con dos cosas reales
confirmadas: `tactic` va **sin guiones** ("442", no "4-4-2" — ver
`to_api_tactic()` en `lineup_optimizer.py`), y los titulares van en
`items.lineup` como **mapa por número de slot** (ej. portero=slot 11,
un defensa=slot 7 en la prueba real), no como lista plana — la numeración
completa de los 11 slots aún no está terminada de mapear (solo se probaron
2 jugadores).

Verbo HTTP exacto (POST/PUT) y nombres de campo del body en ambos casos:
implementados por convención REST razonable, **sin confirmación al 100%**
(no hay forma de leer verbo/body reales sin repetir el submit). Si
`place_bid`/`set_lineup` devuelven 4xx, revisar esto primero.

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

## Setup local

Requiere Python 3.11+ (`.venv` de este repo usa 3.14).

```bash
python3.14 -m venv .venv
source .venv/bin/activate
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
