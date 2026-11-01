"""Circuito completo con Teams: reuniones, asistencia, cámara, robot, correo y aviso en Teams.

Corre el código de producción (graph.py, teams.py, orquestador.py, rpa_cervantes.py)
contra el Graph simulado, que responde con los formatos de la documentación de Microsoft.
"""
from datetime import timedelta

import httpx
import pytest

from app import config, crear_reuniones, db, graph_simulado, orquestador, teams
from app.graph import GraphPendiente, mismo_link, parse_dt

from .conftest import BASE


def _clase_terminada(curso_id: str, minutos_atras: int = 1, checkins: int = 5) -> int:
    fin = db.ahora().replace(microsecond=0) - timedelta(minutes=minutos_atras)
    ini = fin - timedelta(minutes=80)
    cid = orquestador.crear_clase(curso_id, ini, fin)
    graph_simulado.preparar_reunion(curso_id, ini, fin)
    for a in db.q("SELECT matricula FROM alumnos WHERE curso_id=? ORDER BY matricula LIMIT ?", (curso_id, checkins)):
        db.ejecutar("INSERT OR IGNORE INTO checkins (clase_id, matricula, hora, metodo) VALUES (?,?,?,?)",
                    (cid, a["matricula"], ini.isoformat(), "wifi"))
    db.ejecutar("UPDATE clases SET estado='esperando_teams' WHERE id=?", (cid,))
    return cid


def test_fechas_y_links_de_graph():
    assert parse_dt("2026-10-15T11:00:00.0000000").isoformat() == "2026-10-15T11:00:00+00:00"
    assert parse_dt("2026-10-15T11:00:00Z").hour == 11
    assert parse_dt("2026-10-15T08:00:00.1234567-03:00").utcoffset().total_seconds() == -3 * 3600
    link = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0"
    assert mismo_link(link, "https://teams.microsoft.com/l/meetup-join/19%253ameeting_abc%2540thread.v2/0")
    assert not mismo_link(link, link.replace("abc", "xyz"))


def test_crear_reuniones_del_cuatrimestre_es_idempotente(capsys):
    assert crear_reuniones.main() == 0
    salida = capsys.readouterr().out
    assert salida.count("Creada:") == len(config.CURSOS)
    serie = graph_simulado.SERIES[0]
    assert serie["isOnlineMeeting"] and serie["onlineMeetingProvider"] == "teamsForBusiness"
    assert serie["recurrence"]["pattern"]["daysOfWeek"]
    assert len(serie["attendees"]) == 30  # los alumnos del padrón reciben la invitación
    assert crear_reuniones.main() == 0
    assert capsys.readouterr().out.count("Ya existe:") == len(config.CURSOS)


def test_asistencia_y_camara_desde_teams():
    cid = _clase_terminada("PROG2-A")
    teams.vincular_reuniones(db.ahora())
    clase = db.q1("SELECT * FROM clases WHERE id=?", (cid,))
    assert clase["teams_url"], "la clase debería quedar vinculada a su reunión de Teams"
    virtuales = teams.asistencia_virtual(clase)
    emails = [v["email"] for v in virtuales]
    assert config.PROFESOR["email"] not in emails, "el profesor (organizador) no cuenta"
    presenciales = {a["email"] for a in db.q(
        "SELECT a.email FROM checkins c JOIN alumnos a ON a.matricula=c.matricula AND a.curso_id='PROG2-A' WHERE c.clase_id=?",
        (cid,))}
    assert not presenciales & set(emails)
    alumnos = [v for v in virtuales if v["email"]]
    assert len(alumnos) >= 8, "con más de 10 registros se recorre la paginación de Graph"
    assert all(0 <= v["pct_camara"] <= 100 for v in alumnos)
    assert any(v["pct_camara"] < 30 for v in alumnos) and any(v["pct_camara"] > 70 for v in alumnos)
    assert any(not v["email"] for v in virtuales), "el invitado externo llega sin correo y no se cruza"


