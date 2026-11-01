"""Quién está usando el sistema.

- Docentes (el profesor de config/cursos.json y los correos de ADMINS) ven el
  panel. Entran con su cuenta de Microsoft 365 o, dentro de Teams, solos (SSO).
- Alumnos sólo hacen el check-in del QR.
- La pantalla del aula se abre con la clave de kiosco (KIOSCO_CLAVE) o con una
  sesión docente.
En modo DEMO no se pide nada.
"""
from __future__ import annotations

import hmac
from functools import lru_cache

from fastapi import HTTPException, Request

from . import config


class NoAutorizado(Exception):
    """Se convierte en redirección al login (páginas) o en 401 (API)."""


def usuario(request: Request) -> dict | None:
    return request.session.get("usuario")


def es_docente(email: str | None) -> bool:
    email = (email or "").lower()
    return bool(email) and (email == config.PROFESOR["email"].lower() or email in config.ADMINS)


def iniciar_sesion(request: Request, email: str, nombre: str = ""):
    email = email.lower()
    request.session["usuario"] = {"email": email, "nombre": nombre, "docente": es_docente(email)}


def requiere_docente(request: Request):
    if config.DEMO:
        return
    u = usuario(request)
    if not u or not u.get("docente"):
        raise NoAutorizado()


def requiere_kiosco(request: Request):
    """Pantalla del aula: clave de kiosco en la URL (?k=...) o sesión docente."""
    if config.DEMO or not config.KIOSCO_CLAVE:
        return
    clave = request.query_params.get("k") or request.session.get("kiosco", "")
    if clave and hmac.compare_digest(clave, config.KIOSCO_CLAVE):
        request.session["kiosco"] = clave
        return
    requiere_docente(request)


# ── SSO de Teams ──────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _jwks():
    import jwt

    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{config.MS_TENANT_ID}/discovery/v2.0/keys")


def validar_token_teams(token: str) -> dict:
    """Valida el token que entrega Teams (authentication.getAuthToken) y devuelve sus claims.

    Audiencia: el recurso declarado en webApplicationInfo del manifiesto
    (api://<dominio>/<client id>) o el client id. Se aceptan emisores v1 y v2.
    """
    import jwt

    if not (config.MS_TENANT_ID and config.MS_CLIENT_ID):
        raise HTTPException(503, "SSO no configurado: faltan MS_TENANT_ID y MS_CLIENT_ID")
    host = config.BASE_URL.split("://", 1)[-1]
    clave = _jwks().get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token, clave, algorithms=["RS256"],
        audience=[f"api://{host}/{config.MS_CLIENT_ID}", config.MS_CLIENT_ID],
        options={"require": ["exp", "iat", "aud", "iss"]},
    )
    emisores = {f"https://login.microsoftonline.com/{config.MS_TENANT_ID}/v2.0",
                f"https://sts.windows.net/{config.MS_TENANT_ID}/"}
    if claims.get("iss") not in emisores:
        raise HTTPException(401, "El token no es de la cuenta de la institución")
    return claims
