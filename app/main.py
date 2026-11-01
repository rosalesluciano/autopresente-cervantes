"""Servidor web: pantalla del aula (QR), check-in del alumno, panel, app de Teams y portal simulado."""
from __future__ import annotations

import ipaddress
import json
import math
import random
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, db, graph_simulado, mock_cervantes, orquestador, padron, qr
from .auth import NoAutorizado, requiere_docente, requiere_kiosco

ESTADOS = ["en_curso", "esperando_teams", "cargando_cervantes", "enviando_reporte", "completada"]

# Dónde se puede mostrar el sistema dentro de un iframe: Teams (web, escritorio, móvil) y Microsoft 365.
FRAME_ANCESTORS = ("frame-ancestors 'self' https://teams.microsoft.com https://*.teams.microsoft.com "
                   "https://*.teams.cloud.microsoft https://*.cloud.microsoft https://*.office.com "
                   "https://*.microsoft365.com https://*.skype.com")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    padron.cargar()
    if config.ORQUESTADOR:
        orquestador.iniciar()
    modo = "DEMO" if config.DEMO else ("PRUEBA con Graph simulado" if config.GRAPH_SIMULADO else "PRODUCCIÓN")
    db.log(f"Sistema iniciado en modo {modo}")
    yield
    orquestador.detener()


app = FastAPI(title="AutoPresente", lifespan=lifespan)
# Dentro de Teams la página corre en un iframe de otro dominio: la cookie necesita SameSite=None (y HTTPS).
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=60 * 60 * 24 * 120,
                   same_site="none" if config.HTTPS else "lax", https_only=config.HTTPS)
app.mount("/static", StaticFiles(directory=config.RAIZ / "static"), name="static")
if config.CERVANTES_URL.startswith(config.BASE_URL):  # portal simulado sólo si el robot apunta a él
    app.include_router(mock_cervantes.router)
if config.GRAPH_SIMULADO:
    app.include_router(graph_simulado.router)
T = Jinja2Templates(directory=config.RAIZ / "templates")
T.env.globals.update(DEMO=config.DEMO, PRUEBAS=config.PRUEBAS, PROFESOR=config.PROFESOR, BASE_URL=config.BASE_URL,
                     SSO=bool(config.MS_CLIENT_ID) and not config.PRUEBAS,
                     PORTAL_SIMULADO=config.CERVANTES_URL.startswith(config.BASE_URL))


@app.middleware("http")
async def cabeceras(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = FRAME_ANCESTORS
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.exception_handler(NoAutorizado)
async def no_autorizado(request: Request, _exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Iniciá sesión con tu cuenta de docente."}, 401)
    return RedirectResponse(f"/ingresar?despues={quote(request.url.path)}", status_code=303)


def _ctx(request: Request, **kw):
    return {"request": request, "usuario": auth.usuario(request), **kw}


def _clase_activa(cursos: list[str]) -> dict | None:
    if not cursos:
        return None
    marcas = ",".join("?" * len(cursos))
    return db.q1(f"SELECT * FROM clases WHERE curso_id IN ({marcas}) AND estado='en_curso' ORDER BY inicio DESC LIMIT 1",
                 tuple(cursos))


def _en_vivo(clase: dict | None) -> dict:
    """Estado de una clase en curso para la pantalla del aula y la pestaña de Teams."""
    if not clase:
        return {"activa": False, "hora": db.ahora().strftime("%H:%M")}
    curso = config.CURSOS[clase["curso_id"]]
    token, restante = qr.token_actual(clase["id"])
    url = f"{config.BASE_URL}/c/{token}"
    ultimos = db.q("""SELECT a.nombre FROM checkins c JOIN alumnos a ON a.matricula=c.matricula AND a.curso_id=?
                      WHERE c.clase_id=? ORDER BY c.hora DESC LIMIT 6""", (clase["curso_id"], clase["id"]))
    total = db.q1("SELECT COUNT(*) n FROM alumnos WHERE curso_id=?", (clase["curso_id"],))["n"]
    return {
        "activa": True, "clase_id": clase["id"], "materia": curso["materia"], "division": curso["division"],
        "aula": curso["aula"], "horario": f"{clase['inicio'][11:16]} – {clase['fin'][11:16]}",
        "svg": qr.svg(url), "url": url, "restante": restante, "rotacion": qr.VENTANA,
        "presentes": db.q1("SELECT COUNT(*) n FROM checkins WHERE clase_id=?", (clase["id"],))["n"],
        "total": total, "ultimos": [u["nombre"].split(", ")[-1] + " " + u["nombre"].split(",")[0] for u in ultimos],
        "hora": db.ahora().strftime("%H:%M"),
    }


def _distancia_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ─── Panel (docentes) ─────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(requiere_docente)])
def panel(request: Request):
    return T.TemplateResponse(request, "panel.html", _ctx(request, cursos=config.CURSOS, aulas=config.AULAS))


