"""Servidor Flask de GlossaLibros. Ejecutar con:  python app.py"""
import csv
import io
import math
import os
import re
import secrets
import socket
import tempfile
import time
from pathlib import Path

from flask import (Flask, Response, redirect, render_template, request, send_file,
                   send_from_directory, session, url_for)

import db
import respaldo
import servicios

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # fotos de hasta 25 MB
# La carpeta de fotos se puede cambiar con GLOSSA_FOTOS (pruebas, servidores)
CARPETA_FOTOS = Path(os.environ.get("GLOSSA_FOTOS") or Path(__file__).parent / "static" / "fotos")

# Contraseña de acceso (obligatoria al publicar en internet). Si no se define, la app queda abierta,
# como al usarla solo en el WiFi de la casa. SECRET_KEY firma la cookie de sesión.
CLAVE = os.environ.get("APP_PASSWORD")
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 90  # recordar el acceso 90 días

# Crear o actualizar la base y la carpeta de fotos al cargar la app (también en un servidor web)
db.iniciar()
CARPETA_FOTOS.mkdir(parents=True, exist_ok=True)

ESTADOS = ["Como nuevo", "Muy bueno", "Bueno", "Aceptable", "Deteriorado"]
ESTADOS_IG = ["Pendiente", "Listo", "Publicado", "Vendido"]
GENEROS = ["Novela", "Cuento", "Poesía", "Teatro", "Ensayo", "Historia", "Filosofía",
           "Religión", "Ciencia", "Arte", "Viajes", "Biografía", "Derecho y política",
           "Infantil", "Referencia", "Clásicos"]
MENSAJES = {
    "guardado": "Guardado.",
    "venta": "Venta registrada. Se descontó del stock.",
    "anulada": "Venta anulada. Los ejemplares volvieron al stock.",
    "vender": "Para marcarlo como vendido, registra la venta más abajo: así se descuenta el stock.",
    "error_venta": "No se pudo registrar: la cantidad debe ser entre 1 y el stock disponible.",
}
MENSAJES_VENTA = {"venta", "anulada", "vender", "error_venta"}
# Nombres de los campos para avisos (por ejemplo, cuando la ficha cambió en otro dispositivo)
ETIQUETAS = {
    "titulo": "Título", "autor": "Autor", "editorial": "Editorial", "lugar_edicion": "Lugar de edición",
    "fecha_edicion": "Año / fecha", "idioma": "Idioma", "edicion": "Edición", "isbn": "ISBN",
    "genero": "Género", "temas": "Temas", "procedencia": "Procedencia", "caracteristicas": "Características",
    "estado": "Estado", "notas_estado": "Notas del estado", "precio": "Precio", "stock": "Stock",
    "ubicacion": "Ubicación", "estado_ig": "Instagram", "fecha_ig": "Fecha de posteo",
}
POR_PAGINA = 50
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


# ---------- Acceso ----------

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
            if not destino.startswith("/") or destino.startswith("//") or "\\" in destino:
                destino = url_for("inventario")
            return redirect(destino)
        error = "Contraseña incorrecta."
    return render_template("entrar.html", error=error)


@app.route("/salir")
def salir():
    session.clear()
    return redirect(url_for("inventario"))


# ---------- Ayudas para las plantillas ----------

def _campo(libro, clave):
    """Lee un campo de una fila de la base o de un diccionario del formulario."""
    try:
        return libro[clave]
    except (KeyError, IndexError):
        return None


@app.template_filter("clp")
def formato_clp(valor):
    """85000 -> $85.000"""
    return "$" + f"{int(valor or 0):,}".replace(",", ".")


@app.template_filter("fecha")
def formato_fecha(valor):
    """2026-11-18 -> 18-11-2026 (deja igual cualquier otro texto)."""
    texto = str(valor or "")
    if re.match(r"\d{4}-\d{2}-\d{2}", texto):
        return f"{texto[8:10]}-{texto[5:7]}-{texto[:4]}"
    return texto


@app.template_global()
def imagen_libro(libro):
    """Foto propia, si no la portada guardada en la app, si no la portada externa del catálogo."""
    if _campo(libro, "foto"):
        return url_for("foto", nombre=libro["foto"])
    if _campo(libro, "portada_local"):
        return url_for("foto", nombre="portadas/" + libro["portada_local"])
    return _campo(libro, "portada_url") or ""


def opciones():
    """Listas para los menús y el autocompletado del formulario."""
    return {
        "estados": ESTADOS, "estados_ig": ESTADOS_IG,
        "generos": sorted(set(GENEROS) | set(db.valores("genero"))),
        "idiomas": db.valores("idioma") or ["Español", "Inglés", "Francés", "Latín"],
        "temas": db.valores("temas"), "caracteristicas": db.valores("caracteristicas"),
        "ubicaciones": db.valores("ubicacion"), "procedencias": db.valores("procedencia"),
    }


