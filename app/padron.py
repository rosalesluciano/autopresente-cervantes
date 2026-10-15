"""Padrón de alumnos por curso.

Producción: un CSV por curso en config/padrones/<CURSO_ID>.csv con columnas
matricula;nombre;email (export del SIU/Cervantes, se hace una vez por cuatrimestre).
Demo: si no hay CSV se genera un padrón ficticio.
"""
import csv
import random
import unicodedata

from . import config, db

NOMBRES = ["Ana", "Carlos", "Lucía", "Mateo", "Valentina", "Santiago", "Camila", "Benjamín", "Martina",
           "Thiago", "Sofía", "Joaquín", "Julieta", "Tomás", "Florencia", "Agustín", "Milagros", "Facundo",
           "Rocío", "Nicolás", "Abril", "Lautaro", "Micaela", "Franco", "Delfina", "Ignacio", "Paula", "Bruno"]
APELLIDOS = ["Gómez", "López", "Fernández", "Rodríguez", "Pérez", "Martínez", "García", "Sosa", "Romero",
             "Díaz", "Álvarez", "Torres", "Ruiz", "Ramírez", "Benítez", "Acosta", "Medina", "Herrera",
             "Suárez", "Aguirre", "Giménez", "Molina", "Castro", "Ortiz", "Silva", "Luna", "Rojas", "Vera"]


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def cargar():
    carpeta = config.RAIZ / "config" / "padrones"
    for curso_id in config.CURSOS:
        archivo = carpeta / f"{curso_id}.csv"
        if archivo.exists():
            with open(archivo, encoding="utf-8-sig") as f:
                filas = [(r["matricula"].strip(), curso_id, r["nombre"].strip(), r["email"].strip().lower())
                         for r in csv.DictReader(f, delimiter=";")]
        elif db.q1("SELECT 1 FROM alumnos WHERE curso_id=?", (curso_id,)):
            continue
        else:
            filas = _ficticio(curso_id)
        db.ejecutar_muchos("INSERT OR REPLACE INTO alumnos (matricula, curso_id, nombre, email) VALUES (?,?,?,?)",
                           filas)
        db.log(f"Padrón {curso_id}: {len(filas)} alumnos")


def _ficticio(curso_id: str, n: int = 30) -> list[tuple]:
    rnd = random.Random(curso_id)
    usados, filas = set(), []
    dominio = config.MS_DOMINIO_ALUMNOS or "universidad.edu.ar"
    while len(filas) < n:
        nom, ape = rnd.choice(NOMBRES), rnd.choice(APELLIDOS)
        if (nom, ape) in usados:
            continue
        usados.add((nom, ape))
        mat = str(rnd.randint(40000, 49999))
        filas.append((mat, curso_id, f"{ape}, {nom}", f"{_ascii(nom)}.{_ascii(ape)}@{dominio}"))
    return filas
