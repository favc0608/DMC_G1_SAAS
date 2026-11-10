"""Carga del perfil estadístico de Olist y muestreo ponderado."""
import bisect
import gzip
import json
from functools import lru_cache
from importlib import resources
from itertools import accumulate


class Distribucion:
    """Elección ponderada de valores (bisección sobre pesos acumulados)."""

    def __init__(self, valores, pesos):
        self.valores = list(valores)
        self.acumulados = list(accumulate(float(p) for p in pesos))
        self.total = self.acumulados[-1]

    @classmethod
    def de_pares(cls, pares):
        return cls([v for v, _ in pares], [p for _, p in pares])

    def elegir(self, rng):
        return self.valores[bisect.bisect_right(self.acumulados, rng.random() * self.total)]


def cuantil(rng, cuantiles):
    """Valor aleatorio interpolando entre cuantiles equiespaciados."""
    x = rng.random() * (len(cuantiles) - 1)
    i = int(x)
    if i >= len(cuantiles) - 1:
        return cuantiles[-1]
    return cuantiles[i] + (cuantiles[i + 1] - cuantiles[i]) * (x - i)


class Perfil:
    """Vista del perfil con índices y distribuciones precalculadas."""

    def __init__(self, datos):
        self.datos = datos
        cat = datos["catalogo"]
        campos = cat["productos_campos"]
        self.productos = [dict(zip(campos, p)) for p in cat["productos"]]
        self.productos_por_popularidad = Distribucion(range(len(self.productos)),
                                                      (p["ventas"] for p in self.productos))
        self.vendedores = [dict(zip(cat["vendedores_campos"], v)) for v in cat["vendedores"]]
        self.vendedores_de = [Distribucion.de_pares(p["vendedores"]) for p in self.productos]

        comp = datos["composicion"]
        self.productos_por_pedido = Distribucion.de_pares(comp["productos_por_pedido"])
        self.unidades_por_producto = Distribucion.de_pares(comp["unidades_por_producto"])
        self.dias_limite_envio = comp["dias_limite_envio"]

        geo = datos["geografia"]
        self.zips = [dict(zip(geo["zips_campos"], z)) for z in geo["zips"]]
        self.zips_por_peso = Distribucion(range(len(self.zips)), (z["peso"] for z in self.zips))
        self.zips_por_estado = {}
        for i, z in enumerate(self.zips):
            self.zips_por_estado.setdefault(z["estado"], ([], []))
            self.zips_por_estado[z["estado"]][0].append(i)
            self.zips_por_estado[z["estado"]][1].append(z["peso"])
        self.zips_por_estado = {e: Distribucion(*v) for e, v in self.zips_por_estado.items()}
        self.clientes = [dict(zip(geo["clientes_campos"], c)) for c in geo["clientes"]]
        self.tasa_recompra = geo["tasa_recompra"]
        self.tasa_cambio_direccion = geo["tasa_cambio_direccion"]
        self.cambio_mismo_estado = geo["cambio_mismo_estado"]

        est = datos["estacionalidad"]
        self.semana_hora = est["semana_hora"]
        self.ventas_dia_referencia = est["ventas_dia_referencia"]

        pg = datos["pagos"]
        self.tipo_pago = Distribucion.de_pares(pg["tipo_pago"])
        self.tasa_pago_combinado = pg["tasa_pago_combinado"]
        self.tramos_valor = pg["tramos_valor"]
        self.cuotas_tarjeta = {int(k): Distribucion.de_pares(v) for k, v in pg["cuotas_tarjeta"].items()}

        self.flete = datos["flete"]
        self.logistica = datos["logistica"]
        rv = datos["resenas"]
        self.tasa_resena = rv["tasa_resena"]
        self.puntajes = {g: Distribucion.de_pares(v) for g, v in rv["puntajes"].items()}
        self.dias_tras_entrega = rv["dias_tras_entrega"]
        self.horas_respuesta = rv["horas_respuesta"]
        self.textos = {int(k): v for k, v in rv["textos"].items()}

    def muestras_logistica(self, estado_vendedor, estado_cliente):
        lg = self.logistica
        return (lg["rutas"].get(f"{estado_vendedor}>{estado_cliente}")
                or lg["destinos"].get(estado_cliente) or lg["global"])

    def base_flete(self, estado_vendedor, estado_cliente):
        f = self.flete
        return (f["base_ruta"].get(f"{estado_vendedor}>{estado_cliente}")
                or f["base_destino"].get(estado_cliente) or f["base_global"])

    def tramo_valor(self, valor):
        return bisect.bisect_right(self.tramos_valor, valor) - 1


@lru_cache(maxsize=1)
def cargar_perfil():
    """Perfil incluido en el paquete (olist_simulador/datos/perfil.json.gz)."""
    ruta = resources.files("olist_simulador") / "datos" / "perfil.json.gz"
    with ruta.open("rb") as f:
        return Perfil(json.loads(gzip.decompress(f.read()).decode("utf-8")))
