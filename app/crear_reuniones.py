"""Crea una reunión de Teams semanal por curso en el calendario del profesor.

Se corre una sola vez al empezar el cuatrimestre:

    python -m app.crear_reuniones            # crea las que falten
    python -m app.crear_reuniones --simular  # muestra qué haría, sin crear nada

Cada reunión usa el asunto de config/cursos.json (teams_asunto), que es como el
sistema la reconoce después. Si "invitar_alumnos" está activo, los alumnos del
padrón reciben la invitación en su calendario: entrar desde ahí es lo único que
tienen que hacer para quedar presentes en modalidad virtual.
"""
from __future__ import annotations

import sys

from . import config, db, padron
from .graph import cliente

DIAS_GRAPH = {"lunes": "monday", "martes": "tuesday", "miercoles": "wednesday", "jueves": "thursday",
              "viernes": "friday", "sabado": "saturday", "domingo": "sunday"}


def planes() -> list[dict]:
    """Una reunión por curso y horario (mismo horario en varios días = una sola serie)."""
    cuat = config.CUATRIMESTRE
    salida = []
    for curso in config.CURSOS.values():
        grupos: dict[tuple[str, str], list[str]] = {}
        for h in curso["horarios"]:
            grupos.setdefault((h["inicio"], h["fin"]), []).append(DIAS_GRAPH[h["dia"]])
        for (ini, fin), dias in grupos.items():
            invitados = [a["email"] for a in db.q("SELECT email FROM alumnos WHERE curso_id=? AND email<>''", (curso["id"],))] \
                if cuat.get("invitar_alumnos") else []
            salida.append({"curso": curso["id"], "asunto": curso["teams_asunto"], "dias": dias, "inicio": ini,
                           "fin": fin, "desde": cuat["inicio"], "hasta": cuat["fin"], "invitados": invitados})
    return salida


def main(simular: bool = False) -> int:
    db.init()
    padron.cargar()
    if not config.CUATRIMESTRE.get("inicio"):
        print("Falta el bloque 'cuatrimestre' (inicio y fin) en config/cursos.json.")
        return 1
    g = cliente()
    if g is None and not simular:
        print("Graph no está configurado (DEMO=1 o faltan MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET).")
        return 1
    profesor = config.PROFESOR["email"]
    zona = str(config.TZ)
    todos = planes()
    for p in todos:
        dias = ", ".join(p["dias"])
        detalle = f"«{p['asunto']}» {dias} {p['inicio']}-{p['fin']}, {p['desde']} → {p['hasta']}, {len(p['invitados'])} invitados"
        if simular:
            print("Crearía:", detalle)
            continue
        # Un curso con dos horarios distintos necesita dos series; el asunto sigue conteniendo teams_asunto.
        asunto = p["asunto"] if sum(x["curso"] == p["curso"] for x in todos) == 1 else f"{p['asunto']} ({p['inicio']})"
        existente = g.buscar_evento_serie(profesor, asunto)
        if existente:
            print("Ya existe:", detalle)
            continue
        ev = g.crear_reunion_recurrente(profesor, asunto, p["dias"], p["inicio"], p["fin"], p["desde"],
                                        p["hasta"], zona, p["invitados"])
        link = (ev.get("onlineMeeting") or {}).get("joinUrl", "")
        db.log(f"Reunión de Teams creada: {detalle}")
        print("Creada:", detalle, "\n  ", link)
    return 0


if __name__ == "__main__":
    sys.exit(main(simular="--simular" in sys.argv))
