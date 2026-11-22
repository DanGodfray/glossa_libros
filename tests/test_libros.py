"""Ficha del libro: guardar, duplicados, años, precios, estados y ediciones simultáneas."""
from conftest import editar, id_de_redireccion, libro


def test_isbn_repetido_avisa_y_con_confirmacion_guarda_aparte(g):
    g.db.guardar({"titulo": "A", "isbn": "9788420471839"})
    r = g.cliente.post("/guardar", data={"titulo": "Otro A", "isbn": "978-84-204-7183-9", "accion": "guardar"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Ya existe" in html and 'name="confirmar_duplicado"' in html
    assert len(g.db.listar({})) == 1
    r = g.cliente.post("/guardar", data={"titulo": "Otro A", "isbn": "978-84-204-7183-9",
                                         "confirmar_duplicado": "1", "accion": "guardar"})
    assert r.status_code == 302 and len(g.db.listar({})) == 2
    assert g.db.listar({"q": "Otro A"})[0]["isbn"] == "9788420471839"  # se guarda normalizado


def test_fecha_de_edicion_de_libros_antiguos(g):
    """A4: la fecha se guarda tal cual; el año para ordenar sale de ella."""
    casos = {"1850-1860": 1850, "[18--]": None, "s.f.": None, "c. 1890": 1890, "1963": 1963}
    for texto, anio in casos.items():
        r = g.cliente.post("/guardar", data={"titulo": texto, "fecha_edicion": texto, "accion": "guardar"})
        guardado = libro(id_de_redireccion(r))
        assert guardado["fecha_edicion"] == texto and guardado["anio"] == anio
    assert [x["fecha_edicion"] for x in g.db.listar({"orden": "anio"})][-3:] == ["1850-1860", "c. 1890", "1963"]


def test_precio_en_pesos(g):
    """A5: separadores de miles y decimales."""
    casos = {"15.000": 15000, "15000,50": 15001, "15000,49": 15000, "$ 12.990": 12990,
             "15,000": 15000, "15.000,5": 15001, "": None, "gratis": None}
    for texto, esperado in casos.items():
        assert g.db.parsear_clp(texto) == esperado, texto
    i = g.db.guardar({"titulo": "P", "precio": "15000,50"})
    assert libro(i)["precio"] == 15001


def test_reponer_stock_saca_de_vendido(g):
    """A2: un libro con stock nunca queda 'Vendido'."""
    i = g.db.guardar({"titulo": "Agotado", "stock": "1", "precio": "1000"})
    g.db.registrar_venta(i, 1, 1000, "2026-11-01")
    assert libro(i)["estado_ig"] == "Vendido"
    editar(g.cliente, i, stock="2", estado_ig="Vendido")
    assert libro(i)["stock"] == 2 and libro(i)["estado_ig"] == "Pendiente"
    g.db.cambiar_estado_ig(i, "Vendido")  # tampoco por el cambio rápido
    assert libro(i)["estado_ig"] == "Pendiente"


def test_formulario_viejo_no_pisa_una_venta(g):
    """A1: se edita en un teléfono mientras en otro se vende."""
    i = g.db.guardar({"titulo": "Concurrencia", "stock": "2", "precio": "5000"})
    formulario_viejo = libro(i)
    g.db.registrar_venta(i, 1, 5000, "2026-11-01")  # otro teléfono vende 1 (stock 2 -> 1)

    datos = {"titulo": "Concurrencia editada", "stock": "2", "precio": "6000", "accion": "guardar",
             "modificado": formulario_viejo["modificado"], "stock_original": "2"}
    r = g.cliente.post(f"/libro/{i}/guardar", data=datos)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "cambió en otro dispositivo" in html  # no guarda; pide revisar
    assert "Precio" in html and libro(i)["titulo"] == "Concurrencia" and libro(i)["stock"] == 1

    r = editar(g.cliente, i, titulo="Concurrencia editada", precio="6000")  # confirma con datos al día
    assert r.status_code == 302
    assert libro(i)["titulo"] == "Concurrencia editada" and libro(i)["precio"] == 6000 and libro(i)["stock"] == 1


def test_stock_se_aplica_como_diferencia(g):
    i = g.db.guardar({"titulo": "Delta", "stock": "3"})
    g.db.guardar({"titulo": "Delta", "stock": "5"}, i, stock_original=3)  # +2
    assert libro(i)["stock"] == 5
    g.db.registrar_venta(i, 2, 0, "2026-11-01")  # 3
    g.db.guardar({"titulo": "Delta", "stock": "6"}, i, stock_original=5)  # +1 sobre el valor actual
    assert libro(i)["stock"] == 4


def test_texto_instagram(g):
    i = g.db.guardar({"titulo": "Rayuela", "autor": "Julio Cortázar", "fecha_edicion": "c. 1963",
                      "precio": "85000", "stock": "1"})
    texto = g.servicios.caption_instagram(g.db.obtener(i))
    assert "c. 1963" in texto and "$85.000 CLP" in texto and "Escríbenos por DM" in texto
    g.db.registrar_venta(i, 1, 85000, "2026-11-01")
    texto = g.servicios.caption_instagram(g.db.obtener(i))
    assert "Vendido ✔" in texto and "Escríbenos" not in texto
