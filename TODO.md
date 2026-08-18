# TODOs pendientes

Lista única y autocontenida de los TODOs abiertos en el código, para no tener
que ir a buscarlos uno a uno por los archivos fuente. Ordenados de mayor a
menor importancia (criterio: bug real en producción > gate de seguridad que
bloquea funcionalidad > riesgo de dinero real > asunción de negocio sin
confirmar > limitación de API externa > mejora futura/calibración).

Cada entrada indica **qué pasa**, **por qué importa**, **a qué afecta** y
**dónde está** en el código (archivo:línea) por si hace falta profundizar.
Última revisión: 2026-08-18.

---

## 1. ~~Rotar/sustituir titulares ya colocados en el campo~~ (arreglado y confirmado en vivo, 2026-08-18)

**Qué pasaba**: se creía que sustituir a alguien en el campo o el
banquillo era un solo `change` con `"to"` (quien entra) + `"from"`
(quien sale) a la vez. Con esa forma, Futmondo rechazaba la operación con
`"api.error.in_field"` o `"api.error.in_bench"` según de dónde viniera el
que entra.

**Investigado abriendo el navegador contra la propia app web de Futmondo**
(a petición del usuario, misma liga de pruebas): se interceptó `fetch` en
la página para capturar los payloads reales mientras se hacía a mano, en
la UI, el intercambio de un titular DEF (Tárrega) por su suplente
(Cortés). **Hallazgo clave**: Futmondo NUNCA acepta un `change` con
`"to"` y `"from"` juntos. Cada `change` es una de estas dos operaciones,
mutuamente excluyentes:

  1. **Vaciar**: `{"from": <id>, "position": <slot>, "isBench": <bool>}`
     (sin `"to"`) — el jugador queda como reserva libre.
  2. **Rellenar**: `{"to": <id>, "position": <slot>, "isBench": <bool>}`
     (sin `"from"`) — solo funciona si el slot está vacío.

  Confirmado en vivo en ambas direcciones (bench→campo con `"api.error.
  in_bench"`, campo→banquillo con `"api.error.in_field"`) y que mandar el
  par junto en una sola llamada HTTP falla igual (no es un problema de
  atomicidad). Después, siguiendo el patrón real de la UI (vaciar origen
  y destino por separado, luego rellenar), se completó el intercambio
  Tárrega↔Cortés de verdad contra la cuenta real y se revirtió al estado
  inicial — confirmado con `get_lineup()` antes/después de cada paso.

**Arreglado**: `engine/lineup_optimizer.py` — `build_lineup_changes()`,
`build_bench_changes()` y `build_substitution_changes()` ahora generan
siempre parejas separadas de vaciar+rellenar (nunca un `change`
combinado), deduplicando por jugador cuando el mismo aparece como
"ocupante a desalojar" desde un grupo y como "entrante a vaciar" desde
otro (ej. un jugador "multiposition"). Esto además **simplifica** el
diseño anterior: ya no hace falta ningún caso especial ni banquillo libre
para el caso "multiposition" — vaciar el origen (campo o banquillo, en
cualquier posición) siempre es posible. `jobs/set_lineup.py` y
`jobs/manage_substitutes.py` actualizados para leer resultados de
`change_lineup()` que ya no siempre tienen `"to"` (los de vaciar solo
tienen `"from"`) — `manage_substitutes.py` en particular ahora agrupa los
4 `changes`/resultados de cada sustitución en bloques, en vez de asumir 2.

**Verificado con tests** (`tests/test_lineup_optimizer.py`,
`tests/test_jobs_manage_substitutes.py`) y **confirmado en vivo contra la
liga de pruebas real** (2026-08-18) para el caso de sustitución titular↔
suplente. Sin confirmar todavía con una prueba real específica el caso
"multiposition" de `build_lineup_changes()` (mismo mecanismo, pero esa
combinación exacta de grupos cruzados no se ha dado aún en la liga de
pruebas) — bajo riesgo, ya que usa el mismo primitivo (vaciar+rellenar)
confirmado para la sustitución.

