# AutoPresente — asistencia sin que el profesor toque nada

Toma la asistencia presencial (QR dinámico) y la virtual (reunión de Teams + tiempo de cámara), la carga sola
en **Autogestión Cervantes** con un robot y le avisa al profesor en **Teams** y por correo. El profesor no abre
nada, no aprieta nada y no instala nada.

## Cómo se logra "cero intervención"

| Momento | Qué pasa | Quién lo dispara |
|---|---|---|
| Inicio del cuatrimestre | Se cargan horario (`config/cursos.json`) y padrón (`config/padrones/<CURSO>.csv`). `python -m app.crear_reuniones` crea en el calendario del profesor una reunión de Teams semanal por curso y **invita a los alumnos**. | Una vez, sistemas/bedelía |
| 10 min antes de clase | La pantalla del aula (kiosco) y la pestaña de Teams de la reunión muestran el QR que rota cada 20 s. | El horario |
| Durante la clase | En el aula: el alumno escanea, entra con su cuenta Microsoft 365 y queda presente si su GPS está a menos de 150 m **o** si está en el Wi-Fi de la facultad. | El alumno |
| Durante la clase | Remotos: entran a la reunión desde la invitación. Teams registra solo quién entró, cuándo y cuánto. | Teams |
| Fin de clase | Se baja el reporte de asistencia y los `callRecords` (streams de video = cámara). Si Teams todavía no los publicó, se vuelve a consultar cada 5 min (hasta 4 h). | El horario |
| Enseguida | Cruce con el padrón: presencial > virtual (≥ 60 % del tiempo) > ausente. CSV por clase. | Automático |
| Enseguida | El robot (Playwright) carga Cátedra › Asistencia de alumnos en Cervantes y guarda. | Automático |
| Enseguida | Llega al profesor una **tarjeta en Teams** y un correo, con totales y alertas de cámara (< 30 %). Si algo falla del todo, también llega un aviso a Teams. | Automático |

## La app de Teams

| Dónde | Qué ve |
|---|---|
| **Pestaña personal "Panel"** | El panel completo de clases, dentro de Teams, con el tema de Teams (claro/oscuro) e inicio de sesión automático (SSO). |
| **Pestaña en la reunión o el canal de la materia** | La clase en curso: QR, contador y nombres en vivo. Al docente, además, el resumen de la última clase. |
| **Panel lateral de la reunión** | Lo mismo en chico, con el botón *Mostrar el QR en la pantalla de la reunión* (lo comparte en el escenario de Teams para proyectarlo en el aula). |
| **Chat del profesor** | La tarjeta (Adaptive Card) con el resumen de cada clase y el botón *Ver detalle*. |

## Modos

| Modo | Para qué | Cómo |
|---|---|---|
| **Demo** | Mostrar: todo simulado, sin login. | `DEMO=1` (por defecto). `powershell -ExecutionPolicy Bypass -File iniciar.ps1` |
| **Prueba con Teams simulado** | Probar el sistema de producción completo (login, panel protegido, Graph, robot, avisos) sin un tenant. Un Microsoft 365 falso responde con los mismos formatos que Graph. | `powershell -ExecutionPolicy Bypass -File scripts\probar_teams.ps1` e ingresar con `profesor@cervantes.edu.ar` |
| **Producción** | La facultad. | `DEMO=0` y los pasos de abajo. |

### Pruebas automáticas

```bash
.venv\Scripts\python.exe -m pytest tests -q
```

Corren el código de producción contra el Graph simulado: creación de reuniones (idempotente), asistencia con
paginación, cámara por streams, espera cuando Teams no publicó el reporte, robot en Edge sin ventana, correo,
tarjeta de Teams, protección del panel y de la pantalla del aula, validación del token SSO de Teams y paquete
de la app.

## Pasar a producción

1. **Servidor con HTTPS.** Teams sólo carga pestañas por https (y el GPS del celular también lo necesita).
   Publicar detrás de IIS/nginx/Caddy con certificado y poner `BASE_URL=https://<dominio>`.
   Para probar antes desde una PC: `devtunnel host -p 8000 --allow-anonymous` y usar la URL que da.
