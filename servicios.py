"""Leer el ISBN desde una foto y buscar los datos del libro en APIs gratuitas."""
import os

import requests
import zxingcpp
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()  # permite abrir fotos HEIC del iPhone

# Open Library pide identificarse con el nombre de la app y un correo de contacto.
# El correo se configura en la variable OPENLIBRARY_EMAIL para no publicarlo en el código.
_CONTACTO = os.environ.get("OPENLIBRARY_EMAIL")
CABECERAS = {"User-Agent": f"GlossaLibros/1.0 ({_CONTACTO})" if _CONTACTO else "GlossaLibros/1.0"}
GOOGLE_BOOKS_KEY = os.environ.get("GOOGLE_BOOKS_KEY")  # opcional

IDIOMAS = {
    "es": "Español", "spa": "Español", "en": "Inglés", "eng": "Inglés",
    "fr": "Francés", "fre": "Francés", "fra": "Francés", "de": "Alemán", "ger": "Alemán",
    "it": "Italiano", "ita": "Italiano", "pt": "Portugués", "por": "Portugués",
    "la": "Latín", "lat": "Latín", "ca": "Catalán", "cat": "Catalán",
}

# Prefijos de grupo ISBN (después de 978/979) -> zona del editor. Es una pista, no una regla.
GRUPOS_ISBN = {
    "9780": "Zona inglesa (EE. UU., Reino Unido, etc.)",
    "9781": "Zona inglesa (EE. UU., Reino Unido, etc.)",
    "9782": "Zona francesa", "9783": "Zona alemana", "9784": "Japón",
    "9785": "Rusia / ex URSS", "9787": "China", "97884": "España",
    "97885": "Brasil", "97888": "Italia", "978950": "Argentina", "978987": "Argentina",
    "978956": "Chile", "978958": "Colombia", "978959": "Cuba", "978968": "México",
    "978970": "México", "978607": "México", "978972": "Portugal", "978989": "Portugal",
    "978980": "Venezuela", "978612": "Perú", "9789972": "Perú", "9789974": "Uruguay",
    "9789978": "Ecuador", "9789942": "Ecuador", "9798": "EE. UU.", "97910": "Francia",
    "97912": "Italia",
}


# ---------- ISBN ----------

def normalizar_isbn(texto):
    """Quita guiones y espacios; convierte ISBN-10 en ISBN-13. Devuelve None si no es válido."""
    s = "".join(c for c in (texto or "").upper() if c.isdigit() or c == "X")
    if len(s) == 10 and _valido_10(s):
        s = "978" + s[:9]
        return s + _digito_13(s)
    if len(s) == 13 and s[:3] in ("978", "979") and s[-1] == _digito_13(s[:12]):
        return s
    return None


def _valido_10(s):
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
    return total % 11 == 0


def _digito_13(doce):
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(doce))
    return str((10 - total % 10) % 10)


def zona_por_isbn(isbn):
    """Busca el prefijo más largo que coincida."""
    for largo in range(7, 3, -1):
        if isbn[:largo] in GRUPOS_ISBN:
            return GRUPOS_ISBN[isbn[:largo]]
    return ""


# ---------- Código de barras ----------

def leer_isbn_de_foto(archivo):
    """Recibe la foto subida y busca un código de barras.

    Devuelve (isbn, codigos): el ISBN-13 o None, y la lista de códigos leídos
    (sirve para avisar cuando se leyó un código que no es ISBN, p. ej. libros antiguos).
    """
    img = Image.open(archivo)
    img = ImageOps.exif_transpose(img).convert("L")  # girar según la cámara y pasar a gris
    img.thumbnail((2000, 2000))  # fotos más chicas se leen más rápido
    codigos = [c.text for c in zxingcpp.read_barcodes(img)]
    for texto in codigos:
        isbn = normalizar_isbn(texto)
        if isbn:
            return isbn, codigos
    return None, codigos


# ---------- Búsqueda en APIs ----------

