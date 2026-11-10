"""Pruebas del simulador (sin AWS).

Ejecutar desde la raíz del repositorio:  python -m unittest discover -s tests -v
"""
import unittest
from collections import Counter
from datetime import datetime

from olist_simulador import Generador, ParametrosSimulacion, TIPOS, validar
from olist_simulador.credenciales import leer_csv
from olist_simulador.ejecucion import Ejecucion
from olist_simulador.emisor import EmisorPrueba, lotes


def eventos(**kwargs):
    p = ParametrosSimulacion(**{"dias": 20, "duracion_min": 2, "caudal": 50, **kwargs})
    return list(Generador(p).eventos())


class PruebasParametros(unittest.TestCase):

    def test_parametros_por_defecto_validos(self):
        self.assertEqual(ParametrosSimulacion().errores(), [])

    def test_rangos_invalidos(self):
        p = ParametrosSimulacion(dias=0, duracion_min=0, caudal=10_000, tipos=("pedido_aprobado",))
        self.assertEqual(len(p.errores()), 4)

    def test_generador_rechaza_parametros_invalidos(self):
        with self.assertRaises(ValueError):
            Generador(ParametrosSimulacion(caudal=0))

    def test_plan_consistente(self):
        p = ParametrosSimulacion(dias=30, duracion_min=10, caudal=50)
        plan = Generador(p).plan()
        self.assertEqual(plan.total_eventos, 30_000)
        self.assertAlmostEqual(plan.segundos_por_dia, 20)
        self.assertAlmostEqual(plan.ventas_estimadas * plan.eventos_por_venta, 30_000, delta=5)


