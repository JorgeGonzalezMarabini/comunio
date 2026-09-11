import runpy
from pathlib import Path
from unittest.mock import patch

import config
import jobs.sync_data as sync_data
from clients.futmondo_client import FutmondoClient
from db.models import get_connection, get_player_features

NOW = "2026-08-16T18:00:00+00:00"
JOB_PATH = str(Path(__file__).resolve().parent.parent / "jobs" / "sync_data.py")


def test_upsert_player_and_snapshot_roster_uses_own_market_field(tmp_db, roster_player_factory):
    roster_player = roster_player_factory(id=1001, name="Jugador Propio", role="defensa", on_market=False)
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, roster_player, NOW)

    features = get_player_features()
    assert features[0]["position"] == "DEF"  # normalizado desde "defensa"
    assert features[0]["on_market"] == 0


def test_upsert_player_and_snapshot_market_player_always_on_market_true(tmp_db, market_player_factory):
    """
    Regresión del bug real de Comunio del que se hereda la misma cautela:
    los items de get_market() NO traen el campo "market" (exclusivo de
    roster) -- sin el parámetro on_market=True explícito, todo jugador de
    mercado se guardaría con on_market=0 y run_market() nunca encontraría
    candidatos.
    """
    market_player = market_player_factory(id=2002, name="Jugador Mercado", role="delantero")
    assert "market" not in market_player  # confirma la forma real: sin ese campo
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, market_player, NOW, on_market=True)

    features = get_player_features(only_on_market=True)
    assert len(features) == 1
    assert features[0]["id"] == "2002"


def test_upsert_player_and_snapshot_market_player_stores_listing_price_separately_from_value(tmp_db, market_player_factory):
    """
    A petición del usuario (2026-08-22): el precio de salida del listado
    ("price", lo elige quien vende) debe guardarse aparte del VM ("value",
    siempre lo calcula Futmondo) -- necesario para que
    engine.bidding_strategy.decide_bid() pueda compararlos.
    """
    market_player = market_player_factory(id=3003, value=1_000_000, price=1_800_000, is_clause=True)
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, market_player, NOW, on_market=True)

    features = get_player_features(only_on_market=True)
    assert features[0]["price"] == 1_000_000          # VM real
    assert features[0]["listing_price"] == 1_800_000  # precio de salida, distinto del VM
    assert features[0]["is_clause"] == 1


def test_upsert_player_and_snapshot_roster_player_has_no_listing_price(tmp_db, roster_player_factory):
    """El roster propio no trae "price"/"isClause" (exclusivos de market) -- deben quedar NULL."""
    roster_player = roster_player_factory(id=1001, name="Jugador Propio", role="defensa")
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, roster_player, NOW)

    features = get_player_features()
    assert features[0]["listing_price"] is None
    assert features[0]["is_clause"] is None


def test_upsert_player_and_snapshot_parses_dash_as_null(tmp_db, roster_player_factory):
    """Futmondo, igual que Comunio, puede devolver "-" en pretemporada en vez de omitir el campo o dar 0."""
    player = roster_player_factory(id=1, name="J", role="defensa")
    player["points"] = "-"
    player["average"]["fitness"] = ["-"]
    player["average"]["average"] = "-"

    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, player, NOW)

    features = get_player_features()
    assert features[0]["points"] is None
    assert features[0]["last_points"] is None
    assert features[0]["average_points"] is None


def test_upsert_player_and_snapshot_uses_last_fitness_entry_as_last_points(tmp_db, roster_player_factory):
    player = roster_player_factory(id=1, name="J", role="defensa")
    player["average"]["fitness"] = [3, 7, 9]

    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, player, NOW)

    features = get_player_features()
    assert features[0]["last_points"] == 9  # último elemento de "fitness"


def test_reconcile_bids_all_three_cases(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)", ("1001", 500_000, "placed", NOW))  # ganada: en plantilla
        conn.execute("INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)", ("1002", 500_000, "placed", NOW))  # perdida: ni en plantilla ni en mercado
        conn.execute("INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)", ("1003", 500_000, "placed", NOW))  # sigue pendiente: en mercado, no en plantilla
        conn.execute("INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)", ("1004", 500_000, "failed", NOW))  # ya no está 'placed', no se toca

    result = sync_data._reconcile_bids(roster_player_ids={"1001"}, market_player_ids={"1003"})
    assert result == {"won": 1, "lost": 1}

    with get_connection() as conn:
        statuses = {r["player_id"]: r["status"] for r in conn.execute("SELECT player_id, status FROM bids")}
    assert statuses == {"1001": "won", "1002": "lost", "1003": "placed", "1004": "failed"}