def mostrar_ficha(libro, **extra):
    """Muestra la ficha. Para un libro ya guardado agrega el texto de Instagram y sus ventas."""
    libro_id = _campo(libro, "id")
    if libro_id:
        guardado = db.obtener(libro_id)
        extra.setdefault("caption", servicios.caption_instagram(guardado))
        extra.setdefault("ventas", db.ventas_de(libro_id))
        extra.setdefault("hoy", time.strftime("%Y-%m-%d"))
    return render_template("ficha.html", libro=libro, **extra, **opciones())


# ---------- Fotos ----------

def guardar_foto(archivo, nombre_base):
    """Guarda la foto del libro reducida a 1600 px (suficiente para Instagram).
    Lanza servicios.ImagenInvalida si el archivo no es una imagen."""
    try:
        img = servicios.abrir_imagen(archivo).convert("RGB")
        img.thumbnail((1600, 1600))
    except OSError as e:  # imagen truncada o dañada
        raise servicios.ImagenInvalida(str(e)) from e
    base = "".join(c for c in nombre_base if c.isalnum())[:20] or "libro"  # nada de rutas como ../
    nombre = f"{base}_{int(time.time())}_{secrets.token_hex(3)}.jpg"
    img.save(CARPETA_FOTOS / nombre, "JPEG", quality=88)
    return nombre


def foto_valida(nombre):
    """Devuelve el nombre solo si es un archivo que existe dentro de la carpeta de fotos.
    El formulario manda el nombre en un campo oculto, así que no se confía en él."""
    nombre = Path(nombre or "").name
    if not nombre or nombre.startswith("."):
        return None
    return nombre if (CARPETA_FOTOS / nombre).is_file() else None


def borrar_foto(nombre):
    nombre = foto_valida(nombre)
    if nombre:
        (CARPETA_FOTOS / nombre).unlink(missing_ok=True)


def borrar_portada_si_no_se_usa(nombre):
    """Las portadas descargadas se comparten entre libros con la misma URL: se borran al quedar sin uso."""
    nombre = Path(nombre or "").name
    if nombre and not nombre.startswith(".") and not db.portada_en_uso(nombre):
        (CARPETA_FOTOS / "portadas" / nombre).unlink(missing_ok=True)


@app.route("/fotos/<path:nombre>")
def foto(nombre):
    # send_from_directory rechaza rutas fuera de la carpeta (../)
    return send_from_directory(CARPETA_FOTOS, nombre, max_age=60 * 60 * 24 * 30)


# ---------- Ingreso y edición de libros ----------

@app.route("/")
def inicio():
    return redirect(url_for("inventario"))


@app.route("/nuevo")
def nuevo():
    # "Guardar y agregar otro" manda la ubicación y procedencia para no reescribirlas
    libro = {"stock": 1, "estado_ig": "Pendiente",
             "ubicacion": request.args.get("ubicacion", ""),
             "procedencia": request.args.get("procedencia", "")}
    return mostrar_ficha(libro)


@app.route("/escanear", methods=["POST"])
def escanear():
    """Recibe la foto del código de barras (o un ISBN escrito) y prellena la ficha."""
    aviso = ""
    isbn = servicios.normalizar_isbn(request.form.get("isbn"))
    foto_codigo = request.files.get("foto_codigo")
    if not isbn and foto_codigo and foto_codigo.filename:
        try:
            isbn, codigos = servicios.leer_isbn_de_foto(foto_codigo.stream)
        except servicios.ImagenInvalida:
            isbn, codigos = None, []
            aviso = "Ese archivo no es una imagen válida. Saca la foto del código de nuevo o escribe el ISBN."
        if not isbn and codigos:
            aviso = (f"Leí el código {codigos[0]}, pero no es un ISBN (pasa en libros antiguos). "
                     "Escribe el ISBN que aparece impreso, normalmente arriba del código de barras.")
        elif not isbn and not aviso:
            aviso = "No pude leer el código. Acerca más la cámara, con buena luz, o escribe el ISBN."
    elif not isbn:
        aviso = "Ese ISBN no es válido. Revisa los dígitos."

    libro = {"stock": 1, "estado_ig": "Pendiente",
             "ubicacion": request.form.get("ubicacion", ""),
             "procedencia": request.form.get("procedencia", "")}
    if isbn:
        existente = db.buscar_por_isbn(isbn)
        if existente:
            return mostrar_ficha(existente, aviso="Este ISBN ya está en el inventario. Puedes sumar stock aquí.")
        encontrado, fallas = servicios.buscar_libro(isbn)
        libro.update({k: v for k, v in encontrado.items() if v})
        no_consultados = "No se pudo consultar: " + "; ".join(fallas) + "." if fallas else ""
        if encontrado.get("titulo"):
            aviso = "Datos encontrados: revísalos antes de guardar." + (" " + no_consultados if fallas else "")
        elif fallas:
            aviso = (f"ISBN {isbn} leído, pero no se pudieron revisar todos los catálogos. {no_consultados} "
                     "Puede que el libro sí exista: intenta más tarde o completa a mano.")
            if any("Google Books" in f for f in fallas) and not servicios.GOOGLE_BOOKS_KEY:
                aviso += " Con una clave propia de Google Books (GOOGLE_BOOKS_KEY) no dependes del límite compartido."
        else:
            aviso = f"ISBN {isbn} leído, pero no aparece en los catálogos. Completa a mano."
    return mostrar_ficha(libro, aviso=aviso)