class PruebasGenerador(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.lista = eventos(bloques=("pagos", "producto", "vendedor", "textos_resena"))

    def test_todos_los_eventos_cumplen_el_contrato(self):
        for _, m in self.lista:
            self.assertIsNone(validar(m), m)

    def test_orden_cronologico_y_dentro_del_periodo(self):
        momentos = [t for t, _ in self.lista]
        self.assertEqual(momentos, sorted(momentos))
        self.assertGreaterEqual(momentos[0], datetime(2026, 9, 4))
        self.assertLess(momentos[-1], datetime(2026, 9, 24))

    def test_volumen_cercano_al_objetivo(self):
        self.assertAlmostEqual(len(self.lista), 6_000, delta=6_000 * 0.15)

    def test_evento_id_unico(self):
        ids = [m["evento_id"] for _, m in self.lista]
        self.assertEqual(len(ids), len(set(ids)))

    def test_misma_semilla_mismos_eventos(self):
        a = eventos(semilla=7, dias=5, duracion_min=1)
        b = eventos(semilla=7, dias=5, duracion_min=1)
        c = eventos(semilla=8, dias=5, duracion_min=1)
        self.assertEqual([m for _, m in a], [m for _, m in b])
        self.assertNotEqual([m["evento_id"] for _, m in a][:50], [m["evento_id"] for _, m in c][:50])

    def test_ciclo_de_vida_coherente(self):
        fechas = {}
        for t, m in self.lista:
            p = m["payload"]
            oid = p["pedido"]["order_id"] if m["tipo"] == "pedido_creado" else p.get("order_id")
            fechas.setdefault(oid, {})[m["tipo"]] = t
        orden = ["pedido_creado", "pedido_aprobado", "pedido_enviado", "pedido_entregado"]
        for f in fechas.values():
            presentes = [f[t] for t in orden if t in f]
            self.assertEqual(presentes, sorted(presentes))
            if "pedido_cancelado" in f:
                self.assertNotIn("pedido_entregado", f)
            if "resena_creada" in f and "pedido_entregado" in f:
                self.assertGreater(f["resena_creada"], f["pedido_entregado"])

    def test_tipos_y_bloques_seleccionados(self):
        lista = eventos(tipos=("pedido_creado", "pedido_entregado"), bloques=())
        self.assertEqual({m["tipo"] for _, m in lista}, {"pedido_creado", "pedido_entregado"})
        creado = next(m["payload"] for _, m in lista if m["tipo"] == "pedido_creado")
        self.assertNotIn("pagos", creado)
        self.assertEqual(set(creado["items"][0]["producto"]), {"product_id"})
        self.assertEqual(set(creado["items"][0]["vendedor"]), {"seller_id"})

    def test_pagos_cuadran_con_items(self):
        for _, m in self.lista:
            if m["tipo"] == "pedido_creado":
                p = m["payload"]
                total = sum(i["price"] + i["freight_value"] for i in p["items"])
                self.assertAlmostEqual(sum(x["payment_value"] for x in p["pagos"]), total, delta=0.02)

    def test_proporciones_similares_a_olist(self):
        lista = eventos(dias=120, duracion_min=20, caudal=40)
        tipos = Counter(m["tipo"] for _, m in lista)
        creados = {m["payload"]["pedido"]["order_id"]: m["payload"] for _, m in lista
                   if m["tipo"] == "pedido_creado"}
        entregas = [(creados[m["payload"]["order_id"]], datetime.fromisoformat(m["payload"]["fecha"]))
                    for _, m in lista if m["tipo"] == "pedido_entregado"]
        tarde = sum(e.date() > datetime.fromisoformat(c["pedido"]["order_estimated_delivery_date"]).date()
                    for c, e in entregas) / len(entregas)
        tarjeta = sum(c["pagos"][-1]["payment_type"] == "credit_card" for c in creados.values()) / len(creados)
        self.assertTrue(0.04 < tarde < 0.10, tarde)
        self.assertTrue(0.70 < tarjeta < 0.83, tarjeta)
        self.assertLess(tipos["pedido_cancelado"] / tipos["pedido_creado"], 0.03)


class PruebasEnvio(unittest.TestCase):

    def test_lotes_de_hasta_diez(self):
        mensajes = [m for _, m in eventos(dias=2, duracion_min=1, caudal=10)][:95]
        tamanos = [len(l) for l in lotes(mensajes)]
        self.assertEqual(sum(tamanos), 95)
        self.assertTrue(all(t <= 10 for t in tamanos))

    def test_ejecucion_en_prueba_sin_envio(self):
        g = Generador(ParametrosSimulacion(dias=3, duracion_min=1, caudal=20))
        ej = Ejecucion(g, EmisorPrueba(), sin_ritmo=True)
        ej.iniciar()
        ej.esperar(60)
        m = ej.metricas.copia()
        self.assertEqual(m["estado"], "finalizada")
        self.assertEqual(m["fallidos"], 0)
        self.assertEqual(m["enviados"], len(list(g.eventos())))
        self.assertTrue(set(m["por_tipo"]) <= set(TIPOS))


class PruebasCredenciales(unittest.TestCase):
    CLAVE = "AKIA" + "A" * 16
    SECRETO = "s" * 40

    def test_csv_de_access_keys(self):
        contenido = f"﻿Access key ID,Secret access key\n{self.CLAVE},{self.SECRETO}\n".encode()
        c = leer_csv(contenido)
        self.assertEqual(c.access_key_id, self.CLAVE)
        self.assertNotIn(self.SECRETO, repr(c))

    def test_csv_con_otras_columnas(self):
        contenido = (f"User name,Password,Access key ID,Secret access key,Console login link\n"
                     f"u,p,{self.CLAVE},{self.SECRETO},x\n").encode()
        self.assertEqual(leer_csv(contenido).secret_access_key, self.SECRETO)

    def test_csv_invalido(self):
        for contenido in (b"", b"a,b\n1,2\n", f"Access key ID,Secret access key\nX,{self.SECRETO}\n".encode()):
            with self.assertRaises(ValueError):
                leer_csv(contenido)


if __name__ == "__main__":
    unittest.main()
