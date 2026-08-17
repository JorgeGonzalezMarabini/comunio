# Futmondo Liga Bot

Bot de gestión automática de un equipo en Futmondo (liga privada, modo
"Social"): fichajes/pujas, evaluación de jugadores (Futmondo + stats
externas), alineaciones automáticas y notificación de cada acción. Coste
0: sin APIs de pago ni VPS de pago.

**Nota de historia**: este proyecto empezó como un bot de Comunio (ver
`git log` para esa fase) y se migró por completo a Futmondo el
2026-08-17, a petición del usuario. Nada de Comunio queda en el código —
API, esquema de BD y engine están reescritos para Futmondo. Se deja esta
nota porque parte del razonamiento de diseño (p.ej. por qué el precio no
debe importar al elegir alineación, o por qué vender jugadores no debe
dejar una posición sin cobertura) se heredó tal cual de esa fase por
seguir siendo válido, no por descuido.

## Estado actual

- [x] Captura de endpoints reales de Futmondo — hecha el 2026-08-17 con Chrome DevTools sobre una liga de prueba real ("Liga de prueba bot", modo Social, creada para la propia sesión de captura). **Ojo**: la plantilla de esa liga de prueba resultó ser toda de equipos de Premier League (confirmado el mismo día revisando `players.team` en la BD), no de LaLiga — irrelevante para todo lo que ya usa `league="La_liga"` (Understat, Fotmob) mientras la liga REAL de destino sea LaLiga (confirmado con el usuario), pero explica por qué una prueba en vivo de `manage_substitutes.py` contra esta liga de prueba no encuentra nunca partidos de LaLiga para estos jugadores.
- [x] Cliente de Futmondo (`clients/futmondo_client.py`) — lectura (plantilla/mercado/alineación/ficha de jugador) y escritura (pujar, poner en venta, cambiar alineación) contra endpoints reales, cada uno marcado explícitamente como confirmado con captura propia o heredado sin verificar de la referencia comunitaria (ver detalle abajo)
- [x] Cliente de stats externas (`clients/laliga_stats_client.py`) — sin cambios frente a la fase de Comunio: Understat contra su endpoint JSON real; FBref descartado (bloquea con Cloudflare)
- [x] Esquema de base de datos (`db/models.py`) — reescrito para los campos reales de Futmondo (`value`/`buyPrice`/`role` en vez de `quotedprice`/`purchaseInfo`/posición en inglés)
- [x] Motor de evaluación (`engine/evaluator.py`) — misma lógica que en la fase de Comunio (normaliza features 0..1 dentro del pool, pesos en `config.py`), adaptada a los nombres de columna nuevos
- [x] Estrategia de pujas (`engine/bidding_strategy.py`) — misma lógica de seguridad (tope por jugador, % de presupuesto por jornada, reserva mínima, prima sobre el VM real) — la protección contra saldo negativo se mantiene por precaución aunque en Futmondo no se ha podido confirmar la regla exacta de penalización (ver sección dedicada)
- [x] Optimizador de alineaciones (`engine/lineup_optimizer.py`) — **reescrito de cero**: Futmondo numera los slots de la alineación de forma totalmente distinta a Comunio (enteros 0..10 en vez de un string de posición + categoría de banquillo), confirmado solo para la formación 4-4-2 (ver TODO en el propio módulo)
- [x] `jobs/sync_data.py` — mapeo real Futmondo + Understat -> `db/models.py`; reconcilia pujas/ventas comparando plantilla y mercado actuales (sin endpoint externo de "mis ofertas", a diferencia de Comunio — ver sección dedicada); cruce con Understat por cascada de fiabilidad (nombre completo -> apellido+equipo -> apellido único -> similitud de texto), no solo nombre exacto — ver sección dedicada
- [x] `jobs/run_market.py` — pipeline completo: candidatos de mercado -> evaluator -> bidding_strategy -> `place_bid()` real
- [x] `jobs/set_lineup.py` — pipeline completo: plantilla -> evaluator (pesos distintos a los de puja) -> dificultad de rival -> `pick_lineup()` -> `build_lineup_changes()`/`build_bench_changes()` -> `change_lineup()` real, titulares y un suplente por posición (slot fijo confirmado, ver sección dedicada)
- [x] `jobs/run_sales.py` — identifica jugadores con plusvalía suficiente (`engine/selling_strategy.py`) y los pone en venta — **cambio de comportamiento deliberado** frente a Comunio: ya no se filtra por "solo comprados por el bot" (ver sección dedicada)
- [x] `jobs/manage_substitutes.py` — sustitución MANUAL de un titular confirmado fuera (lesionado/en duda, o no incluido en el once real de su equipo hoy) por su suplente ya asignado en el banquillo (`engine.lineup_optimizer.build_substitution_changes()`), para ligas sin el "entrenador automático" de pago activado (ver sección "Banquillo/suplentes"); `config.ENABLE_SUBSTITUTE_AUTO_SUBMIT` **activado** desde el 2026-08-17 (decisión explícita del usuario), aunque la secuencia de sustitución en sí sigue sin confirmarse todavía con un caso real
- [x] Cliente de alineaciones reales (`clients/football_lineups_client.py`, Fotmob) — detecta titulares sanos no incluidos en el once real de su equipo (rotación, no solo lesión); Fotmob elegida tras descartar en vivo API-Football (plan gratuito sin acceso a temporada en curso), SofaScore (403 en todo, bloqueo de bot a nivel de borde igual que FBref), ESPN (403 Akamai) y TheSportsDB (datos de alineación corruptos); detrás de `config.ENABLE_REAL_LINEUP_CHECK` (**false por defecto**, sin key ni cuenta que configurar) — ver sección "Banquillo/suplentes" para el TODO pendiente (timing exacto de confirmación, estabilidad a medio plazo de una API no oficial)
- [~] Jobs y scheduler en GitHub Actions — los 5 YAMLs están actualizados a las variables de entorno de Futmondo (`FUTMONDO_*`); **pendiente**: configurar los nuevos Secrets/Variables en GitHub (ver sección "Activar el cron")
- [x] Notificaciones por Telegram (`notifier.py`) — sin cambios, es independiente de la plataforma