**Afecta a**: envío de cambios de alineación (`jobs/set_lineup.py`) y
sustituciones manuales (`jobs/manage_substitutes.py`, activado en
producción — ahora debería funcionar de verdad).

**Dónde**: `engine/lineup_optimizer.py` (`build_lineup_changes`,
`build_bench_changes`, `build_substitution_changes`),
`clients/futmondo_client.py:442-518` (`change_lineup`),
`jobs/set_lineup.py`, `jobs/manage_substitutes.py`,
`tests/test_lineup_optimizer.py`, `tests/test_jobs_manage_substitutes.py`.

---

## 2. Numeración de slots de alineación solo confirmada para la formación 4-4-2

**Qué pasa**: el mapeo de `position` (enteros 0..10, delanteros →
centrocampistas → defensas → portero) que usa Futmondo para colocar la
alineación solo se ha verificado con una prueba real en formación 4-4-2. No
se sabe si la numeración consecutiva generalizada es correcta para otras
formaciones (4-2-4, 3-6-1, 3-3-4, 4-6-0, 5-2-3) o si Futmondo usa una tabla
fija de slots por posición distinta.

**Por qué importa**: es un gate de seguridad activo — `config.py` mantiene
`ENABLE_LINEUP_AUTO_SUBMIT` en `false` por defecto salvo que la formación de
la liga real sea 4-4-2, precisamente por este TODO. Mientras no se confirme,
el envío automático de alineación queda limitado a esa formación.

**Afecta a**: `engine/lineup_optimizer.py`, `jobs/set_lineup.py`, envío real
de alineación a Futmondo.

**Dónde**: `engine/lineup_optimizer.py:66-77,137`, `config.py:218`,
`clients/futmondo_client.py:315,483`, `jobs/set_lineup.py:13`.

---

## 3. ~~Protección de presupuesto en pujas más débil que en Comunio~~ (confirmado en vivo y arreglado, 2026-08-18)

**Qué pasaba**: Futmondo no expone (que se hubiera encontrado hasta ahora)
un endpoint equivalente al de Comunio para consultar "mis ofertas/pujas
pendientes" (Comunio sí tenía `GET .../offers?current` + regla oficial de
penalización por saldo negativo). La única pista era un campo `bid`/
`player.bid` en items de mercado, sin confirmar su forma exacta. La
protección de presupuesto (`pending_committed`) se apoyaba solo en la
auditoría local en BD, más débil que consultar el estado real en el
servidor.

**Investigado con una llamada de solo lectura contra la cuenta real**
(2026-08-18, `get_market()`, 12 pujas propias ya colocadas ese día y el
anterior): confirmado que cada item de `get_market()` en el que tenemos
puja pendiente trae `"bid": {"id": <str>, "price": <int>}`, ausente por
completo en los items donde no hemos pujado — cruzado 1:1 contra nuestra
tabla `bids` local, sin ningún "bid" huérfano de otro manager. Sí es,
efectivamente, el equivalente de Futmondo al `GET .../offers?current` de
Comunio.

**Hallazgo adicional durante la misma investigación**: en 5 de los 12
casos reales el bot había pujado DOS VECES sobre el mismo jugador todavía
sin resolver (una "escalada" de precio entre dos ejecuciones del cron).
Ambas llamadas a `place_bid()` devolvieron `"api.general.ok"` (ninguna
lanzó error), pero `"bid.price"` en el mercado en vivo siempre coincidía
con el importe de la PRIMERA puja, nunca con el de la segunda — Futmondo
acepta la llamada pero ignora el nuevo importe si ya había una puja
abierta sobre ese jugador. Esto también significa que la auditoría local
(sumar `bids.amount` sin más) duplicaba el compromiso real de esos 5
jugadores.

**Arreglado**:
- `clients/futmondo_client.py`: nueva `real_pending_bid_amount(market_items)`,
  suma `item["bid"]["price"]` de la respuesta de `get_market()` — fuente
  confirmada del propio Futmondo.
