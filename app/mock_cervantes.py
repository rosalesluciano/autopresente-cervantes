"""Réplica simplificada de «Autogestión Cervantes» para la demo.

Imita el flujo real (login → Cátedra → Asistencia de alumnos → filtros →
tabla con radios P/A → Guardar) con estética de sistema viejo, a propósito.
"""
from __future__ import annotations

import html
import json
from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import config, db

router = APIRouter(prefix="/cervantes")

MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre"]

db.init()
db.ejecutar("""CREATE TABLE IF NOT EXISTS cervantes_asistencia (
    materia TEXT, division TEXT, fecha TEXT, matricula TEXT, estado TEXT,
    PRIMARY KEY (materia, division, fecha, matricula))""")

CSS = """
body{font-family:Tahoma,Verdana,sans-serif;font-size:12px;background:#d4d0c8;margin:0;color:#000}
.top{background:linear-gradient(#6a7fa8,#3b5080);color:#fff;padding:8px 12px;font-weight:bold;font-size:14px}
.top small{font-weight:normal;font-size:11px;opacity:.85}
.menu{background:#ece9d8;border-bottom:1px solid #999;padding:4px 8px}
.menu a{color:#003399;margin-right:14px;text-decoration:underline}
.caja{background:#fff;border:1px solid #808080;margin:14px;padding:12px;max-width:820px}
h2{font-size:13px;background:#ece9d8;margin:-12px -12px 10px;padding:6px 12px;border-bottom:1px solid #808080}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #a0a0a0;padding:3px 6px}
th{background:#ece9d8;text-align:left}tr:nth-child(even) td{background:#f4f4f4}
button{font-family:inherit;font-size:12px;padding:2px 12px}
.ok{background:#e8ffe8;border:1px solid #2e8b2e;padding:6px;margin-bottom:8px;color:#185c18;font-weight:bold}
label{display:inline-block;width:70px}select,input{font-family:inherit;font-size:12px;margin:2px 0}
"""


def _pagina(cuerpo: str, menu: bool = True) -> HTMLResponse:
    nav = (
        '<div class="menu"><a href="/cervantes/inicio">Inicio</a><a href="/cervantes/catedra">Cátedra</a>'
        '<a href="#">Mensajes</a><a href="/cervantes">Salir</a></div>' if menu else ""
    )
    return HTMLResponse(
        f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Autogestión Cervantes</title>"
        f"<style>{CSS}</style></head><body><div class='top'>Autogestión Cervantes "
        f"<small>v2.3.1 — Módulo Docente</small></div>{nav}{cuerpo}</body></html>"
    )


@router.get("", response_class=HTMLResponse)
def login_form():
    return _pagina(
        """<div class="caja" style="max-width:320px"><h2>Ingreso al sistema</h2>
        <form method="post" action="/cervantes/login">
        <label>Usuario</label><input name="usuario"><br>
        <label>Clave</label><input name="clave" type="password"><br><br>
        <button type="submit">Ingresar</button></form></div>""",
        menu=False,
    )


@router.post("/login")
def login(usuario: str = Form(...), clave: str = Form(...)):
    if usuario != config.CERVANTES_USUARIO or clave != config.CERVANTES_CLAVE:
        return _pagina('<div class="caja">Usuario o clave incorrectos. <a href="/cervantes">Volver</a></div>', False)
    return RedirectResponse("/cervantes/inicio", status_code=303)


@router.get("/inicio", response_class=HTMLResponse)
def inicio():
    return _pagina('<div class="caja"><h2>Bienvenido/a</h2>Use el menú superior '
                   'para gestionar sus materias.</div>')


@router.get("/catedra", response_class=HTMLResponse)
def catedra():
    return _pagina('<div class="caja"><h2>Cátedra</h2><ul>'
                   '<li><a href="/cervantes/asistencia">Asistencia de alumnos</a></li>'
                   '<li><a href="#">Carga de notas</a></li><li><a href="#">Programa de la materia</a></li>'
                   '</ul></div>')


