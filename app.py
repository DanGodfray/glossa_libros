"""Servidor Flask de GlossaLibros. Ejecutar con:  python app.py"""
import csv
import io
import os
import secrets
import socket
import time
from pathlib import Path

from flask import Flask, Response, redirect, render_template, request, session, url_for
from PIL import Image, ImageOps

import db
import servicios

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # fotos de hasta 25 MB
CARPETA_FOTOS = Path(__file__).parent / "static" / "fotos"

# Contraseña de acceso (obligatoria al publicar en internet). Si no se define, la app queda abierta,
# como al usarla solo en el WiFi de la casa. SECRET_KEY firma la cookie de sesión.
CLAVE = os.environ.get("APP_PASSWORD")
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 90  # recordar el acceso 90 días

# Crear la tabla y la carpeta de fotos al cargar la app (también cuando la carga un servidor web)
db.iniciar()
CARPETA_FOTOS.mkdir(parents=True, exist_ok=True)


@app.before_request
def pedir_clave():
    if not CLAVE or session.get("ok") or request.endpoint in ("entrar", "static"):
        return None
    return redirect(url_for("entrar", next=request.full_path))


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    error = ""
    if request.method == "POST":
        if CLAVE and secrets.compare_digest(request.form.get("clave", ""), CLAVE):
            session["ok"] = True
            session.permanent = True
            destino = request.args.get("next", "")
            # Solo rutas internas, para que nadie arme un enlace que redirija a otro sitio
            if not destino.startswith("/") or destino.startswith("//"):
                destino = url_for("inventario")
            return redirect(destino)
        error = "Contraseña incorrecta."
    return render_template("entrar.html", error=error)


@app.route("/salir")
def salir():
    session.clear()
    return redirect(url_for("inventario"))

ESTADOS = ["Como nuevo", "Muy bueno", "Bueno", "Aceptable", "Deteriorado"]
ESTADOS_IG = ["Pendiente", "Listo", "Publicado", "Vendido"]
GENEROS = ["Novela", "Cuento", "Poesía", "Teatro", "Ensayo", "Historia", "Filosofía",
           "Religión", "Ciencia", "Arte", "Viajes", "Biografía", "Derecho y política",
           "Infantil", "Referencia", "Clásicos"]


def opciones():
    """Listas para los menús y el autocompletado del formulario."""
    return {
        "estados": ESTADOS, "estados_ig": ESTADOS_IG,
        "generos": sorted(set(GENEROS) | set(db.valores("genero"))),
        "idiomas": db.valores("idioma") or ["Español", "Inglés", "Francés", "Latín"],
        "temas": db.valores("temas"), "caracteristicas": db.valores("caracteristicas"),
        "ubicaciones": db.valores("ubicacion"), "procedencias": db.valores("procedencia"),
    }


def guardar_foto(archivo, nombre_base):
    """Guarda la foto del libro reducida a 1600 px (suficiente para Instagram)."""
    img = ImageOps.exif_transpose(Image.open(archivo)).convert("RGB")
    img.thumbnail((1600, 1600))
    nombre = f"{nombre_base}_{int(time.time())}.jpg"
    img.save(CARPETA_FOTOS / nombre, "JPEG", quality=88)
    return nombre


@app.route("/")
def inicio():
    return redirect(url_for("inventario"))


@app.route("/nuevo")
def nuevo():
    # "Guardar y agregar otro" manda la ubicación y procedencia para no reescribirlas
    libro = {"stock": 1, "estado_ig": "Pendiente",
             "ubicacion": request.args.get("ubicacion", ""),
             "procedencia": request.args.get("procedencia", "")}
    return render_template("ficha.html", libro=libro, **opciones())