def _cambios_respecto_a(guardado, datos):
    """Campos en que el formulario difiere de lo que hay guardado ahora: (etiqueta, guardado, formulario)."""
    nuevos = db._limpiar(datos)
    diferencias = []
    for campo, etiqueta in ETIQUETAS.items():
        antes, despues = guardado[campo], nuevos[campo]
        if (antes if antes not in ("", None) else None) != despues and campo != "stock":
            diferencias.append((etiqueta, antes or "—", despues or "—"))
    return diferencias


@app.route("/guardar", methods=["POST"])
@app.route("/libro/<int:libro_id>/guardar", methods=["POST"])
def guardar(libro_id=None):
    datos = request.form.to_dict()
    datos["temas"] = ", ".join(t.strip() for t in datos.get("temas", "").split(",") if t.strip())
    datos["caracteristicas"] = ", ".join(request.form.getlist("caracteristicas") +
                                         [c.strip() for c in datos.get("otras_caract", "").split(",") if c.strip()])
    datos["isbn"] = servicios.normalizar_isbn(datos.get("isbn")) or datos.get("isbn", "").strip()

    anterior = db.obtener(libro_id) if libro_id else None
    if libro_id and not anterior:
        return redirect(url_for("inventario"))
    stock_original = db.entero(datos.get("stock_original")) if anterior else None

    # La ficha cambió en otro dispositivo mientras se editaba (por ejemplo, una venta): no pisar nada.
    # Se vuelve a mostrar lo escrito, con el stock ya ajustado, para revisarlo y guardar otra vez.
    if anterior and datos.get("modificado", "") != (anterior["modificado"] or ""):
        stock_form = db.entero(datos.get("stock")) or 0
        base = stock_original if stock_original is not None else stock_form
        libro = dict(datos, id=libro_id, foto=anterior["foto"], portada_local=anterior["portada_local"],
                     modificado=anterior["modificado"], stock_original=anterior["stock"],
                     stock=max(0, (anterior["stock"] or 0) + stock_form - base))
        return mostrar_ficha(libro, conflicto=_cambios_respecto_a(anterior, datos),
                             stock_actual=anterior["stock"] or 0,
                             subio_foto=bool(request.files.get("foto") and request.files["foto"].filename))

    # La foto actual sale de la base al editar; en un libro nuevo, del campo oculto (validado)
    datos["foto"] = anterior["foto"] if anterior else foto_valida(datos.get("foto"))
    archivo = request.files.get("foto")
    if archivo and archivo.filename:
        try:
            datos["foto"] = guardar_foto(archivo.stream, datos.get("isbn") or "libro")
        except servicios.ImagenInvalida:
            libro = dict(datos, id=libro_id) if libro_id else datos
            return mostrar_ficha(libro, aviso="La foto que elegiste no es una imagen válida. "
                                              "Elige otra (JPG, PNG o HEIC) y vuelve a guardar.")

    # Libro nuevo con un ISBN que ya existe: avisar antes de crear un duplicado
    if not libro_id and datos["isbn"] and not datos.get("confirmar_duplicado"):
        existente = db.buscar_por_isbn(datos["isbn"])
        if existente:
            return mostrar_ficha(datos, duplicado=existente)

    # Portada: se guarda una copia en la app la primera vez (o si cambió la URL)
    url_portada = (datos.get("portada_url") or "").strip()
    datos["portada_local"] = anterior["portada_local"] if anterior else None
    if not url_portada:
        datos["portada_local"] = None
    elif not anterior or anterior["portada_url"] != url_portada or not anterior["portada_local"]:
        datos["portada_local"] = servicios.descargar_portada(url_portada, CARPETA_FOTOS / "portadas")

    nuevo_id = db.guardar(datos, libro_id, stock_original=stock_original)
    if anterior and anterior["foto"] and anterior["foto"] != datos["foto"]:
        borrar_foto(anterior["foto"])  # se reemplazó la foto: borrar la antigua
    if anterior and anterior["portada_local"] and anterior["portada_local"] != datos["portada_local"]:
        borrar_portada_si_no_se_usa(anterior["portada_local"])
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
    de_venta = msg in MENSAJES_VENTA
    return mostrar_ficha(libro, aviso="" if de_venta else MENSAJES.get(msg, ""),
                         aviso_venta=MENSAJES.get(msg, "") if de_venta else "")


