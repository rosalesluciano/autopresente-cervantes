"""Configuración central: variables de entorno + config/cursos.json."""
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

DATA = RAIZ / "data"
OUTBOX = DATA / "outbox"
EXPORTS = DATA / "exports"
CAPTURAS = DATA / "capturas_rpa"
for d in (DATA, OUTBOX, EXPORTS, CAPTURAS):
    d.mkdir(parents=True, exist_ok=True)


def _env(nombre: str, defecto: str = "") -> str:
    return os.getenv(nombre, defecto).strip()


DEMO = _env("DEMO", "1") == "1"
BASE_URL = _env("BASE_URL", "http://localhost:8000").rstrip("/")
SECRET_KEY = _env("SECRET_KEY", "dev-secret-cambiar")
TZ = ZoneInfo(_env("TZ", "America/Argentina/Cordoba"))

MS_TENANT_ID = _env("MS_TENANT_ID")
MS_CLIENT_ID = _env("MS_CLIENT_ID")
MS_CLIENT_SECRET = _env("MS_CLIENT_SECRET")
MS_DOMINIO_ALUMNOS = _env("MS_DOMINIO_ALUMNOS")
GRAPH_CONFIGURADO = bool(MS_TENANT_ID and MS_CLIENT_ID and MS_CLIENT_SECRET)

CERVANTES_URL = _env("CERVANTES_URL", f"{BASE_URL}/cervantes").rstrip("/")
CERVANTES_USUARIO = _env("CERVANTES_USUARIO", "profesor")
CERVANTES_CLAVE = _env("CERVANTES_CLAVE", "demo")
RPA_HEADLESS = _env("RPA_HEADLESS", "0") == "1"
RPA_SLOW_MO_MS = int(_env("RPA_SLOW_MO_MS", "250") or 0)
RPA_BROWSER_CHANNEL = _env("RPA_BROWSER_CHANNEL", "msedge") or None

SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587") or 587)
SMTP_USUARIO = _env("SMTP_USUARIO")
SMTP_CLAVE = _env("SMTP_CLAVE")
SMTP_REMITENTE = _env("SMTP_REMITENTE") or SMTP_USUARIO

with open(RAIZ / "config" / "cursos.json", encoding="utf-8") as f:
    CFG = json.load(f)
with open(RAIZ / "config" / "cervantes_selectores.json", encoding="utf-8") as f:
    SELECTORES = json.load(f)

PROFESOR = CFG["profesor"]
AULAS = CFG["aulas"]
CURSOS = {c["id"]: c for c in CFG["cursos"]}
REGLAS = CFG["reglas"]

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
