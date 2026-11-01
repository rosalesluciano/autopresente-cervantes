"""Levanta el servidor real en modo producción con Graph simulado, en una carpeta de datos temporal."""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

PUERTO = 8765
BASE = f"http://127.0.0.1:{PUERTO}"

# La configuración se lee al importar app.config: se fija antes de importar la app.
os.environ.update({
    "DEMO": "0",
    "GRAPH_SIMULADO": "1",
    "BASE_URL": BASE,
    "CERVANTES_URL": f"{BASE}/cervantes",
    "DATA_DIR": tempfile.mkdtemp(prefix="autopresente-pruebas-"),
    "ORQUESTADOR": "0",
    "RPA_HEADLESS": "1",
    "RPA_SLOW_MO_MS": "0",
    "TEAMS_WEBHOOK_URL": f"{BASE}/_graph/v1.0/_webhook",
    "SECRET_KEY": "clave-de-prueba",
    "ADMINS": "",
    "KIOSCO_CLAVE": "kiosco-prueba",
    "MS_TENANT_ID": "",
    "MS_CLIENT_ID": "",
    "MS_CLIENT_SECRET": "",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
import uvicorn  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def servidor():
    from app.main import app

    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PUERTO, log_level="warning"))
    hilo = threading.Thread(target=srv.run, daemon=True)
    hilo.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", PUERTO), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield BASE
    srv.should_exit = True
    hilo.join(timeout=5)
