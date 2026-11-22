"""Base de datos SQLite: esquema con migraciones, libros, ventas y consultas del inventario."""
import datetime
import os
import re
import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

# La ruta se puede cambiar con GLOSSA_DB (pruebas, servidores); por defecto, junto a la app
DB_PATH = Path(os.environ.get("GLOSSA_DB") or Path(__file__).parent / "inventario.db")

# Campos de la ficha que se guardan tal cual (todos menos id, creado y modificado)
CAMPOS = [
    "isbn", "titulo", "autor", "editorial", "lugar_edicion", "procedencia",
    "idioma", "anio", "fecha_edicion", "edicion", "genero", "temas", "caracteristicas",
    "estado", "notas_estado", "precio", "stock", "ubicacion",
    "portada_url", "portada_local", "foto", "estado_ig", "fecha_ig",
]
DIAS_STOCK_MUERTO = 180  # desde cuántos días sin movimiento un libro cuenta como stock muerto
AGRUPABLES = {"temas", "genero", "idioma", "ubicacion", "procedencia", "estado", "estado_ig"}

# Cada cambio de esquema es una migración nueva al final de la lista; nunca se edita una ya publicada.
# PRAGMA user_version guarda cuántas se aplicaron. Una base de la v0.2 tiene versión 0.
MIGRACIONES = [
    # 1: esquema de la v0.2 (en una base de la v0.2 no cambia nada)
    """
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
        titulo      TEXT,
        fecha       TEXT NOT NULL,
        cantidad    INTEGER NOT NULL DEFAULT 1,
        precio      INTEGER,
        comprador   TEXT,
        notas       TEXT,
        creado      TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """,
    # 2 (v0.3): fecha de edición como texto, portada guardada en la app, control de cambios e índices
    """
    ALTER TABLE libros ADD COLUMN fecha_edicion TEXT;
    ALTER TABLE libros ADD COLUMN portada_local TEXT;
    ALTER TABLE libros ADD COLUMN modificado TEXT;
    UPDATE libros SET fecha_edicion = CAST(anio AS TEXT) WHERE anio IS NOT NULL;
    UPDATE libros SET modificado = creado;
    CREATE INDEX IF NOT EXISTS idx_libros_isbn ON libros (isbn);
    CREATE INDEX IF NOT EXISTS idx_ventas_libro ON ventas (libro_id);
    """,
]


def conectar():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row  # filas accesibles como diccionario: fila["titulo"]
    return con


def iniciar():
    """Crea la base si no existe y aplica las migraciones pendientes, cada una en una transacción."""
    con = conectar()
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        for numero in range(version + 1, len(MIGRACIONES) + 1):
            try:
                con.executescript(f"BEGIN;\n{MIGRACIONES[numero - 1]}\nPRAGMA user_version = {numero};\nCOMMIT;")
            except sqlite3.Error:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                raise
    finally:
        con.close()


def ahora():
    """Marca de tiempo con microsegundos: sirve para detectar si una ficha cambió mientras se editaba."""
    return datetime.datetime.now().isoformat(sep=" ", timespec="microseconds")


# ---------- Conversión de valores ----------

def entero(texto):
    """Solo dígitos: para cantidades y stock ('3 unidades' -> 3)."""
    digitos = "".join(c for c in str(texto or "") if c.isdigit())
    return int(digitos) if digitos else None