def test_si_teams_no_publico_el_reporte_se_espera():
    graph_simulado.DEMORA = timedelta(hours=2)
    try:
        cid = _clase_terminada("BD-B")
        teams.vincular_reuniones(db.ahora())
        with pytest.raises(GraphPendiente):
            teams.asistencia_virtual(db.q1("SELECT * FROM clases WHERE id=?", (cid,)))
        orquestador.procesar(cid)
        clase = db.q1("SELECT * FROM clases WHERE id=?", (cid,))
        assert clase["estado"] == "esperando_teams" and clase["proximo_intento"]
        assert clase["detalle"].startswith("Esperando a Teams")
    finally:
        graph_simulado.DEMORA = timedelta(0)


def test_circuito_completo_robot_correo_y_aviso_en_teams():
    cid = _clase_terminada("PROG2-A", minutos_atras=2, checkins=4)
    teams.vincular_reuniones(db.ahora())
    antes_mails, antes_webhook = len(graph_simulado.CORREOS), len(graph_simulado.WEBHOOK)
    orquestador.procesar(cid)
    clase = db.q1("SELECT * FROM clases WHERE id=?", (cid,))
    assert clase["estado"] == "completada", clase["detalle"]
    assert "Teams" in clase["detalle"]

    regs = db.q("SELECT * FROM registros WHERE clase_id=?", (cid,))
    assert sum(r["modalidad"] == "Presencial" for r in regs) == 4
    assert any(r["modalidad"] == "Virtual" for r in regs)

    # El robot cargó el portal: misma cantidad de presentes que el consolidado.
    fecha = clase["inicio"][8:10] + "/" + clase["inicio"][5:7] + "/" + clase["inicio"][:4]
    portal = db.q("SELECT estado FROM cervantes_asistencia WHERE materia='Programación II' AND division='A' AND fecha=?",
                  (fecha,))
    assert sum(p["estado"] == "P" for p in portal) == sum(r["estado"] == "Presente" for r in regs)

    assert len(graph_simulado.CORREOS) == antes_mails + 1
    correo = graph_simulado.CORREOS[-1]["message"]
    assert correo["toRecipients"][0]["emailAddress"]["address"] == config.PROFESOR["email"]

    assert len(graph_simulado.WEBHOOK) == antes_webhook + 1
    tarjeta = graph_simulado.WEBHOOK[-1]["attachments"][0]
    assert tarjeta["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert tarjeta["content"]["version"] == "1.4"
    assert tarjeta["content"]["actions"][0]["url"].endswith(f"/clase/{cid}")


def test_panel_pide_sesion_de_docente():
    with httpx.Client(base_url=BASE, follow_redirects=False) as c:
        r = c.get("/")
        assert r.status_code == 303 and r.headers["location"].startswith("/ingresar")
        assert c.get("/api/estado").status_code == 401
        c.post("/ingresar/prueba", data={"email": "alumno@cervantes.edu.ar"})
        assert c.get("/api/estado").status_code == 401, "un alumno no ve el panel"
        c.post("/ingresar/prueba", data={"email": config.PROFESOR["email"]})
        assert c.get("/api/estado").status_code == 200
        assert c.get("/").status_code == 200


def test_pantalla_del_aula_con_clave_de_kiosco():
    with httpx.Client(base_url=BASE, follow_redirects=False) as c:
        assert c.get("/aula/A-201").status_code == 303
        assert c.get("/aula/A-201?k=otra").status_code == 303
        assert c.get("/aula/A-201?k=kiosco-prueba").status_code == 200
        assert c.get("/api/aula/A-201").status_code == 200  # la clave queda en la sesión del kiosco


def test_paginas_de_teams_se_pueden_mostrar_dentro_de_teams():
    with httpx.Client(base_url=BASE) as c:
        for ruta in ("/teams/panel", "/teams/config", "/teams/clase?curso=PROG2-A", "/privacidad", "/terminos"):
            r = c.get(ruta)
            assert r.status_code == 200, ruta
            assert "https://teams.microsoft.com" in r.headers["content-security-policy"]
        assert "MicrosoftTeams.min.js" in c.get("/teams/panel").text
        assert c.post("/teams/sso", json={"token": ""}).json()["docente"] is True  # modo prueba
        r = c.get("/api/curso/PROG2-A/en-vivo")
        assert r.status_code == 200 and r.json()["docente"] is True
