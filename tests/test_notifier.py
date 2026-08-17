from unittest.mock import patch

import pytest

from notifier import notify_on_crash


def test_notify_on_crash_notifies_and_reraises_on_exception():
    """
    Regresión del fallo real (2026-08-17, ver notifier.py:notify_on_crash):
    jobs/run_market.py cayó entero en GitHub Actions sin ningún aviso --
    el wrapper debe notificar por Telegram Y dejar que la excepción se
    propague igual (GitHub Actions debe seguir marcando el job en rojo).
    """
    captured = []
    with patch("notifier.notify", side_effect=lambda m: captured.append(m)):
        with pytest.raises(ValueError):
            with notify_on_crash("run_market"):
                raise ValueError("boom")

    assert len(captured) == 1
    assert "run_market" in captured[0]
    assert "ValueError" in captured[0]
    assert "boom" in captured[0]


def test_notify_on_crash_does_not_notify_when_no_exception():
    captured = []
    with patch("notifier.notify", side_effect=lambda m: captured.append(m)):
        with notify_on_crash("run_market"):
            pass  # sin excepción -- no debe notificar nada

    assert captured == []
