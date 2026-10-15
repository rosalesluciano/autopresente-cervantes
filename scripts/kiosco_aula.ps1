# Se instala UNA vez en la PC/TV de cada aula (Inicio de Windows o Programador de tareas).
# Abre la pantalla del QR a pantalla completa; el QR aparece solo cuando empieza la clase.
param(
    [string]$Aula = "A-201",
    [string]$Servidor = "http://autopresente.facultad.local:8000"
)
Start-Process "msedge.exe" -ArgumentList "--kiosk `"$Servidor/aula/$Aula`" --edge-kiosk-type=fullscreen --no-first-run"
