"""Persistencia en SQLite (un archivo, sin servidor)."""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from .config import DATA, TZ

RUTA = DATA / "asistencia.db"
_lock = threading.RLock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS alumnos (
    matricula TEXT NOT NULL,
    curso_id  TEXT NOT NULL,
    nombre    TEXT NOT NULL,
    email     TEXT,
    PRIMARY KEY (matricula, curso_id)
);
CREATE TABLE IF NOT EXISTS clases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    curso_id    TEXT NOT NULL,
    fecha       TEXT NOT NULL,          -- YYYY-MM-DD
    inicio      TEXT NOT NULL,          -- ISO datetime
    fin         TEXT NOT NULL,
    teams_url   TEXT,
    estado      TEXT NOT NULL DEFAULT 'programada',
    -- programada > en_curso > esperando_teams > cargando_cervantes > enviando_reporte > completada | error
    intentos    INTEGER NOT NULL DEFAULT 0,
    proximo_intento TEXT,
    detalle     TEXT,
    resumen     TEXT,                   -- JSON con totales
    UNIQUE (curso_id, inicio)
);
CREATE TABLE IF NOT EXISTS checkins (
    clase_id   INTEGER NOT NULL,
    matricula  TEXT NOT NULL,
    hora       TEXT NOT NULL,
    metodo     TEXT,                    -- gps | wifi
    distancia  REAL,
    PRIMARY KEY (clase_id, matricula)
);
CREATE TABLE IF NOT EXISTS registros (
    clase_id    INTEGER NOT NULL,
    matricula   TEXT NOT NULL,
    nombre      TEXT,
    estado      TEXT NOT NULL,          -- Presente | Ausente
    modalidad   TEXT,                   -- Presencial | Virtual | -
    minutos     REAL,
    pct_tiempo  REAL,
    pct_camara  REAL,
    entrada     TEXT,
    salida      TEXT,
    cargado     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (clase_id, matricula)
);
CREATE TABLE IF NOT EXISTS eventos (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    nivel    TEXT NOT NULL,
    clase_id INTEGER,
    mensaje  TEXT NOT NULL
);
"""


def ahora() -> datetime:
    return datetime.now(TZ)


@contextmanager
def conexion():
    with _lock:
        con = sqlite3.connect(RUTA, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()


def init():
    with conexion() as con:
        con.executescript(ESQUEMA)


def q(sql: str, params=()) -> list[dict]:
    with conexion() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def q1(sql: str, params=()) -> dict | None:
    filas = q(sql, params)
    return filas[0] if filas else None


def ejecutar(sql: str, params=()) -> int:
    with conexion() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid


def ejecutar_muchos(sql: str, filas: list[tuple]):
    with conexion() as con:
        con.executemany(sql, filas)


def log(mensaje: str, nivel: str = "info", clase_id: int | None = None):
    ejecutar(
        "INSERT INTO eventos (ts, nivel, clase_id, mensaje) VALUES (?,?,?,?)",
        (ahora().isoformat(timespec="seconds"), nivel, clase_id, mensaje),
    )
    print(f"[{nivel.upper():5}] {('#'+str(clase_id)+' ') if clase_id else ''}{mensaje}", flush=True)