def test_reconcile_sales_marks_sold_when_player_left_roster(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO sales (player_id, asking_price, status, created_at) VALUES (?,?,?,?)", ("2748", 1_100_000, "listed", NOW))

    sold = sync_data._reconcile_sales(roster_player_ids=set())
    assert sold == 1

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM sales WHERE player_id = ?", ("2748",)).fetchone()
    assert row["status"] == "sold"


def test_reconcile_sales_leaves_still_owned_players_listed(tmp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO sales (player_id, asking_price, status, created_at) VALUES (?,?,?,?)", ("2748", 1_100_000, "listed", NOW))

    sold = sync_data._reconcile_sales(roster_player_ids={"2748"})
    assert sold == 0


def test_rescue_sales_at_risk_cancels_listing_when_position_left_understaffed(tmp_db, roster_player_factory):
    """
    Escenario real (clausulazo u otra baja de un jugador de la misma
    posición mientras una venta sigue listada): con formación 4-4-2 por
    defecto, DEF necesita 4 titulares. Aquí solo quedan 3 DEF sanos en
    plantilla + el propio DEF puesto en venta (que ya no cuenta como
    disponible) -- margen NEGATIVO (`deficit=1`), así que la venta debe
    cancelarse para recuperar el cuerpo antes de que se cierre sola.
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sales (player_id, asking_price, status, created_at) VALUES (?,?,?,?)",
            ("2001", 1_000_000, "listed", NOW),
        )

    roster_items = [roster_player_factory(id=2001, role="defensa", on_market=True)] + [
        roster_player_factory(id=2000 + i, role="defensa") for i in range(2, 5)
    ]

    cancelled = []

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": roster_items}

        def get_market(self):
            return {"answer": []}

        def cancel_sale(self, player_id):
            cancelled.append(player_id)
            return {"answer": {"code": "api.general.ok"}}

    captured = []
    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=({"players": []}, "2025", {})):
        sync_data.run()

    assert cancelled == ["2001"]
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM sales WHERE player_id = ?", ("2001",)).fetchone()
    assert row["status"] == "delisted"
    assert any("riesgo de plantilla" in m for m in captured)


def test_rescue_sales_at_risk_leaves_fresh_listing_alone(tmp_db, roster_player_factory):
    """
    Justo tras listar una venta con margen exacto (bench=0, `at_risk` pero
    SIN déficit), no hay que cancelar nada -- eso deshacería cualquier
    venta en la primera pasada después de crearla (ver docstring de
    `engine.squad_risk.sales_to_cancel`).
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sales (player_id, asking_price, status, created_at) VALUES (?,?,?,?)",
            ("2001", 1_000_000, "listed", NOW),
        )

    # 4 DEF sanos en plantilla (justo lo requerido) + el propio DEF en venta.
    roster_items = [roster_player_factory(id=2001, role="defensa", on_market=True)] + [
        roster_player_factory(id=2000 + i, role="defensa") for i in range(2, 6)
    ]

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": roster_items}

        def get_market(self):
            return {"answer": []}

        def cancel_sale(self, player_id):
            raise AssertionError("no debería cancelarse ninguna venta con déficit 0")

    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify"), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=({"players": []}, "2025", {})):
        sync_data.run()

    with get_connection() as conn:
        row = conn.execute("SELECT status FROM sales WHERE player_id = ?", ("2001",)).fetchone()
    assert row["status"] == "listed"


def test_backfill_initial_squad_bids_adds_won_bid_at_current_value(tmp_db, roster_player_factory):
    """
    Un jugador de la plantilla inicial (nunca comprado por el bot, cero
    filas en `bids`) debe recibir una puja 'won' sintética por su VM actual,
    para que engine.selling_strategy.decide_sales() no lo descarte sin
    evaluarlo (motivo real: Mendy, lesionado, nunca puesto a la venta).
    """
    roster_player = roster_player_factory(id=1001, name="Mendy", role="defensa", value=4_603_280)

    with get_connection() as conn:
        added = sync_data._backfill_initial_squad_bids(conn, [roster_player], NOW)

    assert added == 1
    with get_connection() as conn:
        row = conn.execute("SELECT amount, status FROM bids WHERE player_id = ?", ("1001",)).fetchone()
    assert row["status"] == "won"
    assert row["amount"] == 4_603_280


def test_backfill_initial_squad_bids_is_idempotent_once_won_bid_exists(tmp_db, roster_player_factory):
    """No debe duplicar ni tocar a un jugador que ya tiene una puja 'won' (comprado por el bot, o ya backfilleado antes)."""
    roster_player = roster_player_factory(id=1001, name="Comprado por el bot", role="defensa", value=2_000_000)
    with get_connection() as conn:
        conn.execute("INSERT INTO bids (player_id, amount, status, created_at) VALUES (?,?,?,?)", ("1001", 1_500_000, "won", NOW))

    with get_connection() as conn:
        added = sync_data._backfill_initial_squad_bids(conn, [roster_player], NOW)

    assert added == 0
    with get_connection() as conn:
        rows = conn.execute("SELECT amount FROM bids WHERE player_id = ?", ("1001",)).fetchall()
    assert [r["amount"] for r in rows] == [1_500_000]  # sin fila nueva, precio real de compra intacto


def test_backfill_initial_squad_bids_skips_players_without_reliable_value(tmp_db, roster_player_factory):
    """Sin VM fiable (None o <= 0) no se puede fijar un precio de referencia -- no se inventa una puja."""
    roster_player = roster_player_factory(id=1001, name="Sin VM", role="defensa", value=None)

    with get_connection() as conn:
        added = sync_data._backfill_initial_squad_bids(conn, [roster_player], NOW)

    assert added == 0
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM bids WHERE player_id = ?", ("1001",)).fetchone()
    assert row["n"] == 0


def test_run_full_job_backfills_won_bid_for_initial_squad_player(tmp_db, roster_player_factory):
    """Test de integración: run() completo debe dejar al jugador de plantilla inicial disponible en get_won_bid_prices()."""
    roster_player = roster_player_factory(id=1001, name="Mendy", role="defensa", value=4_603_280)

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    captured = []
    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=({"players": []}, "2025", {})):
        sync_data.run()

    from db.models import get_won_bid_prices
    assert get_won_bid_prices() == {"1001": 4_603_280}
    assert "Pujas 'won' sintéticas añadidas para plantilla inicial: 1" in captured[-1]


