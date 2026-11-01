"""Configuración central: variables de entorno + config/cursos.json."""
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

def _env(nombre: str, defecto: str = "") -> str:
    return os.getenv(nombre, defecto).strip()


DATA = Path(_env("DATA_DIR") or RAIZ / "data")  # las pruebas usan una carpeta aparte
OUTBOX = DATA / "outbox"
EXPORTS = DATA / "exports"
CAPTURAS = DATA / "capturas_rpa"
for d in (DATA, OUTBOX, EXPORTS, CAPTURAS):
    d.mkdir(parents=True, exist_ok=True)


DEMO = _env("DEMO", "1") == "1"
BASE_URL = _env("BASE_URL", "http://localhost:8000").rstrip("/")
SECRET_KEY = _env("SECRET_KEY", "dev-secret-cambiar")
TZ = ZoneInfo(_env("TZ", "America/Argentina/Cordoba"))

MS_TENANT_ID = _env("MS_TENANT_ID")
MS_CLIENT_ID = _env("MS_CLIENT_ID")
MS_CLIENT_SECRET = _env("MS_CLIENT_SECRET")
MS_DOMINIO_ALUMNOS = _env("MS_DOMINIO_ALUMNOS")
# GRAPH_SIMULADO=1 usa el Graph falso de app/graph_simulado.py (mismas rutas y
# formatos que el real) para probar el circuito completo sin un tenant.
GRAPH_SIMULADO = _env("GRAPH_SIMULADO", "0") == "1"
GRAPH_CONFIGURADO = GRAPH_SIMULADO or bool(MS_TENANT_ID and MS_CLIENT_ID and MS_CLIENT_SECRET)
# Login real con Microsoft 365 (alumnos y docentes) sólo con credenciales reales.
LOGIN_MICROSOFT = not DEMO and not GRAPH_SIMULADO and bool(MS_TENANT_ID and MS_CLIENT_ID and MS_CLIENT_SECRET)
# Controles para simular clases: en la demo y en las pruebas con Graph simulado.
PRUEBAS = DEMO or GRAPH_SIMULADO
# ORQUESTADOR=0 apaga el reloj automático (lo usan las pruebas, que avanzan las clases a mano).
ORQUESTADOR = _env("ORQUESTADOR", "1") == "1"
# Correos de quienes pueden ver el panel (además del profesor de config/cursos.json).
ADMINS = {e.strip().lower() for e in _env("ADMINS").split(",") if e.strip()}

# Teams: aviso con Adaptive Card (URL de un flujo "Workflows" de Teams) y app de Teams.
TEAMS_WEBHOOK_URL = _env("TEAMS_WEBHOOK_URL")
TEAMS_APP_ID = _env("TEAMS_APP_ID", "6f1c2a4e-8b3d-4c5e-9a7f-2d1e0b9c8a71")
HTTPS = BASE_URL.startswith("https://")
# Clave para abrir la pantalla del aula sin iniciar sesión: /aula/A-201?k=<clave>
KIOSCO_CLAVE = _env("KIOSCO_CLAVE")

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
CUATRIMESTRE = CFG.get("cuatrimestre", {})
AULAS = CFG["aulas"]
CURSOS = {c["id"]: c for c in CFG["cursos"]}
REGLAS = CFG["reglas"]

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
