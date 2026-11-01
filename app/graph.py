"""Cliente Microsoft Graph (permisos de aplicación, sin intervención humana).

Lo que usamos de Graph:
  • events / calendarView del profesor  → crear y encontrar la reunión de Teams de cada clase
  • onlineMeetings + attendanceReports  → quién entró, a qué hora y cuánto tiempo
  • communications/callRecords         → streams de video salientes = cámara encendida
  • sendMail                           → reporte al profesor

Permisos de aplicación (consentimiento de admin): Calendars.ReadWrite,
OnlineMeetings.Read.All, OnlineMeetingArtifact.Read.All, CallRecords.Read.All,
User.Read.All, Mail.Send. Además, una Application Access Policy de Teams que
habilite a la app sobre las reuniones del profesor.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

import httpx

from . import config

GRAPH = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    pass


class GraphPendiente(GraphError):
    """Teams todavía no publicó el dato (reporte o callRecord): reintentar más tarde."""


def utc_txt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(txt: str) -> datetime:
    """Fechas de Graph: '2026-10-15T11:00:00Z', con 7 decimales o sin zona (= UTC)."""
    txt = txt.replace("Z", "+00:00")
    if "." in txt:
        base, resto = txt.split(".", 1)
        zona = resto[resto.find("+"):] if "+" in resto else (resto[resto.find("-"):] if "-" in resto else "")
        txt = base + zona
    dt = datetime.fromisoformat(txt)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def mismo_link(a: str | None, b: str | None) -> bool:
    """Los joinUrl vienen codificados distinto según el endpoint: comparar decodificados."""
    if not a or not b:
        return False
    norm = lambda u: unquote(unquote(u)).strip().rstrip("/").lower()  # noqa: E731
    return norm(a) == norm(b)


class Graph:
    def __init__(self):
        self._http = httpx.Client(timeout=30)
        self._cache_usuarios: dict[str, dict] = {}
        if config.GRAPH_SIMULADO:
            self.base, self._app = f"{config.BASE_URL}/_graph/v1.0", None
        else:
            import msal

            self.base = GRAPH
            self._app = msal.ConfidentialClientApplication(
                config.MS_CLIENT_ID,
                authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}",
                client_credential=config.MS_CLIENT_SECRET,
            )

    # ── base ──────────────────────────────────────────────────────────
    def _token(self) -> str:
        if self._app is None:
            return "simulado"
        r = self._app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in r:
            raise GraphError(f"No se pudo obtener token: {r.get('error_description')}")
        return r["access_token"]

    def _req(self, metodo: str, url: str, headers: dict | None = None, **kw) -> dict:
        if not url.startswith("http"):
            url = self.base + url
        for intento in range(5):
            h = {"Authorization": f"Bearer {self._token()}", **(headers or {})}
            resp = self._http.request(metodo, url, headers=h, **kw)
            if resp.status_code in (429, 500, 502, 503, 504):  # throttling o caída momentánea
                time.sleep(min(30, int(resp.headers.get("Retry-After", 2 ** intento))))
                continue
            if resp.status_code >= 400:
                raise GraphError(f"{metodo} {url.split('?')[0]} → {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.content else {}
        raise GraphError(f"{metodo} {url}: demasiados reintentos")

    def _todas(self, url: str, headers: dict | None = None) -> list[dict]:
        items, siguiente = [], url
        while siguiente:
            data = self._req("GET", siguiente, headers=headers)
            items += data.get("value", [])
            siguiente = data.get("@odata.nextLink")
        return items

    # ── usuarios ──────────────────────────────────────────────────────
    def usuario(self, id_o_upn: str) -> dict:
        if id_o_upn not in self._cache_usuarios:
            self._cache_usuarios[id_o_upn] = self._req(
                "GET", f"/users/{quote(id_o_upn)}?$select=id,displayName,mail,userPrincipalName"
            )
        return self._cache_usuarios[id_o_upn]

    # ── calendario: crear y encontrar reuniones ───────────────────────
    def reuniones_teams(self, profesor: str, desde: datetime, hasta: datetime) -> list[dict]:
        """Ocurrencias con reunión de Teams en el calendario del profesor (horarios en UTC)."""
        url = (
            f"/users/{quote(profesor)}/calendarView"
            f"?startDateTime={utc_txt(desde)}&endDateTime={utc_txt(hasta)}"
            "&$select=subject,start,end,isOnlineMeeting,onlineMeeting,isCancelled&$top=100"
        )
        eventos = self._todas(url, headers={"Prefer": 'outlook.timezone="UTC"'})
        return [
            {
                "asunto": e.get("subject", ""),
                "inicio": parse_dt(e["start"]["dateTime"]),
                "fin": parse_dt(e["end"]["dateTime"]),
                "join_url": (e.get("onlineMeeting") or {}).get("joinUrl"),
            }
            for e in eventos
            if e.get("isOnlineMeeting") and e.get("onlineMeeting") and not e.get("isCancelled")
        ]

    def buscar_evento_serie(self, profesor: str, asunto: str) -> dict | None:
        filtro = quote(f"subject eq '{asunto.replace(chr(39), chr(39) * 2)}' and type eq 'seriesMaster'")
        data = self._req("GET", f"/users/{quote(profesor)}/events?$filter={filtro}&$select=id,subject,onlineMeeting")
        return (data.get("value") or [None])[0]

    def crear_reunion_recurrente(self, profesor: str, asunto: str, dias: list[str], inicio: str, fin: str,
                                 desde: str, hasta: str, zona: str, invitados: list[str]) -> dict:
        """Evento semanal con reunión de Teams. Los alumnos reciben la invitación al calendario."""
        cuerpo = {
            "subject": asunto,
            "body": {"contentType": "HTML", "content":
                     "Clase registrada por AutoPresente. Entrá desde esta invitación con tu cuenta institucional: "
                     "la asistencia virtual se toma sola."},
            "start": {"dateTime": f"{desde}T{inicio}:00", "timeZone": zona},
            "end": {"dateTime": f"{desde}T{fin}:00", "timeZone": zona},
            "recurrence": {
                "pattern": {"type": "weekly", "interval": 1, "daysOfWeek": dias, "firstDayOfWeek": "monday"},
                "range": {"type": "endDate", "startDate": desde, "endDate": hasta},
            },
            "isOnlineMeeting": True,
            "onlineMeetingProvider": "teamsForBusiness",
            "allowNewTimeProposals": False,
            "responseRequested": False,
            "attendees": [{"emailAddress": {"address": e}, "type": "optional"} for e in invitados],
        }
        return self._req("POST", f"/users/{quote(profesor)}/events", json=cuerpo,
                         headers={"Prefer": f'outlook.timezone="{zona}"'})

    # ── asistencia ────────────────────────────────────────────────────
    def _meeting_id(self, profesor_id: str, join_url: str) -> str:
        url_ok = join_url.replace("'", "''")  # el link tal como lo da el evento
        data = self._req("GET", f"/users/{profesor_id}/onlineMeetings?$filter={quote(f'JoinWebUrl eq {chr(39)}{url_ok}{chr(39)}')}")
        if not data.get("value"):
            raise GraphError("No se encontró la reunión: revisar la Application Access Policy del profesor")
        return data["value"][0]["id"]

    def asistencia_reunion(self, profesor: str, join_url: str, inicio: datetime, fin: datetime) -> list[dict]:
        """Registros del reporte que corresponde a ESTA clase.

        En reuniones recurrentes hay un reporte por ocurrencia: se elige el
        que más se solapa con el horario. Si todavía no hay ninguno, Teams no
        terminó de generarlo: GraphPendiente para que el orquestador reintente.
        """
        prof_id = self.usuario(profesor)["id"]
        mid = self._meeting_id(prof_id, join_url)
        base = f"/users/{prof_id}/onlineMeetings/{mid}/attendanceReports"
        mejor, solape_max = None, timedelta(seconds=-1)
        for r in self._todas(base):
            ini_r, fin_r = parse_dt(r["meetingStartDateTime"]), parse_dt(r["meetingEndDateTime"])
            solape = min(fin, fin_r) - max(inicio, ini_r)  # 0 = se tocan en el borde; negativo = no se cruzan
            if solape >= timedelta(0) and solape > solape_max:
                mejor, solape_max = r, solape
        if not mejor:
            raise GraphPendiente("Teams todavía no publicó el reporte de asistencia de esta clase")
        salida = []
        for r in self._todas(f"{base}/{mejor['id']}/attendanceRecords"):
            intervalos = r.get("attendanceIntervals") or []
            ident = r.get("identity") or {}
            salida.append({
                "aad_id": ident.get("id"),
                "email": (r.get("emailAddress") or "").lower(),
                "nombre": ident.get("displayName"),
                "rol": r.get("role"),
                "segundos": r.get("totalAttendanceInSeconds") or 0,
                "entrada": intervalos[0].get("joinDateTime") if intervalos else None,
                "salida": intervalos[-1].get("leaveDateTime") if intervalos else None,
            })
        return salida

    # ── cámara (callRecords) ──────────────────────────────────────────
    def segundos_camara(self, join_url: str, inicio: datetime, fin: datetime) -> dict[str, float] | None:
        """aad_id → segundos enviando video de cámara. None si Teams aún no publicó el registro.

        Cada sesión de un participante tiene segmentos con sus medios. Un medio
        'main-video' con un stream que sale del participante significa cámara
        encendida (compartir pantalla es 'applicationsharing-video'). Se usa la
        duración del stream cuando viene; si no, la del segmento.
        """
        filtro = quote(f"startDateTime ge {utc_txt(inicio - timedelta(hours=1))} and startDateTime lt {utc_txt(fin)}")
        registros = self._todas(f"/communications/callRecords?$filter={filtro}&$select=id,joinWebUrl,startDateTime")
        rec = next((r for r in registros if mismo_link(r.get("joinWebUrl"), join_url)), None)
        if not rec:
            return None
        camara: dict[str, float] = {}
        for sesion in self._todas(f"/communications/callRecords/{rec['id']}/sessions?$expand=segments"):
            for seg in sesion.get("segments") or []:
                uid, rol = _usuario_de_segmento(seg)
                if not uid:
                    continue
                saliente = "callerToCallee" if rol == "caller" else "calleeToCaller"
                seg_ini, seg_fin = parse_dt(seg["startDateTime"]), parse_dt(seg["endDateTime"])
                for media in seg.get("media") or []:
                    if media.get("label") != "main-video":
                        continue
                    for s in media.get("streams") or []:
                        if s.get("streamDirection") != saliente:
                            continue
                        s_ini = parse_dt(s["startDateTime"]) if s.get("startDateTime") else seg_ini
                        s_fin = parse_dt(s["endDateTime"]) if s.get("endDateTime") else seg_fin
                        camara[uid] = camara.get(uid, 0) + max(0.0, (s_fin - s_ini).total_seconds())
        return camara

    # ── correo ────────────────────────────────────────────────────────
    def enviar_mail(self, remitente: str, para: str, asunto: str, html: str):
        self._req("POST", f"/users/{quote(remitente)}/sendMail", json={
            "message": {
                "subject": asunto,
                "body": {"contentType": "HTML", "content": html},
                "toRecipients": [{"emailAddress": {"address": para}}],
            },
            "saveToSentItems": True,
        })


def _usuario_de_segmento(seg: dict) -> tuple[str | None, str | None]:
    for rol in ("caller", "callee"):
        ident = ((seg.get(rol) or {}).get("identity") or {}).get("user") or {}
        if ident.get("id"):
            return ident["id"], rol
    return None, None


_cliente: Graph | None = None


def cliente() -> Graph | None:
    """Graph real (o simulado) si está configurado y no estamos en demo; si no, None."""
    global _cliente
    if config.DEMO or not config.GRAPH_CONFIGURADO:
        return None
    if _cliente is None:
        _cliente = Graph()
    return _cliente
