"""El reloj del sistema: nadie aprieta ningún botón.

Cada 30 s revisa el horario y avanza cada clase por su máquina de estados:

  programada ─▶ en_curso ─▶ esperando_teams ─▶ cargando_cervantes ─▶ enviando_reporte ─▶ completada
     (crea)     (QR activo)   (fin + espera)     (robot Playwright)     (correo)

Cada paso es idempotente y reintenta con espera creciente si algo falla
(portal caído, reporte de Teams todavía no generado, etc.).
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, consolidar, db, reporte, rpa_cervantes, teams

MAX_INTENTOS = 6
_pipeline = threading.Lock()  # un solo navegador a la vez
_ultimo_vinculo: datetime | None = None


def espera_post_clase() -> timedelta:
    # En demo no esperamos: el reporte "de Teams" se simula al instante.
    return timedelta(seconds=5) if config.DEMO else timedelta(minutes=config.REGLAS["minutos_espera_tras_clase"])


def planificar(hoy: datetime):
    """Crea las clases del día según el horario de cada curso."""
    dia = config.DIAS[hoy.weekday()]
    for curso in config.CURSOS.values():
        for h in curso["horarios"]:
            if h["dia"] != dia:
                continue
            ini = hoy.replace(hour=int(h["inicio"][:2]), minute=int(h["inicio"][3:]), second=0, microsecond=0)
            fin = hoy.replace(hour=int(h["fin"][:2]), minute=int(h["fin"][3:]), second=0, microsecond=0)
            if fin > hoy:  # no se inventan clases que ya pasaron sin el sistema encendido
                crear_clase(curso["id"], ini, fin)


def crear_clase(curso_id: str, ini: datetime, fin: datetime) -> int:
    existente = db.q1("SELECT id FROM clases WHERE curso_id=? AND inicio=?", (curso_id, ini.isoformat()))
    if existente:
        return existente["id"]
    cid = db.ejecutar("INSERT INTO clases (curso_id, fecha, inicio, fin) VALUES (?,?,?,?)",
                      (curso_id, ini.date().isoformat(), ini.isoformat(), fin.isoformat()))
    db.log(f"Clase programada {config.CURSOS[curso_id]['materia']} {ini:%H:%M}-{fin:%H:%M}", clase_id=cid)
    return cid


def tick():
    global _ultimo_vinculo
    try:
        ahora = db.ahora()
        planificar(ahora)
        if not config.DEMO and (_ultimo_vinculo is None or ahora - _ultimo_vinculo > timedelta(minutes=10)):
            teams.vincular_reuniones(ahora)
            _ultimo_vinculo = ahora

        tolerancia = timedelta(minutes=config.REGLAS["checkin_tolerancia_minutos_antes"])
        for c in db.q("SELECT * FROM clases WHERE estado NOT IN ('completada','error')"):
            ini, fin = datetime.fromisoformat(c["inicio"]), datetime.fromisoformat(c["fin"])
            if c["estado"] == "programada" and ahora >= ini - tolerancia:
                _estado(c["id"], "en_curso", "QR activo en el aula")
            elif c["estado"] in ("programada", "en_curso") and ahora >= fin:
                _estado(c["id"], "esperando_teams", "Clase finalizada, esperando reporte de Teams")
            elif c["estado"] in ("esperando_teams", "cargando_cervantes", "enviando_reporte") \
                    and ahora >= fin + espera_post_clase() \
                    and (not c["proximo_intento"] or ahora >= datetime.fromisoformat(c["proximo_intento"])):
                threading.Thread(target=procesar, args=(c["id"],), daemon=True).start()
    except Exception:  # noqa: BLE001 — el reloj nunca se detiene
        db.log("Error en el ciclo del orquestador:\n" + traceback.format_exc(limit=3), "error")


def procesar(clase_id: int):
    if not _pipeline.acquire(blocking=False):
        return
    try:
        clase = db.q1("SELECT * FROM clases WHERE id=?", (clase_id,))
        try:
            if clase["estado"] == "esperando_teams":
                virtuales = teams.asistencia_virtual(clase)
                res = consolidar.consolidar(clase, virtuales)
                db.log(f"Consolidado: {res['presenciales']} presenciales, {res['virtuales']} virtuales, "
                       f"{res['ausentes']} ausentes", clase_id=clase_id)
                if res["no_en_padron"]:
                    db.log(f"En Teams pero no en el padrón: {', '.join(res['no_en_padron'])}", "warn", clase_id)
                clase = _estado(clase_id, "cargando_cervantes", "Robot iniciando sesión en Autogestión Cervantes")

            if clase["estado"] == "cargando_cervantes":
                r = rpa_cervantes.cargar(clase)
                db.ejecutar("UPDATE registros SET cargado=1 WHERE clase_id=?", (clase_id,))
                faltan = {m for m, _ in r["padron"]} ^ {x["matricula"] for x in
                                                        db.q("SELECT matricula FROM registros WHERE clase_id=?", (clase_id,))}
                if faltan:
                    db.log(f"Diferencias padrón local vs Cervantes: {sorted(faltan)}", "warn", clase_id)
                clase = _estado(clase_id, "enviando_reporte",
                                f"Cervantes actualizado: {r['presentes']} presentes, {r['ausentes']} ausentes")

            if clase["estado"] == "enviando_reporte":
                via = reporte.enviar(clase)
                _estado(clase_id, "completada", f"Reporte enviado al profesor vía {via}")
        except Exception as e:  # noqa: BLE001
            intentos = clase["intentos"] + 1
            if intentos >= MAX_INTENTOS:
                db.ejecutar("UPDATE clases SET estado='error', intentos=?, detalle=? WHERE id=?",
                            (intentos, str(e)[:500], clase_id))
                db.log(f"Falló definitivamente en «{clase['estado']}»: {e}", "error", clase_id)
            else:
                espera = timedelta(minutes=2 ** intentos) if not config.DEMO else timedelta(seconds=20)
                db.ejecutar("UPDATE clases SET intentos=?, detalle=?, proximo_intento=? WHERE id=?",
                            (intentos, str(e)[:500], (db.ahora() + espera).isoformat(), clase_id))
                db.log(f"Error en «{clase['estado']}» (intento {intentos}/{MAX_INTENTOS}), reintento automático: {e}",
                       "warn", clase_id)
    finally:
        _pipeline.release()


def _estado(clase_id: int, estado: str, detalle: str) -> dict:
    db.ejecutar("UPDATE clases SET estado=?, detalle=?, intentos=0, proximo_intento=NULL WHERE id=?",
                (estado, detalle, clase_id))
    db.log(detalle, clase_id=clase_id)
    return db.q1("SELECT * FROM clases WHERE id=?", (clase_id,))


_scheduler: BackgroundScheduler | None = None


def iniciar():
    global _scheduler
    _scheduler = BackgroundScheduler(timezone=config.TZ)
    _scheduler.add_job(tick, "interval", seconds=10 if config.DEMO else 30, next_run_time=db.ahora(),
                       max_instances=1, coalesce=True)
    _scheduler.start()


def detener():
    if _scheduler:
        _scheduler.shutdown(wait=False)