def test_run_reconciles_todays_real_bid_before_backfill_no_duplicate_won_row(tmp_db, roster_player_factory):
    """
    Regresión de un bug real (corregido 2026-09-07, ver conversación: el
    "precio de compra" registrado no coincidía con lo realmente pagado):
    una puja REAL colocada por el bot y ganada el mismo día en que corre
    este sync todavía está 'placed' en `bids` cuando arranca run() -- con
    el orden antiguo (backfill de plantilla inicial ANTES de reconciliar
    pujas), _backfill_initial_squad_bids() no veía todavía ninguna fila
    'won' para ese jugador y lo confundía con plantilla inicial,
    insertando una fila 'won' SINTÉTICA por su VM actual -- update_bid_
    status() actualiza la fila 'placed' original in place (mismo id), así
    que quedaban DOS filas 'won' para el mismo jugador, y get_won_bid_
    prices() (ORDER BY id DESC) se quedaba con la sintética, no con el
    precio real pagado, corrompiendo profit/profit_pct en la venta
    posterior de ese jugador (engine.selling_strategy.decide_sales()).

    Con el orden correcto (reconciliar primero), la fila real ya está
    'won' cuando el backfill mira "¿ya tiene alguna fila 'won'?" y la
    salta -- solo debe quedar UNA fila 'won', con el precio REAL pagado
    (bids.amount), no el VM del roster de hoy.
    """
    real_price_paid = 3_200_000
    current_vm_today = 4_603_280  # distinto a propósito -- el VM sube tras un fichaje que sale bien
    roster_player = roster_player_factory(id=1001, name="Fichaje de Hoy", role="defensa", value=current_vm_today)

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO bids (player_id, amount, status, score, created_at) VALUES (?,?,?,?,?)",
            ("1001", real_price_paid, "placed", 0.80, NOW),
        )

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    captured = []
    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=({"players": []}, "2025", {})):
        sync_data.run()

    from db.models import get_won_bid_prices

    assert get_won_bid_prices() == {"1001": real_price_paid}
    with get_connection() as conn:
        won_rows = conn.execute("SELECT amount FROM bids WHERE player_id = '1001' AND status = 'won'").fetchall()
    assert len(won_rows) == 1  # nunca una fila 'won' duplicada (real + sintética)
    assert "Pujas 'won' sintéticas añadidas para plantilla inicial" not in captured[-1]