def buscar_libro(isbn):
    """Consulta Open Library y, si faltan datos, Google Books. Devuelve un diccionario."""
    datos = {"isbn": isbn}
    try:
        datos.update(_open_library(isbn))
    except requests.RequestException:
        pass
    faltan = not datos.get("titulo") or not datos.get("idioma")
    if faltan:
        try:
            for campo, valor in _google_books(isbn).items():
                if not datos.get(campo):  # solo rellena lo que Open Library no trajo
                    datos[campo] = valor
        except requests.RequestException:
            pass
    if not datos.get("lugar_edicion"):
        datos["lugar_edicion"] = zona_por_isbn(isbn)
    return datos


def _open_library(isbn):
    r = requests.get(
        "https://openlibrary.org/api/books",
        params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
        headers=CABECERAS, timeout=10,
    )
    r.raise_for_status()
    libro = r.json().get(f"ISBN:{isbn}")
    if not libro:
        return {}
    titulo = libro.get("title", "")
    if libro.get("subtitle"):
        titulo += ": " + libro["subtitle"]
    return _sin_vacios({
        "titulo": titulo,
        "autor": ", ".join(a["name"] for a in libro.get("authors", [])),
        "editorial": ", ".join(p["name"] for p in libro.get("publishers", [])),
        "lugar_edicion": ", ".join(p["name"] for p in libro.get("publish_places", [])),
        "anio": _anio(libro.get("publish_date", "")),
        "portada_url": libro.get("cover", {}).get("large") or libro.get("cover", {}).get("medium", ""),
        "temas": ", ".join(s["name"] for s in libro.get("subjects", [])[:4]),
    })


def _google_books(isbn):
    params = {"q": f"isbn:{isbn}"}
    if GOOGLE_BOOKS_KEY:
        params["key"] = GOOGLE_BOOKS_KEY
    r = requests.get("https://www.googleapis.com/books/v1/volumes", params=params, timeout=10)
    r.raise_for_status()
    items = r.json().get("items") or []
    if not items:
        return {}
    v = items[0]["volumeInfo"]
    titulo = v.get("title", "") + (": " + v["subtitle"] if v.get("subtitle") else "")
    portada = v.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://")
    return _sin_vacios({
        "titulo": titulo,
        "autor": ", ".join(v.get("authors", [])),
        "editorial": v.get("publisher", ""),
        "anio": _anio(v.get("publishedDate", "")),
        "idioma": IDIOMAS.get(v.get("language", ""), v.get("language", "")),
        "genero": (v.get("categories") or [""])[0],
        "portada_url": portada,
    })


def _anio(texto):
    """Extrae el primer año de 4 dígitos de un texto como 'March 1967' o '1967-05-30'."""
    for i in range(len(texto) - 3):
        if texto[i:i + 4].isdigit():
            return texto[i:i + 4]
    return ""


def _sin_vacios(d):
    return {k: v for k, v in d.items() if v}


# ---------- Instagram ----------

def caption_instagram(libro):
    """Arma un texto base para el posteo. Edítalo a gusto antes de publicar."""
    lineas = [f"📚 {libro['titulo']}"]
    if libro["autor"]:
        lineas.append(f"✍️ {libro['autor']}")
    detalle = ", ".join(str(x) for x in (libro["editorial"], libro["lugar_edicion"], libro["anio"]) if x)
    if detalle:
        lineas.append(detalle)
    if libro["edicion"]:
        lineas.append(libro["edicion"])
    if libro["caracteristicas"]:
        lineas.append("✨ " + libro["caracteristicas"])
    if libro["estado"]:
        estado = libro["estado"] + (f" ({libro['notas_estado']})" if libro["notas_estado"] else "")
        lineas.append(f"Estado: {estado}")
    if libro["precio"]:
        lineas.append(f"💰 ${libro['precio']:,} CLP".replace(",", "."))
    lineas.append("Escríbenos por DM para comprar.")
    etiquetas = ["#librosantiguos", "#libroslatam", "#bibliofilia", "#glossalibros"]
    for texto in (libro["genero"], libro["temas"], libro["autor"]):
        for parte in (texto or "").split(","):
            limpio = "".join(c for c in parte.title() if c.isalnum())
            if limpio:
                etiquetas.append("#" + limpio)
    return "\n".join(lineas) + "\n\n" + " ".join(dict.fromkeys(etiquetas))
