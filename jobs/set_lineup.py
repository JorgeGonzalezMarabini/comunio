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
prefiere revisar antes de dejarlo escribir solo contra una liga real;
cualquier fallo al enviar se audita sin romper la ejecución.

De momento solo se manda el once titular, sin banquillo/suplentes: la
numeración de esos slots no se ha confirmado con ninguna prueba real (ver
TODO en engine/lineup_optimizer.py) — más seguro omitirlos que adivinar y
mandar algo que Futmondo podría rechazar o interpretar mal en silencio.
"""
import json
from datetime import datetime, timezone

import config
from clients.futmondo_client import FutmondoClient
from clients.laliga_stats_client import get_league_data, next_match_difficulty
from db.models import get_connection, get_player_features
from engine.evaluator import evaluate_players
from engine.lineup_optimizer import apply_fixture_difficulty, build_lineup_changes, pick_lineup
from engine.squad_risk import assess_squad_depth, depth_warnings
from notifier import notify


def run():
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
    reason = (
        f"Formación {lineup['formation']}, elegidos por expected_score "
        f"(evaluator + dificultad del próximo rival): " + "; ".join(starters_summary)
    )

    now = datetime.now(timezone.utc).isoformat()
    submitted = False
    submit_error = None

    if config.ENABLE_LINEUP_AUTO_SUBMIT:
        try:
            changes = build_lineup_changes(adjusted, lineup["starters"])
            client.change_lineup(changes)
            submitted = True
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
    if config.ENABLE_LINEUP_AUTO_SUBMIT:
        message.append("Enviada a Futmondo." if submitted else f"NO enviada a Futmondo (error: {submit_error}).")
    else:
        message.append("NO enviada a Futmondo (ENABLE_LINEUP_AUTO_SUBMIT=false).")

    if risk_warnings:
        message.append("⚠️ Riesgo de plantilla (posición sin suplente disponible):")
        message.extend(f"  - {w}" for w in risk_warnings)

    notify("\n".join(message))


if __name__ == "__main__":
    run()
