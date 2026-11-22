"""Respaldo de la base de datos y las fotos en un zip.

Usa la copia en caliente de SQLite (Connection.backup), así que funciona aunque la app esté abierta.
Desde la consola:  python respaldo.py   -> crea respaldos/glossalibros_AAAA-MM-DD_HHMM.zip
"""
import contextlib
import datetime
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

import db

RAIZ = Path(__file__).parent
CARPETA_RESPALDOS = RAIZ / "respaldos"
CONSERVAR = 10  # respaldos que se guardan en la carpeta; los más antiguos se borran


def escribir_respaldo(destino, carpeta_fotos, db_path=None):
    """Escribe el zip en `destino` (ruta o archivo abierto): inventario.db + static/fotos/..."""
    db_path = Path(db_path or db.DB_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "inventario.db"
        with contextlib.closing(sqlite3.connect(db_path)) as origen, \
                contextlib.closing(sqlite3.connect(copia)) as destino_db:
            origen.backup(destino_db)
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(copia, "inventario.db")
            carpeta_fotos = Path(carpeta_fotos)
            if carpeta_fotos.is_dir():
                for archivo in sorted(carpeta_fotos.rglob("*")):
                    if archivo.is_file():
                        z.write(archivo, "static/fotos/" + archivo.relative_to(carpeta_fotos).as_posix())


def crear_respaldo(carpeta_fotos, carpeta=CARPETA_RESPALDOS, conservar=CONSERVAR):
    """Crea un respaldo con fecha en `carpeta` y deja solo los `conservar` más recientes."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    archivo = carpeta / f"glossalibros_{marca}.zip"
    escribir_respaldo(archivo, carpeta_fotos)
    for viejo in sorted(carpeta.glob("glossalibros_*.zip"))[:-conservar]:
        viejo.unlink(missing_ok=True)
    return archivo


if __name__ == "__main__":
    import app  # noqa: E402  (para usar la misma carpeta de fotos que la app)

    if not Path(db.DB_PATH).exists():
        sys.exit(f"No hay base de datos en {db.DB_PATH}")
    print("Respaldo creado:", crear_respaldo(app.CARPETA_FOTOS).resolve())