- `jobs/run_market.py`: `pending_committed` ahora es
  `max(db.models.get_pending_bid_amount(), real_pending_bid_amount(...))`
  — nunca solo la auditoría local, así que una BD perdida/no reconciliada
  a tiempo ya no puede subestimar el compromiso real. Además, excluye de
  los candidatos a evaluar cualquier jugador con una puja local todavía
  `'placed'` (`db.models.get_open_bids()`), evitando reintentar una
  "mejora" de puja que Futmondo ignora en silencio.

**Verificado con tests**: `tests/test_futmondo_client.py`
(`test_real_pending_bid_amount_*`), `tests/test_jobs_run_market.py`
(cross-check BD-vacía-pero-mercado-real y skip de candidato ya pujado). No
se ha podido confirmar todavía si existe alguna otra vía distinta de "ya
tenía una puja abierta" para que `place_bid()` ignore el importe (p. ej.
algún límite de incremento mínimo) — bajo impacto porque ahora se evita
por completo la re-puja sobre un candidato ya pendiente.

**Afecta a**: `engine/bidding_strategy.py` (`max_biddable_amount`,
`decide_bids_for_market`), `jobs/run_market.py` (presupuesto disponible y
selección de candidatos), `clients/futmondo_client.py` (`place_bid`).

**Dónde**: `clients/futmondo_client.py` (`real_pending_bid_amount`,
`place_bid`), `engine/bidding_strategy.py`, `jobs/run_market.py`,
`tests/test_futmondo_client.py`, `tests/test_jobs_run_market.py`.

---

## 4. ~~`buyPrice` no distingue "comprado por el bot" de "plantilla inicial"~~ (resuelto sin depender de ese campo, 2026-08-18)

**Qué pasaba**: en Comunio, `purchaseInfo == null` identificaba de forma
fiable a los jugadores de la plantilla inicial (no comprados). En Futmondo,
`buyPrice` aparece en ambos casos (plantilla inicial y compra real por puja),
a veces con valor 0 en plantilla inicial y a veces no, sin haber podido
confirmarlo ganando una puja de prueba. `decide_sales()` trataba cualquier
`buyPrice > 0` como precio de referencia válido, sin filtrar por origen —
una asunción de negocio sin confirmar que afectaba directamente a
decisiones de venta con dinero real.

