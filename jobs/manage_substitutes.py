"""
Job: sustituye a mano, dentro de la alineación YA guardada en Futmondo, a
cualquier titular confirmado FUERA (lesionado/en duda, o simplemente no
incluido en el once real de su equipo hoy -- ver más abajo) por el
suplente de su misma posición ya asignado en el banquillo (ver
engine.lineup_optimizer.build_substitution_changes()).

Por qué existe como job APARTE de jobs/set_lineup.py: set_lineup decide la
alineación UNA VEZ, antes del cierre de jornada (ver set_lineup.yml,
viernes 18:00 UTC) — pero "¿quién está confirmado fuera?" (lesión, sanción,
no convocado) solo se sabe con fiabilidad cerca de cada partido, y los
partidos de una misma jornada se reparten de viernes a lunes. Un job
semanal no puede reaccionar a eso; este job está pensado para correr con
más frecuencia (varias veces al día, ver manage_substitutes.yml) durante la
jornada, no una sola vez.

Además, según la FAQ oficial de Futmondo
(https://help.futmondo.com/article/159-entrenador-automatico; ver también
README, "Banquillo/suplentes"), la sustitución real de un titular que no
juega la hace el "entrenador automático", una función de pago (gratis en
modo PRO) que NO está activada por defecto — sin ella, el suplente que
coloca set_lineup.py es decorativo. Este job es el sustituto manual de esa
función para ligas donde no esté activada (como la liga privada de
destino, ver conversación con el usuario 2026-08-17).

Señal usada para "confirmado fuera": DOS fuentes independientes, cualquiera
de las dos basta (ver engine.lineup_optimizer.build_substitution_changes()):

  1. clients.futmondo_client.is_injury_status() sobre el campo `status` de
     Futmondo — la misma aproximación sin confirmar con un caso real que ya
     usa engine/squad_risk.py (ver TODO en clients/futmondo_client.py). Si
     Futmondo confirma valores exactos de `status` más adelante, actualizar
     ahí, no aquí.
  2. clients.football_lineups_client.find_players_confirmed_out_of_real_lineup()
     — un titular SANO que no aparece en el once REAL de su equipo hoy
     (rotación, decisión táctica del entrenador real, no solo lesión — el
     caso más frecuente en la práctica, ver README). Vía Fotmob (API no
     oficial, gratis, sin key — ver docstring del propio cliente para las
     otras tres fuentes descartadas antes de llegar a esta). Detrás de
     config.ENABLE_REAL_LINEUP_CHECK (False por defecto) — si está
     desactivado, este job se comporta exactamente igual que antes de que
     existiera esta fuente.

Sigue detrás de config.ENABLE_SUBSTITUTE_AUTO_SUBMIT (activado desde el
2026-08-17, decisión explícita del usuario): build_substitution_changes()
genera 4 llamadas HTTP reales por sustitución (vaciar titular, vaciar
suplente, rellenar campo, rellenar banquillo — ver su docstring para el
porqué: Futmondo nunca acepta un `change` con `"to"` y `"from"` a la vez,
confirmado con una prueba real el 2026-08-18 interceptando la propia app
web, ver TODO.md #1). Cada sustitución decidida se audita en
`substitution_decisions` pase lo que pase con el envío, igual que
jobs/set_lineup.py hace con `lineup_decisions`.

A diferencia del resto de jobs (que siempre notifican un resumen, incluso
"nada que hacer"), este SOLO notifica cuando hay alguna sustitución
decidida, o cuando encuentra algo anómalo (alineación vacía). Pensado para
correr varias veces al día en días de partido — notificar en cada
ejecución sin novedad sería puro ruido en Telegram.
"""
from datetime import datetime, timezone

import config
from clients.futmondo_client import FutmondoClient
from db.models import get_connection, get_player_features
from engine.lineup_optimizer import build_substitution_changes
from notifier import notify, track_job_run


