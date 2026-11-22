"""Ventas: registrar, rechazar, anular, página de ventas y exportación."""
from conftest import libro


def test_vender_descuenta_y_marca_vendido_al_agotarse(g):
    i = g.db.guardar({"titulo": "A", "stock": "2", "precio": "10000"})
    g.cliente.post(f"/libro/{i}/vender", data={"cantidad": "1", "precio": "9.000", "fecha": "2026-11-20",
                                              "comprador": "@ana"})
    assert libro(i)["stock"] == 1 and libro(i)["estado_ig"] != "Vendido"
    assert g.db.ventas_de(i)[0]["precio"] == 9000
    g.cliente.post(f"/libro/{i}/vender", data={"cantidad": "1", "fecha": "2026-11-21"})
    assert libro(i)["stock"] == 0 and libro(i)["estado_ig"] == "Vendido"


def test_vender_mas_que_el_stock_se_rechaza(g):
    i = g.db.guardar({"titulo": "B", "stock": "1"})
    r = g.cliente.post(f"/libro/{i}/vender", data={"cantidad": "2", "fecha": "2026-11-01"})
    assert "msg=error_venta" in r.headers["Location"] and libro(i)["stock"] == 1


def test_anular_devuelve_stock_y_estado(g):
    i = g.db.guardar({"titulo": "C", "stock": "1"})
    g.db.registrar_venta(i, 1, 5000, "2026-11-01")
    venta = g.db.ventas_de(i)[0]["id"]
    g.cliente.post(f"/venta/{venta}/anular")
    assert libro(i)["stock"] == 1 and libro(i)["estado_ig"] == "Pendiente"


def test_vendido_desde_la_lista_lleva_a_registrar_la_venta(g):
    i = g.db.guardar({"titulo": "D", "stock": "1"})
    r = g.cliente.post(f"/libro/{i}/ig", data={"estado_ig": "Vendido"})
    assert "msg=vender" in r.headers["Location"] and "#venta" in r.headers["Location"]
    assert libro(i)["estado_ig"] != "Vendido"
    g.cliente.post(f"/libro/{i}/ig", data={"estado_ig": "Inventado"})
    assert libro(i)["estado_ig"] != "Inventado"


def test_ficha_muestra_el_aviso_dentro_de_ventas(g):
    i = g.db.guardar({"titulo": "E", "stock": "2", "precio": "1000"})
    g.db.registrar_venta(i, 1, 1000, "2026-11-18", "@ana")
    html = g.cliente.get(f"/libro/{i}?msg=venta").get_data(as_text=True)
    assert "Venta registrada" in html and "@ana" in html and "18-11-2026" in html and "Anular" in html
    assert html.index("Venta registrada") > html.index('id="venta"')


def test_pagina_de_ventas_y_libro_eliminado(g):
    a = g.db.guardar({"titulo": "Libro A", "stock": "2"})
    b = g.db.guardar({"titulo": "Libro B", "stock": "1"})
    g.db.registrar_venta(a, 1, 9000, "2026-11-05")
    g.db.registrar_venta(b, 1, 5000, "2026-10-01")
    g.cliente.post(f"/libro/{b}/eliminar")
    html = g.cliente.get("/ventas").get_data(as_text=True)
    assert "2 ventas" in html and "$14.000" in html and "Noviembre 2026" in html and "Octubre 2026" in html
    assert "eliminado del inventario" in html and "05-11-2026" in html
    csv = g.cliente.get("/ventas.csv").get_data(as_text=True)
    assert csv.splitlines()[0].lstrip("﻿").startswith("id;libro_id;titulo")
