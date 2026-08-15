"""
Job: decide y (opcionalmente) fija la alineación antes del cierre de
jornada.

Asume que jobs/sync_data.py ya corrió antes en el cron. La decisión
(quién juega, con qué formación y por qué) SIEMPRE se calcula y se audita
en `lineup_decisions`, pase lo que pase con el envío a Comunio.

El envío real a la API (client.set_lineup) está BLOQUEADO por defecto
(config.ENABLE_LINEUP_AUTO_SUBMIT=False): la numeración de los 11 slots de
`items.lineup` solo se ha confirmado para 2 de 11 posiciones (ver
clients/comunio_client.py) — enviar una alineación completa con un mapeo
adivinado podría guardar cualquier cosa en la cuenta real sin que lo
notemos. Hasta cerrar esa prueba, este job dimensiona/audita la decisión
pero no la envía; `_build_lineup_slots` lanza NotImplementedError a
propósito si algún día se activa el flag antes de tiempo, para fallar alto
y claro en vez de enviar algo silenciosamente mal.
"""
import json
from datetime import datetime, timezone

import config
from clients.comunio_client import ComunioClient
from clients.laliga_stats_client import get_league_data, next_match_difficulty
from db.models import get_connection, get_player_features
from engine.evaluator import evaluate_players
from engine.lineup_optimizer import apply_fixture_difficulty, pick_lineup, to_api_tactic
from notifier import notify


def _build_lineup_slots(formation: str, starter_ids: list[str]) -> dict:
    """
    Traduciría `starter_ids` al mapa {slot: player_id} que espera
    client.set_lineup(). NO implementado: solo se conocen 2 de los 11
    slots reales (portero=11, un defensa=7 en la prueba controlada del
    2026-08-15) — hace falta guardar un once completo una vez más y volcar
    get_lineup() para terminar el mapeo antes de poder escribir esto de
    verdad.
    """
    raise NotImplementedError(
        "Mapeo de slots de alineación incompleto (solo 2 de 11 confirmados). "
        "Hace falta una prueba real con el once completo antes de activar el envío automático."
    )


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
            slots = _build_lineup_slots(lineup["formation"], lineup["starters"])
            client.set_lineup(to_api_tactic(lineup["formation"]), slots)
            submitted = True
        except (NotImplementedError, Exception) as e:  # noqa: BLE001 — cualquier fallo aquí no debe tumbar la auditoría
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
        message.append("NO enviada a Comunio (ENABLE_LINEUP_AUTO_SUBMIT=false, mapeo de slots sin confirmar del todo).")

    notify("\n".join(message))


if __name__ == "__main__":
    run()