def test_run_full_job_with_fake_client(tmp_db, roster_player_factory):
    roster_player = roster_player_factory(id=1001, name="Jugador Propio", role="defensa")

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    captured = []
    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=({"players": []}, "2025", {})):
        sync_data.run()

    assert len(captured) >= 1
    assert "1 en plantilla" in captured[-1]
    features = get_player_features()
    assert len(features) == 1


def test_run_crosses_with_understat_by_surname_and_reports_breakdown(tmp_db, roster_player_factory):
    """
    Caso real y habitual (ver README): Futmondo muestra solo el apellido de
    un jugador conocido -- el cruce con Understat (nombre completo) debe
    resolverlo por apellido+equipo, no solo por nombre exacto, y el resumen
    de la notificación debe reflejar qué estrategia se usó.
    """
    roster_player = roster_player_factory(id=1001, name="Cairney", role="delantero", team="Fulham")
    league_data = {"players": [{"player_name": "Tom Cairney", "team_title": "Fulham"}]}

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    captured = []
    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=(league_data, "2025", {})):
        sync_data.run()

    assert "1/1 cruzados con Understat (1 surname+team)" in captured[-1]
    features = get_player_features()
    assert features[0]["xg"] is None  # el fixture de player_name no trae xG -- solo se comprueba que cruzó, no las stats
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM external_stats WHERE player_id = ?", ("1001",)).fetchone()
    assert row["n"] == 1


def test_team_games_by_title_counts_history_entries():
    """
    Regresión de TODO.md #12: confirmado en vivo (2026-08-18, get_league_data
    real) que `teams[id]["history"]` trae solo partidos YA DISPUTADOS
    (con "result"/"scored"/"missed"), así que su longitud es exactamente
    "partidos jugados por el equipo esta temporada".
    """
    league_data = {
        "teams": {
            "1": {"title": "Sevilla", "history": [{"result": "w"}]},
            "2": {"title": "Malaga", "history": []},
        }
    }
    assert sync_data._team_games_by_title(league_data) == {"Sevilla": 1, "Malaga": 0}


def test_run_stores_team_games_from_current_season_league_data(tmp_db, roster_player_factory):
    """El team_games guardado en external_stats debe salir de team["history"], no de "games" del jugador."""
    roster_player = roster_player_factory(id=1001, name="Cairney", role="delantero", team="Fulham")
    league_data = {
        "players": [{"player_name": "Tom Cairney", "team_title": "Fulham", "games": "1", "time": "90"}],
        "teams": {"1": {"title": "Fulham", "history": [{"result": "w"}, {"result": "d"}]}},
    }

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify"), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=(league_data, "2025", {})):
        sync_data.run()

    features = get_player_features()
    assert features[0]["games"] == 1        # partidos que jugó ÉL
    assert features[0]["team_games"] == 2   # partidos jugados por el EQUIPO (mayor: se perdió uno)


def test_team_clean_sheets_by_title_counts_missed_zero_entries():
    """
    Análisis a petición del usuario (2026-09-11): Futmondo da puntos extra
    por portería a cero -- `_team_clean_sheets_by_title()` cuenta, de
    `history`, los partidos con `missed` == 0 (sin encajar).
    """
    league_data = {
        "teams": {
            "1": {"title": "Sevilla", "history": [{"missed": "0"}, {"missed": "2"}, {"missed": 0}]},
            "2": {"title": "Malaga", "history": [{"missed": "1"}]},
        }
    }
    assert sync_data._team_clean_sheets_by_title(league_data) == {"Sevilla": 2, "Malaga": 0}


