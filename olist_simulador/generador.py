"""Generación de ventas sintéticas y de su línea de tiempo de eventos.

Cada venta recorre el ciclo de Olist: compra -> aprobación -> envío -> entrega (o cancelación)
-> reseña. Los tiempos, montos, rutas y puntajes se muestrean del perfil estadístico.
Con la misma semilla y los mismos parámetros se generan exactamente los mismos eventos.
"""
import heapq
import math
import random
import uuid
from datetime import datetime, timedelta

from .config import ParametrosSimulacion, Plan
from .contrato import VERSION
from .perfil import cargar_perfil, cuantil

ESPACIO_IDS = uuid.UUID("3d0c8a52-5b1e-4f7a-9c61-7a2e4f0b9d13")
PRIORIDAD = {"cliente_actualizado": 0, "pedido_creado": 1, "pedido_aprobado": 2, "pedido_enviado": 3,
             "pedido_entregado": 4, "pedido_cancelado": 5, "resena_creada": 6}
UNA_HORA = timedelta(hours=1)
MUESTRAS_CALIBRACION = 2000


def _hex(rng):
    return f"{rng.getrandbits(128):032x}"


def _fecha(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _medianoche(dt):
    return datetime(dt.year, dt.month, dt.day)


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limite, k, p = math.exp(-lam), 0, rng.random()
    while p > limite:
        k += 1
        p *= rng.random()
    return k


class Generador:

    def __init__(self, parametros: ParametrosSimulacion, perfil=None):
        errores = parametros.errores()
        if errores:
            raise ValueError(" ".join(errores))
        self.p = parametros
        self.perfil = perfil or cargar_perfil()
        self.tasa_recompra = (self.perfil.tasa_recompra if parametros.tasa_recompra is None
                              else parametros.tasa_recompra)
        self.tasa_cambio = (self.perfil.tasa_cambio_direccion if parametros.tasa_cambio_direccion is None
                            else parametros.tasa_cambio_direccion)
        self.fin = parametros.inicio + timedelta(days=parametros.dias)
        self._plan = None

    # ==========================================
    # 1. UNA VENTA
    # ==========================================
    def _cliente(self, rng):
        pf = self.perfil
        cambio = False
        if rng.random() < self.tasa_recompra:
            c = rng.choice(pf.clientes)
            unico, z = c["customer_unique_id"], {"zip": c["zip"], "ciudad": c["ciudad"], "estado": c["estado"]}
            if rng.random() < self.tasa_cambio:
                cambio = True
                mismo = rng.random() < pf.cambio_mismo_estado and z["estado"] in pf.zips_por_estado
                dist = pf.zips_por_estado[z["estado"]] if mismo else pf.zips_por_peso
                z = pf.zips[dist.elegir(rng)]
        else:
            unico, z = _hex(rng), pf.zips[pf.zips_por_peso.elegir(rng)]
        cliente = {"customer_id": _hex(rng), "customer_unique_id": unico,
                   "customer_zip_code_prefix": z["zip"], "customer_city": z["ciudad"],
                   "customer_state": z["estado"]}
        return cliente, cambio

    def _items(self, rng, compra, estado_cliente):
        pf, bloques = self.perfil, self.p.bloques
        n = pf.productos_por_pedido.elegir(rng)
        elegidos = []
        for _ in range(n * 5):
            i = pf.productos_por_popularidad.elegir(rng)
            if i not in elegidos:
                elegidos.append(i)
            if len(elegidos) == n:
                break
        items, secuencia, estado_vendedor = [], 1, None
        for i in elegidos:
            prod = pf.productos[i]
            vend = pf.vendedores[pf.vendedores_de[i].elegir(rng)]
            estado_vendedor = estado_vendedor or vend["seller_state"]
            precio = round(max(0.85, prod["precio_mediano"] * math.exp(rng.gauss(0, prod["precio_log_sd"]))), 2)
            peso_kg = (prod["product_weight_g"] or 500) / 1000
            log_flete = (pf.base_flete(vend["seller_state"], estado_cliente)
                         + pf.flete["pendiente_peso"] * math.log(peso_kg + 0.1)
                         + rng.gauss(0, pf.flete["ruido_sd"]))
            flete = round(min(math.exp(log_flete), 400.0), 2)
            limite = compra + timedelta(days=cuantil(rng, pf.dias_limite_envio))
            producto = {"product_id": prod["product_id"]}
            if "producto" in bloques:
                producto.update({k: prod[k] for k in (
                    "product_category_name", "product_name_lenght", "product_description_lenght",
                    "product_photos_qty", "product_weight_g", "product_length_cm",
                    "product_height_cm", "product_width_cm")})
            vendedor = {"seller_id": vend["seller_id"]}
            if "vendedor" in bloques:
                vendedor.update({k: vend[k] for k in ("seller_zip_code_prefix", "seller_city", "seller_state")})
            for _ in range(pf.unidades_por_producto.elegir(rng)):
                items.append({"order_item_id": secuencia, "shipping_limit_date": _fecha(limite),
                              "price": precio, "freight_value": flete,
                              "producto": producto, "vendedor": vendedor})
                secuencia += 1
        return items, estado_vendedor

    def _pagos(self, rng, total):
        pf = self.perfil
        tipo = pf.tipo_pago.elegir(rng)
        cuotas = pf.cuotas_tarjeta[pf.tramo_valor(total)].elegir(rng) if tipo == "credit_card" else 1
        if tipo != "voucher" and rng.random() < pf.tasa_pago_combinado:
            voucher = round(total * rng.uniform(0.05, 0.5), 2)
            return [{"payment_sequential": 1, "payment_type": "voucher", "payment_installments": 1,
                     "payment_value": voucher},
                    {"payment_sequential": 2, "payment_type": tipo, "payment_installments": cuotas,
                     "payment_value": round(total - voucher, 2)}]
        return [{"payment_sequential": 1, "payment_type": tipo, "payment_installments": cuotas,
                 "payment_value": total}]

    def _resena(self, rng, order_id, grupo, base):
        pf = self.perfil
        puntaje = pf.puntajes[grupo].elegir(rng)
        creacion = _medianoche(base) + timedelta(days=max(1, round(cuantil(rng, pf.dias_tras_entrega))))
        respuesta = creacion + timedelta(hours=cuantil(rng, pf.horas_respuesta))
        titulo = mensaje = None
        if "textos_resena" in self.p.bloques:
            textos = pf.textos[puntaje]
            if rng.random() < textos["p_titulo"]:
                titulo = rng.choice(textos["titulos"])
            if rng.random() < textos["p_mensaje"]:
                mensaje = rng.choice(textos["mensajes"])
        review_id = _hex(rng)
        payload = {"review_id": review_id, "order_id": order_id, "review_score": puntaje,
                   "review_comment_title": titulo, "review_comment_message": mensaje,
                   "review_creation_date": _fecha(creacion), "review_answer_timestamp": _fecha(respuesta)}
        return creacion, f"{review_id}|{order_id}", payload

    def _venta(self, rng, compra):
        """Eventos de una venta: lista de (momento, tipo, clave, payload)."""
        pf = self.perfil
        cliente, cambio = self._cliente(rng)
        order_id = _hex(rng)
        items, estado_vendedor = self._items(rng, compra, cliente["customer_state"])
        aprob_h, despacho_d, entrega_d, estimada_d = rng.choice(
            pf.muestras_logistica(estado_vendedor, cliente["customer_state"]))
        aprobado = compra + timedelta(hours=aprob_h)
        estimada = _medianoche(compra) + timedelta(days=estimada_d)
        eventos = []

        if cambio:
            momento = compra - timedelta(minutes=rng.uniform(1, 30))
            eventos.append((momento, "cliente_actualizado", f"{cliente['customer_unique_id']}|{order_id}",
                            {**cliente, "motivo": "cambio_direccion"}))

        creado = {"pedido": {"order_id": order_id, "order_status": "created",
                             "order_purchase_timestamp": _fecha(compra),
                             "order_estimated_delivery_date": _fecha(estimada)},
                  "cliente": cliente, "items": items}
        if "pagos" in self.p.bloques:
            creado["pagos"] = self._pagos(rng, round(sum(i["price"] + i["freight_value"] for i in items), 2))
        eventos.append((compra, "pedido_creado", order_id, creado))
        eventos.append((aprobado, "pedido_aprobado", order_id,
                        {"order_id": order_id, "order_status": "approved", "fecha": _fecha(aprobado)}))

        lg = pf.logistica
        if rng.random() < lg["tasa_cancelacion"]:
            estado = "unavailable" if rng.random() < lg["cancelacion_unavailable"] else "canceled"
            cancelado = aprobado + timedelta(days=rng.uniform(0.2, 3))
            eventos.append((cancelado, "pedido_cancelado", order_id,
                            {"order_id": order_id, "order_status": estado, "fecha": _fecha(cancelado)}))
            grupo, base = "cancelado", cancelado
        else:
            enviado = compra + timedelta(days=despacho_d)
            entregado = compra + timedelta(days=entrega_d)
            eventos.append((enviado, "pedido_enviado", order_id,
                            {"order_id": order_id, "order_status": "shipped", "fecha": _fecha(enviado)}))
            eventos.append((entregado, "pedido_entregado", order_id,
                            {"order_id": order_id, "order_status": "delivered", "fecha": _fecha(entregado)}))
            atraso = (_medianoche(entregado) - estimada).days
            grupo = ("a_tiempo" if atraso <= 0 else "tarde_1_3" if atraso <= 3
                     else "tarde_4_7" if atraso <= 7 else "tarde_8_mas")
            base = entregado

        if rng.random() < pf.tasa_resena:
            creacion, clave, payload = self._resena(rng, order_id, grupo, base)
            eventos.append((creacion, "resena_creada", clave, payload))
        return eventos

    # ==========================================
    # 2. MENSAJE
    # ==========================================
    def _mensaje(self, momento, tipo, clave, payload):
        return {"evento_id": str(uuid.uuid5(ESPACIO_IDS, f"{self.p.semilla}|{tipo}|{clave}")),
                "tipo": tipo, "ts_evento": momento.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "version": VERSION, "payload": payload}

    # ==========================================
    # 3. PLAN
    # ==========================================
    def eventos_por_venta(self):
        """Eventos promedio por venta que caen dentro del periodo (estimación por muestreo)."""
        rng = random.Random(self.p.semilla ^ 0x5EED)
        tipos, total = set(self.p.tipos), 0
        segundos = self.p.dias * 86400
        for _ in range(MUESTRAS_CALIBRACION):
            compra = self.p.inicio + timedelta(seconds=rng.random() * segundos)
            total += sum(1 for m, tipo, _, _ in self._venta(rng, compra) if tipo in tipos and m < self.fin)
        return total / MUESTRAS_CALIBRACION

    def plan(self):
        if self._plan:
            return self._plan
        p, pf = self.p, self.perfil
        por_venta = self.eventos_por_venta()
        total = p.total_eventos
        ventas = total / por_venta
        por_dia = ventas / p.dias
        pico = max(max(fila) for fila in pf.semana_hora) * 24
        avisos = []
        if por_dia > 20 * pf.ventas_dia_referencia:
            avisos.append(f"El volumen es {por_dia / pf.ventas_dia_referencia:,.0f} veces el histórico de Olist.")
        if p.segundos_por_dia < 2:
            avisos.append("El reloj avanza más de medio día por segundo: los eventos de un pedido llegarán casi juntos.")
        if p.dias < 15:
            avisos.append("Con menos de 15 días simulados, muchas ventas no alcanzan a entregarse.")
        if p.caudal * pico > 600:
            avisos.append(f"En horas pico el caudal llegará a ~{p.caudal * pico:,.0f} eventos/s.")
        self._plan = Plan(
            total_eventos=total, ventas_estimadas=round(ventas), ventas_por_dia=por_dia,
            eventos_por_venta=por_venta, segundos_por_dia=p.segundos_por_dia,
            ventas_dia_referencia=pf.ventas_dia_referencia,
            archivos_por_minuto=max(1.0, p.caudal * 60 / 1000),
            costo_usd=total / 10 * 3 * 0.40 / 1e6, fin_simulado=self.fin, avisos=avisos)
        return self._plan

    # ==========================================
    # 4. LÍNEA DE TIEMPO
    # ==========================================
    def eventos(self):
        """Iterador de (momento_simulado, mensaje) en orden cronológico."""
        rng = random.Random(self.p.semilla)
        tipos = set(self.p.tipos)
        por_dia = self.plan().ventas_por_dia
        pesos = self.perfil.semana_hora
        cola, secuencia = [], 0
        hora = self.p.inicio
        while hora < self.fin:
            fin_bloque = min(hora + UNA_HORA, self.fin)
            while cola and cola[0][0] < hora:
                momento, _, _, tipo, clave, payload = heapq.heappop(cola)
                yield momento, self._mensaje(momento, tipo, clave, payload)
            fraccion = (fin_bloque - hora) / UNA_HORA
            n = poisson(rng, por_dia * pesos[hora.weekday()][hora.hour] * fraccion)
            compras = sorted(hora + (fin_bloque - hora) * rng.random() for _ in range(n))
            for compra in compras:
                for momento, tipo, clave, payload in self._venta(rng, compra):
                    if tipo in tipos and momento < self.fin:
                        momento = max(momento, hora)
                        heapq.heappush(cola, (momento, PRIORIDAD[tipo], secuencia, tipo, clave, payload))
                        secuencia += 1
            hora = fin_bloque
        while cola:
            momento, _, _, tipo, clave, payload = heapq.heappop(cola)
            yield momento, self._mensaje(momento, tipo, clave, payload)
