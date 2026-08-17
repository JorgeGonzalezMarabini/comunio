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

- [x] Captura de endpoints reales de Futmondo — hecha el 2026-08-17 con Chrome DevTools sobre una liga de prueba real ("Liga de prueba bot", modo Social, creada para la propia sesión de captura)
- [x] Cliente de Futmondo (`clients/futmondo_client.py`) — lectura (plantilla/mercado/alineación/ficha de jugador) y escritura (pujar, poner en venta, cambiar alineación) contra endpoints reales, cada uno marcado explícitamente como confirmado con captura propia o heredado sin verificar de la referencia comunitaria (ver detalle abajo)
- [x] Cliente de stats externas (`clients/laliga_stats_client.py`) — sin cambios frente a la fase de Comunio: Understat contra su endpoint JSON real; FBref descartado (bloquea con Cloudflare)
- [x] Esquema de base de datos (`db/models.py`) — reescrito para los campos reales de Futmondo (`value`/`buyPrice`/`role` en vez de `quotedprice`/`purchaseInfo`/posición en inglés)
- [x] Motor de evaluación (`engine/evaluator.py`) — misma lógica que en la fase de Comunio (normaliza features 0..1 dentro del pool, pesos en `config.py`), adaptada a los nombres de columna nuevos
- [x] Estrategia de pujas (`engine/bidding_strategy.py`) — misma lógica de seguridad (tope por jugador, % de presupuesto por jornada, reserva mínima, prima sobre el VM real) — la protección contra saldo negativo se mantiene por precaución aunque en Futmondo no se ha podido confirmar la regla exacta de penalización (ver sección dedicada)
- [x] Optimizador de alineaciones (`engine/lineup_optimizer.py`) — **reescrito de cero**: Futmondo numera los slots de la alineación de forma totalmente distinta a Comunio (enteros 0..10 en vez de un string de posición + categoría de banquillo), confirmado solo para la formación 4-4-2 (ver TODO en el propio módulo)
- [x] `jobs/sync_data.py` — mapeo real Futmondo + Understat -> `db/models.py`; reconcilia pujas/ventas comparando plantilla y mercado actuales (sin endpoint externo de "mis ofertas", a diferencia de Comunio — ver sección dedicada); cruce con Understat por cascada de fiabilidad (nombre completo -> apellido+equipo -> apellido único -> similitud de texto), no solo nombre exacto — ver sección dedicada
- [x] `jobs/run_market.py` — pipeline completo: candidatos de mercado -> evaluator -> bidding_strategy -> `place_bid()` real
- [x] `jobs/set_lineup.py` — pipeline completo: plantilla -> evaluator (pesos distintos a los de puja) -> dificultad de rival -> `pick_lineup()` -> `build_lineup_changes()` -> `change_lineup()` real. Por ahora solo manda el once titular, sin banquillo (ver TODO)
- [x] `jobs/run_sales.py` — identifica jugadores con plusvalía suficiente (`engine/selling_strategy.py`) y los pone en venta — **cambio de comportamiento deliberado** frente a Comunio: ya no se filtra por "solo comprados por el bot" (ver sección dedicada)
- [~] Jobs y scheduler en GitHub Actions — los 4 YAMLs están actualizados a las variables de entorno de Futmondo (`FUTMONDO_*`); **pendiente**: configurar los nuevos Secrets/Variables en GitHub (ver sección "Activar el cron")
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

**No probado con
ninguna formación distinta de 4-4-2** — `engine/lineup_optimizer.py`
generaliza el mismo criterio (numeración consecutiva, portero al final)
para el resto de formaciones de `config.py`, pero es una extrapolación
razonada, no una confirmación. Tampoco se ha probado el banquillo/
suplentes: `jobs/set_lineup.py` de momento solo manda el once titular.

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
y los 4 jobs, migrada íntegramente al contrato de Futmondo (nada de
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
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
2. **Variables** (misma sección, pestaña `Variables` — no son secretas, solo
   IDs): `FUTMONDO_CHAMPIONSHIP_ID`, `FUTMONDO_USERTEAM_ID`.
3. Hacer `git push` de este repo a `origin` (el agente que escribió este
   código no tiene acceso de push desde este entorno — hace falta hacerlo
   manualmente o darle acceso).
4. Los 4 workflows (`sync_data` cada hora, `run_market` y `run_sales`
   2x/día, `set_lineup` viernes 18:00 UTC) ya tienen el `schedule:`
   activado — correrán solos en cuanto 1-2 estén hechos. Cada uno comitea
   `db/futmondo.db`/`logs/` de vuelta al repo al terminar (si no, cada
   ejecución perdería lo sincronizado en la anterior).

**Antes de apuntar esto a la liga real** (no la de pruebas creada durante
la sesión de captura): revisar unos días de ejecución primero, y tener en
cuenta que `ENABLE_LINEUP_AUTO_SUBMIT=true` por defecto — el bot escribirá
de verdad, sin confirmación manual, en cuanto el cron esté activo. El
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
jobs/       -> entrypoints ejecutados por cron (sync_data, run_market, run_sales, set_lineup)
tests/      -> batería pytest (ver sección "Tests" más arriba)
notifier.py -> resumen por Telegram tras cada job
logs/       -> auditoría de decisiones
```