def test_team_clean_sheets_by_title_treats_missing_or_unparseable_missed_as_not_clean_sheet():
    """Sin `missed` parseable en una entrada de `history`, no cuenta como portería a cero (ninguna evidencia de haberla mantenido)."""
    league_data = {"teams": {"1": {"title": "Sevilla", "history": [{"result": "w"}, {"missed": None}]}}}
    assert sync_data._team_clean_sheets_by_title(league_data) == {"Sevilla": 0}


def test_run_stores_team_clean_sheets_from_current_season_league_data(tmp_db, roster_player_factory):
    """El team_clean_sheets guardado en external_stats debe salir de team["history"], igual que team_games."""
    roster_player = roster_player_factory(id=1001, name="Cairney", role="delantero", team="Fulham")
    league_data = {
        "players": [{"player_name": "Tom Cairney", "team_title": "Fulham", "games": "1", "time": "90"}],
        "teams": {"1": {"title": "Fulham", "history": [{"missed": "0"}, {"missed": "1"}]}},
    }

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify"), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=(league_data, "2025", {})):
        sync_data.run()

    features = get_player_features()
    assert features[0]["team_games"] == 2
    assert features[0]["team_clean_sheets"] == 1


def test_run_does_not_set_team_clean_sheets_for_previous_season_fallback_rows(tmp_db, roster_player_factory):
    """Mismo motivo que team_games (ver test de fallback de abajo): sin esto, se mezclaría team_clean_sheets de la temporada actual con minutos/games de la anterior."""
    roster_player = roster_player_factory(id=1001, name="Cairney", role="delantero", team="Fulham")
    league_data = {
        "players": [{"player_name": "Tom Cairney", "team_title": "Fulham", "games": "38", "time": "3000", "_source_season": "2024"}],
        "teams": {"1": {"title": "Fulham", "history": [{"missed": "0"}]}},
    }

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify"), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=(league_data, "2025", {"Fulham": "2024"})):
        sync_data.run()

    features = get_player_features()
    assert features[0]["team_clean_sheets"] is None


def test_run_does_not_set_team_games_for_previous_season_fallback_rows(tmp_db, roster_player_factory):
    """
    Regresión: si la fila viene del fallback a temporada anterior (ver
    get_league_data_with_fallback), `games`/`minutes_played` son de una
    temporada COMPLETA distinta -- team_games de la temporada actual (aún
    con pocos partidos) daría un ratio sin sentido, así que debe quedar
    NULL en vez de mezclarlo (ver TODO.md #12).
    """
    roster_player = roster_player_factory(id=1001, name="Cairney", role="delantero", team="Fulham")
    league_data = {
        "players": [{"player_name": "Tom Cairney", "team_title": "Fulham", "games": "38", "time": "3000", "_source_season": "2024"}],
        "teams": {"1": {"title": "Fulham", "history": [{"result": "w"}]}},  # temporada ACTUAL, solo 1 partido jugado
    }

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}

    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify"), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=(league_data, "2025", {"Fulham": "2024"})):
        sync_data.run()

    features = get_player_features()
    assert features[0]["games"] == 38     # de la temporada anterior (fallback), sin tocar
    assert features[0]["team_games"] is None


def test_sync_data_skips_entirely_when_bot_disabled(tmp_db, monkeypatch, capsys):
    """ENABLE_BOT=false -- ni siquiera debe construirse el cliente, no digamos llamar a la red."""
    monkeypatch.setattr(config, "ENABLE_BOT", False)

    class BoomClient(FutmondoClient):
        def __init__(self, *args, **kwargs):
            raise AssertionError("no debería construirse FutmondoClient con ENABLE_BOT=false")

    captured = []
    with patch("jobs.sync_data.FutmondoClient", BoomClient), patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)):
        sync_data.run()

    assert captured == []
    assert "ENABLE_BOT=false" in capsys.readouterr().out


