"""Genera las capturas del README con datos de demostración (nunca usa inventario.db).

Requiere Windows con Microsoft Edge e internet (portadas de Open Library y una búsqueda real de ISBN).
Uso, desde la carpeta del proyecto:

    .venv\\Scripts\\python.exe docs\\generar_capturas.py . docs\\capturas %TEMP%\\capturas_html
"""
import datetime
import pathlib
import subprocess
import sys
import tempfile

from PIL import Image

PROY, SALIDA, HTMLS = (pathlib.Path(p).resolve() for p in sys.argv[1:4])
SALIDA.mkdir(parents=True, exist_ok=True)
HTMLS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PROY))

import db  # noqa: E402

db.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "demo.db"  # nunca la base real
import app as a  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PERFIL = tempfile.mkdtemp()
COVER = "https://covers.openlibrary.org/b/id/{}-M.jpg"

DEMO = [
    dict(titulo="Rayuela", autor="Julio Cortázar", editorial="Sudamericana", lugar_edicion="Buenos Aires",
         anio="1963", idioma="Español", edicion="Primera", genero="Novela", temas="Literatura argentina, Boom latinoamericano",
         caracteristicas="Primera edición, Rústica", estado="Bueno", notas_estado="Lomo con desgaste leve",
         precio="85000", stock="1", ubicacion="Vitrina", procedencia="Biblioteca familiar", estado_ig="Publicado",
         fecha_ig="2026-11-10", portada_url=COVER.format(1047466)),
    dict(titulo="Ficciones", autor="Jorge Luis Borges", editorial="Emecé", lugar_edicion="Buenos Aires", anio="1956",
         idioma="Español", edicion="Segunda", genero="Cuento", temas="Literatura argentina",
         caracteristicas="Tapa dura, Ex libris", estado="Muy bueno", precio="45000", stock="1",
         ubicacion="Estante A, fila 1", procedencia="Feria Persa Biobío", estado_ig="Listo",
         portada_url=COVER.format(10832290)),
    dict(titulo="Pedro Páramo", autor="Juan Rulfo", editorial="Fondo de Cultura Económica", lugar_edicion="México",
         anio="1971", idioma="Español", genero="Novela", temas="Literatura mexicana", caracteristicas="Rústica",
         estado="Bueno", precio="12000", stock="2", ubicacion="Estante A, fila 2", procedencia="Feria Persa Biobío",
         estado_ig="Pendiente", portada_url=COVER.format(5419076)),
    dict(titulo="La casa de los espíritus", autor="Isabel Allende", editorial="Plaza & Janés", lugar_edicion="Barcelona",
         anio="1982", idioma="Español", edicion="Primera", genero="Novela", temas="Literatura chilena",
         caracteristicas="Primera edición, Sobrecubierta", estado="Muy bueno", precio="60000", stock="1",
         ubicacion="Vitrina", procedencia="Biblioteca familiar", estado_ig="Listo", portada_url=COVER.format(3205226)),
    dict(titulo="Veinte poemas de amor y una canción desesperada", autor="Pablo Neruda", editorial="Nascimento",
         lugar_edicion="Santiago", anio="1932", idioma="Español", edicion="Segunda", genero="Poesía",
         temas="Literatura chilena, Poesía amorosa", caracteristicas="Firmado o dedicado, Rústica", estado="Aceptable",
         notas_estado="Hojas amarillentas", precio="250000", stock="1", ubicacion="Vitrina",
         procedencia="Remate", estado_ig="Pendiente", portada_url=COVER.format(747596)),
    dict(titulo="Martín Rivas", autor="Alberto Blest Gana", editorial="Zig-Zag", lugar_edicion="Santiago", anio="1955",
         idioma="Español", genero="Novela", temas="Literatura chilena, Chile siglo XIX", caracteristicas="Tapa dura",
         estado="Bueno", precio="15000", stock="3", ubicacion="Estante B, fila 1", procedencia="Feria Persa Biobío",
         estado_ig="Publicado", fecha_ig="2026-09-01", portada_url=COVER.format(4909788)),
    dict(titulo="El túnel", autor="Ernesto Sabato", editorial="Seix Barral", lugar_edicion="Barcelona", anio="1978",
         idioma="Español", genero="Novela", temas="Literatura argentina", caracteristicas="Rústica", estado="Como nuevo",
         precio="9000", stock="1", ubicacion="Estante B, fila 2", procedencia="Donación", estado_ig="Pendiente",
         portada_url=COVER.format(5517733)),
]
for libro in DEMO:
    db.guardar(libro)

