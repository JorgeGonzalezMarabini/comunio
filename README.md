# Comunio Liga Bot

Bot de gestión automática de un equipo en Comunio (liga privada): fichajes/pujas,
evaluación de jugadores (Comunio + stats externas), alineaciones automáticas y
notificación de cada acción. Coste 0: sin APIs de pago ni VPS de pago.

## Estado actual

- [x] Captura de endpoints reales de Comunio — hecha el 2026-08-15 con Chrome DevTools sobre una liga de prueba real
- [x] Cliente de Comunio (`clients/comunio_client.py`) — login y lectura (standings/squad/market/offers/lineup) implementados contra endpoints reales; pujar y guardar alineación **validados con una prueba real controlada** (path confirmado, verbo/body por convención REST sin poder confirmar al 100%)
- [x] Cliente de stats externas (`clients/laliga_stats_client.py`) — Understat implementado contra su endpoint JSON real (`getLeagueData`); **FBref descartado** (bloquea con Cloudflare, ver abajo)
- [x] Esquema de base de datos (`db/models.py`) — campos alineados con lo confirmado de Understat; nombres exactos del JSON de Comunio pendientes (ver nota abajo)
- [x] Motor de evaluación (`engine/evaluator.py`) — pesos configurables en `config.py`, normalización pendiente de calibrar
- [x] Estrategia de pujas (`engine/bidding_strategy.py`) — límites de seguridad configurables en `config.py`
- [x] Optimizador de alineaciones (`engine/lineup_optimizer.py`) — formaciones básicas (formato real "4-4-2"), falta incorporar dificultad del rival (ya disponible vía `laliga_stats_client.team_fixture_difficulty`)
- [~] Jobs (`jobs/sync_data.py` etc.) — orquestación real escrita; falta mapear el JSON real de squad/market de Comunio a `db/models.py` (bloqueado por lo mismo: no se ha podido inspeccionar el body sin exponer el token)
- [x] Jobs y scheduler en GitHub Actions — YAMLs listos en modo manual (`workflow_dispatch`), cron comentado hasta tener credenciales
- [x] Notificaciones por Telegram (`notifier.py`)

## Endpoints reales de Comunio (capturados 2026-08-15)

Dos dominios distintos: `www.comunio.es` es el frontend (Next.js, no se usa
desde el bot) y `https://api.comunio.es` es la API REST real que sí se usa.

Auth: NO es cookie de sesión — `POST /login` devuelve `access_token` /
`refresh_token` que el frontend guarda en `localStorage` y reenvía en cada
llamada como header `Authorization` (esquema asumido `Bearer`, sin confirmar
el prefijo literal a propósito — ver nota de privacidad más abajo).

| Acción | Endpoint |
|---|---|
| Login | `POST /login` — body `{username, password, tzoffset}` |
| Estado de sesión | `GET /login/state` |
| Plantilla | `GET /users/{userId}/squad` |
| Clasificación | `GET /communities/{communityId}/standings?period=total&wpe=true` |
| Miembros de la liga | `GET /communities/{communityId}/members` |
| Mercado (compra/venta) | `GET /communities/{communityId}/users/{userId}/exchangemarket` |
| Ofertas/pujas activas | `GET /communities/{communityId}/users/{userId}/offers` |
| Alineación actual | `GET /communities/{communityId}/users/{userId}/lineup` |
| Logout | `POST /communities/{communityId}/users/{userId}/logout` — body `{userId}` |

**Pujar y guardar alineación**, validado el 2026-08-15 con una prueba real
controlada (con permiso explícito, en la liga de prueba): ambas acciones
reutilizan el mismo path que su GET correspondiente (`.../offers` y
`.../lineup`), patrón REST típico de create/replace. El verbo HTTP exacto
(POST/PUT) y los nombres de campo del body están implementados por
convención razonable pero **sin confirmación al 100%** — la Performance API
del navegador usada para capturar no expone ni el verbo ni el body real. Si
`place_bid`/`set_lineup` devuelven 4xx, revisar esto primero.

**Nota de privacidad de la captura:** el valor real del `access_token` nunca
se leyó ni se registró en ningún sitio — la extensión de captura bloquea
activamente la lectura de cookies/tokens, y esa protección se respetó tal
cual en vez de intentar sortearla. Solo se registraron nombres de endpoints,
métodos, nombres de cabecera y nombres de campo del payload.

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

**Lesiones/dudas**: ninguna de las dos fuentes externas lo resuelve bien.
Se usará el propio estado que ya marca Comunio en su plantilla/mercado (se
ve un icono en la UI) una vez se confirmen los nombres de campo exactos del
JSON — evita depender de una tercera fuente para algo que ya tenemos.

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
