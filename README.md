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

## TODOs pendientes

Lista completa y priorizada de los TODOs abiertos en el código (bugs sin
resolver, asunciones sin confirmar, limitaciones de APIs externas), con qué
afecta cada uno y dónde está: ver [`TODO.md`](TODO.md).

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
- [x] `jobs/run_sales.py` — identifica jugadores con plusvalía suficiente (`engine/selling_strategy.py`) y los pone en venta — **cambio de comportamiento deliberado** frente a Comunio: ya no se filtra por "solo comprados por el bot" (ver sección dedicada); pide el VM tal cual salvo revalorización rápida sostenida, donde aplica una prima acotada por encima (`config.ENABLE_SELLING_REVALUATION_PREMIUM`, ver sección dedicada)
- [x] `jobs/manage_substitutes.py` — sustitución MANUAL de un titular confirmado fuera (lesionado/en duda, o no incluido en el once real de su equipo hoy) por su suplente ya asignado en el banquillo (`engine.lineup_optimizer.build_substitution_changes()`), para ligas sin el "entrenador automático" de pago activado (ver sección "Banquillo/suplentes"); `config.ENABLE_SUBSTITUTE_AUTO_SUBMIT` **activado** desde el 2026-08-17 (decisión explícita del usuario); la secuencia de sustitución real (4 llamadas: vaciar+rellenar, nunca "to"+"from" combinados) se arregló y se confirmó en vivo el 2026-08-18 abriendo el navegador contra la propia app de Futmondo (ver "Banquillo/suplentes" y `TODO.md` #1)
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
| Cancelar puja de compra | `POST /1/market/cancelbid` | ✅ captura propia (no documentado en ninguna referencia previa, ver TODO.md #13) |
| Poner en venta | `POST /1/market/putonmarket` | ✅ captura propia |
| Ficha de jugador (+ histórico de precio) | `POST /1/player/summary` | ✅ captura propia |
| Quitar de la venta | `POST /1/market/cancelsell` | ✅ captura propia (TODO.md #6) |
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

**Bugs/límites reales encontrados en producción, todos arreglados** — ver
docstring de `change_lineup()` / `build_lineup_changes()` /
`build_substitution_changes()` para el detalle completo:

1. (2026-08-17) Mandar los 11 cambios de golpe en una sola llamada hacía
   que Futmondo solo aplicara el primero, devolviendo igualmente
   `"api.general.ok"` — corregido mandando una llamada HTTP por jugador.
2. (2026-08-17) Sustituir un slot que ya tiene un jugador DISTINTO exige
   vaciarlo primero — corregido leyendo `get_lineup()` antes de construir
   los cambios y solo tocando los slots que de verdad hacen falta.
3. (2026-08-18) **Hallazgo clave, confirmado abriendo el navegador contra
   la propia app web de Futmondo** (interceptando `fetch` mientras se
   hacía a mano, en la UI, un intercambio titular↔suplente real): Futmondo
   NUNCA acepta un `change` que combine `"to"` (quien entra) y `"from"`
   (quien sale) a la vez — rechaza con `"api.error.in_bench"` si el que
   entra viene del banquillo, o `"api.error.in_field"` si viene del
   campo. La forma correcta, confirmada en vivo, es SIEMPRE dos `changes`
   separados: vaciar (`"from"` solo) y después rellenar (`"to"` solo).
   `build_lineup_changes()`, `build_bench_changes()` y
   `build_substitution_changes()` reescritos para generar siempre esa
   pareja — lo que además simplifica el caso "multiposition" (un titular
   nuevo ya en el campo en el slot de OTRO grupo): ya no hace falta
   ningún paso intermedio por banquillo, basta vaciar su slot de origen
   (sea campo o banquillo) antes de rellenar el de destino. Ver `TODO.md`
   #1 para el detalle completo de la investigación.

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
`6,26,46 10-23 * * 5,6,0,1` (cada 20 min, 10:00-23:00 UTC, en minutos
:06/:26/:46 en vez de en punto — ver "Delay de GitHub Actions" más abajo)
— un valor conservador mientras se acumulan más casos reales, no uno
calibrado con varios partidos.

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
también presente en el once real) asignado en el banquillo, genera los
`changes` que los intercambian
(`engine.lineup_optimizer.build_substitution_changes()`). **Arreglado y
confirmado en vivo (2026-08-18, ver `TODO.md` #1)**: se creía que bastaba
una pareja de 2 `changes` con `"to"`+`"from"` combinados — probado a mano
contra la liga de pruebas (abriendo el navegador e interceptando `fetch`
mientras se hacía el intercambio real en la UI de Futmondo), Futmondo
rechaza esa combinación siempre (`"api.error.in_bench"` o `"api.error.
in_field"` según el sentido). La forma real, confirmada en vivo haciendo
el intercambio completo y revirtiéndolo, son 4 `changes` separados: vaciar
al titular (campo), vaciar al suplente (banquillo), rellenar el campo con
el suplente, rellenar el banquillo con el titular — nunca `"to"` y
`"from"` juntos en el mismo `change`. Cada sustitución decidida se audita
en `substitution_decisions` pase lo que pase con el envío. A diferencia
del resto de jobs, solo notifica por Telegram cuando hay algo que decidir
(o algo anómalo) — con la frecuencia con la que está pensado correr,
notificar "nada que hacer" en cada ejecución sería ruido.

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
el usuario al activarlo: la secuencia de dos llamadas seguía sin
confirmarse con un caso real. **Actualización 2026-08-18**: probado con un
caso real (forzado, sin lesión de verdad, solo para validar la mecánica) y
arreglado tras descubrir que Futmondo no acepta la forma original de 2
`changes` combinados (ver más arriba y `TODO.md` #1) — la secuencia
correcta de 4 `changes` está confirmada funcionando en vivo.

**Poner en venta — CONFIRMADO AL 100%** (2026-08-17, jugador real puesto
en venta desde la pestaña "Vender" + comprobado en la UI que aparece en
"Mis ventas"):

```
POST /1/market/putonmarket
body.query: {..., "price": <asking_price>, "player_id": <id>, "isClause": null, "mode": null, "toLoan": null}
respuesta real: {"answer": {"code": "api.general.ok"}, ...}
```

**Quitar de la venta — CONFIRMADO AL 100%** (2026-08-18, TODO.md #6): un
primer intento en la sesión anterior (botón "Cancelar venta" del
frontend) no llegó a disparar la llamada de red esperada, así que se
confirmó directamente contra la API: se puso en venta un jugador real de
mínimo valor y lesionado (Sergi Canós, para minimizar impacto), se releyó
con `get_my_players_in_market()` (confirma también la forma real de un
item listado — precio pedido, `expirationDate`, `bids: []`, `isClause`),
se canceló con `cancel_sale()` y se verificó que volvía a la plantilla sin
cambios (`get_roster()`) y que `get_my_players_in_market()` quedaba vacío
de nuevo — sin que nadie pujara por él en el intervalo.

```
POST /1/market/cancelsell
body.query: {..., "player_id": <id>}
respuesta real: {"answer": {"code": "api.general.ok"}, ...}
```

**Nota de privacidad de la captura:** igual que en la fase de Comunio, el
valor real de `token`/`userid` nunca se expuso ni se registró en ningún
sitio — se leyeron solo dentro del propio navegador (`localStorage`) para
las peticiones de prueba, y el resultado devuelto fue solo la forma/
valores de los datos de negocio (plantilla, mercado — nada sensible).

## Stats externas: Understat (sin cambios frente a Comunio)

Ver `clients/laliga_stats_client.py` — este módulo es independiente de la
plataforma de fantasy y no cambió con la migración. Único ajuste: el
estado de lesión/duda no viene con una lista cerrada de valores como en
Comunio (`ACTIVE`/`WEAKENED`/`INJURED`) — Futmondo expone un campo
`status` en INGLÉS (contra lo que asumía la referencia comunitaria
original, en español) cuyos valores reales ya se confirmaron en
producción el 2026-08-18: "" y "ok" (sano), "doubt" (duda) e "injuredN"
(lesionado, tier numérico — visto "injured2"). Ver TODO.md #5 y
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

## Reconciliación de pujas y ventas, y protección de presupuesto (TODO.md #3, resuelto)

Comunio exponía `GET .../offers?current` con la lista real de ofertas
pendientes propias, con id. **Futmondo no tiene un endpoint dedicado
equivalente**, pero SÍ trae el mismo dato por otra vía, confirmada en vivo
(2026-08-18): cada item de `get_market()` en el que tenemos puja
pendiente trae un campo `"bid": {"id": ..., "price": ...}`, ausente por
completo si no hemos pujado — cruzado 1:1 contra nuestra propia tabla
`bids` sin ningún "bid" huérfano de otro manager. Es, en la práctica, el
mismo dato que daba el endpoint de Comunio, solo que embebido en el
listado del mercado en vez de en un endpoint propio (ver
`clients.futmondo_client.real_pending_bid_amount()`).

`jobs/sync_data.py` sigue reconciliando el HISTÓRICO de cada puja
(`'placed'` -> `'won'`/`'lost'`) por pertenencia, ya que eso sí necesita
guardarse más allá de que el listado siga vivo o no:

- Una puja `'placed'` se marca `'won'` si el jugador ya aparece en la
  plantilla (`get_roster()`).
- Se marca `'lost'` si el jugador ya no está ni en la plantilla ni en el
  mercado actual (`get_market()`) — el listado expiró o se resolvió sin
  nosotros, sin poder distinguir si ganó otro manager o si expiró sin
  comprador.
- Si sigue en el mercado y no en la plantilla, se asume que la puja sigue
  abierta y no se toca.

Para la protección de presupuesto en sí
(`engine.bidding_strategy.max_biddable_amount`), `jobs/run_market.py` ya
NO depende solo de la auditoría local: `pending_committed` es el MAYOR
entre `db.models.get_pending_bid_amount()` (tabla `bids` local) y
`real_pending_bid_amount()` (el mercado en vivo) — así una BD
perdida/desincronizada no puede hacer que se subestime el compromiso
real, igual de fuerte que la protección de Comunio.

**Hallazgo relacionado, confirmado en el mismo fetch real**: pujar dos
veces sobre el mismo jugador mientras la primera puja sigue abierta
responde `"api.general.ok"` las dos veces, pero Futmondo NO actualiza el
importe — se queda en el de la primera llamada. `jobs/run_market.py` por
eso excluye de los candidatos a cualquier jugador con una puja local
todavía `'placed'`, en vez de reintentar una "mejora" sin ningún efecto
real (y que además duplicaría el compromiso en la auditoría local).

## `buyPrice`: por qué `run_sales` filtra "comprados por el bot" sin usar ese campo

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

En vez de esperar a poder ganar una puja de prueba real para confirmarlo
(TODO.md #4), la solución fue dejar de depender de `buyPrice` para esto:
`engine/selling_strategy.decide_sales()` recibe ahora `bought_by_bot`
(`db.models.get_won_bid_prices()`), el registro **local** de pujas que el
propio bot colocó y `jobs/sync_data.py` ya reconcilia como `'won'` en la
tabla `bids`. Solo esos jugadores son candidatos a venta, y el precio de
referencia es el importe realmente pagado en esa puja — misma semántica
que `purchaseInfo != null` en Comunio, sin necesitar confirmar el
significado exacto de un campo de la API de Futmondo.

**Backfill de la plantilla inicial** (a petición del usuario, 2026-08-22,
caso real: Mendy lesionado, nunca evaluado para venta): la consecuencia de
lo anterior es que un jugador de la plantilla INICIAL (nunca comprado por
el bot vía puja) no tenía NINGUNA fila `'won'` en `bids`, así que
`decide_sales()` lo descartaba como candidato sin más, sin llegar siquiera
a mirar su lesión/pérdida de valor. Como el VM de esos jugadores SÍ se
descontó del presupuesto inicial al repartir el equipo, `jobs/sync_data.py`
(`_backfill_initial_squad_bids()`) registra en cada sync una puja `'won'`
sintética por su VM ACTUAL (no se guardó el VM exacto del momento del
reparto, es la mejor aproximación disponible) para todo jugador de
plantilla sin ninguna puja `'won'` — idempotente: en cuanto un jugador
tiene alguna puja `'won'` (esta sintética, o una real más adelante), deja
de tocarse. Efecto colateral esperado: como el precio de referencia
backfilleado es igual al VM del día del backfill, ese jugador arranca en
`profit_pct=0%` — no se pondrá a la venta el mismo día salvo que ya
concentre mucho capital o esté lesionado (ver más abajo, "se vende
SIEMPRE"); se irá evaluando en los sync siguientes según se mueva su VM
real.

## Cláusula de rescisión y riesgo de plantilla (`engine/squad_risk.py`)

Futmondo también tiene cláusula de rescisión (`POST /1/market/rosterclause`,
visto en la propia UI de la app, con un icono de cláusula en cada
jugador). No se ha confirmado con una fuente oficial si penaliza igual
que Comunio (-4 puntos por posición vacía en la alineación) cuando te
deja sin cobertura en una posición — `engine/squad_risk.py` mantiene la
misma vigilancia de todos modos, por precaución conservadora: quedarte
sin poder alinear a nadie en una posición nunca es deseable, confirmada o
no la penalización exacta.

El bot no puede detectar un clausulazo como evento propio (Futmondo no
distingue esa causa de una venta aceptada, una retirada manual o una
lesión — ver docstring de `jobs/sync_data.py`), pero sí reacciona a su
EFECTO sobre la plantilla: si mientras un jugador nuestro sigue puesto en
venta, otro jugador de esa misma posición desaparece del todo del roster
(clausulado por un tercero, vendido, lo que sea) y eso deja la posición
con margen NEGATIVO (`assess_squad_depth()[...]["deficit"] > 0` — ni
siquiera contando de vuelta al que está en venta llegaríamos a los
titulares requeridos), `jobs/sync_data._rescue_sales_at_risk()` cancela
esa venta (`FutmondoClient.cancel_sale()`) para recuperar el cuerpo antes
de que se cierre sola y nos deje cortos. Vive en `sync_data` (corre cada
hora) y no en `run_sales` (cada 2h) para reaccionar lo antes posible.

Deliberadamente NO se usa el `at_risk` de más arriba (margen CERO) para
decidir la cancelación, solo `deficit` (margen NEGATIVO,
`engine.squad_risk.sales_to_cancel`): poner un jugador en venta ya resta
uno de "disponible" por diseño, así que `at_risk` sin margen es el estado
normal justo después de listar cualquier venta — usarlo aquí deshacería
la venta en la primera pasada tras crearla. Solo actúa cuando el margen
se ha vuelto de verdad insuficiente.

## Fichajes que mejoran el once, no solo tapan huecos (`engine/squad_risk.weakest_starter_scores`)

`assess_squad_depth()` (arriba) solo mira CANTIDAD: si una posición tiene
algún suplente sano, no dice nada más, aunque ese suplente sea muy
inferior al peor titular actual. `weakest_starter_scores()` añade la señal
de CALIDAD que faltaba: calcula, con los mismos pesos con los que se
decide la alineación real (`config.LINEUP_EVALUATOR_WEIGHTS` — el precio
no debe importar, ya está comprado), el score del titular más flojo de
cada posición hoy. `jobs/run_market.py` compara cada candidato de mercado
contra ese listón (evaluando plantilla + mercado EN LA MISMA llamada a
`evaluate_players()`, para que sean comparables entre sí — normalizar cada
pool por separado los dejaría en escalas distintas) y, si lo supera, suma
`config.BIDDING_UPGRADE_BOOST` a su score de puja
(`engine.bidding_strategy.apply_position_priority`) — misma mecánica
aditiva que el boost por riesgo de plantilla, señal distinta, se pueden
sumar las dos en el mismo candidato. Igual que el resto de boosts de
puja: nunca fuerza una puja mala, solo da ventaja frente a otro candidato
de score similar que no mejoraría el once.

## Pujas vs. alineación: pesos distintos a propósito

Igual que en la fase de Comunio: `engine/evaluator.py` acepta un
`weights` opcional porque el mismo score no vale para pujar (el precio
importa, relación calidad/precio con presupuesto limitado) que para
elegir alineación (el precio de un jugador ya en tu plantilla es coste
hundido, no debe influir en quién juega) — ver `config.EVALUATOR_WEIGHTS`
vs `config.LINEUP_EVALUATOR_WEIGHTS`.

## Lesión confirmada descarta el fichaje; duda solo penaliza el score

A petición del usuario (2026-08-22): hasta ahora `EVALUATOR_WEIGHTS`
trataba "doubt" (duda) e "injuredN" (lesión confirmada) exactamente igual
— una única `injury_penalty` de 0.10 sobre el score, sin distinguir. Eso
significaba que un candidato de mercado con lesión confirmada pero muy
buenas stats previas podía seguir ganando en score a un candidato sano
mediocre y recibir puja real, arriesgando presupuesto en un jugador que
normalmente es baja segura varias jornadas.

Ahora (`jobs/run_market.py` + `clients.futmondo_client.
is_confirmed_injured_status()`): un candidato con lesión confirmada se
descarta del mercado ANTES de evaluar/puntuar, igual que los candidatos
con puja local ya abierta — nunca recibe puja, por buenas que sean sus
stats. "doubt" (duda: el jugador todavía puede llegar a jugar) sigue
evaluándose con normalidad, solo que con una penalización de score más
dura que antes (`config.EVALUATOR_WEIGHTS["doubt_penalty"] = 0.20`, el
doble del antiguo `injury_penalty`).

Cambio deliberadamente acotado a la decisión de FICHAJE en esta primera
iteración: `engine/squad_risk.py` y `engine/lineup_optimizer.py` seguían
usando `is_injury_status()` sin distinguir gravedad (solo sano/no-sano).
`config.LINEUP_EVALUATOR_WEIGHTS` tampoco cambia: sigue con
`injury_penalty = 0.10` sobre `is_injured_or_doubtful` (duda Y lesión por
igual), igual que siempre. `engine/selling_strategy.py` y (más abajo)
`engine/lineup_optimizer.py` sí distinguen gravedad, en cambios
posteriores.

## Alineación: duda por delante de lesión confirmada, no en el mismo grupo

A petición del usuario (2026-08-22): `engine/lineup_optimizer.
_rank_healthy_first()` — usada tanto por `pick_lineup()` (titulares) como
por `pick_substitutes()` (suplente designado) — agrupaba duda y lesión
confirmada en un único grupo "no sano", ordenado solo por
`expected_score` entre sí. Pero un "doubt" todavía puede acabar jugando,
mientras que una lesión confirmada normalmente es baja segura — no
debería hacer falta que un lesionado tenga peor score que un "doubt" para
perder el puesto frente a él.

Ahora `_rank_healthy_first()` ordena en TRES niveles en vez de dos: sano
> en duda > lesión confirmada, cada uno ordenado por `expected_score`
entre sí. Si no queda nadie sano en una posición, se prefiere siempre al
mejor "doubt" disponible antes que a CUALQUIER lesionado confirmado,
aunque el lesionado tenga mejor score. Solo si tampoco queda nadie en
duda se recurre a la lesión confirmada — el mismo criterio conservador de
siempre (mejor cubrir el hueco que dejarlo vacío), solo que ahora en tres
escalones en vez de dos.

`build_substitution_changes()` (decidir si sustituir a un titular ya
puesto, cerca de la jornada) NO cambia — sigue tratando duda y lesión
confirmada igual (`is_injury_status()`, ambas disparan la sustitución):
esa función responde a una pregunta distinta ("¿este titular está
confirmado fuera?"), no a una prioridad entre varios candidatos
disponibles.

## Venta: corte de pérdidas (un único umbral para todos los estados)

A petición del usuario (2026-08-22, tres iteraciones, ver historial de
commits): hasta el primer cambio, un jugador solo era candidato a venta
si su revalorización superaba `config.SELLING_MIN_PROFIT_PCT` (10% por
defecto) — sano, en duda o lesionado, sin distinción, y sin ninguna otra
vía posible. Esperar a que "recupere" plusvalía sin límite es la falacia
del coste hundido en cualquier jugador, no solo en los lesionados:
aferrarse a cuánto se pagó en el pasado en vez de valorar el jugador por
lo que es AHORA.

Por eso `engine/selling_strategy.py:decide_sales()` añade un umbral de
PÉRDIDA, además del de rentabilidad: `config.SELLING_MAX_LOSS_PCT` (10%
por defecto) — si un jugador ha perdido más de esto, se pone en venta
igualmente, aunque sea con pérdidas. **El mismo umbral para CUALQUIER
estado** (sano, en duda o lesión confirmada) — antes (segunda iteración)
había dos umbrales distintos: uno genérico (20%) para sano/duda y otro
más agresivo (10%) solo para lesión confirmada, razonado porque un
lesionado tiende a seguir perdiendo valor cuanto más tiempo pasa sin
jugar. Esa distinción se retiró en la tercera iteración: la lesión
confirmada ya tiene su propia vía incondicional (ver más abajo, "se vende
SIEMPRE"), así que ya no hace falta protegerla con un umbral aparte —
el argumento de "cortar antes porque va a seguir cayendo" se generaliza
sin más al resto de la plantilla, del mismo modo que empezar a subir de
valor ya bastaba para vender sin mirar el estado del jugador.

El corte de pérdidas de un lesionado (o en duda) tampoco compite por
margen de banquillo (ninguno de los dos contó nunca como "disponible" en
`assess_squad_depth`, ver arriba) — puede vender incluso si es el único
jugador de su posición. El corte de un jugador SANO sí sigue respetando
ese margen, igual que una venta por rentabilidad normal.

## Venta: lesión confirmada, se vende SIEMPRE

A petición del usuario (2026-08-22, caso real: Mendy lesionado, recién
backfilleado a `profit_pct=0%` — ver "Backfill de la plantilla inicial"
más arriba —, sin pérdida ni concentración de capital suficiente para
activar ninguna de las vías anteriores, se quedaba sin vender
indefinidamente pese a ser inservible): un jugador con lesión CONFIRMADA
(`clients.futmondo_client.is_confirmed_injured_status()`, no "doubt") es
sencillamente INSERVIBLE mientras dure — no puede jugar ni puntuar — y
ocupa una plaza de plantilla que podría liberarse para fichar a otro que
sí sume.

Por eso `engine/selling_strategy.py:decide_sales()` vende SIEMPRE a
cualquier jugador con lesión confirmada, sin mirar
rentabilidad/pérdida/concentración en absoluto — generalización sin
condiciones de las vías de corte de pérdidas y concentración de arriba,
que solo cubrían el caso cuando además cruzaba un umbral de pérdida o de
tamaño. "doubt" queda fuera, igual que en las otras vías de lesión —
todavía puede llegar a jugar, así que su valor actual sigue siendo
información útil. Tampoco compite por margen de banquillo, igual que el
resto de vías de lesión.

## Venta: concentración de capital en lesión confirmada

A petición del usuario (2026-08-22): las vías de arriba solo miran
RENTABILIDAD (plusvalía o pérdida de la operación). Pero un lesionado
puede ser un problema aunque su pérdida sea pequeña, si simplemente
representa una parte demasiado grande del capital del equipo — mientras
esté lesionado no se puede usar, así que tener mucho capital inmovilizado
ahí es un coste de oportunidad real (ese dinero no puede fichar a nadie
más).

Por eso `engine/selling_strategy.py:decide_sales()` tiene también esta
vía, que no mira plusvalía/pérdida en absoluto: un jugador con lesión
CONFIRMADA cuyo valor supera `config.SELLING_INJURY_CONCENTRATION_MAX_PCT`
(15% por defecto) del capital TOTAL del equipo (suma del valor de toda la
plantilla + `budget` disponible, este último ahora obtenido por
`jobs/run_sales.py` vía `client.get_information()` y pasado a
`decide_sales()`) se pone en venta igualmente. En la práctica, cualquier
lesión confirmada que llegue aquí ya se vendería de todos modos por la
vía incondicional de arriba — esta vía se mantiene porque da un motivo
más específico en el `reason` auditado cuando también aplica (útil para
saber SI ADEMÁS concentraba demasiado capital, no solo que estaba
lesionado). Como con las demás vías de lesión, "doubt" queda fuera —
todavía puede llegar a jugar, y un jugador SANO tampoco entra aquí por
mucho que concentre capital. Esta venta forzada tampoco compite por
margen de banquillo.

## Prima sobre el precio de venta por revalorización rápida sostenida

A petición del usuario (2026-08-22): hasta ahora `asking_price` era
siempre el VM tal cual (`value`), sin más — pedir el VM es la opción más
simple y segura (ver más arriba). Pero el VM oficial de Futmondo puede
tardar en reflejar del todo una subida muy reciente, así que
`engine/selling_strategy.py` añade una prima opcional POR ENCIMA del VM
cuando un jugador lleva subiendo de forma rápida y sostenida.

`compute_revaluation_premium_pct()` usa el histórico diario de VM de
`FutmondoClient.get_player_summary()["answer"]["prices"]` (formato
CONFIRMADO en vivo el mismo día contra la cuenta real — Koke y Roberto
Fernández, 7 días de histórico cada uno tras el reinicio de la liga: fecha
ISO-8601 con milisegundos, orden ascendente, `price` = VM diario real
idéntico al `value` de roster/market en esa fecha) y exige TRES
condiciones antes de proponer nada: datos suficientes dentro de la
ventana (`config.SELLING_REVALUATION_MIN_DATA_POINTS`/`_LOOKBACK_DAYS`),
ninguna bajada día a día dentro de esa ventana (un solo día a la baja
descarta la prima entera — busca tendencia sostenida, no un pico
puntual), y una subida total por encima de
`config.SELLING_REVALUATION_MIN_PCT_TO_PROJECT`. Si se cumplen, proyecta
solo una FRACCIÓN de esa subida
(`config.SELLING_REVALUATION_PROJECTION_FRACTION`, 50% por defecto),
nunca la subida completa, y siempre topada por
`config.SELLING_REVALUATION_MAX_PREMIUM_PCT` (15% por defecto) —
corrido contra el histórico real de Roberto Fernández (+30.2% sostenido
en 7 días) dio una prima de +15.0% (tope alcanzado), confirmando que el
cálculo funciona sobre datos reales.

`jobs/run_sales.py` solo llama a `get_player_summary()` sobre los
candidatos que `decide_sales()` YA decidió vender (no toda la plantilla),
y cualquier fallo de red deja el precio en el VM tal cual, sin bloquear la
venta. Controlado por `config.ENABLE_SELLING_REVALUATION_PREMIUM`
(**activado** desde que se confirmó el formato real, ver `TODO.md` #14) —
impacto acotado en cualquier caso: solo sube el precio PEDIDO, nunca gasta
dinero.

## Venta: aceptar ofertas recibidas (TODO.md #15, resuelto)

Poner un jugador en venta (`list_for_sale()`) no lo vende solo: otros
managers hacen OFERTAS sobre el listado, y hace falta ACEPTAR una
explícitamente para completar la venta — confirmado en vivo dos veces
(2026-08-22, liga de prueba): una oferta real aceptada desde la UI de
app.futmondo.com, y otra aceptada llamando directamente por API sin tocar
la UI, ambas verificando el efecto real (`budget` sube exactamente el
importe de la oferta, el jugador sale de la plantilla). Ver docstring de
`clients.futmondo_client.FutmondoClient.accept_sale_offer()` para el
detalle completo de cómo se confirmó el endpoint (`POST
/1/market/acceptbid`).

Criterio de aceptación (a petición del usuario, 2026-08-22, sin más
margen todavía): `jobs/run_sales.py` acepta SIEMPRE la oferta más alta
que SUPERE el precio de salida pedido — si ninguna oferta lo supera, el
listado se deja tal cual esperando una mejor. Este paso corre ANTES de
decidir nuevos listados en cada ejecución, leyendo
`get_my_players_in_market()[].bids`.

Precisamente por este paso, `run_sales.yml` (y `run_market.yml`, para que
libere presupuesto/plaza igual de rápido tras una venta) pasaron de 2 a 8
pasadas/día (cada 2h en horario activo, mismo día) — con solo 2 pasadas,
una oferta real podía quedar sin aceptar hasta 12h; ver "Delay de GitHub
Actions y horas críticas" más abajo para el detalle completo del cambio
de cron.

Cada oferta vista (aceptada o no, una sola vez por su id real de
Futmondo) se registra en `db.models.received_sale_offers` — pensado
puramente para ANÁLISIS: con datos reales acumulados, ver si el
`asking_price` que calcula `engine/selling_strategy.py` es realista
frente a lo que el mercado realmente ofrece (¿se acepta casi siempre al
precio pedido justo, o muy por encima/por debajo?) y ajustar el cálculo
si hace falta más adelante. No es la fuente de verdad operativa de nada
(para eso siguen estando `sales` y `bids`).

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

## Resiliencia ante fallos de red (reintentos + aviso de caída)

Fallo real en producción (GitHub Actions, 2026-08-17): `jobs/run_market.py`
cayó entero con `http.client.RemoteDisconnected` al llamar a
`/1/userteam/information` — Futmondo cerró la conexión sin responder, sin
relación con los datos de la petición. Sin ningún reintento ni aviso, el
job murió en silencio: la única forma de enterarse era revisar los logs de
Actions a mano.

Dos arreglos, ninguno oculta el fallo si de verdad hace falta que se vea:

- **Reintentos ante `ConnectionError`** (`clients/futmondo_client.py:
  FutmondoClient._post`, `config.FUTMONDO_READ_MAX_RETRIES`, 2 por
  defecto, backoff 1s/2s): solo en las llamadas de LECTURA
  (`get_roster`/`get_information`/`get_lineup`/`get_market`/
  `get_my_players_in_market`/`get_player_summary`). Las ESCRITURAS
  (`place_bid`, `change_lineup`...) se quedan a propósito en 0 reintentos:
  un `ConnectionError` ahí puede pasar DESPUÉS de que Futmondo ya procesara
  la petición de verdad (se perdió la respuesta, no la petición), y
  reintentar podría duplicar el efecto (pujar dos veces, mandar el mismo
  cambio de alineación dos veces). Un `HTTPError` (4xx/5xx CON respuesta)
  tampoco se reintenta nunca — ya hubo respuesta, repetir no cambiaría nada.

- **Aviso inmediato de caída + instrumentación de tiempos**
  (`notifier.track_job_run`, envuelve el `if __name__ == "__main__":` de
  los 5 jobs): si `run()` deja escapar cualquier excepción no controlada
  (agotados los reintentos o de otro tipo), se notifica por Telegram ANTES
  de dejarla propagar — GitHub Actions sigue marcando el job en rojo
  igual, esto solo evita depender de mirar los logs a mano para enterarse.
  Cada job ya notificaba siempre sus fallos "esperados" (rechazo de una
  puja, envío parcial de la alineación...) vía `notify()` al final de
  `run()`; esto cubre el hueco de los fallos que ni siquiera llegan a esa
  última línea. Además mide cuánto tarda `run()` y lo persiste en la tabla
  `job_runs` (éxito o fallo, con la excepción si la hubo) — antes no había
  ninguna forma de saber cuánto tarda cada job sin abrir cada ejecución de
  GitHub Actions a mano.

## Pausar el bot sin tocar los 5 workflows (`ENABLE_BOT`)

GitHub no deja pausar un `schedule:` desde una variable — para dejar de
disparar un workflow hay que editar su `.yml` (comentar la línea
`schedule:`) o desactivarlo a mano uno a uno desde la pestaña `Actions`,
incómodo teniendo 5 workflows independientes (`sync_data`, `run_market`,
`run_sales`, `set_lineup`, `manage_substitutes`).

`config.ENABLE_BOT` (por defecto `true`) es el atajo: con la GitHub
Variable `ENABLE_BOT=false` puesta UNA vez (`Settings → Secrets and
variables → Actions → Variables`), los 5 `schedule:` se siguen disparando
igual (GitHub los lanza de todos modos), pero cada `run()` hace return en
la primera línea sin tocar la red ni la BD — pausa efectiva en un solo
sitio, sin editar ni reactivar workflows. No notifica por Telegram al
saltarse (spamearía en cada disparo mientras esté pausado a propósito) —
solo un `print` visible en el log de Actions si alguien lo revisa a mano.
Volver a poner `ENABLE_BOT=true` (o borrar la variable) reactiva todo tal
cual estaba.

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
   definirla mientras no se active) y `ENABLE_BOT=false` para pausar todo
   (ver sección anterior).
3. Hacer `git push` de este repo a `origin` (el agente que escribió este
   código no tiene acceso de push desde este entorno — hace falta hacerlo
   manualmente o darle acceso).
4. Los 5 workflows (`sync_data` cada hora, `run_market` y `run_sales`
   cada 2h las 24h, `set_lineup` viernes cada 20 min (cron 15:23-20:43 UTC,
   filtrado a la ventana local real 17:23-21:43 Europe/Madrid — ver más
   abajo), `manage_substitutes` cada 20 min
   de viernes a lunes) ya tienen el `schedule:` activado — correrán solos
   en cuanto 1-2 estén hechos. Cada uno comitea `db/futmondo.db`/`logs/` de
   vuelta al repo al terminar (si no, cada ejecución perdería lo
   sincronizado en la anterior). Ver también "Delay de GitHub Actions y
   horas críticas" más abajo antes de fijar los horarios definitivos.

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

## Delay de GitHub Actions y horas críticas

**Hallazgo real (2026-08-18)**: `sync_data.yml`, programado en punto
(`0 8-23 * * *`), entraba sistemáticamente ~30 min tarde. Es
comportamiento documentado de GitHub: los eventos `schedule` se retrasan
en picos de carga, y el minuto en punto de cada hora es el más solicitado
de toda la plataforma (todo el mundo programa sus cron ahí) — GitHub
recomienda explícitamente NO usar el minuto `:00`. Por eso `sync_data.yml`
y `manage_substitutes.yml` ahora usan minutos "raros" (`:06`, `:06/:26/:46`)
en vez de en punto/en múltiplos de 20 exactos.

Ese cambio es seguro para esos dos jobs porque ninguno tiene un deadline
externo real — solo necesitan correr "con frecuencia suficiente", no en un
instante exacto.

**(2026-08-19)** `run_market.yml` y `run_sales.yml` seguían en el minuto
`:00` (`0 8,20 * * *` / `0 9,21 * * *`) pese a que la sección de abajo ya
había confirmado que ninguno de los dos tiene un cierre de reloj real al
que ajustarse — no había motivo para dejarlos en el minuto pico. Movidos a
`:06` (mismo minuto que `sync_data`), conservando el hueco de 1h entre
ambos.

**`set_lineup` y `run_market` sí tenían, en teoría, un deadline externo
real** — se investigó cuál es exactamente en la FAQ oficial de Futmondo
(help.futmondo.com) en vez de suponerlo, con resultado bien distinto para
cada uno:

- **Mercado de fichajes (`run_market`): NO existe un cierre diario único.**
  Cada jugador puesto en venta tiene su propio temporizador individual (1,
  2 o 3 días, configurable por el admin de la liga — confirmado también en
  el propio código, `expirationDate` por item en
  `clients/futmondo_client.py:get_market()`). No hay ninguna hora a la que
  "ajustar" el cron: con el listado más corto posible (1 día), 2
  pasadas/día ya dan margen de sobra. Por eso baja de criticidad frente a
  lo que se pensó en un primer momento.
- **Alineación (`set_lineup`): SÍ hay un límite real, pero no es una hora
  de reloj fija.** La FAQ oficial (help.futmondo.com/article/92) lo dice
  literalmente: "el inicio de la jornada siempre coincide con el primer
  partido de la misma". Y el primer partido de una jornada de LaLiga varía
  cada semana — puede arrancar viernes desde ~19:00 CEST (17:00 UTC). El
  cron que había antes (`0 18 * * 5` = 18:00 UTC = **20:00 CEST**) podía
  quedar DESPUÉS de un primer partido a esa hora — un problema estructural
  independiente de cualquier delay de GitHub. Se adelantó a las 15:23 UTC
  (minuto ":23", no en punto, mismo motivo que `sync_data.yml`; hora
  elegida explícitamente por el usuario, 2026-08-18, en vez de la
  alternativa más conservadora de 10:00 UTC), dejando ~1h40 de margen
  frente al horario más temprano documentado.

  **Además, repetido cada 20 min hasta las 19:43 UTC** (`3,23,43 15-19 * *
  5`, en vez de una sola ejecución) — decisión explícita del usuario
  (2026-08-18) para mitigar el riesgo de un "clausulazo" (u otra baja) de
  última hora sobre un titular YA decidido: como `jobs/set_lineup.py`
  siempre recalcula desde cero contra el estado actual de la plantilla, si
  un titular desaparece entre dos pasadas, la siguiente ya lo sustituye
  antes del cierre real — antes, con una sola ejecución, esa decisión
  quedaba fija toda la semana pasara lo que pasara justo después. Mismo
  razonamiento que llevó a `manage_substitutes.yml` de cada 2h a cada 20
  min (ver "Banquillo/suplentes"). Sin coste de ruido en Telegram: el job
  ya no notifica en las pasadas sin novedad (con auto-submit activado, sin
  cambios que aplicar y sin avisos de riesgo — ver docstring de
  `jobs/set_lineup.py`), igual que `manage_substitutes` ya hacía.

  **Sin confirmar con un caso real**: qué responde la API de Futmondo si
  se manda un cambio de alineación YA pasado el cierre de jornada. Se
  asume que lo rechaza o no tiene efecto (como la mayoría de fantasy
  bloquean la plantilla al empezar el partido), pero correr repetido
  después del cierre, si la asunción fuera falsa, tampoco haría daño —
  como mucho sería redundante (ver docstring del job).

  **Sigue sin ser un dato dinámico real** — no hay en el código ninguna
  fuente que consulte el calendario real de cada jornada (a diferencia de
  Fotmob para "quién juega hoy", no se usa nada equivalente para "cuándo
  empieza la jornada"); si alguna jornada excepcional empezara antes de
  las 17:23 hora local del viernes, seguiría sin cubrirse. La solución
  completa sería leer ese calendario real en vez de asumir un día/hora
  fijos — no implementado.

Ranking de criticidad actualizado (de más a menos sensible al delay):

1. **`set_lineup`** (MEDIO, bajó de CRÍTICO) — ya no corre una sola vez
   por semana: repetido cada 20 min los viernes en ventana LOCAL
   17:23-21:43 Europe/Madrid (cron ampliado a 15:23-20:43 UTC + filtro por
   `scheduling.is_within_local_window()`, ver "El problema del cambio de
   hora" más abajo), con la misma red de seguridad por frecuencia que
   `manage_substitutes`. El riesgo que queda es el mismo tipo que el de
   `manage_substitutes` (delay acumulado reduciendo el margen entre
   pasadas) más el caso de una jornada excepcional que empiece antes de
   las 17:23 hora local del viernes (sin cobertura, no es un tema de
   frecuencia).
2. **`manage_substitutes`** (MEDIO, mitigado por frecuencia) — el cron a
   20 min ya está para esto: si UNA pasada llega tarde, la siguiente (20
   min después) puede seguir cubriendo la ventana. El riesgo no es un
   fallo puntual sino que el delay ACUMULADO reduzca el margen frente a la
   ventana de 41 min observada (ver sección "Banquillo/suplentes") — con
   +10/+15 min de delay por pasada, dos pasadas seguidas con mala suerte
   podrían acercarse a ese límite. Mitigado (no eliminado) con el cambio de
   minuto de este mismo apartado.
3. **`run_market`** (BAJO, corregido — ver arriba) — sin cierre diario
   real al que llegar tarde; ya daba margen de sobra frente a listados de
   1-3 días con 2x/día, así que subir a 12x/día, las 24h (2026-08-22 a
   8x/día en horario activo, TODO.md #15; ampliado a 24h el 2026-08-23 —
   ver "El problema del cambio de hora" más abajo) no cambia este análisis,
   solo reduce la latencia hasta reaccionar al presupuesto/plaza liberados
   por una venta.
4. **`run_sales`** (BAJO) — 12x/día, las 24h (antes 2x/día en horario
   activo, luego 8x/día, mismo historial que `run_market` de arriba), 1h
   después de cada pasada de `run_market` a propósito. Un delay de ~30 min
   en cualquiera de los dos no invierte el orden porque el hueco entre
   ambos (1h) es mayor que el delay típico observado.
5. **`sync_data`** (BAJO en sí mismo, pero ver nota) — no tiene deadline
   propio; solo necesita alimentar a los demás con datos razonablemente
   frescos.

**Nota aparte, no resuelta por horario** — `run_market`/`run_sales`/
`set_lineup` asumen en su docstring que `sync_data` "ya corrió antes en el
cron" ESE mismo ciclo (p. ej. `run_market` a las 8:00 asume que el
`sync_data` de las 8:00 ya escribió). Como los delays de GitHub son
independientes por workflow, esto es una carrera, no una garantía —
`run_market` podría arrancar antes de que `sync_data` termine y trabajar
con datos de la hora anterior. No es grave en la práctica (`sync_data`
corre cada hora, así que el dato "viejo" tiene como mucho ~1h) y no se ha
tocado aquí porque arreglarlo de verdad es un cambio de arquitectura
(p. ej., que cada job llame a `sync_data.run()` él mismo en vez de confiar
en el cron), no de horario — se deja anotado para si se decide abordar.

**Resuelto (2026-08-18)** — `set_lineup` y `run_market` llevaban desde el
principio comentados como "ajustar al cierre real de jornada/mercado" sin
que se hubiera confirmado nunca esa hora contra la FAQ oficial de
Futmondo. Ya confirmado (ver arriba) y ajustado en `set_lineup.yml` /
`run_market.yml`. Pendiente real que queda, y que no se resuelve con
horario: una fuente que consulte el calendario real de cada jornada, para
que `set_lineup` no dependa de asumir "siempre viernes, siempre antes de
las 15:23 UTC".

**(2026-08-23) `sync_data`/`run_market`/`run_sales` pasan de "horario
activo" a las 24h.** El límite a horario diurno (8-23 UTC según el job) se
fijó pensando implícitamente en actividad HUMANA — pero, como ya queda
confirmado más arriba, jugar contra el mercado de Futmondo es jugar contra
un temporizador individual por jugador (`expirationDate`), no contra la
actividad de otros managers en tiempo real; y aceptar ofertas recibidas
(sección "Venta: aceptar ofertas recibidas") tampoco depende de que sea de
día — una oferta real puede llegar a cualquier hora. Sin ninguna razón
real para el hueco nocturno, los tres pasan a correr las 24h:
`sync_data` cada hora (antes 08:06-23:06 UTC), `run_market`/`run_sales` a
12 pasadas/día cada uno (antes 8), manteniendo el hueco de 1h entre ambos.

**(2026-08-23) El problema del cambio de hora (CET/CEST) y su solución.**
El cron de GitHub Actions es SIEMPRE en UTC — no admite zona horaria. Para
los tres jobs de arriba esto deja de importar (corren las 24h, cualquier
hora vale), pero `set_lineup` sí depende de una ventana en hora LOCAL
española (el cierre real de jornada, ligado al primer partido de LaLiga,
se piensa en hora española) — y con el cron fijo en UTC, la MISMA hora UTC
cae en una hora local distinta según haya cambio de hora o no: el rango
`15:23-19:43 UTC` fijado el 2026-08-18 corresponde a `17:23-21:43` hora
local SOLO en CEST (verano, UTC+2); en CET (invierno, UTC+1) esa misma
franja UTC equivale a `16:23-20:43` local, una hora antes de lo pensado —
podía cerrar la ventana 1h antes de lo esperado frente al partido más
tardío de una jornada de invierno. Retocar el cron a mano dos veces al año
(y las fechas exactas de cambio de hora varían cada año) es frágil y fácil
de olvidar.

Solución adoptada, pensada para no requerir mantenimiento nunca:
`scheduling.is_within_local_window()` (nuevo módulo, sin dependencia
extra: usa `zoneinfo`, stdlib desde Python 3.9, contra la base de datos de
zonas horaria del sistema, que sabe las fechas exactas del cambio de hora
cada año) convierte la hora UTC real de la ejecución a hora local de
`Europe/Madrid` y compara contra la ventana local real
(`LOCAL_WINDOW_START`/`LOCAL_WINDOW_END` en `jobs/set_lineup.py`,
17:23-21:43). El cron de `set_lineup.yml` se amplía a la UNIÓN de los dos
rangos UTC posibles (`15:23-20:43 UTC`, cubre la ventana local tanto en
CET como en CEST) y el filtro, en el bloque `if __name__ == "__main__":`
(no dentro de `run()`, para no afectar a los tests que llaman a `run()`
directamente sin controlar la hora — mismo criterio que la duplicación del
chequeo de `ENABLE_BOT`), descarta sin tocar la red ni la BD las pasadas
que caen fuera de la ventana local real. Sale más caro en minutos de
Actions (algunas pasadas por jornada arrancan y salen enseguida sin hacer
nada) pero el resultado es correcto en cualquier época del año sin volver
a tocar el cron. Ver `tests/test_scheduling.py` para los casos límite
(verano/invierno) verificados. El mismo patrón queda disponible para
`manage_substitutes` si su margen (hoy generoso en ambos DST, ver
"Banquillo/suplentes") se llegara a estrechar.

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