@app.route("/libro/<int:libro_id>/eliminar", methods=["POST"])
def eliminar(libro_id):
    libro = db.obtener(libro_id)
    db.eliminar(libro_id)
    if libro:
        borrar_foto(libro["foto"])
        borrar_portada_si_no_se_usa(libro["portada_local"])
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


# ---------- Ventas ----------

@app.route("/libro/<int:libro_id>/vender", methods=["POST"])
def vender(libro_id):
    f = request.form
    try:
        db.registrar_venta(libro_id, db.entero(f.get("cantidad")) or 0, db.parsear_clp(f.get("precio")),
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


def respuesta_csv(filas, nombre):
    salida = io.StringIO()
    csv.writer(salida, delimiter=";").writerows(filas)  # ";" para que Excel en español lo abra bien
    return Response("﻿" + salida.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={nombre}"})


@app.route("/ventas.csv")
def exportar_ventas():
    columnas = ["id", "libro_id", "titulo", "fecha", "cantidad", "precio", "comprador", "notas"]
    filas = [columnas + ["total"]]
    filas += [[v[c] for c in columnas] + [(v["precio"] or 0) * v["cantidad"]] for v in db.listar_ventas()]
    return respuesta_csv(filas, "ventas_glossalibros.csv")


# ---------- Inventario ----------

@app.route("/inventario")
def inventario():
    args = request.args.to_dict()
    if args.pop("limpiar", None):
        session.pop("filtros", None)
        return redirect(url_for("inventario"))
    # Sin parámetros (por ejemplo, desde el menú) se retoman los últimos filtros usados
    if not args and session.get("filtros"):
        return redirect(url_for("inventario", **session["filtros"]))
    pagina = db.entero(args.pop("pagina", None)) or 1
    filtros = {k: v for k, v in args.items() if v}
    if filtros.get("agrupar") not in db.AGRUPABLES:
        filtros.pop("agrupar", None)
    session["filtros"] = filtros

    resumen = db.resumen(filtros)
    paginas = max(1, math.ceil(resumen["titulos"] / POR_PAGINA))
    pagina = min(pagina, paginas)
    libros = db.listar(filtros, limite=POR_PAGINA, desde=(pagina - 1) * POR_PAGINA)

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
    if agrupar == "temas":
        grupos = dict(sorted(grupos.items(), key=lambda g: g[0].lower()))
    return render_template("inventario.html", grupos=grupos, filtros=filtros, resumen=resumen,
                           pagina=pagina, paginas=paginas, dias_muerto=db.DIAS_STOCK_MUERTO, **opciones())


@app.route("/exportar.csv")
def exportar():
    filas = [["id"] + db.CAMPOS + ["creado", "ultimo_movimiento", "dias_sin_movimiento"]]
    for l in db.listar(request.args.to_dict()):
        filas.append([l["id"]] + [l[c] for c in db.CAMPOS] + [l["creado"], l["ultimo_mov"], l["dias_sin_mov"]])
    return respuesta_csv(filas, "inventario_glossalibros.csv")


# ---------- Respaldo ----------

@app.route("/respaldo")
def descargar_respaldo():
    """Descarga un zip con la base y las fotos. Se arma en un archivo temporal que se borra solo."""
    archivo = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)
    respaldo.escribir_respaldo(archivo, CARPETA_FOTOS)
    archivo.seek(0)
    return send_file(archivo, mimetype="application/zip", as_attachment=True,
                     download_name=f"glossalibros_{time.strftime('%Y-%m-%d_%H%M')}.zip")


# ---------- Errores ----------

@app.errorhandler(404)
def no_encontrado(_error):
    return render_template("error.html", titulo="No encontramos esa página",
                           detalle="Puede que el enlace esté incompleto o que el libro ya no exista."), 404


@app.errorhandler(413)
def demasiado_grande(_error):
    return render_template("error.html", titulo="La foto pesa demasiado",
                           detalle="El máximo es 25 MB. Saca la foto de nuevo o elige una más liviana."), 413


@app.errorhandler(500)
def error_interno(_error):
    # Flask ya deja el detalle del error en el registro (la ventana de iniciar.bat o el log del servidor)
    return render_template("error.html", titulo="Algo salió mal",
                           detalle="No se pudo completar la acción. Vuelve atrás e inténtalo de nuevo; "
                                   "si se repite, revisa el registro de la app."), 500


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
