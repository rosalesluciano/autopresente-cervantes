"""Microsoft Graph simulado para probar el circuito real sin un tenant.

Responde en /_graph/v1.0 las mismas rutas que usa app/graph.py, con la forma
de los JSON de la documentación oficial: eventos y calendarView, onlineMeetings
con filtro por JoinWebUrl, attendanceReports/attendanceRecords, callRecords con
sesiones/segmentos/streams, usuarios y sendMail. Así el código de producción
(graph.py, teams.py, crear_reuniones.py) corre tal cual.

Se activa con GRAPH_SIMULADO=1. Los datos viven en memoria.
Para imitar la demora de Teams en publicar reportes: GRAPH_SIMULADO_DEMORA (segundos).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request

from . import config, db
from .graph import mismo_link, parse_dt, utc_txt

router = APIRouter(prefix="/_graph/v1.0", include_in_schema=False)
_lock = threading.RLock()
DEMORA = timedelta(seconds=int(os.getenv("GRAPH_SIMULADO_DEMORA", "0") or 0))
DIAS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Estado en memoria
SERIES: list[dict] = []      # eventos recurrentes creados con POST /events
SUELTAS: list[dict] = []     # ocurrencias sueltas (clases simuladas desde el panel)
REPORTES: dict[str, list[dict]] = {}   # meeting id → reportes generados
LLAMADAS: list[dict] = []    # callRecords generados
CORREOS: list[dict] = []


def _id_reunion(join_url: str) -> str:
    return "MSo" + hashlib.sha1(join_url.encode()).hexdigest()[:24]


def _nuevo_link() -> str:
    hilo = uuid.uuid4().hex[:20]
    return f"https://teams.microsoft.com/l/meetup-join/19%3ameeting_{hilo}%40thread.v2/0?context=%7b%22Tid%22%3a%22simulado%22%7d"


def _usuario(id_o_upn: str) -> dict | None:
    prof = config.PROFESOR["email"].lower()
    if id_o_upn.lower() in (prof, "prof-0001"):
        return {"id": "prof-0001", "displayName": config.PROFESOR["nombre"], "mail": prof, "userPrincipalName": prof}
    a = db.q1("SELECT * FROM alumnos WHERE lower(email)=lower(?) OR 'aad-'||matricula=?", (id_o_upn, id_o_upn))
    if a:
        return {"id": f"aad-{a['matricula']}", "displayName": a["nombre"], "mail": a["email"], "userPrincipalName": a["email"]}
    return None


# ── Funciones que usa el panel en modo prueba ─────────────────────────
def preparar_reunion(curso_id: str, ini: datetime, fin: datetime):
    """Asegura una reunión de Teams para la clase simulada desde el panel."""
    curso = config.CURSOS[curso_id]
    with _lock:
        serie = next((s for s in SERIES if curso["teams_asunto"].lower() in s["subject"].lower()), None)
        link = serie["onlineMeeting"]["joinUrl"] if serie else _nuevo_link()
        SUELTAS.append({"subject": curso["teams_asunto"], "curso": curso_id, "joinUrl": link,
                        "inicio": ini.astimezone(timezone.utc), "fin": fin.astimezone(timezone.utc)})


def terminar_reunion(curso_id: str, fin: datetime):
    with _lock:
        for o in reversed(SUELTAS):
            if o["curso"] == curso_id:
                o["fin"] = fin.astimezone(timezone.utc)
                return


# ── Calendario ─────────────────────────────────────────────────────────
def _ocurrencias(desde: datetime, hasta: datetime) -> list[dict]:
    salida = [o for o in SUELTAS if o["inicio"] < hasta and o["fin"] > desde]
    for s in SERIES:
        rec = s["recurrence"]
        zona = config.TZ
        d = max(date.fromisoformat(rec["range"]["startDate"]), desde.astimezone(zona).date())
        ultimo = min(date.fromisoformat(rec["range"]["endDate"]), hasta.astimezone(zona).date())
        h_ini, h_fin = s["start"]["dateTime"][11:16], s["end"]["dateTime"][11:16]
        while d <= ultimo:
            if DIAS[d.weekday()] in rec["pattern"]["daysOfWeek"]:
                ini = datetime.fromisoformat(f"{d}T{h_ini}").replace(tzinfo=zona).astimezone(timezone.utc)
                fin = datetime.fromisoformat(f"{d}T{h_fin}").replace(tzinfo=zona).astimezone(timezone.utc)
                if ini < hasta and fin > desde:
                    salida.append({"subject": s["subject"], "joinUrl": s["onlineMeeting"]["joinUrl"], "inicio": ini,
                                   "fin": fin, "curso": s.get("_curso")})
            d += timedelta(days=1)
    return salida


@router.get("/users/{usuario}/calendarView")
def calendar_view(usuario: str, startDateTime: str, endDateTime: str):
    desde, hasta = parse_dt(startDateTime), parse_dt(endDateTime)
    with _lock:
        ocs = _ocurrencias(desde, hasta)
    return {"value": [{
        "id": uuid.uuid4().hex, "subject": o["subject"], "isOnlineMeeting": True, "isCancelled": False,
        "start": {"dateTime": o["inicio"].strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
        "end": {"dateTime": o["fin"].strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
        "onlineMeeting": {"joinUrl": o["joinUrl"]},
    } for o in ocs]}


@router.get("/users/{usuario}/events")
def listar_eventos(usuario: str, request: Request):
    filtro = request.query_params.get("$filter", "")
    with _lock:
        return {"value": [s for s in SERIES if f"subject eq '{s['subject']}'" in filtro.replace("''", "'")]}


@router.post("/users/{usuario}/events", status_code=201)
async def crear_evento(usuario: str, request: Request):
    ev = await request.json()
    if not ev.get("isOnlineMeeting"):
        raise HTTPException(400, "Se esperaba isOnlineMeeting=true")
    ev = {**ev, "id": uuid.uuid4().hex, "type": "seriesMaster", "onlineMeeting": {"joinUrl": _nuevo_link()}}
    for c in config.CURSOS.values():
        if c["teams_asunto"].lower() in ev["subject"].lower():
            ev["_curso"] = c["id"]
    with _lock:
        SERIES.append(ev)
    return ev


@router.get("/users/{usuario}")
def usuario_get(usuario: str):
    u = _usuario(usuario)
    if not u:
        raise HTTPException(404, json.dumps({"error": {"code": "Request_ResourceNotFound"}}))
    return u


# ── Reuniones y asistencia ────────────────────────────────────────────
@router.get("/users/{usuario}/onlineMeetings")
def online_meetings(usuario: str, request: Request):
    filtro = request.query_params.get("$filter", "")
    if not filtro.startswith("JoinWebUrl eq '"):
        raise HTTPException(400, "Filtro no soportado")
    link = filtro[len("JoinWebUrl eq '"):-1].replace("''", "'")
    with _lock:
        conocidas = {o["joinUrl"] for o in SUELTAS} | {s["onlineMeeting"]["joinUrl"] for s in SERIES}
    if not any(mismo_link(link, k) for k in conocidas):
        return {"value": []}
    return {"value": [{"id": _id_reunion(next(k for k in conocidas if mismo_link(link, k))), "joinWebUrl": link}]}


def _generar(meeting_id: str):
    """Crea, para cada ocurrencia ya terminada, su reporte de asistencia y su callRecord."""
    ahora = datetime.now(timezone.utc)
    ocs = [o for o in _ocurrencias(ahora - timedelta(days=7), ahora) if _id_reunion(o["joinUrl"]) == meeting_id]
    reportes = REPORTES.setdefault(meeting_id, [])
    for o in ocs:
        if o["fin"] + DEMORA > ahora or any(r["_inicio"] == o["inicio"] for r in reportes):
            continue
        curso = o.get("curso")
        clase = db.q1("SELECT id FROM clases WHERE curso_id=? AND inicio<=? ORDER BY inicio DESC LIMIT 1",
                      (curso, o["fin"].astimezone(config.TZ).isoformat())) if curso else None
        presentes = {r["matricula"] for r in db.q("SELECT matricula FROM checkins WHERE clase_id=?", (clase["id"],))} \
            if clase else set()
        alumnos = db.q("SELECT * FROM alumnos WHERE curso_id=?", (curso,)) if curso else []
        rnd = random.Random(f"{meeting_id}{o['inicio']}")
        dur = (o["fin"] - o["inicio"]).total_seconds()
        participantes = [{"id": "prof-0001", "mail": config.PROFESOR["email"], "nombre": config.PROFESOR["nombre"],
                          "rol": "Organizer", "seg": dur, "cam": dur}]
        for a in alumnos:
            if a["matricula"] in presentes or rnd.random() < 0.4:
                continue
            seg = dur * (rnd.uniform(0.1, 0.4) if rnd.random() < 0.2 else rnd.uniform(0.75, 1.0))
            cam = seg * rnd.choice([rnd.uniform(0.8, 1.0), rnd.uniform(0.3, 0.8), rnd.uniform(0.0, 0.2)])
            participantes.append({"id": f"aad-{a['matricula']}", "mail": a["email"], "nombre": a["nombre"],
                                  "rol": "Attendee", "seg": seg, "cam": cam})
        participantes.append({"id": None, "mail": "", "nombre": "Invitado externo", "rol": "Attendee",
                              "seg": dur * 0.5, "cam": 0})
        rid = uuid.uuid4().hex
        registros = []
        for p in participantes:
            entrada = o["inicio"] + timedelta(seconds=rnd.uniform(0, max(0.0, dur - p["seg"])))
            registros.append({
                "id": uuid.uuid4().hex, "emailAddress": p["mail"] or None, "role": p["rol"],
                "totalAttendanceInSeconds": int(p["seg"]),
                "identity": {"id": p["id"], "displayName": p["nombre"]},
                "attendanceIntervals": [{"joinDateTime": utc_txt(entrada),
                                         "leaveDateTime": utc_txt(entrada + timedelta(seconds=p["seg"])),
                                         "durationInSeconds": int(p["seg"])}],
                "_entrada": entrada,
            })
        reportes.append({"id": rid, "_inicio": o["inicio"], "meetingStartDateTime": utc_txt(o["inicio"]),
                         "meetingEndDateTime": utc_txt(o["fin"]), "totalParticipantCount": len(registros),
                         "_registros": registros})
        sesiones = []
        for r in registros:
            if not r["identity"]["id"]:
                continue
            ent = r["_entrada"]
            sal = ent + timedelta(seconds=r["totalAttendanceInSeconds"])
            cam = next(p["cam"] for p in participantes if p["id"] == r["identity"]["id"])
            sesiones.append({
                "id": uuid.uuid4().hex, "modalities": ["audio", "video"],
                "startDateTime": utc_txt(ent), "endDateTime": utc_txt(sal),
                "caller": {"@odata.type": "#microsoft.graph.callRecords.participantEndpoint",
                           "identity": {"user": {"id": r["identity"]["id"], "displayName": r["identity"]["displayName"]}}},
                "callee": {"@odata.type": "#microsoft.graph.callRecords.serviceEndpoint"},
                "segments": [{
                    "id": uuid.uuid4().hex, "startDateTime": utc_txt(ent), "endDateTime": utc_txt(sal),
                    "caller": {"identity": {"user": {"id": r["identity"]["id"]}}},
                    "callee": {"identity": {}},
                    "media": [
                        {"label": "main-audio", "streams": [{"streamDirection": "callerToCallee"},
                                                             {"streamDirection": "calleeToCaller"}]},
                        {"label": "main-video", "streams": [
                            {"streamDirection": "callerToCallee", "startDateTime": utc_txt(ent),
                             "endDateTime": utc_txt(ent + timedelta(seconds=cam))},
                            {"streamDirection": "calleeToCaller"}]},
                    ],
                }],
            })
        LLAMADAS.append({"id": uuid.uuid4().hex, "joinWebUrl": quote(o["joinUrl"], safe=":/?=&"),
                         "startDateTime": utc_txt(o["inicio"]), "endDateTime": utc_txt(o["fin"]),
                         "type": "groupCall", "_sesiones": sesiones})


def _limpio(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


@router.get("/users/{usuario}/onlineMeetings/{meeting_id}/attendanceReports")
def attendance_reports(usuario: str, meeting_id: str):
    with _lock:
        _generar(meeting_id)
        return {"value": [_limpio(r) for r in REPORTES.get(meeting_id, [])]}


@router.get("/users/{usuario}/onlineMeetings/{meeting_id}/attendanceReports/{reporte}/attendanceRecords")
def attendance_records(usuario: str, meeting_id: str, reporte: str, request: Request):
    with _lock:
        r = next((r for r in REPORTES.get(meeting_id, []) if r["id"] == reporte), None)
    if not r:
        raise HTTPException(404)
    # Paginado de a 10, como Graph, para ejercitar @odata.nextLink.
    desde = int(request.query_params.get("$skiptoken", "0") or 0)
    pagina = {"value": [_limpio(x) for x in r["_registros"][desde:desde + 10]]}
    if desde + 10 < len(r["_registros"]):
        pagina["@odata.nextLink"] = str(request.url.include_query_params(**{"$skiptoken": desde + 10}))
    return pagina


@router.get("/communications/callRecords")
def call_records(request: Request):
    filtro = request.query_params.get("$filter", "")
    partes = filtro.split()
    desde = parse_dt(partes[partes.index("ge") + 1]) if "ge" in partes else datetime.min.replace(tzinfo=timezone.utc)
    hasta = parse_dt(partes[partes.index("lt") + 1]) if "lt" in partes else datetime.max.replace(tzinfo=timezone.utc)
    with _lock:
        return {"value": [_limpio(c) for c in LLAMADAS if desde <= parse_dt(c["startDateTime"]) < hasta]}


@router.get("/communications/callRecords/{rec_id}/sessions")
def sesiones(rec_id: str):
    with _lock:
        rec = next((c for c in LLAMADAS if c["id"] == rec_id), None)
    if not rec:
        raise HTTPException(404)
    s = rec["_sesiones"]
    return {"value": s}


WEBHOOK: list[dict] = []


@router.post("/_webhook", status_code=202)
async def webhook_teams(request: Request):
    """Imita el disparador de Workflows de Teams: recibe la Adaptive Card."""
    mensaje = await request.json()
    if mensaje.get("type") != "message" or not mensaje.get("attachments"):
        raise HTTPException(400, "Formato de mensaje de Teams inválido")
    with _lock:
        WEBHOOK.append(mensaje)
    return None


@router.post("/users/{usuario}/sendMail", status_code=202)
async def send_mail(usuario: str, request: Request):
    cuerpo = await request.json()
    with _lock:
        CORREOS.append(cuerpo)
    return None
