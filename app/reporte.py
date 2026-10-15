"""Reporte por correo al profesor (Graph sendMail → SMTP → archivo en outbox)."""
from __future__ import annotations

import html
import json
import smtplib
from email.mime.text import MIMEText

from . import config, db
from .graph import cliente

AZUL = "#2A3355"


def _fila(r: dict, alerta: float) -> str:
    cam = r["pct_camara"]
    if cam is None:
        cam_txt, color = "—", "#8a90a6"
    else:
        cam_txt = f"{cam:.0f}%"
        color = "#b42318" if cam < alerta else AZUL
    aviso = " &nbsp;<span style='background:#fdecea;color:#b42318;border-radius:4px;padding:1px 6px;font-size:11px'>Alerta de atención</span>" if cam is not None and cam < alerta else ""
    tiempo = f"{r['pct_tiempo']:.0f}% de la clase" if r["pct_tiempo"] is not None else ""
    return (f"<tr><td style='padding:8px 10px;border-bottom:1px solid #e6e8ef'>{html.escape(r['nombre'])}"
            f"<div style='color:#8a90a6;font-size:12px'>{tiempo}</div></td>"
            f"<td style='padding:8px 10px;border-bottom:1px solid #e6e8ef;color:{color};font-weight:600;text-align:right'>"
            f"{cam_txt}{aviso}</td></tr>")


def construir(clase: dict) -> tuple[str, str]:
    curso = config.CURSOS[clase["curso_id"]]
    res = json.loads(clase["resumen"] or "{}")
    alerta = config.REGLAS["alerta_camara_por_debajo_de"]
    virtuales = db.q("SELECT * FROM registros WHERE clase_id=? AND modalidad='Virtual' ORDER BY pct_camara DESC",
                     (clase["id"],))
    ausentes = db.q("SELECT nombre FROM registros WHERE clase_id=? AND estado='Ausente' ORDER BY nombre", (clase["id"],))
    fecha = clase["fecha"][8:10] + "/" + clase["fecha"][5:7] + "/" + clase["fecha"][:4]
    asunto = f"Asistencia cargada · {curso['materia']} {curso['division']} · {fecha}"

    def kpi(n, txt):
        return (f"<td style='padding:14px;text-align:center;border:1px solid #e6e8ef;border-radius:8px'>"
                f"<div style='font-size:26px;font-weight:700;color:{AZUL}'>{n}</div>"
                f"<div style='font-size:12px;color:#5b6280;text-transform:uppercase;letter-spacing:.04em'>{txt}</div></td>")

    filas_v = "".join(_fila(r, alerta) for r in virtuales) or \
        "<tr><td style='padding:8px 10px;color:#8a90a6'>Sin asistentes virtuales.</td></tr>"
    lista_aus = " · ".join(html.escape(a["nombre"]) for a in ausentes) or "Ninguno"

    cuerpo = f"""<div style="font-family:Segoe UI,Arial,sans-serif;background:#f4f5f9;padding:24px">
<div style="max-width:620px;margin:auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e6e8ef">
  <div style="background:{AZUL};color:#fff;padding:20px 24px">
    <div style="font-size:12px;opacity:.75;letter-spacing:.08em;text-transform:uppercase">AutoPresente</div>
    <div style="font-size:20px;font-weight:600;margin-top:4px">Asistencia registrada exitosamente</div>
    <div style="opacity:.85;margin-top:2px">{html.escape(curso['materia'])} · División {html.escape(curso['division'])} · {fecha}</div>
  </div>
  <div style="padding:20px 24px">
    <table style="width:100%;border-collapse:separate;border-spacing:8px 0"><tr>
      {kpi(res.get('presenciales', 0), 'Presenciales')}{kpi(res.get('virtuales', 0), 'Virtuales')}{kpi(res.get('ausentes', 0), 'Ausentes')}
    </tr></table>
    <h3 style="color:{AZUL};font-size:15px;margin:24px 0 6px">Participación virtual · cámara encendida</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px">{filas_v}</table>
    <h3 style="color:{AZUL};font-size:15px;margin:24px 0 6px">Ausentes</h3>
    <p style="font-size:14px;color:#3a4160;margin:0">{lista_aus}</p>
    <p style="font-size:12px;color:#8a90a6;margin-top:24px;border-top:1px solid #e6e8ef;padding-top:12px">
      Cargado automáticamente en Autogestión Cervantes. Virtual = presente si estuvo ≥ {config.REGLAS['porcentaje_minimo_virtual']}% de la clase.
      No necesitás hacer nada; si algo no es correcto, respondé este correo.</p>
  </div>
</div></div>"""
    return asunto, cuerpo


def enviar(clase: dict) -> str:
    asunto, cuerpo = construir(clase)
    para = config.PROFESOR["email"]
    archivo = config.OUTBOX / f"clase_{clase['id']}.html"
    archivo.write_text(f"<!doctype html><meta charset='utf-8'><title>{html.escape(asunto)}</title>{cuerpo}",
                       encoding="utf-8")
    g = cliente()
    if g:
        g.enviar_mail(para, para, asunto, cuerpo)
        return "Graph (Outlook)"
    if config.SMTP_HOST:
        msg = MIMEText(cuerpo, "html", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = asunto, config.SMTP_REMITENTE, para
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as s:
            s.starttls()
            if config.SMTP_USUARIO:
                s.login(config.SMTP_USUARIO, config.SMTP_CLAVE)
            s.send_message(msg)
        return "SMTP"
    return f"bandeja local ({archivo.name})"
