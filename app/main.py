"""Servidor web: pantalla del aula (QR), check-in del alumno, panel y portal simulado."""
from __future__ import annotations

import ipaddress
import json
import math
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import config, db, mock_cervantes, orquestador, padron, qr

ESTADOS = ["en_curso", "esperando_teams", "cargando_cervantes", "enviando_reporte", "completada"]


@asynccontextmanager
async def lifespan(_app):
    db.init()
    padron.cargar()
    orquestador.iniciar()
    db.log(f"Sistema iniciado en modo {'DEMO' if config.DEMO else 'PRODUCCIÓN'}")
    yield
    orquestador.detener()


app = FastAPI(title="AutoPresente", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, same_site="lax", max_age=60 * 60 * 24 * 120)
app.mount("/static", StaticFiles(directory=config.RAIZ / "static"), name="static")
app.include_router(mock_cervantes.router)
T = Jinja2Templates(directory=config.RAIZ / "templates")
T.env.globals.update(DEMO=config.DEMO, PROFESOR=config.PROFESOR)


def _ctx(request: Request, **kw):
    return {"request": request, **kw}


def _clase_aula(aula_id: str) -> dict | None:
    """Clase activa del aula (o la próxima dentro de la tolerancia)."""
    cursos = [c["id"] for c in config.CURSOS.values() if c["aula"] == aula_id]
    if not cursos:
        return None
    marcas = ",".join("?" * len(cursos))
    return db.q1(f"SELECT * FROM clases WHERE curso_id IN ({marcas}) AND estado='en_curso' ORDER BY inicio DESC LIMIT 1",
                 tuple(cursos))


def _distancia_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ─── Panel ────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def panel(request: Request):
    return T.TemplateResponse(request, "panel.html", _ctx(request, cursos=config.CURSOS, aulas=config.AULAS))


@app.get("/api/estado")
def api_estado():
    clases = db.q("SELECT * FROM clases ORDER BY inicio DESC LIMIT 20")
    for c in clases:
        curso = config.CURSOS[c["curso_id"]]
        c["materia"], c["division"], c["aula"] = curso["materia"], curso["division"], curso["aula"]
        c["resumen"] = json.loads(c["resumen"]) if c["resumen"] else None
        c["checkins"] = db.q1("SELECT COUNT(*) n FROM checkins WHERE clase_id=?", (c["id"],))["n"]
        c["paso"] = ESTADOS.index(c["estado"]) if c["estado"] in ESTADOS else -1
    hoy = db.ahora().date().isoformat()
    tot = db.q1("""SELECT SUM(r.estado='Presente' AND r.modalidad='Presencial') p,
                   SUM(r.estado='Presente' AND r.modalidad='Virtual') v, SUM(r.estado='Ausente') a FROM registros r JOIN clases c ON c.id=r.clase_id WHERE c.fecha=?""", (hoy,))
    return {
        "clases": clases,
        "eventos": db.q("SELECT * FROM eventos ORDER BY id DESC LIMIT 40"),
        "hoy": {"presenciales": tot["p"] or 0, "virtuales": tot["v"] or 0, "ausentes": tot["a"] or 0,
                "completadas": sum(c["estado"] == "completada" and c["fecha"] == hoy for c in clases)},
        "ahora": db.ahora().strftime("%H:%M:%S"),
    }


@app.get("/clase/{clase_id}", response_class=HTMLResponse)
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
        tiene_mail=(config.OUTBOX / f"clase_{clase_id}.html").exists()))


@app.get("/correo/{clase_id}")
def ver_correo(clase_id: int):
    archivo = config.OUTBOX / f"clase_{clase_id}.html"
    if not archivo.exists():
        raise HTTPException(404)
    return FileResponse(archivo)


@app.get("/export/{clase_id}")
def exportar(clase_id: int):
    archivos = list(config.EXPORTS.glob(f"*_{clase_id}.csv"))
    if not archivos:
        raise HTTPException(404)
    return FileResponse(archivos[0], filename=archivos[0].name, media_type="text/csv")


# ─── Pantalla del aula (kiosco) ───────────────────────────────────────
@app.get("/aula/{aula_id}", response_class=HTMLResponse)
def aula(request: Request, aula_id: str):
    if aula_id not in config.AULAS:
        raise HTTPException(404)
    return T.TemplateResponse(request, "aula.html", _ctx(request, aula_id=aula_id, aula=config.AULAS[aula_id]))


