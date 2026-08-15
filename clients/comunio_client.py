"""
Cliente de Comunio (ingeniería inversa de la web, sin API pública oficial).

Endpoints y forma de las llamadas capturados con Chrome DevTools el
2026-08-15 sobre una liga de prueba real. Dos dominios distintos:
    - https://www.comunio.es   -> frontend Next.js (no se usa desde el bot)
    - https://api.comunio.es   -> API REST real (todo lo de abajo apunta aquí)

Auth: NO es cookie de sesión. El login devuelve access_token/refresh_token
que el frontend guarda en localStorage y reenvía en cada llamada autenticada
como header `Authorization` (esquema asumido "Bearer <token>" por
convención — no se ha podido confirmar el prefijo exacto sin exponer el
token real; si falla con 401, probar variantes en COMUNIO_AUTH_SCHEME).

Endpoints confirmados por HAR:
    POST /login
        body: {"username": ..., "password": ..., "tzoffset": ...}
        headers: Accept, Content-Type, X-Timezone (sin Authorization)
    GET  /login/state
    GET  /users/{userId}/squad                                   -> plantilla
    GET  /communities/{communityId}/standings?period=total&wpe=true  -> clasificación
    GET  /communities/{communityId}/members                      -> miembros de la liga
    GET  /communities/{communityId}/users/{userId}/exchangemarket -> mercado (compra/venta)
    GET  /communities/{communityId}/users/{userId}/offers         -> ofertas/pujas activas
    GET  /communities/{communityId}/users/{userId}/offers/new/since/{unix_ts}
    GET  /communities/{communityId}/users/{userId}/lineup         -> alineación actual
    POST /communities/{communityId}/users/{userId}/logout  body: {"userId": ...}

Validado con una prueba real controlada (2026-08-15, liga de prueba, con
permiso explícito del usuario):
    - Pujar reutiliza el MISMO path que listar ofertas:
      .../users/{userId}/offers (patrón REST típico: create = POST al
      mismo path que el GET de la colección). Body confirmado por la UI:
      importe prellenado al VM del jugador.
    - Guardar alineación reutiliza el MISMO path que leerla:
      .../users/{userId}/lineup (patrón REST típico: replace = PUT al
      mismo path que el GET del recurso).
    - Formaciones reales del sitio: "4-4-2", "4-3-3", etc. (SIN el "1-" del
      portero, a diferencia de lo asumido inicialmente en lineup_optimizer.py).

No se pudo confirmar con certeza absoluta (la Performance API del navegador
no expone el verbo HTTP ni el body real, solo la URL): el verbo exacto
(asumido POST/PUT por convención REST) y los nombres de campo exactos del
body. Si algún endpoint de escritura devuelve 4xx, revisar esto primero.

Campo exacto de la respuesta de login donde vienen community_id y user_id:
sigue sin confirmar (de momento configurables a mano, ver config.py).
"""
from __future__ import annotations

import requests

import config


class ComunioAuthError(Exception):
    """Login fallido o token inválido/expirado."""


class ComunioClient:
    def __init__(self, email: str = None, password: str = None, community_id: str = None):
        self.email = email or config.COMUNIO_EMAIL
        self.password = password or config.COMUNIO_PASSWORD
        self.community_id = community_id or config.COMUNIO_COMMUNITY_ID
        self.base_url = config.COMUNIO_BASE_URL
        self.session = requests.Session()
        self.token: str | None = None
        self.refresh_token: str | None = None
        self.user_id: str | None = None

    # --- auth ---

    def login(self) -> str:
        """
        Autentica contra Comunio y guarda access_token/refresh_token.

        TODO: confirmar contra la respuesta real:
          - nombres de campo (access_token/accessToken, etc.)
          - dónde viene el user_id (necesario para casi todos los demás
            endpoints, que van scoped a /users/{userId}/...)
        """
        resp = self.session.post(
            f"{self.base_url}/login",
            json={
                "username": self.email,
                "password": self.password,
                "tzoffset": 0,  # TODO: confirmar formato real (minutos vs horas)
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Timezone": "Europe/Madrid",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise ComunioAuthError(f"Login falló ({resp.status_code}): {resp.text[:200]}")

        data = resp.json()
        # TODO: ajustar nombres de campo reales una vez confirmados.
        self.token = data.get("access_token") or data.get("accessToken")
        self.refresh_token = data.get("refresh_token") or data.get("refreshToken")
        self.user_id = data.get("user_id") or data.get("userId")

        if not self.token:
            raise ComunioAuthError(f"Login OK pero no se encontró el token en la respuesta: {data!r}")

        return self.token

    def _auth_headers(self) -> dict:
        if not self.token:
            raise ComunioAuthError("No hay token; llama a login() antes.")
        return {
            config.COMUNIO_AUTH_HEADER: f"{config.COMUNIO_AUTH_SCHEME} {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Timezone": "Europe/Madrid",
        }

    def _get(self, path: str, **kwargs) -> dict:
        resp = self.session.get(f"{self.base_url}{path}", headers=self._auth_headers(), timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _write(self, method: str, path: str, json_body: dict) -> dict:
        resp = self.session.request(
            method, f"{self.base_url}{path}", headers=self._auth_headers(), json=json_body, timeout=15
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # --- lectura ---

    def get_standings(self) -> dict:
        """Clasificación de la liga."""
        return self._get(f"/communities/{self.community_id}/standings", params={"period": "total", "wpe": "true"})

    def get_squad(self) -> dict:
        """Plantilla actual del usuario (jugadores, precios, estado)."""
        return self._get(f"/users/{self.user_id}/squad")

    def get_market(self) -> dict:
        """Jugadores disponibles en el mercado / en subasta (compra + venta)."""
        return self._get(f"/communities/{self.community_id}/users/{self.user_id}/exchangemarket")

    def get_offers(self) -> dict:
        """Ofertas/pujas activas (propias y recibidas)."""
        return self._get(f"/communities/{self.community_id}/users/{self.user_id}/offers")

    def get_lineup(self) -> dict:
        """Alineación actualmente guardada."""
        return self._get(f"/communities/{self.community_id}/users/{self.user_id}/lineup")

    # --- escritura (path confirmado con captura real; verbo/body por convención REST) ---

    def place_bid(self, player_id: str, amount: int) -> dict:
        """
        Puja por un jugador.

        Path confirmado por captura real (reutiliza el de get_offers). Verbo
        y nombres de campo del body por convención REST, sin confirmar al
        100%: si da 4xx, es lo primero a revisar.
        """
        return self._write(
            "POST",
            f"/communities/{self.community_id}/users/{self.user_id}/offers",
            {"playerId": player_id, "amount": amount},
        )

    def set_lineup(self, formation: str, starter_ids: list[str], bench_ids: list[str] = None) -> dict:
        """
        Fija el once inicial para la próxima jornada.

        Path confirmado por captura real (reutiliza el de get_lineup, patrón
        PUT-replace). Formación en formato real del sitio: "4-4-2", "4-3-3",
        etc. (sin el "1-" del portero). Verbo/nombres de campo del body por
        convención REST, sin confirmar al 100%.
        """
        return self._write(
            "PUT",
            f"/communities/{self.community_id}/users/{self.user_id}/lineup",
            {"formation": formation, "starters": starter_ids, "bench": bench_ids or []},
        )
