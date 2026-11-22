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


MENSAJES = {
    "guardado": "Guardado.",
    "venta": "Venta registrada. Se descontó del stock.",
    "anulada": "Venta anulada. Los ejemplares volvieron al stock.",
    "vender": "Para marcarlo como vendido, registra la venta más abajo: así se descuenta el stock.",
    "error_venta": "No se pudo registrar: la cantidad debe ser entre 1 y el stock disponible.",
}
DIAS_STOCK_MUERTO = 180  # desde cuántos días sin ventas un libro cuenta como stock muerto en el resumen
AGRUPABLES = {"temas", "genero", "idioma", "ubicacion", "procedencia", "estado", "estado_ig"}
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


@app.template_filter("clp")
def formato_clp(valor):
    """85000 -> $85.000"""
    return "$" + f"{int(valor or 0):,}".replace(",", ".")


def guardar_foto(archivo, nombre_base):
    """Guarda la foto del libro reducida a 1600 px (suficiente para Instagram)."""
    img = ImageOps.exif_transpose(Image.open(archivo)).convert("RGB")
    img.thumbnail((1600, 1600))
    base = "".join(c for c in nombre_base if c.isalnum())[:20] or "libro"  # nada de rutas como ../
    nombre = f"{base}_{int(time.time())}_{secrets.token_hex(3)}.jpg"
    img.save(CARPETA_FOTOS / nombre, "JPEG", quality=88)
    return nombre


def foto_valida(nombre):
    """Devuelve el nombre solo si es un archivo que existe dentro de static/fotos.
    El formulario manda el nombre en un campo oculto, así que no se confía en él."""
    nombre = Path(nombre or "").name
    if not nombre or nombre.startswith("."):
        return None
    return nombre if (CARPETA_FOTOS / nombre).is_file() else None


def borrar_foto(nombre):
    nombre = foto_valida(nombre)
    if nombre:
        (CARPETA_FOTOS / nombre).unlink(missing_ok=True)


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
    datos["isbn"] = servicios.normalizar_isbn(datos.get("isbn")) or datos.get("isbn", "").strip()

    # La foto actual sale de la base de datos al editar; en un libro nuevo, del campo oculto (validado)
    anterior = db.obtener(libro_id) if libro_id else None
    datos["foto"] = anterior["foto"] if anterior else foto_valida(datos.get("foto"))
    foto = request.files.get("foto")
    if foto and foto.filename:
        datos["foto"] = guardar_foto(foto.stream, datos.get("isbn") or "libro")

    # Libro nuevo con un ISBN que ya existe: avisar antes de crear un duplicado
    if not libro_id and datos["isbn"] and not datos.get("confirmar_duplicado"):
        existente = db.buscar_por_isbn(datos["isbn"])
        if existente:
            return render_template("ficha.html", libro=datos, duplicado=existente, **opciones())

    nuevo_id = db.guardar(datos, libro_id)
    if anterior and anterior["foto"] and anterior["foto"] != datos["foto"]:
        borrar_foto(anterior["foto"])  # se reemplazó la foto: borrar la antigua
    if request.form.get("accion") == "otro":
        return redirect(url_for("nuevo", ubicacion=datos.get("ubicacion", ""),
                                procedencia=datos.get("procedencia", "")))
    return redirect(url_for("ver_libro", libro_id=nuevo_id, msg="guardado"))


@app.route("/libro/<int:libro_id>")
def ver_libro(libro_id):
    libro = db.obtener(libro_id)
    if not libro:
        return redirect(url_for("inventario"))
    msg = request.args.get("msg", "")
    # Los mensajes de ventas se muestran dentro de la sección Ventas, que es donde queda la pantalla
    de_venta = msg in ("venta", "anulada", "vender", "error_venta")
    return render_template("ficha.html", libro=libro,
                           aviso="" if de_venta else MENSAJES.get(msg, ""),
                           aviso_venta=MENSAJES.get(msg, "") if de_venta else "",
                           caption=servicios.caption_instagram(libro), ventas=db.ventas_de(libro_id),
                           hoy=time.strftime("%Y-%m-%d"), **opciones())


