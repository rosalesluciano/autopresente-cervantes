# AutoPresente — asistencia sin que el profesor toque nada

Toma la asistencia presencial (QR dinámico) y la virtual (reporte de asistencia de Teams + tiempo de
cámara), la carga sola en **Autogestión Cervantes** con un robot y le manda al profesor un correo con el
resumen. El profesor no abre nada, no aprieta nada y no instala nada.

## Cómo se logra "cero intervención"

| Momento | Qué pasa | Quién lo dispara |
|---|---|---|
| Inicio del cuatrimestre | Se carga el horario (`config/cursos.json`) y el padrón (`config/padrones/<CURSO>.csv`). | Una vez, sistemas/bedelía |
| 10 min antes de clase | La pantalla del aula (en modo kiosco) muestra el QR que rota cada 20 s. | El horario |
| Durante la clase | El alumno escanea, entra con su cuenta Microsoft 365 y queda presente si su GPS está a menos de 150 m del aula **o** si está conectado al Wi-Fi de la facultad. | El alumno |
| Durante la clase | Los alumnos remotos entran a la reunión de Teams del calendario del profesor. Teams registra solo quién entró, cuándo y cuánto tiempo estuvo. | Teams |
| Fin + 15 min | Se descarga el reporte de asistencia de Teams y los `callRecords` (segmentos con video de cámara) por Graph API. | El horario |
| Enseguida | Se cruza todo con el padrón: presencial > virtual (≥ 60 % del tiempo) > ausente. Se exporta un CSV. | Automático |
| Enseguida | El robot (Playwright) entra a Cervantes con las credenciales del profesor, va a Cátedra › Asistencia de alumnos, filtra materia/división/mes/fecha, marca P/A por matrícula y guarda. | Automático |
| Enseguida | Llega un correo al profesor con presenciales/virtuales/ausentes y el % de cámara de cada alumno virtual (con alerta debajo del 30 %). | Automático |

Si algo falla (Cervantes caído, el reporte de Teams todavía no está listo) el paso se reintenta solo con
espera creciente, hasta 6 veces. Cada paso es idempotente: volver a ejecutarlo no duplica nada.

### Asistencia desde Teams (la observación del equipo)
Cada clase virtual es una reunión de Teams en el calendario del profesor (recurrente, creada una sola vez).
El sistema la encuentra sola por el asunto (`teams_asunto` en `cursos.json`), y cuando termina pide a Graph
`onlineMeetings/{id}/attendanceReports` → elige el reporte de **esa** ocurrencia → `attendanceRecords`.
Cada registro trae el email institucional, que se cruza con el padrón para obtener la matrícula.

### % de cámara sin capturas de pantalla
En vez de un script que saque screenshots en la PC del profesor (que tendría que estar prendida y con Teams
abierto), usamos `communications/callRecords`: Teams guarda, por participante, los segmentos de la llamada y
sus medios. Un medio `main-video` con flujo saliente del alumno = cámara encendida durante ese segmento.
`% cámara = segundos con video / segundos en la reunión`.

## Correr la demo (sin cuentas de Microsoft)

```bash
powershell -ExecutionPolicy Bypass -File iniciar.ps1
```

1. Abrir `http://localhost:8000` → **Simular una clase** → *Iniciar clase*.
2. Abrir *Pantalla del aula* en el proyector. Desde el celular (misma red Wi-Fi) escanear el QR.
   Para eso poné en `.env` `BASE_URL=http://<IP-de-tu-PC>:8000`.
3. *Terminar ya* en el panel (o esperar a que termine la clase). Se abre Edge y el robot llena el
   portal Cervantes simulado frente al jurado; después aparece el correo en *Ver detalle › Ver correo enviado*.

Tip para el jurado: pantalla dividida, de un lado el celular escaneando y del otro el portal llenándose solo
(`RPA_HEADLESS=0`, `RPA_SLOW_MO_MS=250`).

## Pasar a producción

1. **Entra ID → Registro de aplicación.** Permisos de aplicación con consentimiento de admin:
   `Calendars.Read`, `OnlineMeetings.Read.All`, `OnlineMeetingArtifact.Read.All`, `CallRecords.Read.All`,
   `User.Read.All`, `Mail.Send`. Permiso delegado `User.Read` y redirect URI `https://<servidor>/auth/callback`
   para el login de alumnos. Completar `MS_*` en `.env`.
2. **Application Access Policy de Teams** (sin esto Graph no devuelve reportes de reuniones):
   ```powershell
   New-CsApplicationAccessPolicy -Identity AutoPresente -AppIds "<MS_CLIENT_ID>"
   Grant-CsApplicationAccessPolicy -PolicyName AutoPresente -Identity "profesor@universidad.edu.ar"
   ```
3. En las reuniones de Teams del profesor activar *Informe de asistencia* (viene activado por defecto).
4. **Cervantes:** completar `CERVANTES_URL/USUARIO/CLAVE`, `RPA_HEADLESS=1`, e inspeccionar el portal real
   para ajustar `config/cervantes_selectores.json` (el código del robot no cambia).
5. **HTTPS** (necesario para el GPS del celular): publicar detrás de IIS/nginx/Caddy con certificado.
   Sin HTTPS la validación cae al Wi-Fi (`redes_wifi` de cada aula).
6. Correr como servicio de Windows (p. ej. con NSSM apuntando a `iniciar.ps1`) y poner
   `scripts/kiosco_aula.ps1` en el inicio de la PC de cada aula.
7. `DEMO=0`.

## Estructura

```
app/
  main.py            rutas: panel, pantalla del aula, check-in, API
  orquestador.py     reloj + máquina de estados de cada clase (reintentos)
  qr.py              tokens HMAC que rotan cada 20 s
  teams.py, graph.py asistencia y cámara desde Microsoft Graph
  consolidar.py      presencial + virtual + padrón → registros + CSV
  rpa_cervantes.py   robot Playwright
  reporte.py         correo HTML (Graph → SMTP → data/outbox)
  mock_cervantes.py  portal Cervantes simulado para la demo
config/              cursos, horarios, aulas, reglas, selectores del portal
data/                base SQLite, CSV exportados, correos, capturas del robot
```
