"""
Tests del parseo de config.ENABLE_BOT.

Vía subprocess a propósito: config.py calcula sus valores como expresiones
a nivel de módulo (no una función), así que recargar el módulo en proceso
(`importlib.reload`) dejaría "sucio" el resto de atributos de config para
el resto de la suite (todos los demás tests comparten el mismo módulo
`config` ya importado y cacheado por Python).
"""
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _enable_bot_value(env_value):
    """
    Lanza un intérprete Python limpio que importa `config` con `ENABLE_BOT`
    puesto (o AUSENTE si `env_value` es None) y devuelve el
    `config.ENABLE_BOT` resultante.
    """
    env = {"PATH": os.environ.get("PATH", "")}
    if env_value is not None:
        env["ENABLE_BOT"] = env_value
    result = subprocess.run(
        [sys.executable, "-c", "import config; print(config.ENABLE_BOT)"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip() == "True"


def test_enable_bot_defaults_true_when_env_var_unset():
    assert _enable_bot_value(None) is True


def test_enable_bot_true_when_env_var_is_empty_string():
    """
    Regresión real detectada al conectar ENABLE_BOT a los workflows de
    GitHub Actions: una GitHub Variable NO definida se resuelve como
    cadena VACÍA (`${{ vars.ENABLE_BOT }}`) -- la env var SÍ llega al
    proceso, solo que vacía, no se omite. Con un parseo ingenuo
    (`os.getenv(..., "true") == "true"`), eso apagaría el bot en cuanto se
    desplegara este cambio, sin que nadie hubiera pedido pausar nada.
    """
    assert _enable_bot_value("") is True


def test_enable_bot_false_when_explicitly_false():
    assert _enable_bot_value("false") is False
    assert _enable_bot_value("False") is False
    assert _enable_bot_value("0") is False


def test_enable_bot_true_when_explicitly_true():
    assert _enable_bot_value("true") is True
    assert _enable_bot_value("True") is True