@app.get("/api/estado", dependencies=[Depends(requiere_docente)])
def api_estado():
    clases = db.q("SELECT * FROM clases ORDER BY inicio DESC LIMIT 20")
    for c in clases:
        curso = config.CURSOS[c["curso_id"]]
        c["materia"], c["division"], c["aula"] = curso["materia"], curso["division"], curso["aula"]
        c["resumen"] = json.loads(c["resumen"]) if c["resumen"] else None
        c["checkins"] = db.q1("SELECT COUNT(*) n FROM checkins WHERE clase_id=?", (c["id"],))["n"]
        c["paso"] = ESTADOS.index(c["estado"]) if c["estado"] in ESTADOS else -1
        c["teams"] = bool(c["teams_url"])
        c.pop("teams_url", None)
    hoy = db.ahora().date().isoformat()
    tot = db.q1("""SELECT SUM(r.estado='Presente' AND r.modalidad='Presencial') p,
                   SUM(r.estado='Presente' AND r.modalidad='Virtual') v, SUM(r.estado='Ausente') a
                   FROM registros r JOIN clases c ON c.id=r.clase_id WHERE c.fecha=?""", (hoy,))
    return {
        "clases": clases,
        "eventos": db.q("SELECT * FROM eventos ORDER BY id DESC LIMIT 40"),
        "hoy": {"presenciales": tot["p"] or 0, "virtuales": tot["v"] or 0, "ausentes": tot["a"] or 0,
                "completadas": sum(c["estado"] == "completada" and c["fecha"] == hoy for c in clases)},
        "ahora": db.ahora().strftime("%H:%M:%S"),
    }


@app.get("/clase/{clase_id}", response_class=HTMLResponse, dependencies=[Depends(requiere_docente)])
def detalle_clase(request: Request, clase_id: int):
    clase = db.q1("SELECT * FROM clases WHERE id=?", (clase_id,))
    if not clase:
        raise HTTPException(404)
    curso = config.CURSOS[clase["curso_id"]]
    registros = db.q("SELECT * FROM registros WHERE clase_id=? ORDER BY estado DESC, modalidad, nombre", (clase_id,))
    eventos = db.q("SELECT * FROM eventos WHERE clase_id=? ORDER BY id", (clase_id,))
    resumen = json.loads(clase["resumen"]) if clase["resumen"] else None
    return T.TemplateResponse(request, "clase.html", _ctx(
        request, clase=clase, curso=curso, registros=registros, eventos=eventos, resumen=resumen,
        alerta=config.REGLAS["alerta_camara_por_debajo_de"],
        tiene_mail=(config.OUTBOX / f"clase_{clase_id}.html").exists(),
        tiene_tarjeta=(config.OUTBOX / f"teams_resumen_{clase_id}.json").exists()))


@app.get("/correo/{clase_id}", dependencies=[Depends(requiere_docente)])
def ver_correo(clase_id: int):
    archivo = config.OUTBOX / f"clase_{clase_id}.html"
    if not archivo.exists():
        raise HTTPException(404)
    return FileResponse(archivo)


@app.get("/tarjeta-teams/{clase_id}", response_class=HTMLResponse, dependencies=[Depends(requiere_docente)])
def ver_tarjeta(request: Request, clase_id: int):
    archivo = config.OUTBOX / f"teams_resumen_{clase_id}.json"
    if not archivo.exists():
        raise HTTPException(404)
    tarjeta = json.loads(archivo.read_text(encoding="utf-8"))["attachments"][0]["content"]
    return T.TemplateResponse(request, "tarjeta_teams.html", _ctx(request, tarjeta=tarjeta, clase_id=clase_id))


@app.get("/export/{clase_id}", dependencies=[Depends(requiere_docente)])
def exportar(clase_id: int):
    archivos = list(config.EXPORTS.glob(f"*_{clase_id}.csv"))
    if not archivos:
        raise HTTPException(404)
    return FileResponse(archivos[0], filename=archivos[0].name, media_type="text/csv")


