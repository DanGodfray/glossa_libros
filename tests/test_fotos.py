"""Fotos y portadas: nombres seguros, limpieza, archivos inválidos y descarga de portadas."""
import io

from conftest import editar, id_de_redireccion, jpg, libro
from PIL import Image
from servicios import descargar_portada as descargar_portada_real  # antes de que el entorno la reemplace


def test_foto_con_nombre_seguro_y_reemplazo(g):
    r = g.cliente.post("/guardar", data={"titulo": "Con foto", "isbn": "../../malo",
                                         "foto": (jpg(), "foto.jpg"), "accion": "guardar"},
                       content_type="multipart/form-data")
    i = id_de_redireccion(r)
    f1 = libro(i)["foto"]
    assert f1 and (g.fotos / f1).is_file() and "/" not in f1 and ".." not in f1
    editar(g.cliente, i, foto=(jpg(), "otra.jpg"))
    f2 = libro(i)["foto"]
    assert f2 != f1 and (g.fotos / f2).is_file() and not (g.fotos / f1).exists()
    editar(g.cliente, i, titulo="Editado", foto="otra.jpg")  # campo oculto: se ignora al editar
    assert libro(i)["foto"] == f2
    g.cliente.post(f"/libro/{i}/eliminar")
    assert not (g.fotos / f2).exists()


def test_rutas_maliciosas_en_el_campo_oculto(g):
    victima = g.tmp / "no_borrar.txt"
    victima.write_text("x")
    r = g.cliente.post("/guardar", data={"titulo": "Truco", "foto": "../no_borrar.txt", "accion": "guardar"})
    i = id_de_redireccion(r)
    assert libro(i)["foto"] is None
    g.cliente.post(f"/libro/{i}/eliminar")
    assert victima.exists()


def test_archivo_que_no_es_imagen(g):
    """A3: un PDF como foto o como código no rompe la app y no se pierde lo escrito."""
    r = g.cliente.post("/guardar", data={"titulo": "Mi libro", "autor": "Alguien",
                                         "foto": (io.BytesIO(b"%PDF-1.4"), "doc.pdf"), "accion": "guardar"},
                       content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "no es una imagen válida" in html and 'value="Mi libro"' in html
    assert g.db.listar({}) == []
    r = g.cliente.post("/escanear", data={"foto_codigo": (io.BytesIO(b"no es imagen"), "x.jpg")},
                       content_type="multipart/form-data")
    assert r.status_code == 200 and "no es una imagen válida" in r.get_data(as_text=True)


def test_foto_demasiado_grande(g, monkeypatch):
    monkeypatch.setitem(g.app.app.config, "MAX_CONTENT_LENGTH", 1000)
    r = g.cliente.post("/guardar", data={"titulo": "X", "foto": (io.BytesIO(b"x" * 5000), "f.jpg")},
                       content_type="multipart/form-data")
    assert r.status_code == 413 and "pesa demasiado" in r.get_data(as_text=True)


def test_ruta_de_fotos(g):
    (g.fotos / "a.jpg").write_bytes(jpg().getvalue())
    assert g.cliente.get("/fotos/a.jpg").status_code == 200
    assert g.cliente.get("/fotos/../prueba.db").status_code == 404


def test_portada_se_descarga_una_vez_y_se_borra_sin_uso(g, monkeypatch):
    """C5: copia local de la portada, compartida entre libros con la misma URL."""
    descargas = []

    def falsa(url, carpeta):
        descargas.append(url)
        carpeta.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 150)).save(carpeta / "abc.jpg")
        return "abc.jpg"

    monkeypatch.setattr(g.servicios, "descargar_portada", falsa)
    url = "https://covers.openlibrary.org/b/id/1-M.jpg"
    a = id_de_redireccion(g.cliente.post("/guardar", data={"titulo": "A", "portada_url": url, "accion": "guardar"}))
    b = id_de_redireccion(g.cliente.post("/guardar", data={"titulo": "B", "portada_url": url, "accion": "guardar"}))
    assert libro(a)["portada_local"] == "abc.jpg" and len(descargas) == 2
    editar(g.cliente, a, titulo="A editado", portada_url=url)
    assert len(descargas) == 2  # no se vuelve a descargar al editar
    assert "/fotos/portadas/abc.jpg" in g.cliente.get(f"/libro/{a}").get_data(as_text=True)
    g.cliente.post(f"/libro/{a}/eliminar")
    assert (g.fotos / "portadas" / "abc.jpg").exists()  # B todavía la usa
    g.cliente.post(f"/libro/{b}/eliminar")
    assert not (g.fotos / "portadas" / "abc.jpg").exists()


def test_descargar_portada_solo_de_servidores_conocidos(g, monkeypatch):
    """La URL viene de un campo del formulario: no se debe poder pedir cualquier dirección."""
    llamadas = []
    monkeypatch.setattr(g.servicios.requests, "get", lambda *a, **k: llamadas.append(a))
    for url in ["http://192.168.1.1/admin.jpg", "file:///etc/passwd", "https://ejemplo.invalid/x.jpg"]:
        assert descargar_portada_real(url, g.tmp) is None
    assert llamadas == []


def test_descargar_portada_descarta_imagen_vacia_de_open_library(g, monkeypatch):
    class Respuesta:
        content = jpg(1, 1).getvalue()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(g.servicios.requests, "get", lambda *a, **k: Respuesta())
    assert descargar_portada_real("https://covers.openlibrary.org/b/id/0-M.jpg", g.tmp / "p") is None
    Respuesta.content = jpg(120, 180).getvalue()
    nombre = descargar_portada_real("https://covers.openlibrary.org/b/id/5-M.jpg", g.tmp / "p")
    assert nombre and (g.tmp / "p" / nombre).is_file()