def test_sync_data_main_does_not_record_job_run_when_bot_disabled(tmp_db, monkeypatch, capsys):
    """
    Regresión (2026-08-18): con ENABLE_BOT=false, el bloque `__main__` no
    debe entrar en `track_job_run()` -- si lo hiciera, aunque run() no haga
    nada, se registraría igualmente una fila en `job_runs`, ensuciando
    db/futmondo.db y provocando que el workflow comitee/pushee pese a que
    el bot está pausado (ver README, "Pausar el bot sin tocar los 5
    workflows").
    """
    monkeypatch.setattr(config, "ENABLE_BOT", False)

    runpy.run_path(JOB_PATH, run_name="__main__")

    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0]
    assert count == 0
    assert "ENABLE_BOT=false" in capsys.readouterr().out


def test_close_stale_market_listings_invalidates_player_gone_from_market_and_roster(tmp_db, market_player_factory):
    """
    Regresión del fallo real (2026-09-08, ver notifier.py/jobs/run_market.py):
    `_upsert_player_and_snapshot()` solo inserta una fila nueva para
    jugadores presentes en la respuesta ACTUAL de roster/mercado -- un
    jugador de mercado que se resuelve (comprado por otro, retirado,
    expira) sin llegar nunca a nuestro roster se quedaba con
    `on_market=1` para siempre, porque nada volvía a escribir una fila que
    lo contradijera. En vivo esto acumuló 280 "candidatos en mercado" en BD
    con el mercado real de Futmondo en solo 16, y el resumen de
    `run_market` (un id por descartado) acabó superando el límite de 4096
    caracteres de Telegram.
    """
    market_player = market_player_factory(id=9001, name="Jugador Mercado")
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, market_player, NOW, on_market=True)

    assert len(get_player_features(only_on_market=True)) == 1  # sigue en mercado esta pasada

    # Pasada siguiente: el jugador ya no aparece ni en market_players ni en roster_players.
    with get_connection() as conn:
        closed = sync_data._close_stale_market_listings(conn, market_player_ids=set(), roster_player_ids=set(), now="2026-08-17T00:00:00+00:00")

    assert closed == 1
    assert get_player_features(only_on_market=True) == []  # ya no sale como candidato de compra


def test_close_stale_market_listings_skips_player_still_on_market_or_roster(tmp_db, market_player_factory):
    """No debe tocar un jugador que sigue en el mercado, ni uno que ahora está en nuestro roster (aunque su última fila diga on_market=1)."""
    still_on_market = market_player_factory(id=9002, name="Sigue en mercado")
    now_ours = market_player_factory(id=9003, name="Ahora en mi plantilla")
    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, still_on_market, NOW, on_market=True)
        sync_data._upsert_player_and_snapshot(conn, now_ours, NOW, on_market=True)
        closed = sync_data._close_stale_market_listings(
            conn, market_player_ids={"9002"}, roster_player_ids={"9003"}, now="2026-08-17T00:00:00+00:00"
        )

    assert closed == 0
    assert {p["id"] for p in get_player_features(only_on_market=True)} == {"9002", "9003"}


def test_run_full_job_closes_stale_market_listing_and_notifies(tmp_db, roster_player_factory, market_player_factory):
    """Test de integración: run() completo debe invalidar un candidato de mercado de una pasada anterior que ya no aparece en ninguna respuesta, y avisarlo."""
    roster_player = roster_player_factory(id=1001, name="Titular", role="defensa")
    stale_market_player = market_player_factory(id=9001, name="Ya no está en mercado")

    with get_connection() as conn:
        sync_data._upsert_player_and_snapshot(conn, stale_market_player, NOW, on_market=True)
    assert len(get_player_features(only_on_market=True)) == 1

    class FakeClient(FutmondoClient):
        def get_roster(self):
            return {"answer": [roster_player]}

        def get_market(self):
            return {"answer": []}  # el jugador de mercado de arriba ya no aparece

    captured = []
    with patch("jobs.sync_data.FutmondoClient", FakeClient), \
         patch("jobs.sync_data.notify", side_effect=lambda m: captured.append(m)), \
         patch("jobs.sync_data.get_league_data_with_fallback", return_value=({"players": []}, "2025", {})):
        sync_data.run()

    assert get_player_features(only_on_market=True) == []
    assert "1 candidato(s) de mercado obsoleto(s) invalidado(s)" in captured[-1]
