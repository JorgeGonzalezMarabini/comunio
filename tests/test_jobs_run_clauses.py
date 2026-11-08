from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import config
import jobs.run_clauses as run_clauses
from clients.futmondo_client import FutmondoClient, FutmondoOfferError
from db.models import get_connection

OWN_TEAM = "own-team"
PAST = "2026-10-01T21:59:59.999Z"


def _squad(roster_player_factory):
    """Plantilla 4-4-2 con UN solo delantero (déficit en DEL), todos flojos."""
    roles = ["portero"] + ["defensa"] * 4 + ["centrocampista"] * 4 + ["delantero"]
    return [roster_player_factory(id=f"10{i:02d}", name=f"Propio{i}", role=r, average_points=2.0) for i, r in enumerate(roles)]


def _rival(roster_player_factory, id="9001", name="Crack", role="delantero", clause_price=20_000_000, owner="rival-team", date=PAST):
    p = roster_player_factory(id=id, name=name, role=role, value=10_000_000, average_points=8.0, last_points=8)
    p["average"]["matches"] = 10
    p["average"]["fitness"] = [8, 9, 7, 8, 9]
    p.pop("team")
    p.pop("market")
    p.update(
        {
            "userteamId": owner,
            "userteam": "Manager Rival",
            "clause": {"price": clause_price, "date": date, "transferred": False, "suggestedClause": 0},
        }
    )
    return p


def _fake_client(squad, rivals, budget=200_000_000, after_pay_roster=None, pay_error=None, paid=None):
    class FakeClient(FutmondoClient):
        def __init__(self, *a, **k):
            super().__init__(token="t", user_id="u", championship_id="c", userteam_id=OWN_TEAM)
            self._roster_reads = 0

        def get_roster(self):
            self._roster_reads += 1
            if self._roster_reads > 1 and after_pay_roster is not None:
                return {"answer": after_pay_roster}
            return {"answer": squad}

        def get_market(self):
            return {"answer": []}

        def get_information(self):
            return {"answer": {"budget": budget, "configuration": {"maxPlayersInRoster": 18}}}

        def get_championship_players(self):
            own = [{**p, "userteamId": OWN_TEAM, "clause": {"price": 1, "date": PAST, "transferred": False}} for p in squad]
            return {"answer": {"players": own + rivals, "automaticClauses": True}}

        def pay_clause(self, player_id, player_slug, price):
            if pay_error:
                raise pay_error
            if paid is not None:
                paid.append((player_id, price))
            return {"code": "api.general.ok"}

    return FakeClient


def _seed_squad(squad):
    from jobs.sync_data import _upsert_player_and_snapshot

    with get_connection() as conn:
        for p in squad:
            _upsert_player_and_snapshot(conn, p, "2026-11-08T10:00:00+00:00")


def _run(fake_client):
    captured = []
    with patch("jobs.run_clauses.FutmondoClient", fake_client), patch(
        "jobs.run_clauses.notify", side_effect=lambda m: captured.append(m)
    ):
        run_clauses.run()
    return captured


def test_proposal_mode_notifies_and_never_pays(tmp_db, roster_player_factory, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_CLAUSE_AUTO_PAY", False)
    squad = _squad(roster_player_factory)
    _seed_squad(squad)
    paid = []

    captured = _run(_fake_client(squad, [_rival(roster_player_factory)], paid=paid))

    assert paid == []
    assert len(captured) == 1
    assert "Crack" in captured[0] and "NO pagados" in captured[0] and "tapa un hueco" in captured[0]
    with get_connection() as conn:
        rows = conn.execute("SELECT player_id, status, clause_price FROM clause_payments").fetchall()
        team = conn.execute("SELECT team FROM players WHERE id = '9001'").fetchone()["team"]
    assert [(r["player_id"], r["status"], r["clause_price"]) for r in rows] == [("9001", "proposed", 20_000_000)]
    assert team == "Equipo"  # resuelto por teamId desde la plantilla (championshipplayers no trae `team`)


def test_own_players_and_future_or_too_expensive_clauses_are_ignored(tmp_db, roster_player_factory, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_CLAUSE_AUTO_PAY", False)
    squad = _squad(roster_player_factory)
    _seed_squad(squad)
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat().replace("+00:00", "Z")
    rivals = [
        _rival(roster_player_factory, id="9001", clause_price=50_000_000),  # > 20% de 200M
        _rival(roster_player_factory, id="9002", date=future),  # todavía protegido
    ]

    captured = _run(_fake_client(squad, rivals))

    assert captured == []
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM clause_payments").fetchone()[0] == 0


def test_auto_pay_confirmed_records_paid_and_won_bid(tmp_db, roster_player_factory, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_CLAUSE_AUTO_PAY", True)
    squad = _squad(roster_player_factory)
    _seed_squad(squad)
    rival = _rival(roster_player_factory)
    paid = []

    captured = _run(_fake_client(squad, [rival], after_pay_roster=squad + [rival], paid=paid))

    assert paid == [("9001", 20_000_000)]
    assert "clausulazo pagado" in captured[0]
    with get_connection() as conn:
        status = conn.execute("SELECT status FROM clause_payments").fetchone()["status"]
        bid = conn.execute("SELECT amount, status FROM bids WHERE player_id = '9001'").fetchone()
    assert status == "paid"
    assert (bid["amount"], bid["status"]) == (20_000_000, "won")


def test_auto_pay_rejected_records_failed_without_bid(tmp_db, roster_player_factory, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_CLAUSE_AUTO_PAY", True)
    squad = _squad(roster_player_factory)
    _seed_squad(squad)

    captured = _run(
        _fake_client(squad, [_rival(roster_player_factory)], pay_error=FutmondoOfferError("rechazado", code="api.x"))
    )

    assert "falló el clausulazo" in captured[0]
    with get_connection() as conn:
        row = conn.execute("SELECT status, error FROM clause_payments").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM bids").fetchone()[0] == 0
    assert row["status"] == "failed" and "rechazado" in row["error"]


def test_auto_pay_not_in_roster_after_pay_is_unconfirmed(tmp_db, roster_player_factory, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_CLAUSE_AUTO_PAY", True)
    squad = _squad(roster_player_factory)
    _seed_squad(squad)

    captured = _run(_fake_client(squad, [_rival(roster_player_factory)], after_pay_roster=squad, paid=[]))

    assert "todavía no aparece" in captured[0]
    with get_connection() as conn:
        assert conn.execute("SELECT status FROM clause_payments").fetchone()["status"] == "unconfirmed"
        assert conn.execute("SELECT COUNT(*) FROM bids").fetchone()[0] == 0


def test_weekly_cap_blocks_payment(tmp_db, roster_player_factory, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_CLAUSE_AUTO_PAY", True)
    monkeypatch.setattr(config, "CLAUSE_MAX_PER_WEEK", 1)
    squad = _squad(roster_player_factory)
    _seed_squad(squad)
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO clause_payments (player_id, clause_price, status, created_at) VALUES ('1000', 1, 'paid', ?)",
            (recent,),
        )
    paid = []

    captured = _run(_fake_client(squad, [_rival(roster_player_factory)], paid=paid))

    assert paid == [] and captured == []
