"""QR dinámico: un token firmado que cambia cada N segundos.

Una foto del QR reenviada por WhatsApp deja de servir en ~40 s, y el token
sólo identifica la clase, nunca al alumno (él se identifica con su cuenta
institucional).
"""
import base64
import hashlib
import hmac
import io
import time

import qrcode
from qrcode.image.svg import SvgPathImage

from .config import REGLAS, SECRET_KEY

VENTANA = int(REGLAS.get("qr_rotacion_segundos", 20))


def _firma(clase_id: int, ventana: int) -> str:
    msg = f"{clase_id}:{ventana}".encode()
    dig = hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).digest()[:9]
    return base64.urlsafe_b64encode(dig).decode().rstrip("=")


def token_actual(clase_id: int) -> tuple[str, int]:
    """Devuelve (token, segundos hasta la próxima rotación)."""
    t = time.time()
    ventana = int(t // VENTANA)
    restante = VENTANA - int(t % VENTANA)
    return f"{clase_id}.{_firma(clase_id, ventana)}", restante


def validar(token: str) -> int | None:
    """Acepta la ventana actual y la anterior (margen para escanear). Devuelve clase_id."""
    try:
        clase_txt, firma = token.split(".", 1)
        clase_id = int(clase_txt)
    except ValueError:
        return None
    ventana = int(time.time() // VENTANA)
    for v in (ventana, ventana - 1):
        if hmac.compare_digest(firma, _firma(clase_id, v)):
            return clase_id
    return None


def svg(url: str) -> str:
    img = qrcode.make(url, image_factory=SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()
