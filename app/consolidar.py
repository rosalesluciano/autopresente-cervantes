"""Une presencial (QR) + virtual (Teams) contra el padrón del curso."""
from __future__ import annotations

import csv
import json
from datetime import datetime

from . import config, db


def consolidar(clase: dict, virtuales: list[dict]) -> dict:
    ini = datetime.fromisoformat(clase["inicio"])
    fin = datetime.fromisoformat(clase["fin"])
    duracion_min = (fin - ini).total_seconds() / 60
    minimo = config.REGLAS["porcentaje_minimo_virtual"]

    alumnos = db.q("SELECT * FROM alumnos WHERE curso_id=? ORDER BY nombre", (clase["curso_id"],))
    checkins = {c["matricula"]: c for c in db.q("SELECT * FROM checkins WHERE clase_id=?", (clase["id"],))}
    por_email = {v["email"].lower(): v for v in virtuales if v.get("email")}

    filas = []
    for a in alumnos:
        c = checkins.get(a["matricula"])
        v = por_email.get((a["email"] or "").lower())
        fila = {
            "matricula": a["matricula"], "nombre": a["nombre"], "estado": "Ausente",
            "modalidad": "-", "minutos": None, "pct_tiempo": None, "pct_camara": None,
            "entrada": None, "salida": None,
        }
        if c:  # presencial tiene prioridad
            fila.update(estado="Presente", modalidad="Presencial", entrada=c["hora"])
        elif v:
            minutos = v["segundos"] / 60
            pct = min(100.0, 100 * minutos / duracion_min) if duracion_min else 0
            fila.update(
                modalidad="Virtual", minutos=round(minutos, 1), pct_tiempo=round(pct, 1),
                pct_camara=v.get("pct_camara"), entrada=v.get("entrada"), salida=v.get("salida"),
                estado="Presente" if pct >= minimo else "Ausente",
            )
        filas.append(fila)

    db.ejecutar("DELETE FROM registros WHERE clase_id=?", (clase["id"],))
    db.ejecutar_muchos(
        """INSERT INTO registros (clase_id, matricula, nombre, estado, modalidad, minutos,
           pct_tiempo, pct_camara, entrada, salida) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [(clase["id"], f["matricula"], f["nombre"], f["estado"], f["modalidad"], f["minutos"],
          f["pct_tiempo"], f["pct_camara"], f["entrada"], f["salida"]) for f in filas],
    )

    presentes = [f for f in filas if f["estado"] == "Presente"]
    resumen = {
        "total": len(filas),
        "presenciales": sum(f["modalidad"] == "Presencial" for f in presentes),
        "virtuales": sum(f["modalidad"] == "Virtual" for f in presentes),
        "ausentes": len(filas) - len(presentes),
        "virtual_insuficiente": sum(f["modalidad"] == "Virtual" and f["estado"] == "Ausente" for f in filas),
        "no_en_padron": sorted(set(por_email) - {(a["email"] or "").lower() for a in alumnos}),
    }
    db.ejecutar("UPDATE clases SET resumen=? WHERE id=?", (json.dumps(resumen, ensure_ascii=False), clase["id"]))
    _exportar_csv(clase, filas)
    return resumen


def _exportar_csv(clase: dict, filas: list[dict]):
    ruta = config.EXPORTS / f"{clase['fecha']}_{clase['curso_id']}_{clase['id']}.csv"
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Matricula", "Nombre", "Fecha", "Estado", "Modalidad", "Minutos", "% Tiempo", "% Camara"])
        for r in filas:
            w.writerow([r["matricula"], r["nombre"], clase["fecha"], r["estado"], r["modalidad"],
                        r["minutos"] or "", r["pct_tiempo"] or "", r["pct_camara"] if r["pct_camara"] is not None else ""])
