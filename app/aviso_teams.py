"""Aviso al profesor dentro de Teams con una Adaptive Card.

Se publica en un chat o canal de Teams a través de un flujo de Workflows
("Enviar al chat/canal cuando se recibe una solicitud de webhook de Teams"):
el profesor crea el flujo una vez y la URL va en TEAMS_WEBHOOK_URL. Sin URL, la
tarjeta queda guardada en data/outbox para verla en el panel.

Nunca lanza excepciones: si Teams no responde, el correo ya salió igual.
"""
from __future__ import annotations

import json

import httpx

from . import config, db


def _fecha(clase: dict) -> str:
    f = clase["fecha"]
    return f"{f[8:10]}/{f[5:7]}/{f[:4]}"


def _enviar(clase_id: int, tarjeta: dict, nombre: str) -> bool:
    mensaje = {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": tarjeta}]}
    (config.OUTBOX / f"teams_{nombre}_{clase_id}.json").write_text(
        json.dumps(mensaje, ensure_ascii=False, indent=2), encoding="utf-8")
    if not config.TEAMS_WEBHOOK_URL:
        return False
    try:
        r = httpx.post(config.TEAMS_WEBHOOK_URL, json=mensaje, timeout=20)
        if r.status_code >= 300:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        return True
    except Exception as e:  # noqa: BLE001
        db.log(f"No se pudo publicar el aviso en Teams: {e}", "warn", clase_id)
        return False


def tarjeta_resumen(clase: dict) -> dict:
    curso = config.CURSOS[clase["curso_id"]]
    res = json.loads(clase["resumen"] or "{}")
    alerta = config.REGLAS["alerta_camara_por_debajo_de"]
    bajos = db.q("""SELECT nombre, pct_camara FROM registros WHERE clase_id=? AND modalidad='Virtual'
                    AND pct_camara IS NOT NULL AND pct_camara < ? ORDER BY pct_camara""", (clase["id"], alerta))
    cuerpo = [
        {"type": "TextBlock", "text": "Asistencia cargada en Autogestión", "weight": "Bolder", "size": "Medium",
         "wrap": True},
        {"type": "TextBlock", "text": f"{curso['materia']} · División {curso['division']} · {_fecha(clase)}",
         "isSubtle": True, "spacing": "None", "wrap": True},
        {"type": "ColumnSet", "spacing": "Medium", "columns": [
            {"type": "Column", "width": "stretch", "items": [
                {"type": "TextBlock", "text": str(res.get(k, 0)), "size": "ExtraLarge", "weight": "Bolder",
                 "horizontalAlignment": "Center", "color": c},
                {"type": "TextBlock", "text": t, "isSubtle": True, "size": "Small", "horizontalAlignment": "Center",
                 "spacing": "None"}]}
            for k, t, c in (("presenciales", "Presenciales", "Default"), ("virtuales", "Virtuales", "Accent"),
                            ("ausentes", "Ausentes", "Attention"))]},
    ]
    if bajos:
        cuerpo.append({"type": "TextBlock", "text": f"Cámara por debajo del {alerta} %", "weight": "Bolder",
                       "spacing": "Medium", "color": "Attention"})
        cuerpo.append({"type": "FactSet", "facts": [{"title": b["nombre"], "value": f"{b['pct_camara']:.0f} %"}
                                                     for b in bajos[:10]]})
    cuerpo.append({"type": "TextBlock", "isSubtle": True, "size": "Small", "wrap": True, "spacing": "Medium",
                   "text": "No hace falta hacer nada. Si algo no está bien, corregilo desde el detalle."})
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
        "msteams": {"width": "Full"},
        "body": cuerpo,
        "actions": [{"type": "Action.OpenUrl", "title": "Ver detalle", "url": f"{config.BASE_URL}/clase/{clase['id']}"}],
    }


def resumen(clase: dict) -> bool:
    """Publica el resumen de la clase. True si llegó a Teams."""
    clase = db.q1("SELECT * FROM clases WHERE id=?", (clase["id"],))
    return _enviar(clase["id"], tarjeta_resumen(clase), "resumen")


def error(clase: dict, motivo: str) -> bool:
    """Avisa que una clase no se pudo cargar sola, para que el profesor lo sepa."""
    curso = config.CURSOS[clase["curso_id"]]
    tarjeta = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "No se pudo cargar la asistencia", "weight": "Bolder", "size": "Medium",
             "color": "Attention", "wrap": True},
            {"type": "TextBlock", "text": f"{curso['materia']} · División {curso['division']} · {_fecha(clase)}",
             "isSubtle": True, "spacing": "None", "wrap": True},
            {"type": "TextBlock", "text": motivo[:400], "wrap": True, "spacing": "Medium"},
        ],
        "actions": [{"type": "Action.OpenUrl", "title": "Ver detalle", "url": f"{config.BASE_URL}/clase/{clase['id']}"}],
    }
    return _enviar(clase["id"], tarjeta, "error")