def parsear_clp(texto):
    """Precio en pesos: '$15.000' -> 15000, '15000,50' -> 15001, '15,000' -> 15000.
    Una coma o punto seguido de 1-2 dígitos al final se toma como decimales y se redondea."""
    s = re.sub(r"[^\d.,]", "", str(texto or ""))
    if not re.search(r"\d", s):
        return None
    m = re.fullmatch(r"(\d[\d.,]*?)[.,](\d{1,2})", s)
    parte_entera, decimales = (m.group(1), m.group(2)) if m else (s, "")
    valor = Decimal(re.sub(r"\D", "", parte_entera) or "0")
    if decimales:
        valor += Decimal(decimales) / (10 ** len(decimales))
    return int(valor.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def anio_de(texto):
    """Primer año de 4 dígitos de una fecha libre ('c. 1850-1860' -> 1850, '[18--]' -> None)."""
    m = re.search(r"\d{4}", str(texto or ""))
    return int(m.group()) if m else None


def estado_coherente(stock, estado_ig, fecha_ig):
    """Un libro con stock no puede quedar 'Vendido': vuelve a 'Publicado' si se posteó, si no a 'Pendiente'."""
    if (stock or 0) > 0 and estado_ig == "Vendido":
        return "Publicado" if fecha_ig else "Pendiente"
    return estado_ig


def _limpiar(datos):
    """Deja solo los campos conocidos, convierte números y deriva el año."""
    limpio = {}
    for campo in CAMPOS:
        valor = str(datos.get(campo) if datos.get(campo) is not None else "").strip()
        if campo == "precio":
            valor = parsear_clp(valor)
        elif campo == "stock":
            valor = entero(valor)
        limpio[campo] = valor if valor != "" else None
    # La fecha de edición se guarda tal como se escribe; el año (para ordenar) sale de ella
    if not limpio["fecha_edicion"] and limpio["anio"]:
        limpio["fecha_edicion"] = limpio["anio"]
    limpio["anio"] = anio_de(limpio["fecha_edicion"])
    limpio["estado_ig"] = estado_coherente(limpio["stock"], limpio["estado_ig"], limpio["fecha_ig"])
    return limpio


# ---------- Libros ----------

def guardar(datos, libro_id=None, stock_original=None):
    """Crea un libro o actualiza uno existente. Devuelve el id.

    Al editar, si se pasa stock_original (el stock que mostraba el formulario), el stock se aplica
    como diferencia sobre el valor actual: así una venta hecha en otro teléfono no se pisa."""
    d = _limpiar(datos)
    with conectar() as con:
        if libro_id:
            if stock_original is not None:
                fila = con.execute("SELECT stock FROM libros WHERE id = ?", (libro_id,)).fetchone()
                if fila:
                    d["stock"] = max(0, (fila["stock"] or 0) + (d["stock"] or 0) - stock_original)
                    d["estado_ig"] = estado_coherente(d["stock"], d["estado_ig"], d["fecha_ig"])
            asignaciones = ", ".join(f"{c} = ?" for c in CAMPOS)
            con.execute(f"UPDATE libros SET {asignaciones}, modificado = ? WHERE id = ?",
                        [d[c] for c in CAMPOS] + [ahora(), libro_id])
            return libro_id
        columnas = ", ".join(CAMPOS)
        marcas = ", ".join("?" for _ in CAMPOS)
        cur = con.execute(f"INSERT INTO libros ({columnas}, modificado) VALUES ({marcas}, ?)",
                          [d[c] for c in CAMPOS] + [ahora()])
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


def portada_en_uso(nombre):
    """True si algún libro usa esa portada descargada (varios libros pueden compartirla)."""
    with conectar() as con:
        return con.execute("SELECT 1 FROM libros WHERE portada_local = ? LIMIT 1", (nombre,)).fetchone() is not None


def cambiar_estado_ig(libro_id, estado_ig, fecha_ig=None):
    with conectar() as con:
        libro = con.execute("SELECT stock, fecha_ig FROM libros WHERE id = ?", (libro_id,)).fetchone()
        if not libro:
            return
        estado = estado_coherente(libro["stock"], estado_ig, fecha_ig or libro["fecha_ig"])
        con.execute("UPDATE libros SET estado_ig = ?, fecha_ig = COALESCE(?, fecha_ig), modificado = ? "
                    "WHERE id = ?", (estado, fecha_ig, ahora(), libro_id))


# ---------- Inventario: filtros, páginas y resumen ----------

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


def _filtros_sql(f):
    """Arma el WHERE del inventario a partir de los filtros (parámetros de la URL)."""
    sql = " WHERE 1 = 1"
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
        valor = parsear_clp(f.get(clave))
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
    return sql, params


def listar(f, limite=None, desde=0):
    """Lista libros con filtros. Con `limite`, devuelve solo una página.
    Si se agrupa, se ordena primero por ese campo para que cada grupo quede junto entre páginas."""
    where, params = _filtros_sql(f)
    orden = {"titulo": "titulo", "autor": "autor", "anio": "anio",
             "precio": "precio DESC", "reciente": "id DESC",
             "sin_mov": "ultimo_mov ASC, id ASC"}.get(f.get("orden"), "id DESC")
    if f.get("agrupar") in AGRUPABLES:
        orden = f"COALESCE({f['agrupar']}, '') COLLATE NOCASE, {orden}"
    sql = f"SELECT * FROM ({LIBROS_CON_MOVIMIENTO}){where} ORDER BY {orden}"
    if limite:
        sql += " LIMIT ? OFFSET ?"
        params = params + [limite, desde]
    with conectar() as con:
        return con.execute(sql, params).fetchall()


def resumen(f):
    """Totales de todos los libros filtrados (no solo de la página que se ve)."""
    where, params = _filtros_sql(f)
    muerto = f"COALESCE(stock, 0) > 0 AND dias_sin_mov >= {int(DIAS_STOCK_MUERTO)}"
    sql = f"""
        SELECT COUNT(*) AS titulos,
               COALESCE(SUM(COALESCE(stock, 0)), 0) AS ejemplares,
               COALESCE(SUM(COALESCE(precio, 0) * COALESCE(stock, 0)), 0) AS valor,
               COALESCE(SUM(CASE WHEN {muerto} THEN 1 ELSE 0 END), 0) AS muertos,
               COALESCE(SUM(CASE WHEN {muerto} THEN COALESCE(precio, 0) * stock ELSE 0 END), 0) AS valor_muerto
        FROM ({LIBROS_CON_MOVIMIENTO}){where}"""
    with conectar() as con:
        return dict(con.execute(sql, params).fetchone())


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
        con.execute("UPDATE libros SET stock = ?, modificado = ?, "
                    "estado_ig = CASE WHEN ? = 0 THEN 'Vendido' ELSE estado_ig END WHERE id = ?",
                    (nuevo_stock, ahora(), nuevo_stock, libro_id))


def anular_venta(venta_id):
    """Borra la venta y devuelve los ejemplares al stock. Devuelve el id del libro (o None)."""
    with conectar() as con:
        venta = con.execute("SELECT * FROM ventas WHERE id = ?", (venta_id,)).fetchone()
        if not venta:
            return None
        con.execute("DELETE FROM ventas WHERE id = ?", (venta_id,))
        # Si estaba 'Vendido', vuelve a 'Publicado' si alguna vez se posteó, si no a 'Pendiente'
        con.execute("UPDATE libros SET stock = COALESCE(stock, 0) + ?, modificado = ?, estado_ig = CASE "
                    "WHEN estado_ig = 'Vendido' THEN CASE WHEN fecha_ig IS NOT NULL THEN 'Publicado' "
                    "ELSE 'Pendiente' END ELSE estado_ig END WHERE id = ?",
                    (venta["cantidad"], ahora(), venta["libro_id"]))
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