cl = a.app.test_client()


ANCHO = 390  # ancho de un iPhone en píxeles CSS


def capturar(nombre, html, alto=844, ancla=""):
    """Edge headless no acepta ventanas tan angostas, así que la página va en un iframe de 390 px
    dentro de una ventana más ancha, y después se recorta. `ancla` es el id de la sección a mostrar."""
    pagina = HTMLS / f"{nombre}.html"
    pagina.write_text(html, encoding="utf-8")
    marco = HTMLS / f"{nombre}_marco.html"
    src = pagina.name + (f"#{ancla}" if ancla else "")
    marco.write_text(f'<html><body style="margin:0;background:#faf7f2"><iframe src="{src}" '
                     f'style="border:0;display:block;width:{ANCHO}px;height:{alto}px"></iframe></body></html>',
                     encoding="utf-8")
    png = SALIDA / f"{nombre}.png"
    # Edge a veces deja el borde inferior sin dibujar: la ventana es 100 px más alta y eso se descarta
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={PERFIL}",
                    "--force-device-scale-factor=2", f"--window-size=800,{alto + 100}", "--virtual-time-budget=8000",
                    f"--screenshot={png}", marco.as_uri()], check=True, capture_output=True)
    with Image.open(png) as img:
        recorte = img.crop((0, 0, ANCHO * 2, alto * 2))
    recorte.save(png, optimize=True)
    print("ok", png.name, recorte.size)


# Historia de ventas y antigüedad, con fechas relativas a hoy para que el stock muerto siempre se vea igual
def id_de(titulo):
    return db.listar({"q": titulo})[0]["id"]


def hace(dias):
    return (datetime.date.today() - datetime.timedelta(days=dias)).isoformat()


with db.conectar() as con:
    for titulo, dias in (("El túnel", 400), ("Pedro Páramo", 230), ("Martín Rivas", 260), ("Ficciones", 120)):
        con.execute("UPDATE libros SET creado = ? WHERE id = ?", (hace(dias) + " 12:00:00", id_de(titulo)))
db.registrar_venta(id_de("Martín Rivas"), 2, 15000, hace(34), "@lectora_sur", "Envío por Starken")
db.registrar_venta(id_de("Ficciones"), 1, 45000, hace(13), "", "Feria del libro usado")
db.registrar_venta(id_de("Rayuela"), 1, 80000, hace(3), "@bibliofilo.stgo", "Retiro en persona")
with db.conectar() as con:  # Rayuela vuelve a tener 1 ejemplar, para mostrar su ficha con stock
    con.execute("UPDATE libros SET stock = 1, estado_ig = 'Publicado' WHERE id = ?", (id_de("Rayuela"),))

capturar("01_inventario", cl.get("/inventario?orden=reciente").get_data(as_text=True), alto=1250)
capturar("02_inventario_agrupado", cl.get("/inventario?agrupar=estado_ig").get_data(as_text=True), alto=1100)
capturar("03_nuevo_libro", cl.get("/nuevo").get_data(as_text=True))

# Búsqueda real en los catálogos con un ISBN de ejemplo (Cien años de soledad, Alfaguara)
r = cl.post("/escanear", data={"isbn": "978-84-204-7183-9", "ubicacion": "Estante A, fila 1"})
capturar("04_datos_encontrados", r.get_data(as_text=True), alto=1100)

ficha = cl.get(f"/libro/{id_de('Rayuela')}?msg=guardado").get_data(as_text=True)
capturar("05_ficha_guardada", ficha, alto=1100)
capturar("06_texto_instagram", ficha, alto=430, ancla="instagram")
capturar("07_registrar_venta", cl.get(f"/libro/{id_de('Martín Rivas')}?msg=venta").get_data(as_text=True),
         alto=760, ancla="venta")
capturar("08_ventas", cl.get("/ventas").get_data(as_text=True), alto=540)
capturar("09_stock_muerto", cl.get("/inventario?sin_mov=6&orden=sin_mov").get_data(as_text=True), alto=1150)

a.CLAVE = "demo"
capturar("10_contrasena", cl.get("/entrar").get_data(as_text=True), alto=340)