@app.get("/api/aula/{aula_id}")
def api_aula(aula_id: str):
    clase = _clase_aula(aula_id)
    if not clase:
        return {"activa": False, "hora": db.ahora().strftime("%H:%M")}
    curso = config.CURSOS[clase["curso_id"]]
    token, restante = qr.token_actual(clase["id"])
    url = f"{config.BASE_URL}/c/{token}"
    ultimos = db.q("""SELECT a.nombre FROM checkins c JOIN alumnos a ON a.matricula=c.matricula AND a.curso_id=?
                      WHERE c.clase_id=? ORDER BY c.hora DESC LIMIT 6""", (clase["curso_id"], clase["id"]))
    total = db.q1("SELECT COUNT(*) n FROM alumnos WHERE curso_id=?", (clase["curso_id"],))["n"]
    return {
        "activa": True, "materia": curso["materia"], "division": curso["division"],
        "horario": f"{clase['inicio'][11:16]} – {clase['fin'][11:16]}",
        "svg": qr.svg(url), "url": url, "restante": restante, "rotacion": qr.VENTANA,
        "presentes": db.q1("SELECT COUNT(*) n FROM checkins WHERE clase_id=?", (clase["id"],))["n"],
        "total": total, "ultimos": [u["nombre"].split(", ")[-1] + " " + u["nombre"].split(",")[0] for u in ultimos],
        "hora": db.ahora().strftime("%H:%M"),
    }


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


@app.get("/login")
def login(request: Request):
    if config.DEMO or not config.GRAPH_CONFIGURADO:
        pend = request.session.get("pendiente") or {}
        clase = db.q1("SELECT curso_id FROM clases WHERE id=?", (pend.get("clase_id", 0),))
        alumnos = db.q("SELECT * FROM alumnos WHERE curso_id=? ORDER BY nombre", (clase["curso_id"],)) if clase else []
        return T.TemplateResponse(request, "login_demo.html", _ctx(request, alumnos=alumnos))
    import msal
    app_ms = msal.ConfidentialClientApplication(
        config.MS_CLIENT_ID, authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}",
        client_credential=config.MS_CLIENT_SECRET)
    flujo = app_ms.initiate_auth_code_flow(["User.Read"], redirect_uri=f"{config.BASE_URL}/auth/callback")
    request.session["flujo"] = flujo
    return RedirectResponse(flujo["auth_uri"], status_code=303)


@app.post("/login/demo")
async def login_demo(request: Request):
    if not (config.DEMO or not config.GRAPH_CONFIGURADO):
        raise HTTPException(403)
    form = await request.form()
    request.session["alumno"] = {"email": form["email"], "nombre": form.get("nombre", "")}
    return RedirectResponse("/checkin", status_code=303)


@app.get("/auth/callback")
def auth_callback(request: Request):
    import msal
    app_ms = msal.ConfidentialClientApplication(
        config.MS_CLIENT_ID, authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}",
        client_credential=config.MS_CLIENT_SECRET)
    try:
        r = app_ms.acquire_token_by_auth_code_flow(request.session.pop("flujo", {}), dict(request.query_params))
    except ValueError:
        r = {}
    claims = r.get("id_token_claims") or {}
    email = (claims.get("preferred_username") or claims.get("email") or "").lower()
    if not email or (config.MS_DOMINIO_ALUMNOS and not email.endswith("@" + config.MS_DOMINIO_ALUMNOS)):
        return T.TemplateResponse(request, "resultado.html", _ctx(
            request, ok=False, titulo="Cuenta no válida", mensaje="Ingresá con tu cuenta institucional."))
    request.session["alumno"] = {"email": email, "nombre": claims.get("name", "")}
    return RedirectResponse("/checkin", status_code=303)


@app.get("/checkin", response_class=HTMLResponse)
def checkin_page(request: Request):
    pend = request.session.get("pendiente")
    if not pend or not request.session.get("alumno"):
        return RedirectResponse("/", status_code=303)
    clase = db.q1("SELECT * FROM clases WHERE id=?", (pend["clase_id"],))
    curso = config.CURSOS[clase["curso_id"]]
    return T.TemplateResponse(request, "checkin.html", _ctx(
        request, curso=curso, alumno=request.session["alumno"]))


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


# ─── Controles de demo (sólo para mostrar al jurado) ──────────────────
@app.post("/api/demo/simular")
async def demo_simular(request: Request):
    if not config.DEMO:
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
    orquestador.tick()
    return {"clase_id": clase_id, "aula": config.CURSOS[curso_id]["aula"]}


@app.post("/api/demo/finalizar/{clase_id}")
def demo_finalizar(clase_id: int):
    """Adelanta el fin de la clase para no esperar en la presentación."""
    if not config.DEMO:
        raise HTTPException(403)
    db.ejecutar("UPDATE clases SET fin=? WHERE id=? AND estado IN ('programada','en_curso')",
                (db.ahora().replace(microsecond=0).isoformat(), clase_id))
    orquestador.tick()
    return {"ok": True}
