"""Asistencia virtual desde Teams: entra quien aceptó la reunión y se unió.

Flujo sin intervención:
 1. Se buscan en el calendario del profesor las reuniones de Teams del día.
 2. Cada reunión se asocia a un curso por su asunto (config: teams_asunto).
 3. Al terminar la clase (+ espera) se descarga el reporte de asistencia y
    los datos de cámara, y se cruzan con el padrón por email institucional.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from . import config, db
from .graph import cliente


def _utc(txt: str) -> datetime:
    """calendarView devuelve fechas UTC sin offset."""
    dt = datetime.fromisoformat(txt[:19])
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def vincular_reuniones(dia: datetime):
    """Completa clases.teams_url para las clases del día buscando en el calendario."""
    g = cliente()
    if not g:
        return
    desde = dia.replace(hour=0, minute=0, second=0, microsecond=0)
    hasta = desde + timedelta(days=1)
    try:
        reuniones = g.reuniones_teams(config.PROFESOR["email"], desde, hasta)
    except Exception as e:  # noqa: BLE001
        db.log(f"No se pudo leer el calendario de Teams: {e}", "error")
        return
    clases = db.q("SELECT * FROM clases WHERE fecha=? AND teams_url IS NULL", (desde.date().isoformat(),))
    for clase in clases:
        curso = config.CURSOS[clase["curso_id"]]
        ini = datetime.fromisoformat(clase["inicio"])
        fin = datetime.fromisoformat(clase["fin"])
        for r in reuniones:
            if curso["teams_asunto"].lower() not in r["asunto"].lower():
                continue
            if _utc(r["inicio"]) < fin and _utc(r["fin"]) > ini:
                db.ejecutar("UPDATE clases SET teams_url=? WHERE id=?", (r["join_url"], clase["id"]))
                db.log(f"Reunión de Teams vinculada: «{r['asunto']}»", clase_id=clase["id"])
                break


def asistencia_virtual(clase: dict) -> list[dict]:
    """Lista de {email, nombre, segundos, entrada, salida, pct_camara} de la clase."""
    ini = datetime.fromisoformat(clase["inicio"])
    fin = datetime.fromisoformat(clase["fin"])
    g = cliente()
    if g is None:
        return _simulada(clase, ini, fin)
    if not clase.get("teams_url"):
        db.log("La clase no tiene reunión de Teams: sólo asistencia presencial", "warn", clase["id"])
        return []
    registros = g.asistencia_reunion(config.PROFESOR["email"], clase["teams_url"], ini, fin)
    camara = g.segundos_camara(clase["teams_url"], ini, fin)
    for r in registros:
        if r["segundos"]:
            r["pct_camara"] = round(min(100, 100 * camara.get(r["aad_id"], 0) / r["segundos"]), 1)
        else:
            r["pct_camara"] = 0.0
        if not r["email"] and r["aad_id"]:
            try:
                u = g.usuario(r["aad_id"])
                r["email"] = (u.get("mail") or u.get("userPrincipalName") or "").lower()
            except Exception:  # noqa: BLE001
                pass
    return [r for r in registros if r.get("rol") != "Organizer"]


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
        perfil = rnd.random()
        if perfil < 0.15:  # se conectó un ratito y se fue
            segundos = duracion * rnd.uniform(0.1, 0.4)
        else:
            segundos = duracion * rnd.uniform(0.75, 1.0)
        retraso = rnd.uniform(0, duracion - segundos)
        entrada = ini + timedelta(seconds=retraso)
        camara = rnd.choice([rnd.uniform(80, 100), rnd.uniform(40, 80), rnd.uniform(0, 25)])
        salida.append(
            {
                "email": a["email"],
                "nombre": a["nombre"],
                "segundos": round(segundos),
                "entrada": entrada.isoformat(timespec="seconds"),
                "salida": (entrada + timedelta(seconds=segundos)).isoformat(timespec="seconds"),
                "pct_camara": round(camara, 1),
            }
        )
    return salida
