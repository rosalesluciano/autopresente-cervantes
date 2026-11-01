"""Asistencia virtual desde Teams: entra quien aceptó la invitación y se unió.

Flujo sin intervención:
 1. Al empezar el cuatrimestre, `python -m app.crear_reuniones` crea en el
    calendario del profesor una reunión de Teams semanal por curso e invita a
    los alumnos del padrón (una sola vez).
 2. Cada día se buscan esas reuniones y se vinculan con las clases por asunto.
 3. Al terminar la clase se descarga el reporte de asistencia y los streams de
    cámara, y se cruzan con el padrón por mail institucional.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from . import config, db
from .graph import GraphPendiente, cliente

# Cuánto esperar a que Teams publique los registros de cámara antes de seguir sin ellos.
ESPERA_MAX_CAMARA = timedelta(minutes=90)


def vincular_reuniones(dia: datetime):
    """Completa clases.teams_url para las clases del día buscando en el calendario."""
    g = cliente()
    if not g:
        return
    desde = dia.replace(hour=0, minute=0, second=0, microsecond=0)
    hasta = desde + timedelta(days=1)
    clases = db.q("SELECT * FROM clases WHERE fecha=? AND teams_url IS NULL", (desde.date().isoformat(),))
    if not clases:
        return
    try:
        reuniones = g.reuniones_teams(config.PROFESOR["email"], desde, hasta)
    except Exception as e:  # noqa: BLE001
        db.log(f"No se pudo leer el calendario de Teams: {e}", "error")
        return
    for clase in clases:
        curso = config.CURSOS[clase["curso_id"]]
        ini, fin = datetime.fromisoformat(clase["inicio"]), datetime.fromisoformat(clase["fin"])
        for r in reuniones:
            if curso["teams_asunto"].lower() not in r["asunto"].lower():
                continue
            if r["inicio"] <= fin and r["fin"] >= ini:  # incluye clases muy cortas que se tocan en el borde
                db.ejecutar("UPDATE clases SET teams_url=? WHERE id=?", (r["join_url"], clase["id"]))
                db.log(f"Reunión de Teams vinculada: «{r['asunto']}»", clase_id=clase["id"])
                break


def asistencia_virtual(clase: dict) -> list[dict]:
    """Lista de {email, nombre, segundos, entrada, salida, pct_camara} de la clase.

    Lanza GraphPendiente si Teams todavía no publicó el reporte: el orquestador
    reintenta solo.
    """
    ini, fin = datetime.fromisoformat(clase["inicio"]), datetime.fromisoformat(clase["fin"])
    g = cliente()
    if g is None:
        return _simulada(clase, ini, fin)
    if not clase.get("teams_url"):
        vincular_reuniones(ini)
        clase = db.q1("SELECT * FROM clases WHERE id=?", (clase["id"],))
    if not clase.get("teams_url"):
        db.log("La clase no tiene reunión de Teams: sólo asistencia presencial", "warn", clase["id"])
        return []

    profesor = config.PROFESOR["email"].lower()
    registros = g.asistencia_reunion(profesor, clase["teams_url"], ini, fin)
    camara = g.segundos_camara(clase["teams_url"], ini, fin)
    if camara is None:
        if db.ahora() - fin < ESPERA_MAX_CAMARA:
            raise GraphPendiente("Teams todavía no publicó los registros de cámara")
        db.log("Teams no publicó los registros de cámara: el % de cámara queda sin dato", "warn", clase["id"])

    salida = []
    for r in registros:
        if not r["email"] and r["aad_id"]:
            try:
                u = g.usuario(r["aad_id"])
                r["email"] = (u.get("mail") or u.get("userPrincipalName") or "").lower()
            except Exception:  # noqa: BLE001 — invitado externo sin cuenta en el tenant
                pass
        if r.get("rol") == "Organizer" or r["email"] == profesor:
            continue
        if camara is None or not r["segundos"]:
            r["pct_camara"] = None
        else:
            r["pct_camara"] = round(min(100, 100 * camara.get(r["aad_id"], 0) / r["segundos"]), 1)
        salida.append(r)
    db.log(f"Teams: {len(salida)} participantes en el reporte de asistencia", clase_id=clase["id"])
    return salida


def _simulada(clase: dict, ini: datetime, fin: datetime) -> list[dict]:
    """DEMO: simula el reporte de Teams para alumnos que no hicieron check-in."""
    rnd = random.Random(clase["id"])
    presenciales = {r["matricula"] for r in db.q("SELECT matricula FROM checkins WHERE clase_id=?", (clase["id"],))}
    alumnos = db.q("SELECT * FROM alumnos WHERE curso_id=?", (clase["curso_id"],))
    duracion = (fin - ini).total_seconds()
    salida = []
    for a in alumnos:
        if a["matricula"] in presenciales or rnd.random() < 0.35:
            continue
        if rnd.random() < 0.15:  # se conectó un ratito y se fue
            segundos = duracion * rnd.uniform(0.1, 0.4)
        else:
            segundos = duracion * rnd.uniform(0.75, 1.0)
        entrada = ini + timedelta(seconds=rnd.uniform(0, duracion - segundos))
        camara = rnd.choice([rnd.uniform(80, 100), rnd.uniform(40, 80), rnd.uniform(0, 25)])
        salida.append({
            "email": a["email"], "nombre": a["nombre"], "segundos": round(segundos),
            "entrada": entrada.isoformat(timespec="seconds"),
            "salida": (entrada + timedelta(seconds=segundos)).isoformat(timespec="seconds"),
            "pct_camara": round(camara, 1),
        })
    return salida
