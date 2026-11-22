"""Migraciones, catálogos, páginas de error, respaldo y acceso con contraseña."""
import io
import sqlite3
import zipfile

import pytest
import requests
import respaldo
from conftest import jpg
from servicios import buscar_libro as buscar_libro_real  # antes de que el entorno la reemplace


# ---------- Migraciones (C1) ----------

def _version(ruta):
    with sqlite3.connect(ruta) as con:
        return con.execute("PRAGMA user_version").fetchone()[0]


def _columnas(ruta, tabla):
    with sqlite3.connect(ruta) as con:
        return {fila[1] for fila in con.execute(f"PRAGMA table_info({tabla})")}


def test_migrar_una_base_de_la_v02_conserva_los_datos(g, tmp_path, monkeypatch):
    ruta = tmp_path / "v02.db"
    with sqlite3.connect(ruta) as con:  # una base como la de la v0.2: esquema sin número de versión
        con.executescript(g.db.MIGRACIONES[0])
        con.execute("INSERT INTO libros (titulo, anio, stock, precio) VALUES ('Viejo', 1963, 2, 5000)")
        con.execute("INSERT INTO ventas (libro_id, titulo, fecha, cantidad, precio) VALUES (1, 'Viejo', '2026-10-01', 1, 5000)")
    assert _version(ruta) == 0
    monkeypatch.setattr(g.db, "DB_PATH", ruta)
    g.db.iniciar()
    assert _version(ruta) == len(g.db.MIGRACIONES)
    assert {"fecha_edicion", "portada_local", "modificado"} <= _columnas(ruta, "libros")
    libro = g.db.obtener(1)
    assert libro["fecha_edicion"] == "1963" and libro["modificado"] and libro["stock"] == 2
    assert len(g.db.ventas_de(1)) == 1
    g.db.iniciar()  # volver a iniciar no cambia nada
    assert _version(ruta) == len(g.db.MIGRACIONES)


def test_migracion_que_falla_no_deja_cambios_a_medias(g, tmp_path, monkeypatch):
    ruta = tmp_path / "falla.db"
    monkeypatch.setattr(g.db, "DB_PATH", ruta)
    g.db.iniciar()
    version = _version(ruta)
    monkeypatch.setattr(g.db, "MIGRACIONES", g.db.MIGRACIONES + ["ALTER TABLE libros ADD COLUMN extra TEXT; SELEC mal;"])
    with pytest.raises(sqlite3.Error):
        g.db.iniciar()
    assert _version(ruta) == version and "extra" not in _columnas(ruta, "libros")


# ---------- Catálogos (A6) ----------

def _error_http(codigo):
    respuesta = requests.Response()
    respuesta.status_code = codigo
    return requests.HTTPError(response=respuesta)


def test_buscar_libro_informa_los_catalogos_que_fallaron(g, monkeypatch):
    monkeypatch.setattr(g.servicios, "_open_library", lambda isbn: {})

    def google(isbn):
        raise _error_http(429)

    monkeypatch.setattr(g.servicios, "_google_books", google)
    datos, fallas = buscar_libro_real("9788420471839")
    assert datos["isbn"] == "9788420471839" and fallas == ["Google Books (límite diario de consultas alcanzado)"]
    monkeypatch.setattr(g.servicios, "_open_library", lambda isbn: (_ for _ in ()).throw(requests.Timeout()))
    _, fallas = buscar_libro_real("9788420471839")
    assert "Open Library (no respondió a tiempo)" in fallas


def test_aviso_distingue_catalogo_caido_de_libro_inexistente(g, monkeypatch):
    monkeypatch.setattr(g.servicios, "buscar_libro",
                        lambda isbn: ({"isbn": isbn}, ["Google Books (límite diario de consultas alcanzado)"]))
    html = g.cliente.post("/escanear", data={"isbn": "9788420471839"}).get_data(as_text=True)
    assert "no se pudieron revisar todos los catálogos" in html and "límite diario" in html
    assert "no aparece en los catálogos" not in html
    monkeypatch.setattr(g.servicios, "buscar_libro", lambda isbn: ({"isbn": isbn}, []))
    html = g.cliente.post("/escanear", data={"isbn": "9788420471839"}).get_data(as_text=True)
    assert "no aparece en los catálogos" in html


# ---------- Páginas de error (A7) ----------

def test_paginas_de_error_en_espanol(g, monkeypatch):
    r = g.cliente.get("/no-existe")
    assert r.status_code == 404 and "No encontramos esa página" in r.get_data(as_text=True)
    monkeypatch.setattr(g.db, "listar_ventas", lambda: 1 / 0)
    r = g.cliente.get("/ventas")
    assert r.status_code == 500 and "Algo salió mal" in r.get_data(as_text=True)


def test_formato_de_fecha(g):
    assert g.app.formato_fecha("2026-11-18") == "18-11-2026"
    assert g.app.formato_fecha("s.f.") == "s.f." and g.app.formato_fecha(None) == ""


# ---------- Respaldo (C3) ----------

def test_respaldo_incluye_base_y_fotos(g):
    g.db.guardar({"titulo": "Respaldado"})
    (g.fotos / "a.jpg").write_bytes(jpg().getvalue())
    (g.fotos / "portadas").mkdir()
    (g.fotos / "portadas" / "p.jpg").write_bytes(jpg().getvalue())
    destino = io.BytesIO()
    respaldo.escribir_respaldo(destino, g.fotos)
    with zipfile.ZipFile(destino) as z:
        assert set(z.namelist()) == {"inventario.db", "static/fotos/a.jpg", "static/fotos/portadas/p.jpg"}
        (g.tmp / "restaurada.db").write_bytes(z.read("inventario.db"))
    with sqlite3.connect(g.tmp / "restaurada.db") as con:
        assert con.execute("SELECT titulo FROM libros").fetchall() == [("Respaldado",)]


def test_respaldo_conserva_solo_los_mas_recientes(g):
    carpeta = g.tmp / "respaldos"
    carpeta.mkdir()
    for dia in (1, 2, 3):
        (carpeta / f"glossalibros_2020-01-0{dia}_120000.zip").write_bytes(b"viejo")
    nuevo = respaldo.crear_respaldo(g.fotos, carpeta=carpeta, conservar=2)
    assert sorted(p.name for p in carpeta.iterdir()) == ["glossalibros_2020-01-03_120000.zip", nuevo.name]


def test_descargar_respaldo_desde_la_app(g):
    r = g.cliente.get("/respaldo")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert "inventario.db" in zipfile.ZipFile(io.BytesIO(r.data)).namelist()


# ---------- Acceso ----------

def test_contrasena_y_redirecciones(g, monkeypatch):
    monkeypatch.setattr(g.app, "CLAVE", "clave")
    r = g.cliente.get("/inventario")
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    assert g.cliente.get("/fotos/a.jpg").status_code == 302  # las fotos también piden contraseña
    assert "incorrecta" in g.cliente.post("/entrar", data={"clave": "mala"}).get_data(as_text=True)
    for destino, esperado in [("/ventas", "/ventas"), ("//evil.com", "/inventario"), ("/\\evil.com", "/inventario")]:
        cliente = g.app.app.test_client()
        r = cliente.post("/entrar", query_string={"next": destino}, data={"clave": "clave"})
        assert r.headers["Location"] == esperado, destino
    html = cliente.get("/ventas").get_data(as_text=True)
    assert "Cerrar sesión" in html
    cliente.get("/salir")
    assert cliente.get("/ventas").status_code == 302