## Por qué Futmondo y no Comunio

Decisión del usuario (2026-08-17): el proyecto pasa a gestionar una liga
real de Futmondo, no de Comunio. Dado que ambas plataformas tienen APIs
completamente distintas (dominios, forma de autenticación, nombres de
campo, mecánica de alineación...), se optó por sustituir Comunio del todo
en vez de mantener ambos proveedores en paralelo — más simple de mantener
para un proyecto de una sola liga.

## Endpoints reales de Futmondo (capturados 2026-08-17)

Un único dominio: `https://api.futmondo.com` — a diferencia de Comunio no
hay separación frontend/API (la web, `app.futmondo.com`, es una app
Flutter Web que habla contra este mismo dominio).

**Auth — sin login conocido**: `token` y `userid` no se obtienen de un
`POST /login` (no existe, ni en captura propia ni en la referencia
comunitaria de [vicenteqa/futmondo-utils](https://github.com/vicenteqa/futmondo-utils),
que documenta buena parte de esta misma API). Se capturan a mano iniciando
sesión una vez en `app.futmondo.com` y leyendo
`localStorage.getItem("flutter.token")` / `"flutter.id_user"` desde la
consola del navegador — la app es Flutter Web, guarda ahí la sesión igual
que el frontend de Comunio guardaba su `access_token` en `localStorage`.
No se ha confirmado cuánto dura el token ni si refresca solo.

Todas las llamadas observadas son **POST**, incluso las de solo lectura,
con `token`/`userid` en el **body** (no en un header `Authorization` como
Comunio): `{"header": {"token": ..., "userid": ...}, "query": {"championshipId": ..., "userteamId": ..., ...}}`.

| Acción | Endpoint | Confirmado |
|---|---|---|
| Plantilla | `POST /1/userteam/roster` | ✅ captura propia |
| Resumen del equipo (budget, teamValue...) | `POST /1/userteam/information` | ✅ captura propia |
| Alineación actual | `POST /1/userteam/lineup` | ✅ captura propia |
| Cambiar alineación | `POST /2/userteam/changeplayer` | ✅ captura propia |
| Mercado de fichajes | `POST /1/market/players` | ✅ captura propia |
| Mis jugadores en venta | `POST /1/market/myplayers` | ✅ captura propia |
| Pujar | `POST /1/market/bid` | ✅ captura propia |
| Poner en venta | `POST /1/market/putonmarket` | ✅ captura propia |
| Ficha de jugador (+ histórico de precio) | `POST /1/player/summary` | ✅ captura propia |
| Quitar de la venta | `POST /1/market/cancelsell` | ⚠️ solo referencia comunitaria |
| Pagar cláusula | `POST /1/market/rosterclause` | ⚠️ solo referencia comunitaria |
| Ocultar en mercado | `POST /5/market/toggleplayer` | ⚠️ solo referencia comunitaria, no usado por el bot |
| Ofertas de cláusula sobre TU plantilla | `POST /1/market/rosterbids` | ✅ visto en captura, no usado por el bot |
| Equipos de la liga | `POST /1/league/championshipteams` | ✅ visto en captura, no usado por el bot |
| Plantillas de todos los managers | `POST /5/league/championshipplayers` | ✅ visto en captura, no usado por el bot |

Ver el docstring de `clients/futmondo_client.py` para el detalle completo
de cada body/respuesta real, incluidos los campos exactos de cada
recurso (jugador de roster/mercado, respuesta de pujar, etc.).

**Pujar — CONFIRMADO AL 100%** (2026-08-17, puja real sobre un jugador del
mercado en la liga de prueba + relectura confirmando el precio):

```
POST /1/market/bid
body.query: {..., "player_slug": ..., "player_id": ..., "price": <amount>, "isClause": false}
respuesta real: {"answer": {"code": "api.general.ok"}, ...}
```

A diferencia de Comunio, la respuesta **no incluye un id de oferta** — no
hay forma de retirar una puja concreta por id, y el seguimiento de "pujas
pendientes propias" se hace por `player_id` contra la plantilla y el
mercado actuales (ver sección "Reconciliación de pujas" más abajo).

**Cambiar alineación — CONFIRMADO AL 100% solo para 4-4-2** (2026-08-17,
alineación real completa, cada jugador colocado interceptando la llamada
POST real del frontend + relectura con `GET .../lineup` confirmando la
posición numérica asignada):

```
POST /2/userteam/changeplayer
body.query.changes: [{"cpt": false, "to": <playerId>, "position": <int>,
                       "isBench": false, "multiposition": false}, ...]
respuesta real: {"answer": {"code": "api.general.ok", "budget": <int>, "rc": "-1"}, ...}
```

Numeración de `position` confirmada: enteros consecutivos **empezando en
0** (a diferencia de Comunio, que empezaba en 1), en el orden delanteros
-> centrocampistas -> defensas -> portero, portero SIEMPRE el último
índice (10 en un 4-4-2 con 11 titulares). Confirmado en la prueba real:
portero -> 10, tres defensas colocados -> 6, 7, 8.

**Tres bugs/límites reales encontrados el mismo día en producción (2026-08-17),
al notar que la alineación de Telegram no coincidía con la que se veía en
la web** — los tres documentados con detalle en el docstring de
`change_lineup()` / `build_lineup_changes()`:

1. Mandar los 11 cambios de golpe en una sola llamada hacía que Futmondo
   solo aplicara el primero, devolviendo igualmente `"api.general.ok"` —
   corregido mandando una llamada HTTP por jugador.
2. Sustituir un slot que ya tiene un jugador DISTINTO exige incluir
   `"from"` con el id de quien sale, si no la API lo rechaza con
   `"api.error.not_allowed"` — corregido leyendo `get_lineup()` antes de
   construir los cambios y solo generando `change` para los slots que de
   verdad hacen falta.
3. **Sin resolver todavía**: si el jugador que entra en un slot ya está
   en el campo en OTRA posición que también se está cambiando en la misma
   pasada (una rotación entre varios titulares, no una sustitución simple
   desde el banquillo), la API rechaza esos cambios con
   `"api.error.in_field"` pese a llevar el `"from"` correcto. Probablemente
   haga falta un paso intermedio (banquillo) para "liberar" al jugador
   antes de recolocarlo, pero eso depende de la numeración de slots del
   banquillo, sin confirmar (ver más abajo).

**Solo se soporta 4-4-2** (`engine/lineup_optimizer.FORMATIONS`): es la
única formación gratis y siempre disponible en Futmondo, confirmado en su
[FAQ oficial](https://help.futmondo.com/article/160-que-son-las-formaciones-extra-y-como-puedo-activarlas).
Futmondo sí ofrece "formaciones extra" de pago — **4-2-4, 3-6-1, 3-3-4,
4-6-0, 5-2-3** — pero contratadas por jornada suelta vuelven solas a
4-4-2 al terminar esa jornada (mal encaje para un bot automatizado que no
gestiona "mondos"), y su numeración de slots no está confirmada con
ninguna prueba real (la de `build_lineup_changes()` solo se verificó para
4-4-2) — no se añaden a `FORMATIONS` hasta que alguna se necesite de
verdad y se confirme igual que se hizo con 4-4-2. (Nota: `FORMATIONS`
tenía antes `4-3-3`/`3-4-3`/`5-3-2`, heredadas sin querer de la fase de
Comunio — ninguna de esas tres existe en Futmondo.)

**Banquillo/suplentes — CONFIRMADO AL 100%** (2026-08-17, los 4 añadidos
uno a uno desde la pestaña "Suplentes" de la web, interceptando cada POST
real + relectura con `GET .../lineup` confirmando la posición numérica de
cada uno en `bench.players`): a diferencia de los titulares, aquí hay un
slot FIJO por posición (no depende de la formación ni cambia de una
semana a otra):

```
0 = MED, 1 = DEL, 2 = POR, 3 = DEF   (con "isBench": true)
```

Solo hay sitio para **un** suplente por posición (no una lista) — igual
que en Comunio en su momento. `jobs/set_lineup.py` ya elige y manda un
suplente por posición (`engine.lineup_optimizer.pick_substitutes()` +
`build_bench_changes()`), con el mismo criterio de "from"/no repetir
cambios ya aplicados que los titulares (ver arriba) — aunque ese criterio
en concreto (sustituir un suplente ya puesto) no se ha probado en vivo
específicamente para banquillo, solo para titulares.

**Un lesionado/en duda no puede ser titular ni el suplente designado si
hay un sano disponible en su posición** (`engine.lineup_optimizer.
_rank_healthy_first()`, usado por `pick_lineup()` y `pick_substitutes()`,
2026-08-17, a petición del usuario). Antes de esto, `is_injured_or_doubtful`
solo restaba una penalización suave en el score
(`config.EVALUATOR_WEIGHTS["injury_penalty"] = 0.10`), así que un
lesionado con muy buenas stats previas podía seguir ganando en score a un
sano mediocre — y colarse no solo de titular, sino como EL suplente
designado de su propia posición, con lo que `build_substitution_changes()`
lo descartaba al llegar el momento de usarlo y no sustituía a nadie. Ahora
se prefiere siempre a un sano, y solo se recurre a un lesionado/en duda si
no queda ningún sano disponible en esa posición (mejor cubrir el hueco que
dejarlo vacío, mismo criterio que `engine/squad_risk.py`).

**"No juega" es más amplio que "lesionado" — fuente de alineaciones reales
(Fotmob)** (añadido 2026-08-17, a petición del usuario, ver
`clients/football_lineups_client.py`): `build_substitution_changes()`
detectaba como "confirmado fuera" solo a quien tuviera un `status` de
lesión/duda (`is_injury_status()`). Pero el entrenador automático real de
Futmondo sustituye a cualquier titular con **0 minutos jugados**, sea cual
sea el motivo — y en fútbol real la causa más frecuente no es la lesión,
sino que el entrenador del equipo real simplemente no lo pone esa jornada
(rotación, decisión táctica, sanción no reflejada como "lesión" en
`status`...).

**Cuatro fuentes gratuitas probadas en vivo el mismo día, en este orden,
cada una descartada por un motivo real distinto antes de llegar a la que
sí funciona:**

1. **API-Football (plan gratuito)** — BLOQUEADO: con una API key real,
   `GET /teams`/`GET /fixtures` con la temporada en curso (2026) devuelven
   `"errors": {"plan": "Free plans do not have access to this season, try
   from 2022 to 2024."}` — el plan gratuito solo da temporadas históricas
   (2022-2024), nunca la actual. No es un límite de volumen (100
   peticiones/día), es un bloqueo total de acceso a los datos que hacen
   falta. Confirmado de paso, con `season=2023` (dentro del rango
   permitido): el league id de LaLiga (140) sí era correcto.
2. **SofaScore** — BLOQUEADO más duro todavía: `api.sofascore.com` Y
   `www.sofascore.com` (la web entera) devuelven 403 en TODO, incluida la
   ruta interna que usa el propio frontend. Confirmado que es una huella
   de conexión (TLS/HTTP) y no cabeceras: la MISMA URL responde 200 desde
   un navegador Chrome real (probado con Claude in Chrome) y 403 desde
   curl/`requests` con cualquier User-Agent/Referer/sesión. Automatizarlo
   exigiría un navegador headless camuflado para pasar por humano ante un
   sistema anti-bot que activamente intenta impedirlo — un bypass
   deliberado que este proyecto no construye.
3. **ESPN** (`site.api.espn.com`) — BLOQUEADO igual (403 Akamai).
4. **TheSportsDB** (key de prueba pública `"3"`) — responde SIN bloqueo,
   pero la alineación de un partido real ya jugado (Espanyol 3-0 Levante,
   2026-08-16) vino con jugadores de OTROS equipos/temporadas mezclados
   (`strTeam: "Lille"`, `"Hellas Verona"`, `"_Retired Soccer"` en un
   partido de LaLiga) — descartada por integridad de datos, no por acceso.

También descartado **football-data.org**: su plan gratuito no incluye
alineaciones (`lineups`/`substitutions`/`cards` quedan fuera del free
tier según su propia documentación de precios) — solo fixtures/resultados.

**Fotmob (`www.fotmob.com`) es la elegida**: la única de las cinco que
combina acceso sin bloqueo Y datos correctos, ambas cosas confirmadas con
llamadas reales:

```
GET https://www.fotmob.com/api/data/matches?date=YYYYMMDD
    -> 200 sin ninguna protección anti-bot, sin key ni cuenta
    -> {"leagues": [{"id": 87, "name": "LaLiga",
                      "matches": [{"id": ..., "home": {"id","name"}, "away": {...}}]}]}

GET https://www.fotmob.com/api/data/matchDetails?matchId=...
    -> content.lineup: {"lineupType": "predicted" | "standard",
        "homeTeam": {"id","name","starters":[{"id","name",...}]}, "awayTeam": {...}}
```

`lineupType` distingue estimación (`"predicted"`, antes del partido) de
alineación REAL (`"standard"`) — confirmado comparando contra un partido
ya jugado (Espanyol 3-0 Levante): `lineupType` era `"standard"` y los
titulares listados eran correctos, sin mezclar jugadores de otros equipos
(justo lo que sí falló en TheSportsDB). `find_players_confirmed_out_of_real_lineup()`
cruza el once real contra nuestra plantilla (reutilizando la misma cascada
de nombres de `clients/laliga_stats_client.py`: exacto -> apellido+equipo
-> apellido único -> fuzzy) — cualquier titular nuestro que no aparezca
ahí, esté sano o no, se trata igual que uno lesionado en
`build_substitution_changes(..., confirmed_out_ids=...)`. Cachea el
resultado por (equipo, día) en `real_lineup_checks` para no abusar de un
servicio gratuito de terceros sin necesidad (Fotmob no publica un límite
de peticiones/día, a diferencia de API-Football).

Detrás de `config.ENABLE_REAL_LINEUP_CHECK` (**false por defecto**) — sin
key ni cuenta que configurar, a diferencia de API-Football.

**CONFIRMADO en vivo el mismo día (2026-08-17)**, con un monitor que
consultó `matchDetails` cada 5 min hasta el pitido: para Deportivo A
Coruña vs Elche (19:00 UTC), `lineupType` pasó de `"predicted"` a
`"standard"` a las **18:19 UTC — 41 minutos antes del pitido**. En ese
momento se ejecutó `jobs/manage_substitutes.py` de verdad (con
`ENABLE_SUBSTITUTE_AUTO_SUBMIT=false`, solo auditoría/notificación, sin
escribir en Futmondo) y terminó sin errores. No se pudo validar la
decisión de sustitución en sí porque la plantilla usada era la liga de
prueba (equipos de Premier League, no LaLiga — ver nota más abajo), pero
sí quedó confirmado que la consulta a Fotmob, la caché en
`real_lineup_checks` y el resto del flujo funcionan de punta a punta sin
fallar.

Con un solo dato de muestra no se sabe si 41 min es representativo o un
caso particular de este partido — pero **41 min ya deja claro que la
cadencia de 2h del cron era insuficiente**: un hueco de 2h entre
ejecuciones podía dejar pasar TODA la ventana entre "ya se sabe quién
juega" y "el partido ya empezó" sin que el job la viera. Por eso
`manage_substitutes.yml` pasó de `0 */2 * * 5,6,0,1` (cada 2h) a
`*/20 10-23 * * 5,6,0,1` (cada 20 min, 10:00-23:00 UTC) — un valor
conservador mientras se acumulan más casos reales, no uno calibrado con
varios partidos.

**TODO sin confirmar todavía**: si 41 min antes es representativo de
LaLiga en general (un solo dato no basta) — con más casos reales podría
ajustarse la cadencia de nuevo, hacia arriba o hacia abajo; y la
estabilidad a medio plazo de Fotmob, una API no oficial sin contrato ni
SLA igual que SofaScore — hoy responde sin bloqueo, pero podría empezar a
bloquear sin aviso en cualquier momento. Un fallo de esta fuente extra
(eso, red, cambio de forma de la respuesta...) no tumba la comprobación
por lesión — `manage_substitutes.py` avisa por Telegram y
sigue solo con `is_injury_status()`.

**IMPORTANTE — el suplente colocado no hace nada por sí solo**: según la
[FAQ oficial](https://help.futmondo.com/article/159-entrenador-automatico),
la sustitución real de un titular que no juega (0 minutos) por su suplente
de la misma posición la hace el **"entrenador automático"** — una función
APARTE, de pago (1.000 mondos/jornada, gratis en modo PRO), que no está
activada por defecto. `GET .../lineup` de la liga de prueba usada en esta
sesión devuelve `"bench": {"enabled": true, "automatic": false, ...}` —
con `automatic: false`, el suplente que coloca el bot es decorativo: si un
titular no juega, nadie entra a sustituirlo. **TODO sin investigar
todavía**: si se puede activar el entrenador automático vía API (¿toggle
en algún endpoint de configuración?), y si la liga real de destino es
PRO (gratis) o tocaría gestionar mondos — sin esto, `pick_substitutes()`/
`build_bench_changes()` calculan y envían el suplente correcto, pero no
garantizan que llegue a jugar nunca.

**`jobs/manage_substitutes.py` — sustitución manual** (añadido 2026-08-17,
a petición del usuario: la liga privada de destino no tiene activado el
entrenador automático). Complementa a `set_lineup.py`, no lo sustituye:
corre con más frecuencia (pensado para varias veces al día en días de
partido, ver `manage_substitutes.yml`) y, en cada pasada, revisa la
alineación YA guardada en Futmondo — si algún titular aparece con `status`
de lesión/duda (`is_injury_status()`), o (con `config.ENABLE_REAL_LINEUP_CHECK`
activo) no aparece en el once real de su equipo hoy según Fotmob (ver
sección anterior), y su posición tiene un suplente disponible (sano y
también presente en el once real) asignado en el banquillo, genera la
pareja de `changes` que los intercambia
(`engine.lineup_optimizer.build_substitution_changes()`). Se apoya en la
regla ya confirmada de `change_lineup()` (entrar desde el banquillo siempre
funciona, nunca desde otro slot del campo), pero la secuencia completa —dos
llamadas HTTP reales, una detrás de otra— **sigue sin haberse probado
todavía con un caso real** (cero lesionados/rotados disponibles hasta la
fecha). Cada sustitución decidida se audita en `substitution_decisions`
pase lo que pase con el envío. A diferencia del resto de jobs, solo
notifica por Telegram cuando hay algo que decidir (o algo anómalo) — con
la frecuencia con la que está pensado correr, notificar "nada que hacer"
en cada ejecución sería ruido.

**`config.ENABLE_SUBSTITUTE_AUTO_SUBMIT` — activado el 2026-08-17, decisión
explícita del usuario** (por defecto seguía siendo `false` en el código,
más conservador que `ENABLE_LINEUP_AUTO_SUBMIT`, activado vía `.env`/
Variable de GitHub Actions): imprescindible además una vez el cron pasó a
cada 20 min (ver arriba) — el job decide comparando contra la alineación
REAL actual de Futmondo, no contra un registro propio de "ya sustituí a
X", así que con el flag en `false` nunca cambia nada en Futmondo entre
pasadas y **cada ejecución (cada 20 min) vuelve a detectar la misma
situación y a notificar de nuevo** — con el flag activado, en cambio, una
vez la sustitución se aplica de verdad el titular ya no aparece en el
campo en la siguiente lectura, así que deja de re-detectarse por sí solo
sin necesitar ningún dedup adicional. Riesgo aceptado explícitamente por
el usuario: la secuencia de dos llamadas seguía sin confirmarse con un
caso real en el momento de activarlo — cualquier fallo parcial (ver
docstring de `build_substitution_changes()`) queda igualmente auditado en
`substitution_decisions`, no oculto.

**Poner en venta — CONFIRMADO AL 100%** (2026-08-17, jugador real puesto
en venta desde la pestaña "Vender" + comprobado en la UI que aparece en
"Mis ventas"):

```
POST /1/market/putonmarket
body.query: {..., "price": <asking_price>, "player_id": <id>, "isClause": null, "mode": null, "toLoan": null}
respuesta real: {"answer": {"code": "api.general.ok"}, ...}
```

**Quitar de la venta — NO confirmado**: se intentó en la misma sesión
(botón "Cancelar venta" del frontend) pero no llegó a dispararse la
llamada de red esperada en el tiempo disponible. `cancel_sale()` está
implementado igual que el resto de escrituras (mismo patrón, mismo
dominio) siguiendo la referencia comunitaria, pero sin una prueba real
propia que lo confirme — ver TODO en `clients/futmondo_client.py`.

**Nota de privacidad de la captura:** igual que en la fase de Comunio, el
valor real de `token`/`userid` nunca se expuso ni se registró en ningún
sitio — se leyeron solo dentro del propio navegador (`localStorage`) para
las peticiones de prueba, y el resultado devuelto fue solo la forma/
valores de los datos de negocio (plantilla, mercado — nada sensible).

## Stats externas: Understat (sin cambios frente a Comunio)

Ver `clients/laliga_stats_client.py` — este módulo es independiente de la
plataforma de fantasy y no cambió con la migración. Único ajuste: el
estado de lesión/duda ya no viene confirmado de la API del juego (en
Comunio sí, con valores `ACTIVE`/`WEAKENED`/`INJURED` reales) — Futmondo
expone un campo `status` pero no se ha observado un valor de lesión real
todavía (liga de prueba en pretemporada). Ver
`clients/futmondo_client.py:is_injury_status()`.

### Cruce de nombres Futmondo <-> Understat: cascada de fiabilidad decreciente

El cruce por nombre completo exacto (`index_players_by_name()`) no bastaba
en la práctica: Futmondo muestra a menudo solo el APELLIDO de un jugador
conocido (así lo vimos en la captura real — "Cairney", "Konak", "Kamara"…),
mientras que Understat siempre da el nombre completo ("Tom Cairney"). Un
cruce solo por nombre completo se quedaba sin cruzar a la mayoría de la
plantilla/mercado, dejando sus columnas de Understat (xG, minutos...) en
NULL y penalizándolos de facto en el evaluador frente a los pocos
jugadores con nombre completo mostrado.

`build_player_index()` + `match_player()` prueban, en orden de más a menos
fiable, y **paran en la primera que dé un resultado inequívoco**:

1. **Nombre completo exacto** — Futmondo muestra el nombre completo.
2. **Apellido + equipo** — el caso más habitual: apellido de Futmondo
   coincide con la última palabra del nombre completo de Understat, ambos
   en el mismo equipo.
3. **Apellido único en toda la liga** — igual que (2) pero sin poder
   confirmar equipo (nombres de equipo que no coinciden en texto entre las
   dos fuentes); solo se acepta si ningún otro jugador de la liga comparte
   ese apellido.
4. **Similitud de texto** (`difflib`, solo contra jugadores del mismo
   equipo) — para variantes menores de transcripción (guiones, apóstrofes)
   que las anteriores no cubren; se exige una nota alta Y una diferencia
   clara con el segundo mejor candidato, para no "adivinar" entre dos
   apellidos parecidos del mismo equipo.

Las estrategias (3) y (4) —las dos menos fiables— exigen además que la
posición (POR/DEF/MED/DEL) sea compatible con el código de posición de
Understat (`"GK"/"D"/"M"/"F"`, combinables para jugadores polivalentes) —
un desempate barato que no necesita ningún dato nuevo (ya se calcula la
posición corta al ingerir cada jugador). Se descartó cruzar por **dorsal**
(número de camiseta), que en un principio parecía otra vía obvia:
comprobado en vivo contra el endpoint real de Understat, ninguno de los
600 jugadores de La Liga trae ese dato — la API simplemente no lo expone.

Si ninguna da un resultado inequívoco, el jugador se queda sin cruzar esa
sync (mejor eso que cruzarlo mal y contaminar su score con las stats de
otro). `jobs/sync_data.py` cuenta cuántos cruces salieron de cada
estrategia y lo incluye en el resumen de Telegram (ej. "142/150 cruzados
con Understat (120 exact, 20 surname+team, 2 fuzzy)") — una proporción
alta de `fuzzy`/`surname_unique` frente a `exact`/`surname+team` sería
señal de revisarlo.

## Reconciliación de pujas y ventas (sin endpoint externo de "mis ofertas")

Comunio exponía `GET .../offers?current` con la lista real de ofertas
pendientes propias, con id — permitía saber con certeza si una puja seguía
viva. **Futmondo no tiene un endpoint equivalente confirmado** (ni en la
captura propia ni en la referencia comunitaria). `jobs/sync_data.py`
reconcilia por pertenencia, con las limitaciones que eso implica:

- Una puja `'placed'` se marca `'won'` si el jugador ya aparece en la
  plantilla (`get_roster()`).
- Se marca `'lost'` si el jugador ya no está ni en la plantilla ni en el
  mercado actual (`get_market()`) — el listado expiró o se resolvió sin
  nosotros, sin poder distinguir si ganó otro manager o si expiró sin
  comprador.
- Si sigue en el mercado y no en la plantilla, se asume que la puja sigue
  abierta y no se toca.

Consecuencia directa para la protección de presupuesto
(`engine.bidding_strategy.max_biddable_amount`): en vez de sumar ofertas
pendientes reales desde una fuente externa (como hacía
`total_pending_purchase_amount` con Comunio), `db.models.
get_pending_bid_amount()` suma nuestra **propia** tabla `bids` local
(`status='placed'`, sin filtro de fecha). Es una protección más débil que
la de Comunio — si la reconciliación se retrasa o la BD se pierde, podría
desincronizarse — pero es la única fuente disponible, y por diseño solo
puede sobreestimar el compromiso (nunca subestimarlo, que sería el caso
peligroso).

## `buyPrice`: por qué `run_sales` ya no filtra "solo comprados por el bot"

En Comunio, `purchaseInfo == null` distinguía sin ambigüedad "plantilla
inicial" de "comprado por el bot vía puja" — `engine/selling_strategy.py`
solo consideraba vender lo segundo. En Futmondo, el campo más parecido es
`buyPrice` (visto en cada item de `get_roster()`), pero **no sirve para
la misma distinción**: en la liga de prueba se vio tanto un jugador de la
plantilla inicial con `buyPrice=0` (Matz Sels) como otro también inicial
con `buyPrice>0` (Tzolakis, 14.017.740€ en su ficha de alineación) — todo
apunta a que `buyPrice` es más bien "valor de referencia al entrar al
equipo" (incluida la asignación inicial), no "importe pagado en una puja
real nuestra".

Sin poder ganar una puja de prueba real en el tiempo disponible para
confirmarlo del todo, `engine/selling_strategy.py` trata cualquier
`buyPrice > 0` como precio de referencia válido para calcular plusvalía,
**sin filtrar por origen** — un cambio de comportamiento deliberado y
documentado, no un descuido. Si más adelante se confirma que `buyPrice`
sí distingue el origen (comparando el roster antes/después de ganar una
puja real), habría que volver a filtrar como hacía la versión de Comunio.

## Cláusula de rescisión y riesgo de plantilla (`engine/squad_risk.py`)

Futmondo también tiene cláusula de rescisión (`POST /1/market/rosterclause`,
visto en la propia UI de la app, con un icono de cláusula en cada
jugador). No se ha confirmado con una fuente oficial si penaliza igual
que Comunio (-4 puntos por posición vacía en la alineación) cuando te
deja sin cobertura en una posición — `engine/squad_risk.py` mantiene la
misma vigilancia de todos modos, por precaución conservadora: quedarte
sin poder alinear a nadie en una posición nunca es deseable, confirmada o
no la penalización exacta.

## Pujas vs. alineación: pesos distintos a propósito

Igual que en la fase de Comunio: `engine/evaluator.py` acepta un
`weights` opcional porque el mismo score no vale para pujar (el precio
importa, relación calidad/precio con presupuesto limitado) que para
elegir alineación (el precio de un jugador ya en tu plantilla es coste
hundido, no debe influir en quién juega) — ver `config.EVALUATOR_WEIGHTS`
vs `config.LINEUP_EVALUATOR_WEIGHTS`.

## La posición sí importa al puntuar: xG se normaliza por posición

Arreglado 2026-08-17 (ya estaba señalado como TODO desde el principio,
nunca se había corregido): la fórmula de puntos real de Futmondo premia
cosas distintas según la posición — porteros/defensas ganan por portería
a cero, delanteros/centrocampistas por goles/asistencias. El xG de
Understat mide amenaza ofensiva, así que un central top tiene un xG casi
0 no porque rinda mal, sino porque no es su función.

`engine/evaluator.py:normalize_pool()` normalizaba xG (y el resto de
features) contra **todo el pool mezclado** — porteros, defensas, medios y
delanteros juntos —, así que un buen central siempre salía con "xg"
normalizado cerca de 0 frente a cualquier delantero mediocre, sesgando
sistemáticamente a la baja a defensas/porteros. Más grave todavía en
`config.LINEUP_EVALUATOR_WEIGHTS` (peso de "xg" 0.40, el más alto de los
cuatro, precisamente porque ahí se excluye el precio) — para decidir
quién juega, el bot favorecía atacantes sobre defensas/porteros de forma
artificial, no por rendimiento real.

Corregido normalizando cada feature (xG, tendencia, puntos/precio,
minutos) **dentro de su propio grupo de posición** (POR/DEF/MED/DEL) — un
central ahora compite contra otros centrales, no contra delanteros.

## Setup local

Requiere Python 3.11+ (el `venv/` de este repo, gestionado por PyCharm, usa 3.14).

```bash
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y rellenar credenciales (ver comentarios del propio archivo)
python -m db.models    # crea db/futmondo.db con el esquema
```

Para obtener `FUTMONDO_TOKEN`/`FUTMONDO_USER_ID`: iniciar sesión en
https://app.futmondo.com y, con las herramientas de desarrollador
abiertas (F12 -> Console), ejecutar:

```js
localStorage.getItem("flutter.token")
localStorage.getItem("flutter.id_user")
```

Para `FUTMONDO_CHAMPIONSHIP_ID`/`FUTMONDO_USERTEAM_ID`: con la pestaña
Network abierta, navegar a cualquier pantalla de la liga (plantilla,
mercado...) y mirar el `payload` de cualquier POST a `api.futmondo.com` —
ambos vienen en `query`.

## Tests

Batería de tests con `pytest` — cubre `engine/`, `clients/`, `db/models.py`
y los 5 jobs, migrada íntegramente al contrato de Futmondo (nada de
Comunio queda en los fixtures). Nunca toca la red real ni Telegram real
(`tests/conftest.py::no_real_telegram` es `autouse=True`) ni
`db/futmondo.db` (cada test usa una BD SQLite temporal aislada,
`tests/conftest.py::tmp_db`).

```bash
pip install -r requirements-dev.txt   # añade pytest sobre requirements.txt
pytest                                 # corre todo salvo el test marcado @pytest.mark.network
pytest -m network                      # opcional: confirma que Understat sigue respondiendo de verdad
```

## Activar el cron en GitHub Actions

`.env` es SOLO para ejecuciones locales — GitHub Actions no lo lee. El cron
necesita esto en el repo de GitHub (`Settings` del repo, no en el código):

1. **Secrets** (`Settings → Secrets and variables → Actions → Secrets`,
   cifrados, nunca visibles en logs): `FUTMONDO_TOKEN`, `FUTMONDO_USER_ID`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Nada que añadir para la
   fuente de alineaciones reales (Fotmob, ver "Banquillo/suplentes") — no
   exige key ni cuenta.
2. **Variables** (misma sección, pestaña `Variables` — no son secretas, solo
   IDs): `FUTMONDO_CHAMPIONSHIP_ID`, `FUTMONDO_USERTEAM_ID`. Opcional:
   `ENABLE_REAL_LINEUP_CHECK=true` (por defecto `false`, no hace falta
   definirla mientras no se active).
3. Hacer `git push` de este repo a `origin` (el agente que escribió este
   código no tiene acceso de push desde este entorno — hace falta hacerlo
   manualmente o darle acceso).
4. Los 5 workflows (`sync_data` cada hora, `run_market` y `run_sales`
   2x/día, `set_lineup` viernes 18:00 UTC, `manage_substitutes` cada 2h de
   viernes a lunes) ya tienen el `schedule:` activado — correrán solos en
   cuanto 1-2 estén hechos. Cada uno comitea `db/futmondo.db`/`logs/` de
   vuelta al repo al terminar (si no, cada ejecución perdería lo
   sincronizado en la anterior).

**Ya apuntado a la liga real** (2026-08-17: `FUTMONDO_CHAMPIONSHIP_ID`/
`FUTMONDO_USERTEAM_ID` actualizados en `.env` y GitHub a la liga real; la
liga de pruebas de la sesión de captura queda solo en el historial de
commits). `db/futmondo.db` se limpió y se repobló contra la liga real ese
mismo día. Tener en cuenta que `ENABLE_LINEUP_AUTO_SUBMIT=true` por
defecto — el bot escribe de verdad, sin confirmación manual, en cuanto el
cron está activo. `ENABLE_SUBSTITUTE_AUTO_SUBMIT` también se activó ese
día (decisión explícita del usuario, ver sección "Banquillo/suplentes")
**sin haber confirmado todavía la secuencia de sustitución con un caso
real** — riesgo aceptado a propósito, no un descuido: era necesario para
que el cron a 20 min no repitiera la misma notificación cada pasada. El
mapeo de alineación solo está confirmado para 4-4-2 (ver sección de
endpoints) — si la liga real usa otra formación, conviene desactivar
`ENABLE_LINEUP_AUTO_SUBMIT` hasta confirmarlo con una prueba real, o
revisar `lineup_decisions` a mano un tiempo antes de fiarse del envío
automático.

## Estructura

Ver el detalle de cada módulo en su propio docstring. Resumen:

```
clients/    -> integraciones externas (Futmondo, Understat)
db/         -> esquema SQLite + conexión
engine/     -> evaluator, bidding_strategy, lineup_optimizer, squad_risk, selling_strategy
jobs/       -> entrypoints ejecutados por cron (sync_data, run_market, run_sales, set_lineup, manage_substitutes)
tests/      -> batería pytest (ver sección "Tests" más arriba)
notifier.py -> resumen por Telegram tras cada job
logs/       -> auditoría de decisiones
```