# ─── Pantalla del aula (kiosco) ───────────────────────────────────────
@app.get("/aula/{aula_id}", response_class=HTMLResponse, dependencies=[Depends(requiere_kiosco)])
def aula(request: Request, aula_id: str):
    if aula_id not in config.AULAS:
        raise HTTPException(404)
    return T.TemplateResponse(request, "aula.html", _ctx(request, aula_id=aula_id, aula=config.AULAS[aula_id]))


@app.get("/api/aula/{aula_id}", dependencies=[Depends(requiere_kiosco)])
def api_aula(aula_id: str):
    return _en_vivo(_clase_activa([c["id"] for c in config.CURSOS.values() if c["aula"] == aula_id]))


# ─── Ingreso (docentes y alumnos) ─────────────────────────────────────
def _msal():
    import msal

    return msal.ConfidentialClientApplication(
        config.MS_CLIENT_ID, authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}",
        client_credential=config.MS_CLIENT_SECRET)


@app.get("/ingresar")
def ingresar(request: Request, despues: str = "/"):
    if not despues.startswith("/") or despues.startswith("//"):
        despues = "/"
    request.session["despues"] = despues
    if config.LOGIN_MICROSOFT:
        flujo = _msal().initiate_auth_code_flow(["User.Read"], redirect_uri=f"{config.BASE_URL}/auth/callback")
        request.session["flujo"] = flujo
        return RedirectResponse(flujo["auth_uri"], status_code=303)
    return T.TemplateResponse(request, "ingresar_prueba.html", _ctx(request, despues=despues))


@app.post("/ingresar/prueba")
async def ingresar_prueba(request: Request):
    """Sólo sin Microsoft 365 real (demo o Graph simulado): entrar escribiendo el correo."""
    if config.LOGIN_MICROSOFT:
        raise HTTPException(403)
    form = await request.form()
    auth.iniciar_sesion(request, str(form.get("email", "")), "")
    return RedirectResponse(request.session.pop("despues", "/"), status_code=303)


@app.get("/salir")
def salir(request: Request):
    request.session.clear()
    return RedirectResponse("/ingresar", status_code=303)


@app.get("/login")
def login(request: Request):
    """Ingreso del alumno después de escanear el QR."""
    if not config.LOGIN_MICROSOFT:
        pend = request.session.get("pendiente") or {}
        clase = db.q1("SELECT curso_id FROM clases WHERE id=?", (pend.get("clase_id", 0),))
        alumnos = db.q("SELECT * FROM alumnos WHERE curso_id=? ORDER BY nombre", (clase["curso_id"],)) if clase else []
        return T.TemplateResponse(request, "login_demo.html", _ctx(request, alumnos=alumnos))
    request.session["despues"] = "/checkin"
    flujo = _msal().initiate_auth_code_flow(["User.Read"], redirect_uri=f"{config.BASE_URL}/auth/callback")
    request.session["flujo"] = flujo
    return RedirectResponse(flujo["auth_uri"], status_code=303)


@app.post("/login/demo")
async def login_demo(request: Request):
    if config.LOGIN_MICROSOFT:
        raise HTTPException(403)
    form = await request.form()
    request.session["alumno"] = {"email": form["email"], "nombre": form.get("nombre", "")}
    return RedirectResponse("/checkin", status_code=303)


@app.get("/auth/callback")
def auth_callback(request: Request):
    if not config.LOGIN_MICROSOFT:
        raise HTTPException(404)
    try:
        r = _msal().acquire_token_by_auth_code_flow(request.session.pop("flujo", {}), dict(request.query_params))
    except ValueError:
        r = {}
    claims = r.get("id_token_claims") or {}
    email = (claims.get("preferred_username") or claims.get("email") or "").lower()
    if not email or claims.get("tid") != config.MS_TENANT_ID:
        return T.TemplateResponse(request, "resultado.html", _ctx(
            request, ok=False, titulo="Cuenta no válida", mensaje="Ingresá con tu cuenta institucional."))
    auth.iniciar_sesion(request, email, claims.get("name", ""))
    request.session["alumno"] = {"email": email, "nombre": claims.get("name", "")}
    return RedirectResponse(request.session.pop("despues", "/checkin"), status_code=303)


