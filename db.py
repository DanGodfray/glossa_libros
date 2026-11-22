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

CREATE TABLE IF NOT EXISTS ventas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    libro_id    INTEGER,
    titulo      TEXT,          -- copia del título, por si después se elimina el libro
    fecha       TEXT NOT NULL,
    cantidad    INTEGER NOT NULL DEFAULT 1,
    precio      INTEGER,       -- precio unitario de venta
    comprador   TEXT,
    notas       TEXT,
    creado      TEXT DEFAULT (datetime('now', 'localtime'))
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


def entero(texto):
    digitos = "".join(c for c in str(texto or "") if c.isdigit())
    return int(digitos) if digitos else None


# Cada libro con la fecha de su último movimiento (última venta o, si nunca se vendió, su ingreso)
# y los días que lleva sin moverse: sirve para detectar el stock muerto.
LIBROS_CON_MOVIMIENTO = """
SELECT *, CAST(julianday('now', 'localtime') - julianday(ultimo_mov) AS INTEGER) AS dias_sin_mov
FROM (
    SELECT l.*, MAX(date(l.creado), COALESCE(
        (SELECT MAX(v.fecha) FROM ventas v WHERE v.libro_id = l.id), date(l.creado))) AS ultimo_mov
    FROM libros l
)
"""


def listar(f):
    """Lista libros aplicando filtros. f es un diccionario (los parámetros de la URL)."""
    sql = f"SELECT * FROM ({LIBROS_CON_MOVIMIENTO}) WHERE 1 = 1"
    params = []
    meses = entero(f.get("sin_mov"))
    if meses:  # stock muerto: con stock y sin ventas ni ingreso en los últimos N meses
        sql += " AND COALESCE(stock, 0) > 0 AND ultimo_mov <= date('now', 'localtime', ?)"
        params.append(f"-{meses} months")
    if f.get("q"):
        sql += " AND (titulo LIKE ? OR autor LIKE ? OR isbn LIKE ? OR editorial LIKE ?)"
        params += [f"%{f['q']}%"] * 4
    for campo in ("genero", "idioma", "estado", "estado_ig", "ubicacion", "procedencia"):
        if f.get(campo):
            sql += f" AND {campo} = ?"
            params.append(f[campo])
    for clave, operador in (("precio_min", ">="), ("precio_max", "<=")):
        valor = entero(f.get(clave))
        if valor is not None:
            sql += f" AND precio {operador} ?"
            params.append(valor)
    if f.get("disponibilidad") == "stock":
        sql += " AND COALESCE(stock, 0) > 0"
    elif f.get("disponibilidad") == "agotados":
        sql += " AND COALESCE(stock, 0) <= 0"
    if f.get("tema"):
        sql += " AND (',' || REPLACE(temas, ', ', ',') || ',') LIKE ?"
        params.append(f"%,{f['tema']},%")
    orden = {"titulo": "titulo", "autor": "autor", "anio": "anio",
             "precio": "precio DESC", "reciente": "id DESC",
             "sin_mov": "ultimo_mov ASC, id ASC"}.get(f.get("orden"), "id DESC")
    sql += f" ORDER BY {orden}"
    with conectar() as con:
        return con.execute(sql, params).fetchall()


# ---------- Ventas ----------

def registrar_venta(libro_id, cantidad, precio, fecha, comprador="", notas=""):
    """Registra la venta y descuenta el stock. Si se agota, el libro pasa a 'Vendido'.
    Lanza ValueError si la cantidad no es válida para el stock disponible."""
    with conectar() as con:
        libro = con.execute("SELECT * FROM libros WHERE id = ?", (libro_id,)).fetchone()
        stock = (libro["stock"] or 0) if libro else 0
        if not libro or cantidad < 1 or cantidad > stock:
            raise ValueError("cantidad no válida")
        con.execute("INSERT INTO ventas (libro_id, titulo, fecha, cantidad, precio, comprador, notas) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (libro_id, libro["titulo"], fecha, cantidad, precio, comprador or None, notas or None))
        nuevo_stock = stock - cantidad
        con.execute("UPDATE libros SET stock = ?, estado_ig = CASE WHEN ? = 0 THEN 'Vendido' ELSE estado_ig END "
                    "WHERE id = ?", (nuevo_stock, nuevo_stock, libro_id))


def anular_venta(venta_id):
    """Borra la venta y devuelve los ejemplares al stock. Devuelve el id del libro (o None)."""
    with conectar() as con:
        venta = con.execute("SELECT * FROM ventas WHERE id = ?", (venta_id,)).fetchone()
        if not venta:
            return None
        con.execute("DELETE FROM ventas WHERE id = ?", (venta_id,))
        # Si estaba 'Vendido', vuelve a 'Publicado' si alguna vez se posteó, si no a 'Pendiente'
        con.execute("UPDATE libros SET stock = COALESCE(stock, 0) + ?, estado_ig = CASE "
                    "WHEN estado_ig = 'Vendido' THEN CASE WHEN fecha_ig IS NOT NULL THEN 'Publicado' "
                    "ELSE 'Pendiente' END ELSE estado_ig END WHERE id = ?",
                    (venta["cantidad"], venta["libro_id"]))
        return venta["libro_id"]


def ventas_de(libro_id):
    with conectar() as con:
        return con.execute("SELECT * FROM ventas WHERE libro_id = ? ORDER BY fecha DESC, id DESC",
                           (libro_id,)).fetchall()


def listar_ventas():
    """Todas las ventas, con 'existe' = 1 si el libro sigue en el inventario."""
    with conectar() as con:
        return con.execute("SELECT v.*, l.id IS NOT NULL AS existe FROM ventas v "
                           "LEFT JOIN libros l ON l.id = v.libro_id ORDER BY v.fecha DESC, v.id DESC").fetchall()


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