**Arreglado**: en vez de seguir esperando a confirmar el significado exacto
de `buyPrice` (nunca se pudo ganar una puja de prueba para eso), se
encontró una fuente propia que ya resolvía el mismo problema sin depender
de ningún campo ambiguo de Futmondo: la tabla local `bids`, que
`jobs/sync_data.py` ya reconcilia a `status='won'` cuando el bot gana una
puja real (comparando la plantilla antes/después de cada sync, ver TODO
#1 más abajo para el mecanismo). Esa tabla es exactamente "qué compró el
bot y a qué precio" — el mismo dato que `purchaseInfo != null` daba en
Comunio, pero de nuestra propia auditoría, no de la API de Futmondo.

Se añadió `db.models.get_won_bid_prices()` (última puja `'won'` por
jugador, por si se vendió y se recompró más tarde) y
`engine/selling_strategy.decide_sales()` ahora recibe ese resultado como
`bought_by_bot: dict[player_id, precio_pagado]` — solo esos jugadores son
candidatos, y el precio de referencia de la plusvalía es el importe
realmente pagado, no `buyPrice`. `jobs/run_sales.py` pasa
`get_won_bid_prices()` en cada ejecución.

**Verificado con tests**: `tests/test_db_models.py`
(`test_get_won_bid_prices_*`), `tests/test_selling_strategy.py` (reescrito
para pasar `bought_by_bot` en vez de `buyPrice` en el squad, incluyendo un
test explícito de que un `buyPrice > 0` sin puja ganada ya NO cuenta) y
`tests/test_jobs_run_sales.py` (reescrito para registrar pujas `'won'` en
BD en vez de anotar `buyPrice` en el roster falso). Sin poder confirmar
todavía contra una puja real ganada en producción (sigue pendiente de que
se dé el caso), pero el mecanismo ya no depende de esa confirmación.

**Afecta a**: `engine/selling_strategy.py` (`decide_sales`), `jobs/run_sales.py`.

**Dónde**: `clients/futmondo_client.py:100-113`, `engine/selling_strategy.py`,
`jobs/run_sales.py`, `db/models.py` (`get_won_bid_prices`),
`tests/test_selling_strategy.py`, `tests/test_jobs_run_sales.py`,
`tests/test_db_models.py`.

---

## 5. ~~Heurística de detección de lesión sin confirmar con caso real~~ (confirmado con datos reales de producción, 2026-08-18)

**Qué pasaba**: `is_injury_status()` decidía si un jugador está lesionado
buscando subcadenas (`FUTMONDO_INJURY_STATUS_SUBSTRINGS = ("injured",
"lesion", "lesión")`), basado solo en una referencia comunitaria (que
asumía valores en español), sin un caso real observado — la liga de
prueba usada en la captura inicial (2026-08-17) estaba en pretemporada,
0 lesionados vistos.

**Investigado consultando la BD real** (`db/futmondo.db`, sincronizada en
producción por `jobs/sync_data.py` vía cron desde el primer sync,
2026-08-17T18:41, hasta hoy): el campo `status` real de Futmondo SÍ trae
valores de lesión/duda, y son en **inglés**, no en español como asumía la
referencia comunitaria:

  - `""` y `"ok"` — sano (los dos valores ya conocidos; por qué hay dos
    para lo mismo sigue sin explicarse, pero ninguno es un falso positivo
    de lesión con la heurística actual ni con la corregida).
  - `"doubt"` — duda. Visto en Vivian (Athletic de Bilbao, DEF), Pablo
    Durán (Celta de Vigo, DEL) y Boayar (Elche, MED), estable en todos los
    syncs desde el primero. **Este valor NO lo detectaba la heurística
    original** (no contiene "injured" ni "lesion"/"lesión") — un bug real:
    estos 3 jugadores se venían tratando como sanos.
  - `"injured2"` — lesionado (tier numerado). Visto en Sergi Canós
    (Valencia, MED), estable en todos los syncs. Sí lo detectaba la
    heurística original (subcadena "injured"). Sin confirmar todavía si
    existen otros tiers ("injured1", "injured3"...).

**Arreglado**: `FUTMONDO_INJURY_STATUS_SUBSTRINGS` en
`clients/futmondo_client.py` ahora incluye `"doubt"` (confirmado real).
Se mantiene coincidencia de subcadena en vez de una lista cerrada porque
"injured2" sugiere tiers sin confirmar todavía. "lesion"/"lesión" se
dejan como colchón defensivo sin coste (no son subcadena de ningún valor
sano confirmado) aunque la evidencia real ya no los respalda como
necesarios.

**Verificado con tests** (`tests/test_futmondo_client.py::test_is_injury_status`,
casos añadidos para `"doubt"` e `"injured2"`) y con los valores reales
observados en `db/futmondo.db` en producción, no con una prueba manual
puntual — el hallazgo viene de datos ya acumulados por el cron en
funcionamiento normal.

**Afectaba a**: `engine/evaluator.py` (injury_penalty),
`engine/lineup_optimizer.py` (`_rank_healthy_first`),
`jobs/manage_substitutes.py` (detección de "confirmado fuera"),
`engine/squad_risk.py` — los 3 jugadores en "doubt" se puntuaban y
priorizaban como sanos hasta este arreglo.

**Dónde**: `clients/futmondo_client.py:147-181`,
`jobs/manage_substitutes.py:31`, `tests/test_futmondo_client.py`.

---

## 6. ~~`cancel_sale()` nunca confirmado con tráfico real~~ (confirmado en vivo, 2026-08-18)

**Qué pasaba**: la llamada `POST /1/market/cancelsell` se implementó
siguiendo el patrón de la referencia comunitaria, pero en la sesión de
captura original el botón "Cancelar venta" del frontend no llegó a
disparar la petición de red esperada, así que el endpoint nunca se
verificó con tráfico propio.

**Investigado con una prueba real de extremo a extremo** (con permiso
explícito del usuario, dado que exponía brevemente un jugador real a
compra por otro manager/el "Computer"): se eligió a Sergi Canós
(lesionado, `status=injured2`, mínimo valor de la plantilla, 1.000.000€)
para minimizar impacto si alguien pujaba en el intervalo. Secuencia
completa contra la API real:

  1. `get_roster()` — estado inicial del jugador (`market: false`).
  2. `list_for_sale(player_id, 100_000_000)` — precio muy por encima de
     mercado a propósito, disuasorio -> `{"code": "api.general.ok"}`.
  3. `get_my_players_in_market()` — confirma el listado real Y, de paso,
     la forma exacta del item (antes sin confirmar): `{"id", "name",
     "slug", "role", "role2", "photo", "points", "value", "team", "logo",
     "status", "expirationDate", "price", "buyPrice", "isClause",
     "bids": [], "change", "average"}`.
  4. `cancel_sale(player_id)` -> `{"code": "api.general.ok"}`.
  5. `get_my_players_in_market()` — vacío de nuevo.
  6. `get_roster()` — jugador de vuelta, idéntico al paso 1 (`market:
     false`, mismo `value`/`buyPrice`/`status`).

Nadie pujó por el jugador en el intervalo (lesionado + precio disuasorio).

**Arreglado**: `clients/futmondo_client.py` — `cancel_sale()` y
`get_my_players_in_market()` marcados **100% confirmado** con la
respuesta/shape real documentada; `POST /1/market/cancelsell` movido a la
sección de endpoints confirmados en el docstring del módulo.

**Sin confirmar todavía**: qué código de error devuelve `cancel_sale()` si
se intenta cancelar un listado que ya no existe (vendido o cancelado
antes) — no se dio ese caso en esta prueba. Bajo impacto: `list_for_sale`/
`cancel_sale` ya manejan `FutmondoOfferError` igual que el resto de
escrituras.

**Afecta a**: cancelación de ventas propias en Futmondo.

**Dónde**: `clients/futmondo_client.py` (`cancel_sale`,
`get_my_players_in_market`).

---

## 7. Orden cronológico de `average.fitness` sin confirmar

**Qué pasa**: `fitness` (array usado para calcular `last_points`) parece ser
la puntuación de los últimos partidos, pero no se ha podido confirmar si el
más reciente va al final o al principio del array.

**Investigado con una llamada de solo lectura contra la cuenta real**
(2026-08-18, `get_roster()` + `get_market()`): la temporada real ya ha
empezado (jornada 1 en curso) — `fitness` ya NO viene siempre vacío como
cuando se escribió este TODO (liga de prueba en pretemporada). De 10
jugadores de la plantilla propia y 24 del mercado, todos los que ya
disputaron su partido de jornada 1 traen `fitness` de **longitud 1** (p.
ej. `Tenaglia` con `matches: 1, fitness: [16]`), y los que aún no han
jugado esa jornada (algunos partidos de la 1ª jornada todavía no se habían
disputado en el momento de la consulta) traen `matches: 0, fitness: []`.

**Sigue sin poder confirmarse**: con longitud máxima 1 en todos los casos
vistos todavía no hay forma de saber si un futuro `fitness[1]` se
añadiría al final (más reciente al final) o se insertaría al principio
(desplazando el resto). Hace falta repetir esta misma consulta cuando ya
se haya jugado la jornada 2 y comparar qué elemento del array nuevo
coincide con el `points` de la jornada 1 ya conocido.

**Por qué importa**: alimenta la feature de "tendencia reciente" del
evaluador. Si el orden asumido es el contrario del real, la señal de
tendencia estaría invertida sin que nadie lo note hasta que haya
suficientes jornadas para que la tendencia importe.

**Afecta a**: `jobs/sync_data.py` (`_upsert_player_and_snapshot`,
`last_points`) → `engine/evaluator.py` (feature "trend").

**Dónde**: `clients/futmondo_client.py:83-89`, `jobs/sync_data.py:106`.

---

## 8. Fiabilidad y timing de Fotmob (alineaciones reales)

**Qué pasa**: dos incertidumbres sobre el cliente de alineaciones reales
(Fotmob, tras descartar API-Football, SofaScore, ESPN y TheSportsDB): (a) no
se sabe si los ~41 minutos antes del partido observados como momento de
cambio de `lineupType` de "predicted" a "standard" son representativos; (b)
es una API no oficial y no documentada, que podría cambiar de forma o
bloquear sin aviso, como ya pasó con SofaScore.

**Por qué importa**: menos urgente que el resto porque
`config.ENABLE_REAL_LINEUP_CHECK` está en `false` por defecto — el bot no
depende de esto todavía en producción.

**Afecta a**: `jobs/manage_substitutes.py` (detección de titulares sanos
fuera del once real).

**Dónde**: `clients/football_lineups_client.py:75-95`, `config.py:103`.

---

## 9. Futmondo no tiene endpoint de login conocido

**Qué pasa**: el token y el userid se obtienen a mano copiando
`localStorage["flutter.token"]` / `"flutter.id_user"` tras loguearse en la
app; no se ha encontrado un `POST /login` (ni en captura propia ni en la
referencia comunitaria). No se sabe cuánto dura el token ni si hay refresh
automático.

**Por qué importa**: limitación operativa conocida y con workaround
funcionando — si el token expira, hace falta intervención manual para
volver a capturarlo, pero no bloquea nada mientras tanto.

**Afecta a**: autenticación de todo el cliente de Futmondo (todos los jobs
dependen de esto).

**Dónde**: `clients/futmondo_client.py:26`.

---

## 10. Sin investigar: activación del entrenador automático vía API / plan de la liga real

**Qué pasa**: sin el "entrenador automático" (función de pago de Futmondo)
activado, el suplente que coloca el bot es decorativo — nadie sustituye de
verdad a un titular con 0 minutos salvo que `jobs/manage_substitutes.py` lo
haga a mano. No se ha investigado si existe un toggle vía API para activarlo,
ni si la liga real de destino tiene plan PRO (gratis) o habría que gestionar
"mondos" (moneda de pago).

**Por qué importa**: es una pregunta de producto/negocio abierta, no un bug
de código — pero condiciona si las sustituciones automáticas del bot llegan
a tener efecto real en el juego.

**Afecta a**: todo el flujo de sustituciones (`jobs/manage_substitutes.py`,
`pick_substitutes`/`build_bench_changes` en `engine/lineup_optimizer.py`).

**Dónde**: solo documentado en `README.md` (sección "Banquillo/suplentes"),
sin TODO explícito en código.

---

## 11. Pesos del evaluador no calibrados con datos reales

**Qué pasa**: `config.EVALUATOR_WEIGHTS` / `LINEUP_EVALUATOR_WEIGHTS` son "un
punto de partida razonado", no calibrados todavía contra resultados reales
de la liga.

**Por qué importa**: nota de mejora futura, no bug ni asunción rota —
pendiente de ajuste empírico con el tiempo, según se acumulen datos de
temporada real.

**Afecta a**: `engine/evaluator.py` (`score_player`) → pujas y alineación.

**Dónde**: `engine/evaluator.py:47,115`.

---

## 12. ~~`minutes_played_ratio` no distingue "poco jugado en total" de "poco jugado por partido"~~ (arreglado, 2026-08-18)

**Qué pasaba**: la feature medía minutos jugados ÷ (partidos que sí jugó ×
90), no minutos ÷ (partidos totales del equipo × 90). Un jugador con varias
lesiones pero 100% de minutos en los partidos que sí disputó salía con ratio
alto aunque en total hubiera jugado poco.

**Investigado con una llamada de solo lectura a Understat** (2026-08-18,
`get_league_data()` real, jornada 1 de LaLiga en curso): confirmado que
`teams[id]["history"]` trae solo partidos YA DISPUTADOS por ese equipo, con
resultado real (`result`/`scored`/`missed`, ej. Sevilla con 1 entrada tras
ganar su primer partido) — `len(history)` es exactamente "partidos jugados
por el equipo esta temporada", el dato que le faltaba a la feature.

**Arreglado**:
- `db/models.py`: nueva columna `external_stats.team_games` (con migración
  `_ensure_column()` en `init_db()`, necesaria porque `db/futmondo.db` está
  versionado en el repo con datos ya acumulados — `CREATE TABLE IF NOT
  EXISTS` no la habría añadido sola). `get_player_features()` la incluye.
- `jobs/sync_data.py`: nueva `_team_games_by_title()` (a partir de
  `league_data["teams"]`, ANTES del merge de fallback). Se guarda solo si
  la fila es de la temporada ACTUAL — si viene del fallback a temporada
  anterior (`get_league_data_with_fallback()`), se guarda `NULL`: mezclar
  `minutes_played`/`games` de una temporada completa distinta con el
  `team_games` (todavía bajo) de la actual daría un ratio sin sentido.
- `engine/evaluator.py`: `normalize_pool()` usa
  `minutes_played / (team_games * 90)` cuando `team_games` está disponible;
  si no (equipo sin `history` aún en Understat, o fila de fallback), cae de
  vuelta a la aproximación anterior (`minutes_played / (games * 90)`).

**Verificado con tests**: `tests/test_evaluator.py` (caso explícito de un
jugador con lesiones recurrentes que ya no sale con ratio máximo frente a un
titular fijo, y el fallback sin `team_games`), `tests/test_jobs_sync_data.py`
(`team_games` sale de `history` del equipo, no de los partidos del jugador;
`NULL` en filas de fallback a temporada anterior) y `tests/test_db_models.py`
(columna nueva en las features, y migración sobre un esquema "viejo" sin
romper filas existentes). Migración aplicada también contra `db/futmondo.db`
real (348 filas de `external_stats` intactas, columna nueva en `NULL` hasta
el próximo sync).

**Afecta a**: `engine/evaluator.py` (`normalize_pool`), `jobs/sync_data.py`,
`db/models.py`.

**Dónde**: `engine/evaluator.py` (`normalize_pool`), `jobs/sync_data.py`
(`_team_games_by_title`, `_upsert_external_stats`), `db/models.py`
(`_ensure_column`, `external_stats.team_games`),
`clients/laliga_stats_client.py:75-76` (`get_league_data`, docstring de
`history`).

---

## 13. ~~Sin forma conocida de cancelar una puja de compra propia~~ (endpoint encontrado y confirmado en vivo, 2026-08-18)

**Qué pasaba**: `place_bid()` no tenía contrapartida — una vez colocada una
puja, no había ningún método en el cliente ni endpoint documentado (ni en
captura propia ni en vicenteqa/futmondo-utils, que tampoco lo tiene) para
deshacerla. Surgió al evaluar si merecía la pena que `jobs/run_market.py`
cancelara una puja de score bajo para pujar por un candidato mejor aparecido
después — sin el endpoint, la idea era inviable de raíz.

**Investigado inspeccionando la UI real** (con permiso explícito del
usuario, vía automatización de Chrome sobre su sesión ya logueada): en el
panel de detalle de un jugador del mercado, la pestaña "Comprar" muestra un
botón que cambia de "Modificar oferta" a **"Cancelar oferta"** (rojo)
exactamente cuando el importe del campo coincide con el de tu puja abierta —
no es un botón separado, es el mismo control cambiando de intención según el
valor. Interceptando `fetch`/`XMLHttpRequest` de la página (con los valores
de `token`/`userid` redactados antes de leer cualquier resultado, nunca
expuestos) se capturó la petición real al pulsarlo:

    POST /1/market/cancelbid
    body.query: {..., "bid": <bid_id>}
    respuesta real: {"answer": {"code": "api.general.ok"}}

Prueba de extremo a extremo sobre una puja real (1.072.021€ sobre Cairney,
propiedad del "Computer", en "Liga de prueba bot"): tras cancelar, el
importe desapareció de `"Ofertas"` (compromiso total mostrado en la UI) y
de la lectura siguiente de `get_market()`. Efecto secundario observado en
la misma prueba (no buscado, pero confirma el diseño): al quedar Cairney
sin puja local, el cron real de `run_market` — corriendo en paralelo sobre
la cuenta real — volvió a pujar por él con un importe distinto en su
siguiente pasada, igual que predice el docstring de `place_bid()`.

**Arreglado**: `clients/futmondo_client.py` — nuevo método `cancel_bid(bid_id)`,
marcado **100% confirmado**; `POST /1/market/cancelbid` añadido a la sección
de endpoints confirmados del docstring del módulo y a la tabla de `README.md`
(que además tenía `cancel_sale` marcado como "solo referencia comunitaria"
por desactualización — corregido de paso a "captura propia", ver TODO.md #6).

**Integrado en `jobs/run_market.py` (mismo día, 2026-08-18)**: nueva fase
"cancelar+pujar mejor", DESPUÉS del loop normal de pujas, para candidatos
buenos (`engine.bidding_strategy.is_price_worth_bidding`, score/precio
válidos) que se quedan fuera solo por presupuesto/tope — no por calidad. La
decisión de qué sacrificar es pura (`engine.bidding_strategy.
find_cancel_swap_candidates`, testeada a fondo sin red): solo propone
cancelar la puja abierta MÁS FLOJA si el candidato la supera en score por
`config.BIDDING_CANCEL_SWAP_MIN_MARGIN` (0.25 por defecto — bastante por
encima de cualquier boost de prioridad, 0.15 cada uno, para no cancelar por
ruido) y si a esa puja le quedan más de
`config.BIDDING_CANCEL_SWAP_MIN_HOURS_BEFORE_EXPIRY` (6h) antes de expirar.
Nunca cancela una puja sin id real de Futmondo confirmado en el
`get_market()` de esa misma pasada — excluye automáticamente cualquier puja
colocada en el mismo run. Tope de `config.BIDDING_MAX_CANCEL_SWAPS_PER_RUN`
(1 por defecto) mientras no hay histórico real de esta feature. Un fallo a
medias (cancela pero `place_bid()` posterior falla) queda auditado en
`bids` como `'cancelled'`+`'failed'`, nunca silencioso.

**Sin confirmar todavía**:
- Qué código de error devuelve si se intenta cancelar una puja que ya no
  existe (ganada, perdida, o cancelada antes) — no se dio ese caso en la
  prueba real.
- Los valores de margen/horas/máximo de swaps son de partida, sin histórico
  real todavía — pensados para afinarse con los primeros `notify()` de
  producción (el resumen del job ya reporta explícitamente cada swap
  ejecutado o fallido).

**Afecta a**: `clients/futmondo_client.py` (`cancel_bid`), `jobs/run_market.py`,
`engine/bidding_strategy.py`, `db/models.py` (`get_open_bids`,
`update_bid_status`), `config.py`.

**Dónde**: `clients/futmondo_client.py` (`cancel_bid`), `jobs/run_market.py`
(`run`), `engine/bidding_strategy.py` (`is_price_worth_bidding`,
`find_cancel_swap_candidates`), `db/models.py`, `config.py`, `README.md`
(tabla de endpoints).

---

## Ya resuelto (para referencia, no es un pendiente)

**Sesgo de xG por posición en `normalize_pool()`** — arreglado el
2026-08-17. Antes se normalizaba xG (y demás features) contra todo el pool
mezclado, penalizando sistemáticamente a defensas/porteros frente a
delanteros. Corregido normalizando dentro de cada grupo de posición
(POR/DEF/MED/DEL). Ver `tests/test_evaluator.py:58` (test de regresión) y
`README.md` (nota "Arreglado 2026-08-17").
