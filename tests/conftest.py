"""Configuración común de las pruebas: cada prueba usa una base y una carpeta de fotos temporales.

Ejecutar desde la carpeta del proyecto:  .venv\\Scripts\\python.exe -m pytest
"""
import io
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest
from PIL import Image

# Antes de importar la app: que nunca toque inventario.db ni static/fotos reales
_TMP = Path(tempfile.mkdtemp(prefix="glossa_pruebas_"))
os.environ["GLOSSA_DB"] = str(_TMP / "importacion.db")
os.environ["GLOSSA_FOTOS"] = str(_TMP / "fotos_importacion")
os.environ.pop("APP_PASSWORD", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_mod  # noqa: E402
import db  # noqa: E402
import servicios  # noqa: E402


@pytest.fixture
def g(tmp_path, monkeypatch):
    """Entorno aislado: base nueva, carpeta de fotos vacía, sin contraseña y sin internet."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "prueba.db")
    fotos = tmp_path / "fotos"
    fotos.mkdir()
    monkeypatch.setattr(app_mod, "CARPETA_FOTOS", fotos)
    monkeypatch.setattr(app_mod, "CLAVE", None)
    monkeypatch.setattr(servicios, "descargar_portada", lambda url, carpeta: None)
    monkeypatch.setattr(servicios, "buscar_libro", lambda isbn: ({"isbn": isbn}, []))
    db.iniciar()
    return types.SimpleNamespace(db=db, app=app_mod, servicios=servicios, fotos=fotos, tmp=tmp_path,
                                 cliente=app_mod.app.test_client())


def jpg(ancho=50, alto=80):
    b = io.BytesIO()
    Image.new("RGB", (ancho, alto), "red").save(b, "JPEG")
    b.seek(0)
    return b


def libro(libro_id):
    return dict(db.obtener(libro_id))


def id_de_redireccion(respuesta):
    """Extrae el id de una redirección a /libro/<id>?..."""
    return int(respuesta.headers["Location"].split("/libro/")[1].split("?")[0].split("#")[0])


def editar(cliente, libro_id, **campos):
    """Envía la ficha como lo haría el formulario: con la marca de modificación y el stock que mostraba."""
    actual = libro(libro_id)
    datos = {"titulo": actual["titulo"], "stock": str(actual["stock"] or 0), "accion": "guardar",
             "modificado": actual["modificado"], "stock_original": str(actual["stock"] or 0),
             "estado_ig": actual["estado_ig"] or "", "fecha_ig": actual["fecha_ig"] or ""}
    datos.update(campos)
    return cliente.post(f"/libro/{libro_id}/guardar", data=datos, content_type="multipart/form-data")
