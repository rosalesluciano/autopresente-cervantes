# Levanta el sistema como en producción (login, panel protegido, robot, avisos a Teams)
# pero contra el Microsoft Graph simulado, para probar todo sin un tenant de Microsoft 365.
# Datos en data-prueba/ (no toca la base real). Panel: http://localhost:8000 (ingresar con el correo del profesor).
Set-Location (Join-Path $PSScriptRoot "..")
$env:DEMO = "0"
$env:GRAPH_SIMULADO = "1"
$env:BASE_URL = "http://localhost:8000"
$env:CERVANTES_URL = "http://localhost:8000/cervantes"
$env:DATA_DIR = "data-prueba"
$env:TEAMS_WEBHOOK_URL = "http://localhost:8000/_graph/v1.0/_webhook"
$env:MS_TENANT_ID = ""; $env:MS_CLIENT_ID = ""; $env:MS_CLIENT_SECRET = ""
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
