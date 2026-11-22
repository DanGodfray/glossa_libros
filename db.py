"""Base de datos SQLite: crear la tabla, guardar, buscar y listar libros."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "inventario.db"

# Campos que vienen del formulario (todos menos id y creado)
CAMPOS = [
    "isbn", "titulo", "autor", "editorial", "lugar_edicion", "procedencia",
    "idioma", "anio", "edicion", "genero", "temas", "caracteristicas",
    "estado", "notas_estado", "precio", "stock", "ubicacion",
    "portada_url", "foto", "estado_ig", "fecha_ig",
]
NUMERICOS = {"anio", "precio", "stock"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS libros (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    isbn            TEXT,
    titulo          TEXT NOT NULL,
    autor           TEXT,
    editorial       TEXT,
    lugar_edicion   TEXT,
    procedencia     TEXT,
    idioma          TEXT,
    anio            INTEGER,
    edicion         TEXT,
    genero          TEXT,
    temas           TEXT,
    caracteristicas TEXT,
    estado          TEXT,
    notas_estado    TEXT,
    precio          INTEGER,
    stock           INTEGER DEFAULT 1,
    ubicacion       TEXT,
    portada_url     TEXT,
    foto            TEXT,
    estado_ig       TEXT DEFAULT 'Pendiente',
    fecha_ig        TEXT,
    creado          TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row  # filas accesibles como diccionario: fila["titulo"]
    return con


def iniciar():
    with conectar() as con:
        con.executescript(ESQUEMA)


def _limpiar(datos):
    """Deja solo los campos conocidos y convierte números."""
    limpio = {}
    for campo in CAMPOS:
        valor = (datos.get(campo) or "").strip()
        if campo in NUMERICOS:
            solo_digitos = "".join(c for c in valor if c.isdigit())
            valor = int(solo_digitos) if solo_digitos else None
        limpio[campo] = valor if valor != "" else None
    return limpio


def guardar(datos, libro_id=None):
    """Crea un libro nuevo o actualiza uno existente. Devuelve el id."""
    d = _limpiar(datos)
    with conectar() as con:
        if libro_id:
            asignaciones = ", ".join(f"{c} = ?" for c in CAMPOS)
            con.execute(f"UPDATE libros SET {asignaciones} WHERE id = ?",
                        [d[c] for c in CAMPOS] + [libro_id])
            return libro_id
        columnas = ", ".join(CAMPOS)
        marcas = ", ".join("?" for _ in CAMPOS)
        cur = con.execute(f"INSERT INTO libros ({columnas}) VALUES ({marcas})",
                          [d[c] for c in CAMPOS])
        return cur.lastrowid


def obtener(libro_id):
    with conectar() as con:
        return con.execute("SELECT * FROM libros WHERE id = ?", (libro_id,)).fetchone()


def buscar_por_isbn(isbn):
    with conectar() as con:
        return con.execute("SELECT * FROM libros WHERE isbn = ?", (isbn,)).fetchone()


def eliminar(libro_id):
    with conectar() as con:
        con.execute("DELETE FROM libros WHERE id = ?", (libro_id,))


def cambiar_estado_ig(libro_id, estado_ig, fecha_ig=None):
    with conectar() as con:
        con.execute("UPDATE libros SET estado_ig = ?, fecha_ig = COALESCE(?, fecha_ig) WHERE id = ?",
                    (estado_ig, fecha_ig, libro_id))


def listar(f):
    """Lista libros aplicando filtros. f es un diccionario (los parámetros de la URL)."""
    sql = "SELECT * FROM libros WHERE 1 = 1"
    params = []
    if f.get("q"):
        sql += " AND (titulo LIKE ? OR autor LIKE ? OR isbn LIKE ? OR editorial LIKE ?)"
        params += [f"%{f['q']}%"] * 4
    for campo in ("genero", "idioma", "estado", "estado_ig", "ubicacion"):
        if f.get(campo):
            sql += f" AND {campo} = ?"
            params.append(f[campo])
    if f.get("tema"):
        sql += " AND (',' || REPLACE(temas, ', ', ',') || ',') LIKE ?"
        params.append(f"%,{f['tema']},%")
    orden = {"titulo": "titulo", "autor": "autor", "anio": "anio",
             "precio": "precio DESC", "reciente": "id DESC"}.get(f.get("orden"), "id DESC")
    sql += f" ORDER BY {orden}"
    with conectar() as con:
        return con.execute(sql, params).fetchall()


def valores(campo):
    """Valores ya usados en un campo, para autocompletar (datalist)."""
    with conectar() as con:
        filas = con.execute(
            f"SELECT DISTINCT {campo} FROM libros WHERE {campo} IS NOT NULL ORDER BY {campo}"
        ).fetchall()
    if campo in ("temas", "caracteristicas"):  # separar listas "a, b, c"
        sueltos = {t.strip() for (texto,) in filas for t in texto.split(",") if t.strip()}
        return sorted(sueltos, key=str.lower)
    return [v for (v,) in filas]