@app.route("/libro/<int:libro_id>/eliminar", methods=["POST"])
def eliminar(libro_id):
    libro = db.obtener(libro_id)
    db.eliminar(libro_id)
    if libro:
        borrar_foto(libro["foto"])
    return redirect(url_for("inventario"))


@app.route("/libro/<int:libro_id>/ig", methods=["POST"])
def cambiar_ig(libro_id):
    estado = request.form.get("estado_ig")
    libro = db.obtener(libro_id)
    if estado == "Vendido" and libro and (libro["stock"] or 0) > 0:
        # Quedan ejemplares: en vez de solo cambiar la etiqueta, llevar a registrar la venta
        return redirect(url_for("ver_libro", libro_id=libro_id, msg="vender", _anchor="venta"))
    if estado in ESTADOS_IG:
        fecha = time.strftime("%Y-%m-%d") if estado == "Publicado" else None
        db.cambiar_estado_ig(libro_id, estado, fecha)
    return redirect(request.referrer or url_for("inventario"))


@app.route("/libro/<int:libro_id>/vender", methods=["POST"])
def vender(libro_id):
    f = request.form
    try:
        db.registrar_venta(libro_id, db.entero(f.get("cantidad")) or 0, db.entero(f.get("precio")),
                           f.get("fecha") or time.strftime("%Y-%m-%d"),
                           f.get("comprador", "").strip(), f.get("notas", "").strip())
        msg = "venta"
    except ValueError:
        msg = "error_venta"
    return redirect(url_for("ver_libro", libro_id=libro_id, msg=msg, _anchor="venta"))


@app.route("/venta/<int:venta_id>/anular", methods=["POST"])
def anular_venta(venta_id):
    libro_id = db.anular_venta(venta_id)
    if libro_id and db.obtener(libro_id):
        return redirect(url_for("ver_libro", libro_id=libro_id, msg="anulada", _anchor="venta"))
    return redirect(url_for("ventas"))


@app.route("/ventas")
def ventas():
    filas = db.listar_ventas()
    meses = {}
    for v in filas:
        anio, mes = v["fecha"][:4], v["fecha"][5:7]
        clave = f"{MESES[int(mes) - 1].capitalize()} {anio}" if mes.isdigit() and 1 <= int(mes) <= 12 else v["fecha"]
        grupo = meses.setdefault(clave, {"ventas": [], "total": 0})
        grupo["ventas"].append(v)
        grupo["total"] += (v["precio"] or 0) * v["cantidad"]
    resumen = {"ventas": len(filas), "ejemplares": sum(v["cantidad"] for v in filas),
               "total": sum((v["precio"] or 0) * v["cantidad"] for v in filas)}
    return render_template("ventas.html", meses=meses, resumen=resumen)


@app.route("/ventas.csv")
def exportar_ventas():
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";")
    columnas = ["id", "libro_id", "titulo", "fecha", "cantidad", "precio", "comprador", "notas"]
    escritor.writerow(columnas + ["total"])
    for v in db.listar_ventas():
        escritor.writerow([v[c] for c in columnas] + [(v["precio"] or 0) * v["cantidad"]])
    return Response("﻿" + salida.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=ventas_glossalibros.csv"})


@app.route("/inventario")
def inventario():
    filtros = request.args.to_dict()
    libros = db.listar(filtros)
    agrupar = filtros.get("agrupar", "") if filtros.get("agrupar") in AGRUPABLES else ""
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
    muertos = [l for l in libros if (l["stock"] or 0) > 0 and (l["dias_sin_mov"] or 0) >= DIAS_STOCK_MUERTO]
    resumen["muertos"] = len(muertos)
    resumen["valor_muerto"] = sum((l["precio"] or 0) * l["stock"] for l in muertos)
    return render_template("inventario.html", grupos=dict(sorted(grupos.items())),
                           filtros=filtros, resumen=resumen, **opciones())


@app.route("/exportar.csv")
def exportar():
    libros = db.listar(request.args.to_dict())
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";")  # ";" para que Excel en español lo abra bien
    escritor.writerow(["id"] + db.CAMPOS + ["creado", "ultimo_movimiento", "dias_sin_movimiento"])
    for l in libros:
        escritor.writerow([l["id"]] + [l[c] for c in db.CAMPOS] + [l["creado"], l["ultimo_mov"], l["dias_sin_mov"]])
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