# ─── App de Teams ─────────────────────────────────────────────────────
@app.post("/teams/sso")
async def teams_sso(request: Request):
    """Recibe el token de authentication.getAuthToken() de Teams y abre la sesión."""
    if config.PRUEBAS:
        auth.iniciar_sesion(request, config.PROFESOR["email"], config.PROFESOR["nombre"])
        return {"ok": True, "docente": True, "nombre": config.PROFESOR["nombre"]}
    datos = await request.json()
    try:
        claims = auth.validar_token_teams(str(datos.get("token", "")))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — token vencido, firma inválida, audiencia distinta
        raise HTTPException(401, f"Token de Teams no válido: {e}") from e
    email = (claims.get("preferred_username") or claims.get("upn") or claims.get("email") or "").lower()
    auth.iniciar_sesion(request, email, claims.get("name", ""))
    return {"ok": True, "docente": auth.es_docente(email), "nombre": claims.get("name", "")}


@app.get("/teams/panel", response_class=HTMLResponse)
def teams_panel(request: Request):
    """Pestaña personal: el mismo panel, adaptado a Teams (tema, sin barra propia, SSO)."""
    return T.TemplateResponse(request, "panel.html", _ctx(request, cursos=config.CURSOS, aulas=config.AULAS, teams=True))


@app.get("/teams/config", response_class=HTMLResponse)
def teams_config(request: Request):
    return T.TemplateResponse(request, "teams_config.html", _ctx(request, cursos=config.CURSOS, teams=True))


@app.get("/teams/clase", response_class=HTMLResponse)
def teams_clase(request: Request, curso: str = ""):
    if curso not in config.CURSOS:
        raise HTTPException(404, "Curso desconocido")
    return T.TemplateResponse(request, "teams_clase.html", _ctx(request, curso=config.CURSOS[curso], teams=True))


@app.get("/api/curso/{curso_id}/en-vivo")
def api_curso_en_vivo(request: Request, curso_id: str):
    """Para la pestaña de la reunión: cualquier persona de la institución con sesión (docente o alumno)."""
    if curso_id not in config.CURSOS:
        raise HTTPException(404)
    if not config.PRUEBAS and not auth.usuario(request):
        raise NoAutorizado()
    datos = _en_vivo(_clase_activa([curso_id]))
    u = auth.usuario(request) or {}
    datos["docente"] = config.PRUEBAS or bool(u.get("docente"))
    ultima = db.q1("SELECT * FROM clases WHERE curso_id=? AND estado='completada' ORDER BY inicio DESC LIMIT 1",
                   (curso_id,))
    if ultima and datos["docente"]:
        datos["ultima"] = {"id": ultima["id"], "fecha": ultima["fecha"], "resumen": json.loads(ultima["resumen"] or "{}")}
    return datos


@app.get("/privacidad", response_class=HTMLResponse)
def privacidad(request: Request):
    return T.TemplateResponse(request, "legal.html", _ctx(request, pagina="privacidad"))


@app.get("/terminos", response_class=HTMLResponse)
def terminos(request: Request):
    return T.TemplateResponse(request, "legal.html", _ctx(request, pagina="terminos"))


# ─── Check-in del alumno ──────────────────────────────────────────────
@app.get("/c/{token}")
def escanear(request: Request, token: str):
    clase_id = qr.validar(token)
    if not clase_id:
        return T.TemplateResponse(request, "resultado.html", _ctx(
            request, ok=False, titulo="Código vencido",
            mensaje="Este QR ya rotó. Volvé a escanear el código que está proyectado en el aula."))
    request.session["pendiente"] = {"clase_id": clase_id, "ts": time.time()}
    if not request.session.get("alumno"):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/checkin", status_code=303)


@app.get("/checkin", response_class=HTMLResponse)
def checkin_page(request: Request):
    pend = request.session.get("pendiente")
    if not pend:
        return T.TemplateResponse(request, "resultado.html", _ctx(
            request, ok=False, titulo="Escaneá el QR del aula",
            mensaje="Para registrar tu asistencia, escaneá el código que se proyecta en el aula."))
    if not request.session.get("alumno"):
        return RedirectResponse("/login", status_code=303)
    clase = db.q1("SELECT * FROM clases WHERE id=?", (pend["clase_id"],))
    curso = config.CURSOS[clase["curso_id"]]
    return T.TemplateResponse(request, "checkin.html", _ctx(request, curso=curso, alumno=request.session["alumno"]))


