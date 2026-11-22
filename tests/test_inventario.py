"""Inventario: stock muerto, filtros, páginas, resumen y filtros recordados."""
from conftest import libro


def _envejecer(g, libro_id, dias):
    with g.db.conectar() as con:
        con.execute("UPDATE libros SET creado = datetime('now', 'localtime', ?) WHERE id = ?",
                    (f"-{dias} days", libro_id))


def test_stock_muerto(g):
    a = g.db.guardar({"titulo": "Libro A", "stock": "2", "precio": "10000"})
    g.db.guardar({"titulo": "Libro B", "stock": "1", "precio": "5000"})
    _envejecer(g, a, 200)
    html = g.cliente.get("/inventario").get_data(as_text=True)
    assert "1 título lleva más de 6 meses" in html and "Sin moverse hace 6 meses" in html
    assert [x["id"] for x in g.db.listar({"sin_mov": "6"})] == [a]
    assert g.db.listar({"orden": "sin_mov"})[0]["id"] == a
    g.db.registrar_venta(a, 1, 9000, g.db.ahora()[:10])
    assert g.db.listar({"sin_mov": "6"}) == []  # vendido hoy: ya no es stock muerto


def test_filtros(g):
    g.db.guardar({"titulo": "Libro A", "stock": "0", "precio": "10000", "procedencia": "Remate"})
    b = g.db.guardar({"titulo": "Libro B", "stock": "1", "precio": "5000", "procedencia": "Feria"})
    assert [x["id"] for x in g.db.listar({"procedencia": "Feria"})] == [b]
    assert [x["titulo"] for x in g.db.listar({"precio_min": "4.000", "precio_max": "6000"})] == ["Libro B"]
    assert [x["titulo"] for x in g.db.listar({"disponibilidad": "agotados"})] == ["Libro A"]
    for url in ["/inventario?agrupar=foo", "/inventario?agrupar=procedencia",
                "/inventario?sin_mov=12&orden=sin_mov", "/exportar.csv"]:
        assert g.cliente.get(url).status_code == 200, url
    csv = g.cliente.get("/exportar.csv").get_data(as_text=True)
    assert "dias_sin_movimiento" in csv.splitlines()[0] and "fecha_edicion" in csv.splitlines()[0]


def test_paginacion_y_resumen_de_todo_el_filtro(g):
    """C4: 120 libros -> 3 páginas de 50; el resumen cuenta todos, no solo la página."""
    for n in range(120):
        g.db.guardar({"titulo": f"Libro {n:03d}", "stock": "1", "precio": "1000"})
    html = g.cliente.get("/inventario?orden=titulo").get_data(as_text=True)
    assert "120 títulos" in html and "$120.000" in html and "Página 1 de 3" in html
    assert html.count('class="tarjeta"') == 50 and "Libro 000" in html and "Libro 050" not in html
    html = g.cliente.get("/inventario?orden=titulo&pagina=3").get_data(as_text=True)
    assert html.count('class="tarjeta"') == 20 and "Libro 119" in html and "Anterior" in html
    assert 'loading="lazy"' in html or "<img alt=\"\">" in html
    html = g.cliente.get("/inventario?orden=titulo&pagina=99").get_data(as_text=True)
    assert "Página 3 de 3" in html  # una página que no existe muestra la última


def test_agrupar_con_paginas_mantiene_los_grupos_juntos(g):
    for n in range(60):
        g.db.guardar({"titulo": f"L{n}", "ubicacion": "Estante A" if n % 2 else "Estante B"})
    primera = g.db.listar({"agrupar": "ubicacion"}, limite=50)
    ubicaciones = [x["ubicacion"] for x in primera]
    assert ubicaciones == sorted(ubicaciones)  # primero todo el estante A, luego el B


def test_filtros_recordados_y_limpiar(g):
    g.db.guardar({"titulo": "Novela X", "genero": "Novela"})
    g.cliente.get("/inventario?genero=Novela&orden=titulo")
    r = g.cliente.get("/inventario")  # desde el menú
    assert r.status_code == 302 and "genero=Novela" in r.headers["Location"]
    html = g.cliente.get(r.headers["Location"]).get_data(as_text=True)
    assert "Limpiar filtros" in html
    g.cliente.get("/inventario?limpiar=1")
    assert g.cliente.get("/inventario").status_code == 200


def test_resumen_sql_coincide_con_la_lista(g):
    g.db.guardar({"titulo": "A", "stock": "2", "precio": "1000"})
    g.db.guardar({"titulo": "B", "stock": "0", "precio": "5000"})
    g.db.guardar({"titulo": "C", "stock": "3"})
    r = g.db.resumen({})
    assert (r["titulos"], r["ejemplares"], r["valor"]) == (3, 5, 2000)
    assert libro(1)["titulo"] == "A"
