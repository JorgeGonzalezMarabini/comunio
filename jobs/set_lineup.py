"""
Job: decide y fija la alineación antes del cierre de jornada.

Asume que jobs/sync_data.py ya corrió antes en el cron. La decisión (quién
juega, con qué formación y por qué) SIEMPRE se calcula y se audita en
`lineup_decisions`, pase lo que pase con el envío a Comunio.

El envío real a la API (client.set_lineup) usa el mapeo de slots y el body
CONFIRMADOS AL 100% con una prueba real completa (once de 11 jugadores +
interceptación de la llamada PUT real del frontend + réplica exacta
devolviendo 200, 2026-08-15 — ver clients/comunio_client.py y
engine/lineup_optimizer.py). Sigue detrás de config.ENABLE_LINEUP_AUTO_SUBMIT
por si se prefiere revisar antes de dejarlo escribir solo contra una liga
real; cualquier fallo al enviar se audita sin romper la ejecución.
"""
import json
from datetime import datetime, timezone

import config
from clients.comunio_client import ComunioClient
from clients.laliga_stats_client import get_league_data, next_match_difficulty
from db.models import get_connection, get_player_features
from engine.evaluator import evaluate_players
from engine.lineup_optimizer import (
    apply_fixture_difficulty,
    build_lineup_slots,
    pick_lineup,
    pick_substitutes,
    to_api_tactic,
)
from engine.squad_risk import assess_squad_depth, depth_warnings
from notifier import notify


def run():
    client = ComunioClient()
    client.login()

    squad_response = client.get_squad()
    squad_ids = {str(p["id"]) for p in squad_response.get("items", [])}
    if not squad_ids:
        notify("set_lineup: la plantilla vino vacía, no se puede elegir alineación.")
        return

    all_players = get_player_features(only_on_market=False)
    squad_raw = [p for p in all_players if p["id"] in squad_ids]
    missing = squad_ids - {p["id"] for p in squad_raw}
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
    # dejaría un hueco en la alineación (-4 puntos, regla oficial de
    # Comunio). Solo informa por ahora, no bloquea ni cambia la decisión.
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
    reason = (
        f"Formación {lineup['formation']}, elegidos por expected_score "
        f"(evaluator + dificultad del próximo rival): " + "; ".join(starters_summary)
    )

    now = datetime.now(timezone.utc).isoformat()
    submitted = False
    submit_error = None

    if config.ENABLE_LINEUP_AUTO_SUBMIT:
        try:
            slots = build_lineup_slots(adjusted, lineup["starters"])
            bench_players = [by_id[pid] for pid in lineup["bench"]]
            substitutes = pick_substitutes(bench_players)
            client.set_lineup(to_api_tactic(lineup["formation"]), slots, substitutes)
            submitted = True
        except Exception as e:  # noqa: BLE001 — un fallo al enviar no debe tumbar la auditoría de la decisión
            submit_error = str(e)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO lineup_decisions (matchday, formation, player_ids, submitted_to_comunio, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (None, lineup["formation"], json.dumps(lineup["starters"]), 1 if submitted else 0, reason, now),
        )

    message = [f"set_lineup: once decidido ({lineup['formation']}):"] + [f"  - {s}" for s in starters_summary]
    if config.ENABLE_LINEUP_AUTO_SUBMIT:
        message.append("Enviada a Comunio." if submitted else f"NO enviada a Comunio (error: {submit_error}).")
    else:
        message.append("NO enviada a Comunio (ENABLE_LINEUP_AUTO_SUBMIT=false).")

    if risk_warnings:
        message.append("⚠️ Riesgo de plantilla (posición sin suplente disponible):")
        message.extend(f"  - {w}" for w in risk_warnings)

    notify("\n".join(message))


if __name__ == "__main__":
    run()
