"""Cliente Microsoft Graph (permisos de aplicación, sin intervención humana).

Lo que usamos de Graph:
  • calendarView del profesor        → detectar las reuniones de Teams de cada clase
  • onlineMeetings + attendanceReports → quién entró, a qué hora y cuánto tiempo
  • communications/callRecords       → segmentos con video saliente = cámara encendida
  • sendMail                         → reporte al profesor
"""
from __future__ import annotations

import time
from datetime import datetime
from urllib.parse import quote

import httpx

from . import config

GRAPH = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    pass


class Graph:
    def __init__(self):
        import msal

        self._app = msal.ConfidentialClientApplication(
            config.MS_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}",
            client_credential=config.MS_CLIENT_SECRET,
        )
        self._http = httpx.Client(timeout=30)
        self._cache_usuarios: dict[str, dict] = {}

    # ── base ──────────────────────────────────────────────────────────
    def _token(self) -> str:
        r = self._app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in r:
            raise GraphError(f"No se pudo obtener token: {r.get('error_description')}")
        return r["access_token"]

    def _req(self, metodo: str, url: str, **kw) -> dict:
        if not url.startswith("http"):
            url = GRAPH + url
        for intento in range(4):
            resp = self._http.request(
                metodo, url, headers={"Authorization": f"Bearer {self._token()}"}, **kw
            )
            if resp.status_code in (429, 503, 504):  # throttling: respetar Retry-After
                time.sleep(int(resp.headers.get("Retry-After", 2 ** intento)))
                continue
            if resp.status_code >= 400:
                raise GraphError(f"{metodo} {url} → {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.content else {}
        raise GraphError(f"{metodo} {url}: demasiados reintentos")

    def _todas(self, url: str) -> list[dict]:
        items, siguiente = [], url
        while siguiente:
            data = self._req("GET", siguiente)
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

    # ── calendario y reuniones ────────────────────────────────────────
    def reuniones_teams(self, profesor: str, desde: datetime, hasta: datetime) -> list[dict]:
        url = (
            f"/users/{quote(profesor)}/calendarView"
            f"?startDateTime={desde.isoformat()}&endDateTime={hasta.isoformat()}"
            "&$select=subject,start,end,isOnlineMeeting,onlineMeeting&$top=100"
        )
        eventos = self._todas(url)
        return [
            {
                "asunto": e.get("subject", ""),
                "inicio": e["start"]["dateTime"],
                "fin": e["end"]["dateTime"],
                "join_url": (e.get("onlineMeeting") or {}).get("joinUrl"),
            }
            for e in eventos
            if e.get("isOnlineMeeting") and e.get("onlineMeeting")
        ]

    def _meeting_id(self, profesor_id: str, join_url: str) -> str:
        filtro = quote(f"JoinWebUrl eq '{join_url}'")
        data = self._req("GET", f"/users/{profesor_id}/onlineMeetings?$filter={filtro}")
        if not data.get("value"):
            raise GraphError("La reunión no existe o falta la Application Access Policy")
        return data["value"][0]["id"]

    def asistencia_reunion(self, profesor: str, join_url: str, inicio: datetime, fin: datetime) -> list[dict]:
        """Registros de asistencia del reporte que corresponde a ESTA clase.

        En reuniones recurrentes hay un reporte por ocurrencia: elegimos el que
        se solapa con el horario de la clase.
        """
        prof_id = self.usuario(profesor)["id"]
        mid = self._meeting_id(prof_id, join_url)
        base = f"/users/{prof_id}/onlineMeetings/{mid}/attendanceReports"
        reportes = self._todas(base)
        elegido = None
        for r in reportes:
            ini_r = datetime.fromisoformat(r["meetingStartDateTime"].replace("Z", "+00:00"))
            fin_r = datetime.fromisoformat(r["meetingEndDateTime"].replace("Z", "+00:00"))
            if ini_r < fin and fin_r > inicio:
                elegido = r
                break
        if not elegido:
            return []
        registros = self._todas(f"{base}/{elegido['id']}/attendanceRecords")
        salida = []
        for r in registros:
            intervalos = r.get("attendanceIntervals") or []
            salida.append(
                {
                    "aad_id": (r.get("identity") or {}).get("id"),
                    "email": (r.get("emailAddress") or "").lower(),
                    "nombre": (r.get("identity") or {}).get("displayName"),
                    "rol": r.get("role"),
                    "segundos": r.get("totalAttendanceInSeconds") or 0,
                    "entrada": intervalos[0]["joinDateTime"] if intervalos else None,
                    "salida": intervalos[-1]["leaveDateTime"] if intervalos else None,
                }
            )
        return salida

    # ── cámara (callRecords) ──────────────────────────────────────────
    def segundos_camara(self, join_url: str, inicio: datetime, fin: datetime) -> dict[str, float]:
        """aad_id → segundos con video de cámara saliente.

        Teams registra cada segmento de la llamada con sus medios; un medio
        'main-video' con stream del participante hacia el servidor significa
        cámara encendida (compartir pantalla es 'applicationsharing-video').
        """
        desde = inicio.astimezone().isoformat()
        hasta = fin.astimezone().isoformat()
        filtro = quote(f"startDateTime ge {desde} and startDateTime lt {hasta}")
        try:
            registros = self._todas(f"/communications/callRecords?$filter={filtro}")
        except GraphError:
            return {}
        rec = next((r for r in registros if r.get("joinWebUrl") == join_url), None)
        if not rec:
            return {}
        detalle = self._todas(f"/communications/callRecords/{rec['id']}/sessions?$expand=segments")
        camara: dict[str, float] = {}
        for sesion in detalle:
            for seg in sesion.get("segments", []):
                uid, rol = _usuario_de_segmento(seg)
                if not uid:
                    continue
                ini = datetime.fromisoformat(seg["startDateTime"].replace("Z", "+00:00"))
                fin_s = datetime.fromisoformat(seg["endDateTime"].replace("Z", "+00:00"))
                saliente = "callerToCallee" if rol == "caller" else "calleeToCaller"
                for media in seg.get("media", []):
                    if media.get("label") != "main-video":
                        continue
                    if any(s.get("streamDirection") == saliente for s in media.get("streams", [])):
                        camara[uid] = camara.get(uid, 0) + (fin_s - ini).total_seconds()
                        break
        return camara

    # ── correo ────────────────────────────────────────────────────────
    def enviar_mail(self, remitente: str, para: str, asunto: str, html: str):
        self._req(
            "POST",
            f"/users/{quote(remitente)}/sendMail",
            json={
                "message": {
                    "subject": asunto,
                    "body": {"contentType": "HTML", "content": html},
                    "toRecipients": [{"emailAddress": {"address": para}}],
                },
                "saveToSentItems": True,
            },
        )


def _usuario_de_segmento(seg: dict) -> tuple[str | None, str | None]:
    for rol in ("caller", "callee"):
        ident = ((seg.get(rol) or {}).get("identity") or {}).get("user") or {}
        if ident.get("id"):
            return ident["id"], rol
    return None, None


_cliente: Graph | None = None


def cliente() -> Graph | None:
    """Graph real si hay credenciales y no estamos en demo; si no, None."""
    global _cliente
    if config.DEMO or not config.GRAPH_CONFIGURADO:
        return None
    if _cliente is None:
        _cliente = Graph()
    return _cliente
