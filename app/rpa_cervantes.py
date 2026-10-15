"""«Profesor robot»: carga la asistencia en Autogestión Cervantes con Playwright.

Todos los selectores viven en config/cervantes_selectores.json, así el
mismo robot sirve para el portal real y para el simulado de la demo.
"""
from __future__ import annotations

from datetime import datetime

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import config, db

MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def cargar(clase: dict) -> dict:
    """Marca Presente/Ausente para cada alumno de la tabla y guarda.

    Devuelve {marcados, presentes, ausentes, padron: [(matricula, nombre)]}.
    """
    S = config.SELECTORES
    curso = config.CURSOS[clase["curso_id"]]
    fecha = datetime.fromisoformat(clase["inicio"])
    presentes = {r["matricula"] for r in db.q(
        "SELECT matricula FROM registros WHERE clase_id=? AND estado='Presente'", (clase["id"],))}

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=config.RPA_HEADLESS,
            slow_mo=config.RPA_SLOW_MO_MS,
            channel=config.RPA_BROWSER_CHANNEL,
        )
        pagina = navegador.new_page(viewport={"width": 1100, "height": 760})
        pagina.set_default_timeout(20_000)
        try:
            # 1. Login
            pagina.goto(config.CERVANTES_URL)
            pagina.fill(S["login"]["usuario"], config.CERVANTES_USUARIO)
            pagina.fill(S["login"]["clave"], config.CERVANTES_CLAVE)
            pagina.click(S["login"]["boton"])
            pagina.wait_for_selector(S["login"]["exito"])

            # 2. Cátedra > Asistencia de alumnos
            for paso in S["navegacion"]:
                pagina.click(paso)

            # 3. Filtros: materia, división, mes, fecha
            f = S["filtros"]
            pagina.select_option(f["materia"], label=curso["materia"])
            pagina.select_option(f["division"], label=curso["division"])
            pagina.select_option(f["mes"], label=MESES[fecha.month - 1])
            pagina.select_option(f["fecha"], label=fecha.strftime("%d/%m/%Y"))
            pagina.click(f["buscar"])
            pagina.wait_for_selector(S["tabla"]["filas"])

            # 4-5. Recorrer la tabla usando la matrícula como clave
            t = S["tabla"]
            filas = pagina.locator(t["filas"])
            padron, marcados, n_p = [], 0, 0
            for i in range(filas.count()):
                fila = filas.nth(i)
                celdas = fila.locator("td")
                matricula = celdas.nth(t["columna_matricula"]).inner_text().strip()
                nombre = celdas.nth(t["columna_nombre"]).inner_text().strip()
                padron.append((matricula, nombre))
                presente = matricula in presentes
                fila.locator(t["radio_presente"] if presente else t["radio_ausente"]).check()
                marcados += 1
                n_p += presente

            # 6. Guardar
            pagina.click(S["guardar"])
            pagina.wait_for_selector(S["confirmacion"])
            pagina.screenshot(path=str(config.CAPTURAS / f"clase_{clase['id']}_ok.png"), full_page=True)
            return {"marcados": marcados, "presentes": n_p, "ausentes": marcados - n_p, "padron": padron}
        except PWTimeout as e:
            pagina.screenshot(path=str(config.CAPTURAS / f"clase_{clase['id']}_error.png"), full_page=True)
            raise RuntimeError(f"El portal Cervantes no respondió como se esperaba: {e}") from e
        finally:
            navegador.close()