@router.get("/asistencia", response_class=HTMLResponse)
def asistencia(request: Request, materia: str = "", division: str = "", mes: str = "", fecha: str = "",
               guardado: int = 0):
    materias = sorted({c["materia"] for c in config.CURSOS.values()})
    divisiones = sorted({c["division"] for c in config.CURSOS.values()})
    hoy = date.today()

    def opciones(valores, sel):
        return "<option value=''>-- Seleccione --</option>" + "".join(
            f"<option{' selected' if v == sel else ''}>{html.escape(v)}</option>" for v in valores)

    form = f"""<div class="caja"><h2>Asistencia de alumnos</h2>
    <form method="get" action="/cervantes/asistencia">
      <label>Materia</label><select name="materia">{opciones(materias, materia)}</select><br>
      <label>División</label><select name="division">{opciones(divisiones, division)}</select><br>
      <label>Mes</label><select name="mes" onchange="dias()">{opciones(MESES, mes)}</select><br>
      <label>Fecha</label><select name="fecha"><option value=''>-- Seleccione --</option></select><br><br>
      <button id="buscar" type="submit">Buscar</button>
    </form></div>
    <script>
    function dias(){{
      var m=document.querySelector('[name=mes]').selectedIndex, s=document.querySelector('[name=fecha]');
      s.innerHTML="<option value=''>-- Seleccione --</option>"; if(!m) return;
      var y={hoy.year}, n=new Date(y,m,0).getDate();
      for(var d=1;d<=n;d++){{var t=('0'+d).slice(-2)+'/'+('0'+m).slice(-2)+'/'+y;
        var o=document.createElement('option');o.text=t;if(t=={json.dumps(fecha).replace('<', '\\u003c')})o.selected=true;s.add(o);}}
    }} dias();
    </script>"""

    tabla = ""
    curso = next((c for c in config.CURSOS.values() if c["materia"] == materia and c["division"] == division), None)
    if curso and fecha:
        alumnos = db.q("SELECT matricula, nombre FROM alumnos WHERE curso_id=? ORDER BY nombre", (curso["id"],))
        previos = {r["matricula"]: r["estado"] for r in db.q(
            "SELECT matricula, estado FROM cervantes_asistencia WHERE materia=? AND division=? AND fecha=?",
            (materia, division, fecha))}
        filas = "".join(
            f"<tr><td>{a['matricula']}</td><td>{html.escape(a['nombre'])}</td>"
            f"<td><input type='radio' name='a_{a['matricula']}' value='P'"
            f"{' checked' if previos.get(a['matricula']) == 'P' else ''}> Presente</td>"
            f"<td><input type='radio' name='a_{a['matricula']}' value='A'"
            f"{' checked' if previos.get(a['matricula']) == 'A' else ''}> Ausente</td></tr>"
            for a in alumnos)
        ok = '<div class="ok">Asistencia guardada correctamente.</div>' if guardado else ""
        tabla = f"""<div class="caja"><h2>{html.escape(materia)} — División {html.escape(division)} — {html.escape(fecha)}</h2>{ok}
        <form method="post" action="/cervantes/guardar">
        <input type="hidden" name="materia" value="{html.escape(materia)}">
        <input type="hidden" name="division" value="{html.escape(division)}">
        <input type="hidden" name="mes" value="{html.escape(mes)}">
        <input type="hidden" name="fecha" value="{html.escape(fecha)}">
        <table id="tabla-asistencia"><thead><tr><th>Matrícula</th><th>Apellido y nombre</th><th></th><th></th></tr></thead>
        <tbody>{filas}</tbody></table><br><button id="guardar" type="submit">Guardar</button></form></div>"""
    return _pagina(form + tabla)


@router.post("/guardar")
async def guardar(request: Request):
    datos = await request.form()
    materia, division, fecha = datos["materia"], datos["division"], datos["fecha"]
    filas = [(materia, division, fecha, k[2:], v) for k, v in datos.items() if k.startswith("a_")]
    db.ejecutar_muchos("INSERT OR REPLACE INTO cervantes_asistencia VALUES (?,?,?,?,?)", filas)
    from urllib.parse import urlencode
    qs = urlencode({"materia": materia, "division": division, "mes": datos["mes"], "fecha": fecha, "guardado": 1})
    return RedirectResponse(f"/cervantes/asistencia?{qs}", status_code=303)
