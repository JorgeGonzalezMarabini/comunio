"""
Job: decide y fija la alineación antes del cierre de jornada.

Asume que jobs/sync_data.py ya corrió antes en el cron. La decisión (quién
juega, con qué formación y por qué) SIEMPRE se calcula y se audita en
`lineup_decisions`, pase lo que pase con el envío a Futmondo.

El envío real a la API (client.change_lineup) usa el mapeo de slots
CONFIRMADO AL 100% pero solo para la formación 4-4-2 (2026-08-17, prueba
real interceptando la llamada POST real del frontend a
/2/userteam/changeplayer para cada jugador + relectura con get_lineup()
confirmando la posición — ver clients/futmondo_client.py y
engine/lineup_optimizer.py para el detalle y el TODO sobre otras
formaciones). Sigue detrás de config.ENABLE_LINEUP_AUTO_SUBMIT por si se
prefiere revisar antes de dejarlo escribir solo contra una liga real.

`client.change_lineup()` manda una llamada HTTP por jugador y nunca
lanza en el primer fallo — ver su docstring para el bug real confirmado
en producción (2026-08-17) que llevó a este diseño: mandar los 11 cambios
de golpe en una sola llamada hacía que Futmondo solo aplicara el primero,
sin ningún error que lo delatara, dejando la alineación mostrada en la
web totalmente desincronizada de lo que decía la notificación de
Telegram. Aquí se audita cada jugador que falló al colocar por separado
(`lineup_decisions.submitted_to_futmondo` solo es 1 si todos los cambios
necesarios se aplicaron, no si la llamada "no lanzó excepción").

Segundo bug real encontrado el mismo día, al investigar por qué seguía
sin coincidir la alineación tras el primer arreglo: sustituir un slot que
YA tiene un jugador distinto exige mandar `"from"` con el id del que sale
(ver `engine.lineup_optimizer.build_lineup_changes`) — sin él, Futmondo
rechaza el cambio con `"api.error.not_allowed"`. Por eso este job SIEMPRE
lee `client.get_lineup()` antes de construir los cambios: sin saber qué
hay ya en cada slot, no se puede rellenar `"from"` cuando hace falta.

También manda el banquillo/suplentes: un slot FIJO por posición (ver
`engine.lineup_optimizer.BENCH_SLOT_BY_POSITION`), CONFIRMADO AL 100%
(2026-08-17, los 4 añadidos uno a uno desde la web + relectura con
get_lineup() confirmando la posición numérica de cada uno). Solo se
manda un suplente por posición (no una lista) porque Futmondo solo tiene
sitio para eso.
"""
import json
from datetime import datetime, timezone

import config
from clients.futmondo_client import FutmondoClient
from clients.laliga_stats_client import get_league_data, next_match_difficulty
from db.models import get_connection, get_player_features
from engine.evaluator import evaluate_players
from engine.lineup_optimizer import apply_fixture_difficulty, build_bench_changes, build_lineup_changes, pick_lineup, pick_substitutes
from engine.squad_risk import assess_squad_depth, depth_warnings
from notifier import notify, notify_on_crash