2. **Entra ID → Registro de aplicación** (una sola app para todo):
   - *Permisos de aplicación* con consentimiento de admin: `Calendars.ReadWrite`, `OnlineMeetings.Read.All`,
     `OnlineMeetingArtifact.Read.All`, `CallRecords.Read.All`, `User.Read.All`, `Mail.Send`.
   - *Permisos delegados*: `openid`, `profile`, `email`, `User.Read`.
   - *Autenticación → Web*: redirect URI `https://<dominio>/auth/callback`.
   - *Exponer una API*: URI `api://<dominio>/<MS_CLIENT_ID>`, ámbito `access_as_user`, y autorizar a los
     clientes de Teams `1fec8e78-bce4-4aaf-ab1b-5451cc387264` (escritorio/móvil) y
     `5e3ce6c0-2b1f-4285-8d4b-75ee78787346` (web). Esto es el inicio de sesión único de las pestañas.
   - Completar `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET` en `.env`.
3. **Teams (PowerShell de administración):**
   ```powershell
   # Que la app pueda leer las reuniones y reportes del profesor
   New-CsApplicationAccessPolicy -Identity AutoPresente -AppIds "<MS_CLIENT_ID>"
   Grant-CsApplicationAccessPolicy -PolicyName AutoPresente -Identity "profesor@cervantes.edu.ar"
   # Que las reuniones generen el informe de asistencia (suele venir activado)
   Set-CsTeamsMeetingPolicy -Identity Global -AllowEngagementReport Enabled
   ```
4. **Reuniones del cuatrimestre:** completar `cuatrimestre` en `config/cursos.json` y correr una vez
   `python -m app.crear_reuniones --simular` (muestra qué haría) y después `python -m app.crear_reuniones`.
5. **Aviso en Teams:** en Teams, *Workflows* → plantilla *Enviar al chat cuando se recibe una solicitud de
   webhook de Teams* → copiar la URL en `TEAMS_WEBHOOK_URL`.
6. **App de Teams:** `python scripts\empaquetar_teams.py` genera `dist/AutoPresente-teams.zip`. Subirlo en el
   Centro de administración de Teams (o *Aplicaciones → Cargar una aplicación* para probar). Después, en cada
   reunión de la materia: *+ Aplicaciones → AutoPresente* y elegir la materia.
7. **Cervantes:** `CERVANTES_URL/USUARIO/CLAVE`, `RPA_HEADLESS=1`, y ajustar `config/cervantes_selectores.json`
   inspeccionando el portal real (el robot no cambia).
8. **Pantallas de aula:** `KIOSCO_CLAVE` en `.env` y `scripts/kiosco_aula.ps1` al inicio de cada PC de aula,
   con la URL `https://<dominio>/aula/<AULA>?k=<clave>`.
9. **Servicio:** correr `iniciar.ps1` como servicio de Windows (p. ej. con NSSM). `DEMO=0`.

## Estructura

```
app/
  main.py             rutas: panel, pantalla del aula, check-in, app de Teams, API
  auth.py             sesión de docentes, clave de kiosco, validación del token SSO de Teams
  orquestador.py      reloj + máquina de estados de cada clase (reintentos y espera de Teams)
  qr.py               tokens HMAC que rotan cada 20 s
  graph.py            cliente de Microsoft Graph (reuniones, asistencia, cámara, correo)
  teams.py            asistencia virtual: vincula reuniones y cruza el reporte con el padrón
  crear_reuniones.py  crea las reuniones semanales de Teams del cuatrimestre
  aviso_teams.py      tarjeta (Adaptive Card) con el resumen o el error, por Workflows
  consolidar.py       presencial + virtual + padrón → registros + CSV
  rpa_cervantes.py    robot Playwright
  reporte.py          correo HTML (Graph → SMTP → data/outbox)
  graph_simulado.py   Microsoft 365 falso para pruebas (GRAPH_SIMULADO=1)
  mock_cervantes.py   portal Cervantes simulado
scripts/              empaquetar_teams.py, probar_teams.ps1, kiosco_aula.ps1
templates/, static/   pantallas (incluye las pestañas de Teams y el tema oscuro)
tests/                pruebas de punta a punta
config/               cursos, horarios, aulas, cuatrimestre, reglas, selectores del portal
docs/                 demo estática para GitHub Pages
```

## Lo que falta probar en la facultad

Todo lo de Microsoft 365 está programado según la documentación oficial y probado contra el Graph simulado,
pero no contra el tenant real de la institución: permisos, Application Access Policy, SSO de las pestañas y
demora real de los `callRecords`. El primer paso en la facultad es una reunión de prueba de 10 minutos con
tres personas (una con cámara todo el tiempo, otra la mitad, otra nunca) y comparar con un cronómetro.