def run():
    if not config.ENABLE_BOT:
        print("manage_substitutes: ENABLE_BOT=false, no se ejecuta.")
        return

    client = FutmondoClient()

    current_lineup_answer = client.get_lineup().get("answer", {})
    current_lineup_by_position = {p["position"]: p["id"] for p in current_lineup_answer.get("players", [])}
    current_bench_by_position = {
        p["position"]: p["id"] for p in current_lineup_answer.get("bench", {}).get("players", [])
    }
    if not current_lineup_by_position:
        notify("manage_substitutes: la alineación en Futmondo vino vacía (¿corrió set_lineup antes?), nada que revisar.")
        return

    all_players = get_player_features(only_on_market=False)
    players_by_id = {p["id"]: p for p in all_players}

    confirmed_out_ids = set()
    if config.ENABLE_REAL_LINEUP_CHECK:
        # Solo hace falta comprobar los equipos de nuestros TITULARES actuales,
        # no toda la plantilla -- menos llamadas a Fotmob de las necesarias.
        from clients.football_lineups_client import find_players_confirmed_out_of_real_lineup

        starters_by_id = {pid: players_by_id[pid] for pid in current_lineup_by_position.values() if pid in players_by_id}
        try:
            confirmed_out_ids = find_players_confirmed_out_of_real_lineup(starters_by_id)
        except Exception as e:  # noqa: BLE001 -- un fallo de esta fuente extra no debe tumbar la comprobación por lesión
            notify(f"manage_substitutes: fallo consultando alineaciones reales (Fotmob), sigo solo con is_injury_status: {e}")

    changes = build_substitution_changes(players_by_id, current_lineup_by_position, current_bench_by_position, confirmed_out_ids)
    if not changes:
        return  # nadie confirmado fuera con suplente disponible esta pasada -- ver docstring del módulo

    # build_substitution_changes() genera exactamente 4 `changes` por
    # sustitución, siempre en este orden fijo (ver su docstring): vaciar
    # titular (campo), vaciar suplente (banquillo), rellenar campo con el
    # suplente, rellenar banquillo con el titular. change_lineup() manda
    # una llamada HTTP por `change` y devuelve los resultados en el MISMO
    # orden -- por eso los resultados también se pueden agrupar en bloques
    # de 4, sin depender de mirar "to"/"from" (los de vaciar no tienen "to").
    CHANGES_PER_SUBSTITUTION = 4
    decisions = []
    for i in range(0, len(changes), CHANGES_PER_SUBSTITUTION):
        _, _, fill_field, fill_bench = changes[i : i + CHANGES_PER_SUBSTITUTION]
        starter_id, substitute_id = fill_bench["to"], fill_field["to"]
        decisions.append(
            {"starter_id": starter_id, "substitute_id": substitute_id, "position": players_by_id[starter_id]["position"]}
        )

    now = datetime.now(timezone.utc).isoformat()
    submitted_ok = set()
    submit_error = None
    error_by_starter_id = {}

    if config.ENABLE_SUBSTITUTE_AUTO_SUBMIT:
        try:
            results = client.change_lineup(changes)
            for i, d in enumerate(decisions):
                block = results[i * CHANGES_PER_SUBSTITUTION : (i + 1) * CHANGES_PER_SUBSTITUTION]
                if all(r["ok"] for r in block):
                    submitted_ok.add(d["starter_id"])
                else:
                    error_by_starter_id[d["starter_id"]] = next(r["answer_or_error"] for r in block if not r["ok"])
        except Exception as e:  # noqa: BLE001 -- un fallo al enviar no debe tumbar la auditoría de la decisión
            submit_error = str(e)

    message = [f"manage_substitutes: {len(decisions)} sustitución(es) decidida(s):"]
    with get_connection() as conn:
        for d in decisions:
            starter = players_by_id.get(d["starter_id"], {})
            substitute = players_by_id.get(d["substitute_id"], {})
            ok = d["starter_id"] in submitted_ok
            reason = (
                f"{starter.get('name', d['starter_id'])} ({d['position']}, status={starter.get('status')!r}) "
                f"confirmado fuera -> entra suplente {substitute.get('name', d['substitute_id'])}"
            )
            conn.execute(
                """
                INSERT INTO substitution_decisions
                    (starter_id, substitute_id, position, submitted_to_futmondo, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (d["starter_id"], d["substitute_id"], d["position"], 1 if ok else 0, reason, now),
            )
            line = f"  - {reason}"
            if config.ENABLE_SUBSTITUTE_AUTO_SUBMIT and not ok and not submit_error:
                error = error_by_starter_id.get(d["starter_id"], "fallo desconocido")
                line += f" — NO aplicada: {error}"
            message.append(line)

    if config.ENABLE_SUBSTITUTE_AUTO_SUBMIT:
        if submit_error:
            message.append(f"NO enviada(s) a Futmondo (error: {submit_error}).")
        elif len(submitted_ok) < len(decisions):
            message.append(f"⚠️ {len(decisions) - len(submitted_ok)}/{len(decisions)} sustitución(es) NO se pudieron aplicar (detalle arriba).")
        else:
            message.append(f"Enviada(s) a Futmondo ({len(decisions)}/{len(decisions)} sustitución(es) aplicada(s)).")
    else:
        message.append("NO enviada(s) a Futmondo (ENABLE_SUBSTITUTE_AUTO_SUBMIT=false).")

    notify("\n".join(message))


if __name__ == "__main__":
    # Chequeo duplicado a propósito: el de dentro de run() protege a quien
    # llame a run() directamente (tests incluidos); este de aquí evita
    # además que se entre en track_job_run() -- si no, con ENABLE_BOT=false
    # igualmente se registraría una fila en `job_runs`, ese INSERT por sí
    # solo ensuciaría db/futmondo.db, y el step "Commit BD actualizada" del
    # workflow comitearía/pushearía igual aunque el bot no haga nada real.
    if not config.ENABLE_BOT:
        print("manage_substitutes: ENABLE_BOT=false, no se ejecuta.")
    else:
        with track_job_run("manage_substitutes"):
            run()
