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

## 3. Protección de presupuesto en pujas más débil que en Comunio

**Qué pasa**: Futmondo no expone (que se haya encontrado) un endpoint
equivalente al de Comunio para consultar "mis ofertas/pujas pendientes"
(Comunio sí tenía `GET .../offers?current` + regla oficial de penalización
por saldo negativo). La única pista es un campo `bid`/`player.bid` en items
de mercado, sin confirmar su forma exacta.

**Por qué importa**: la protección de presupuesto (`pending_committed`) se
apoya solo en la auditoría local en BD (`db.models.get_open_bids_total()`),
que es más débil que consultar el estado real en el servidor. Si la BD se
pierde o no reconcilia a tiempo, podría subestimarse el compromiso real y
sobrepujar con dinero real.

**Afecta a**: `engine/bidding_strategy.py` (`max_biddable_amount`,
`decide_bids_for_market`), `jobs/run_market.py` (presupuesto disponible).

**Dónde**: `clients/futmondo_client.py:170-197`
(`total_pending_bid_amount`), `engine/bidding_strategy.py:124,333`,
`jobs/run_market.py:34`.

---

## 4. `buyPrice` no distingue "comprado por el bot" de "plantilla inicial"

**Qué pasa**: en Comunio, `purchaseInfo == null` identificaba de forma
fiable a los jugadores de la plantilla inicial (no comprados). En Futmondo,
`buyPrice` aparece en ambos casos (plantilla inicial y compra real por puja),
a veces con valor 0 en plantilla inicial y a veces no, sin haber podido
confirmarlo ganando una puja de prueba.

**Por qué importa**: es un cambio deliberado de comportamiento respecto a
Comunio — `decide_sales()` trata cualquier `buyPrice > 0` como precio de
referencia válido, sin filtrar por origen. Es una asunción de negocio sin
confirmar que afecta directamente a decisiones de venta con dinero real, y
está documentada como reversible si se confirma el campo más adelante.

**Afecta a**: `engine/selling_strategy.py` (`decide_sales`).

**Dónde**: `clients/futmondo_client.py:100-112`, `engine/selling_strategy.py:9`,
`jobs/run_sales.py:13`, `db/models.py:45,96`, `tests/test_selling_strategy.py:14`.

---

## 5. Heurística de detección de lesión sin confirmar con caso real

**Qué pasa**: `is_injury_status()` decide si un jugador está lesionado
buscando subcadenas (`FUTMONDO_INJURY_STATUS_SUBSTRINGS = ("injured",
"lesion", "lesión")`), basado en una referencia comunitaria, no en un caso
real observado (la liga de prueba está en pretemporada, 0 lesionados
vistos). Además hay una inconsistencia sin explicar: el mismo jugador mostró
`status: ""` en el roster y `"ok"` en `/1/userteam/lineup`.

**Por qué importa**: alimenta varias decisiones core del bot. Un falso
negativo/positivo de lesión puede llevar a puntuar mal a un jugador, dejarlo
de titular quien no debería, o no sustituir a quien sí está lesionado.

**Afecta a**: `engine/evaluator.py` (injury_penalty),
`engine/lineup_optimizer.py` (`_rank_healthy_first`),
`jobs/manage_substitutes.py` (detección de "confirmado fuera"),
`engine/squad_risk.py`.

**Dónde**: `clients/futmondo_client.py:147-167`, `jobs/manage_substitutes.py:31`.

---

## 6. `cancel_sale()` nunca confirmado con tráfico real

**Qué pasa**: la llamada `POST /1/market/cancelsell` se implementó siguiendo
el patrón de la referencia comunitaria, pero en la sesión de captura el
botón "Cancelar venta" del frontend no llegó a disparar la petición de red
esperada, así que el endpoint nunca se verificó con tráfico propio.

**Por qué importa**: es un endpoint de escritura sin verificar — si algún
flujo futuro llega a depender de él en producción, el contrato real podría
diferir del asumido.

**Afecta a**: cancelación de ventas propias en Futmondo.

**Dónde**: `clients/futmondo_client.py:406-423`.

---

## 7. Orden cronológico de `average.fitness` sin confirmar

**Qué pasa**: `fitness` (array usado para calcular `last_points`) parece ser
la puntuación de los últimos partidos, pero no se ha podido confirmar si el
más reciente va al final o al principio del array — en la liga de prueba
(pretemporada) siempre viene vacío.

**Por qué importa**: alimenta la feature de "tendencia reciente" del
evaluador. Si el orden asumido es el contrario del real, la señal de
tendencia estaría invertida sin que nadie lo note hasta la temporada regular.

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

## 12. `minutes_played_ratio` no distingue "poco jugado en total" de "poco jugado por partido"

**Qué pasa**: la feature actual mide minutos jugados ÷ (partidos que sí jugó
× 90), no minutos ÷ (partidos totales del equipo × 90). Un jugador con
varias lesiones pero 100% de minutos en los partidos que sí disputó sale con
ratio alto aunque en total haya jugado poco.

**Por qué importa**: refinamiento de una definición de feature, bajo impacto
inmediato. Arreglarlo requeriría guardar el total de partidos del equipo
(disponible en `laliga_stats_client.get_league_data()`).

**Afecta a**: `engine/evaluator.py` (`normalize_pool`).

**Dónde**: `engine/evaluator.py:129,115`.

---

## Ya resuelto (para referencia, no es un pendiente)

**Sesgo de xG por posición en `normalize_pool()`** — arreglado el
2026-08-17. Antes se normalizaba xG (y demás features) contra todo el pool
mezclado, penalizando sistemáticamente a defensas/porteros frente a
delanteros. Corregido normalizando dentro de cada grupo de posición
(POR/DEF/MED/DEL). Ver `tests/test_evaluator.py:58` (test de regresión) y
`README.md` (nota "Arreglado 2026-08-17").