@app.post("/api/checkin")
async def api_checkin(request: Request):
    pend = request.session.get("pendiente")
    alumno_s = request.session.get("alumno")
    if not pend or not alumno_s:
        return JSONResponse({"ok": False, "mensaje": "Escaneá el QR del aula nuevamente."}, 400)
    if time.time() - pend["ts"] > 300:
        return JSONResponse({"ok": False, "mensaje": "Pasaron más de 5 minutos desde que escaneaste. Volvé a escanear."}, 400)
    clase = db.q1("SELECT * FROM clases WHERE id=?", (pend["clase_id"],))
    if not clase or clase["estado"] != "en_curso":
        return JSONResponse({"ok": False, "mensaje": "La clase no está en curso."}, 400)
    alumno = db.q1("SELECT * FROM alumnos WHERE curso_id=? AND lower(email)=lower(?)", (clase["curso_id"], alumno_s["email"]))
    if not alumno:
        return JSONResponse({"ok": False, "mensaje": "Tu cuenta no figura en el padrón de esta materia."}, 403)

    datos = await request.json()
    aula = config.AULAS[config.CURSOS[clase["curso_id"]]["aula"]]
    metodo, distancia = None, None
    if datos.get("lat") is not None and datos.get("lon") is not None:
        distancia = _distancia_m(float(datos["lat"]), float(datos["lon"]), aula["lat"], aula["lon"])
        if distancia <= aula["radio_metros"] + min(float(datos.get("precision") or 0), 100):
            metodo = "gps"
    ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    if not metodo:
        try:
            if any(ipaddress.ip_address(ip) in ipaddress.ip_network(red) for red in aula["redes_wifi"]):
                metodo = "wifi"
        except ValueError:
            pass
    if not metodo:
        txt = f"Estás a {distancia:.0f} m del aula." if distancia is not None else "No pudimos verificar tu ubicación."
        return JSONResponse({"ok": False, "mensaje": f"{txt} Conectate al Wi-Fi de la facultad o activá la ubicación."}, 403)

    previo = db.q1("SELECT 1 FROM checkins WHERE clase_id=? AND matricula=?", (clase["id"], alumno["matricula"]))
    if not previo:
        db.ejecutar("INSERT INTO checkins (clase_id, matricula, hora, metodo, distancia) VALUES (?,?,?,?,?)",
                    (clase["id"], alumno["matricula"], db.ahora().isoformat(timespec="seconds"), metodo, distancia))
    request.session.pop("pendiente", None)
    return {"ok": True, "nombre": alumno["nombre"].split(", ")[-1], "hora": db.ahora().strftime("%H:%M"),
            "repetido": bool(previo), "metodo": metodo}


# ─── Controles de demo y de prueba ────────────────────────────────────
@app.post("/api/demo/simular", dependencies=[Depends(requiere_docente)])
async def demo_simular(request: Request):
    if not config.PRUEBAS:
        raise HTTPException(403)
    datos = await request.json()
    curso_id = datos.get("curso_id") or next(iter(config.CURSOS))
    minutos = float(datos.get("minutos", 3))
    escaneos = int(datos.get("escaneos", 12))
    ini = db.ahora().replace(microsecond=0)
    clase_id = orquestador.crear_clase(curso_id, ini, ini + timedelta(minutes=minutos))
    alumnos = db.q("SELECT matricula FROM alumnos WHERE curso_id=?", (curso_id,))
    for a in random.sample(alumnos, min(escaneos, len(alumnos))):
        db.ejecutar("INSERT OR IGNORE INTO checkins (clase_id, matricula, hora, metodo) VALUES (?,?,?,?)",
                    (clase_id, a["matricula"], ini.isoformat(), "wifi"))
    if config.GRAPH_SIMULADO:
        graph_simulado.preparar_reunion(curso_id, ini, ini + timedelta(minutes=minutos))
    orquestador.tick()
    return {"clase_id": clase_id, "aula": config.CURSOS[curso_id]["aula"]}


@app.post("/api/demo/finalizar/{clase_id}", dependencies=[Depends(requiere_docente)])
def demo_finalizar(clase_id: int):
    """Adelanta el fin de la clase para no esperar en la presentación."""
    if not config.PRUEBAS:
        raise HTTPException(403)
    ahora = db.ahora().replace(microsecond=0)
    clase = db.q1("SELECT * FROM clases WHERE id=?", (clase_id,))
    db.ejecutar("UPDATE clases SET fin=? WHERE id=? AND estado IN ('programada','en_curso')", (ahora.isoformat(), clase_id))
    if config.GRAPH_SIMULADO and clase:
        graph_simulado.terminar_reunion(clase["curso_id"], ahora)
    orquestador.tick()
    return {"ok": True}
