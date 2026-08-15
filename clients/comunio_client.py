"""
Cliente de Comunio (ingeniería inversa de la web, sin API pública oficial).

BLOQUEANTE: nada de este módulo se puede terminar de escribir con confianza
hasta capturar con Chrome DevTools (Network → HAR) el flujo real:
    - URL y payload exacto del login
    - Estructura de la respuesta con el token
    - Header exacto usado para autenticar peticiones posteriores
      (se asume Authorization: Bearer <token>, a confirmar)
    - Endpoints de clasificación, plantilla, mercado y pujas

Hasta entonces, este cliente es un esqueleto con la forma esperada de la
interfaz pública que usarán engine/ y jobs/.
"""
from __future__ import annotations

import requests

import config


class ComunioAuthError(Exception):
    """Login fallido o token inválido/expirado."""


class ComunioClient:
    def __init__(self, email: str = None, password: str = None):
        self.email = email or config.COMUNIO_EMAIL
        self.password = password or config.COMUNIO_PASSWORD
        self.base_url = config.COMUNIO_BASE_URL
        self.session = requests.Session()
        self.token: str | None = None

    def login(self) -> str:
        """
        Autentica contra Comunio y guarda el token en self.token.

        TODO: rellenar endpoint/payload reales una vez capturado el HAR.
        """
        raise NotImplementedError(
            "Pendiente: capturar endpoint y payload real de login (ver HAR)."
        )

    def _auth_headers(self) -> dict:
        if not self.token:
            raise ComunioAuthError("No hay token; llama a login() antes.")
        return {config.COMUNIO_AUTH_HEADER: f"{config.COMUNIO_AUTH_SCHEME} {self.token}"}

    def get_standings(self) -> dict:
        """Clasificación de la liga."""
        raise NotImplementedError("Pendiente: endpoint real de clasificación.")

    def get_squad(self) -> dict:
        """Plantilla actual del usuario (jugadores, precios, estado)."""
        raise NotImplementedError("Pendiente: endpoint real de plantilla.")

    def get_market(self) -> dict:
        """Jugadores disponibles en el mercado / en subasta."""
        raise NotImplementedError("Pendiente: endpoint real de mercado.")

    def place_bid(self, player_id: str, amount: int) -> dict:
        """Puja por un jugador. Debe ser idempotente/segura ante reintentos."""
        raise NotImplementedError("Pendiente: endpoint real de pujas.")

    def set_lineup(self, formation: str, player_ids: list[str]) -> dict:
        """Fija el once inicial para la próxima jornada."""
        raise NotImplementedError("Pendiente: endpoint real de alineación.")