def run():
    if not config.ENABLE_BOT:
        print("set_lineup: ENABLE_BOT=false, no se ejecuta.")
        return

    client = FutmondoClient()

    roster_response = client.get_roster()
    roster_ids = {str(p["id"]) for p in roster_response.get("answer", [])}
    if not roster_ids:
        notify("set_lineup: la plantilla vino vacía, no se puede elegir alineación.")
        return

    all_players = get_player_features(only_on_market=False)
    squad_raw = [p for p in all_players if p["id"] in roster_ids]
    missing = roster_ids - {p["id"] for p in squad_raw}
    if missing:
        notify(f"set_lineup: {len(missing)} jugador(es) de la plantilla sin datos en BD todavía (¿sync_data reciente?).")

    # Pesos de alineación, NO los de puja: el precio de un jugador ya en tu
    # plantilla es coste hundido, no debe influir en quién juega.
    ranked = evaluate_players(squad_raw, weights=config.LINEUP_EVALUATOR_WEIGHTS)

    league_data = get_league_data()
    teams = {p["team"] for p in ranked if p.get("team")}
    difficulty_by_team = {}
    for team in teams:
        difficulty = next_match_difficulty(league_data, team)
        if difficulty is not None:
            difficulty_by_team[team] = difficulty

    adjusted = apply_fixture_difficulty(ranked, difficulty_by_team)

    # Riesgo estructural de plantilla (independiente de la alineación de
    # esta jornada): posiciones sin ningún suplente disponible -> perder al
    # único titular de esa posición (cláusula de rescisión, lesión, sanción)
    # dejaría un hueco en la alineación. Solo informa por ahora, no
    # bloquea ni cambia la decisión.
    depth = assess_squad_depth(adjusted, formation=config.DEFAULT_FORMATION)
    risk_warnings = depth_warnings(depth)

    try:
        lineup = pick_lineup(adjusted, formation=config.DEFAULT_FORMATION)
    except ValueError as e:
        notify(f"set_lineup: no se pudo formar el once ({e}).")
        return

    by_id = {p["id"]: p for p in adjusted}
    starters_summary = [
        f"{by_id[pid]['name']} ({by_id[pid]['position']}, expected_score={by_id[pid]['expected_score']:.3f})"
        for pid in lineup["starters"]
    ]

    bench_players = [p for p in adjusted if p["id"] in lineup["bench"]]
    substitutes_by_position = pick_substitutes(bench_players)
    substitutes_summary = [
        f"{pos}: {by_id[pid]['name']}" for pos, pid in substitutes_by_position.items() if pid is not None
    ]

    reason = (
        f"Formación {lineup['formation']}, elegidos por expected_score "
        f"(evaluator + dificultad del próximo rival): " + "; ".join(starters_summary)
        + ". Suplentes: " + (", ".join(substitutes_summary) if substitutes_summary else "ninguno disponible")
    )

    now = datetime.now(timezone.utc).isoformat()
    submitted = False
    submit_error = None
    failed_players = []  # [(nombre, motivo)] -- cambios individuales que fallaron, ver docstring del módulo
    changes_needed = 0  # 0 si la alineación actual ya coincidía del todo (ver build_lineup_changes)

    if config.ENABLE_LINEUP_AUTO_SUBMIT:
        try:
            current_lineup_answer = client.get_lineup().get("answer", {})
            current_lineup_by_position = {p["position"]: p["id"] for p in current_lineup_answer.get("players", [])}
            current_bench_by_position = {
                p["position"]: p["id"] for p in current_lineup_answer.get("bench", {}).get("players", [])
            }
            changes = build_lineup_changes(adjusted, lineup["starters"], current_lineup_by_position)
            changes += build_bench_changes(substitutes_by_position, current_bench_by_position)
            changes_needed = len(changes)
            results = client.change_lineup(changes)
            for r in results:
                if not r["ok"]:
                    failed_players.append((by_id[r["change"]["to"]]["name"], r["answer_or_error"]))
            submitted = not failed_players  # solo "enviada" de verdad si todos los cambios necesarios se aplicaron
        except Exception as e:  # noqa: BLE001 — un fallo al enviar no debe tumbar la auditoría de la decisión
            submit_error = str(e)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO lineup_decisions (matchday, formation, player_ids, submitted_to_futmondo, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (None, lineup["formation"], json.dumps(lineup["starters"]), 1 if submitted else 0, reason, now),
        )

    message = [f"set_lineup: once decidido ({lineup['formation']}):"] + [f"  - {s}" for s in starters_summary]
    message.append("Suplentes: " + (", ".join(substitutes_summary) if substitutes_summary else "ninguno disponible"))
    if config.ENABLE_LINEUP_AUTO_SUBMIT:
        if submit_error:
            message.append(f"NO enviada a Futmondo (error: {submit_error}).")
        elif changes_needed == 0:
            message.append("La alineación en Futmondo ya coincidía con la decidida, no hizo falta cambiar nada.")
        elif submitted:
            message.append(f"Enviada a Futmondo ({changes_needed}/{changes_needed} cambios aplicados).")
        else:
            message.append(
                f"⚠️ Enviada A MEDIAS a Futmondo: {len(failed_players)}/{changes_needed} cambio(s) NO se pudieron aplicar:"
            )
            message.extend(f"  - {name}: {error}" for name, error in failed_players)
    else:
        message.append("NO enviada a Futmondo (ENABLE_LINEUP_AUTO_SUBMIT=false).")

    if risk_warnings:
        message.append("⚠️ Riesgo de plantilla (posición sin suplente disponible):")
        message.extend(f"  - {w}" for w in risk_warnings)

    notify("\n".join(message))


if __name__ == "__main__":
    with notify_on_crash("set_lineup"):
        run()