@app.route("/escanear", methods=["POST"])
def escanear():
    """Recibe la foto del código de barras (o un ISBN escrito) y prellena la ficha."""
    aviso = ""
    isbn = servicios.normalizar_isbn(request.form.get("isbn"))
    foto = request.files.get("foto_codigo")
    if not isbn and foto and foto.filename:
        isbn, codigos = servicios.leer_isbn_de_foto(foto.stream)
        if not isbn and codigos:
            aviso = (f"Leí el código {codigos[0]}, pero no es un ISBN (pasa en libros antiguos). "
                     "Escribe el ISBN que aparece impreso, normalmente arriba del código de barras.")
        elif not isbn:
            aviso = "No pude leer el código. Acerca más la cámara, con buena luz, o escribe el ISBN."
    elif not isbn:
        aviso = "Ese ISBN no es válido. Revisa los dígitos."

    libro = {"stock": 1, "estado_ig": "Pendiente",
             "ubicacion": request.form.get("ubicacion", ""),
             "procedencia": request.form.get("procedencia", "")}
    if isbn:
        existente = db.buscar_por_isbn(isbn)
        if existente:
            aviso = "Este ISBN ya está en el inventario. Puedes sumar stock aquí."
            return render_template("ficha.html", libro=existente, aviso=aviso, **opciones())
        encontrado = servicios.buscar_libro(isbn)
        libro.update({k: v for k, v in encontrado.items() if v})
        aviso = ("Datos encontrados: revísalos antes de guardar." if encontrado.get("titulo")
                 else f"ISBN {isbn} leído, pero no aparece en los catálogos. Completa a mano.")
    return render_template("ficha.html", libro=libro, aviso=aviso, **opciones())


@app.route("/guardar", methods=["POST"])
@app.route("/libro/<int:libro_id>/guardar", methods=["POST"])
def guardar(libro_id=None):
    datos = request.form.to_dict()
    datos["temas"] = ", ".join(t.strip() for t in datos.get("temas", "").split(",") if t.strip())
    datos["caracteristicas"] = ", ".join(request.form.getlist("caracteristicas") +
                                         [c.strip() for c in datos.get("otras_caract", "").split(",") if c.strip()])
    foto = request.files.get("foto")
    if foto and foto.filename:
        datos["foto"] = guardar_foto(foto.stream, datos.get("isbn") or "libro")
    nuevo_id = db.guardar(datos, libro_id)
    if request.form.get("accion") == "otro":
        return redirect(url_for("nuevo", ubicacion=datos.get("ubicacion", ""),
                                procedencia=datos.get("procedencia", "")))
    return redirect(url_for("ver_libro", libro_id=nuevo_id, ok=1))


@app.route("/libro/<int:libro_id>")
def ver_libro(libro_id):
    libro = db.obtener(libro_id)
    if not libro:
        return redirect(url_for("inventario"))
    aviso = "Guardado." if request.args.get("ok") else ""
    return render_template("ficha.html", libro=libro, aviso=aviso,
                           caption=servicios.caption_instagram(libro), **opciones())


@app.route("/libro/<int:libro_id>/eliminar", methods=["POST"])
def eliminar(libro_id):
    db.eliminar(libro_id)
    return redirect(url_for("inventario"))


@app.route("/libro/<int:libro_id>/ig", methods=["POST"])
def cambiar_ig(libro_id):
    estado = request.form.get("estado_ig")
    fecha = time.strftime("%Y-%m-%d") if estado == "Publicado" else None
    db.cambiar_estado_ig(libro_id, estado, fecha)
    return redirect(request.referrer or url_for("inventario"))


@app.route("/inventario")
def inventario():
    filtros = request.args.to_dict()
    libros = db.listar(filtros)
    agrupar = filtros.get("agrupar", "")
    grupos = {}
    for libro in libros:
        if agrupar == "temas":
            claves = [t.strip() for t in (libro["temas"] or "Sin tema").split(",")]
        elif agrupar:
            claves = [libro[agrupar] or "Sin dato"]
        else:
            claves = ["Todos"]
        for clave in claves:
            grupos.setdefault(clave, []).append(libro)
    resumen = {
        "titulos": len(libros),
        "ejemplares": sum(l["stock"] or 0 for l in libros),
        "valor": sum((l["precio"] or 0) * (l["stock"] or 0) for l in libros),
    }
    return render_template("inventario.html", grupos=dict(sorted(grupos.items())),
                           filtros=filtros, resumen=resumen, **opciones())


@app.route("/exportar.csv")
def exportar():
    libros = db.listar(request.args.to_dict())
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";")  # ";" para que Excel en español lo abra bien
    escritor.writerow(["id"] + db.CAMPOS + ["creado"])
    for l in libros:
        escritor.writerow([l["id"]] + [l[c] for c in db.CAMPOS] + [l["creado"]])
    return Response("﻿" + salida.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=inventario_glossalibros.csv"})


def ip_local():
    """Averigua la IP del computador en el WiFi, para escribirla en el iPhone."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    print(f"\n  Abre en el iPhone:  http://{ip_local()}:5000\n")
    # host="0.0.0.0" = aceptar conexiones de otros equipos del WiFi (tu iPhone).
    # debug=False: el modo debug permitiría ejecutar código a cualquiera en tu red.
    app.run(host="0.0.0.0", port=5000, debug=False)
