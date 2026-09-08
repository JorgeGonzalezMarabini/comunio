from unittest.mock import patch

import pytest
import requests

from db.models import get_connection
from notifier import notify, track_job_run


def test_track_job_run_notifies_and_reraises_on_exception(tmp_db):
    """
    Regresión del fallo real (2026-08-17, ver notifier.py:track_job_run,
    antes notify_on_crash): jobs/run_market.py cayó entero en GitHub
    Actions sin ningún aviso -- el wrapper debe notificar por Telegram Y
    dejar que la excepción se propague igual (GitHub Actions debe seguir
    marcando el job en rojo).

    `tmp_db` no se usa directamente aquí, pero `track_job_run` persiste
    SIEMPRE en `job_runs` (ver notifier.py) -- sin aislar `config.
    DATABASE_PATH` este test escribía de verdad en `db/futmondo.db`, el
    archivo versionado en el repo (encontrado auditando el TODO #5:
    filas "run_market"/"boom" coladas por este mismo test).
    """
    captured = []
    with patch("notifier.notify", side_effect=lambda m: captured.append(m)):
        with pytest.raises(ValueError):
            with track_job_run("run_market"):
                raise ValueError("boom")

    assert len(captured) == 1
    assert "run_market" in captured[0]
    assert "ValueError" in captured[0]
    assert "boom" in captured[0]


def test_track_job_run_does_not_notify_when_no_exception(tmp_db):
    captured = []
    with patch("notifier.notify", side_effect=lambda m: captured.append(m)):
        with track_job_run("run_market"):
            pass  # sin excepción -- no debe notificar nada

    assert captured == []


def test_track_job_run_persists_duration_on_success(tmp_db):
    with track_job_run("run_market"):
        pass

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM job_runs WHERE job_name = 'run_market'").fetchone()
    assert row is not None
    assert row["status"] == "ok"
    assert row["error"] is None
    assert row["duration_seconds"] >= 0
    assert row["started_at"] <= row["finished_at"]


def test_track_job_run_persists_duration_and_error_on_exception(tmp_db):
    with patch("notifier.notify"):
        with pytest.raises(ValueError):
            with track_job_run("run_market"):
                raise ValueError("boom")

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM job_runs WHERE job_name = 'run_market'").fetchone()
    assert row is not None
    assert row["status"] == "error"
    assert "boom" in row["error"]
    assert row["duration_seconds"] >= 0


def test_track_job_run_survives_db_failure_without_masking_original_exception(tmp_db, monkeypatch, capsys):
    """
    Un fallo al persistir en `job_runs` (BD bloqueada, tabla inexistente en
    el primer run tras el despliegue...) no debe enmascarar la excepción
    real de `run()` ni impedir el aviso por Telegram -- mismo espíritu que
    `notify()`, que nunca lanza por un fallo de Telegram.
    """
    import notifier

    def boom(*args, **kwargs):
        raise RuntimeError("BD bloqueada")

    monkeypatch.setattr(notifier, "record_job_run", boom)

    captured = []
    with patch("notifier.notify", side_effect=lambda m: captured.append(m)):
        with pytest.raises(ValueError, match="boom"):
            with track_job_run("run_market"):
                raise ValueError("boom")

    assert len(captured) == 1  # el aviso de Telegram sigue llegando
    assert "Error persistiendo job_runs" in capsys.readouterr().out


def test_notify_splits_message_longer_than_telegram_limit(monkeypatch):
    """
    Regresión del fallo real (2026-09-08): `run_market` dejó de notificar
    porque su resumen (una línea con un id por candidato descartado)
    superó los 4096 caracteres que Telegram acepta en un solo `sendMessage`
    -- la API respondía 400 Bad Request y `notify()` lo tragaba como
    cualquier otro fallo de red, sin avisar de nada. Ahora debe trocearlo
    en varios mensajes en vez de mandar uno solo condenado a fallar.
    """
    import config

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "fake-chat")

    long_message = "\n".join(f"linea {i}: {'x' * 80}" for i in range(80))  # bastante más de 4096 chars
    assert len(long_message) > 4096

    sent_texts = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        sent_texts.append(json["text"])
        return FakeResponse()

    with patch("notifier.requests.post", side_effect=fake_post):
        assert notify(long_message) is True

    assert len(sent_texts) > 1
    assert all(len(t) <= 4096 for t in sent_texts)
    # Ningún trozo del mensaje original se pierde al trocear (cada
    # fragmento, salvo el primero, va prefijado con "(cont.)\n" -- se
    # quita para comparar contra el mensaje original completo).
    reconstructed = "\n".join(
        t if i == 0 else t.replace("(cont.)\n", "", 1) for i, t in enumerate(sent_texts)
    )
    assert reconstructed == long_message


def test_notify_returns_false_and_logs_telegram_error_body(monkeypatch, capsys):
    """El cuerpo de la respuesta de error de Telegram (con el motivo real del 400) se imprime, no solo el genérico de requests."""
    import config

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "fake-chat")

    class FakeResponse:
        text = '{"ok": false, "description": "Bad Request: message is too long"}'

        def raise_for_status(self):
            raise requests.HTTPError("400 Client Error: Bad Request for url: ...", response=self)

    with patch("notifier.requests.post", return_value=FakeResponse()):
        assert notify("hola") is False

    assert "message is too long" in capsys.readouterr().out
