"""Contrato de mensaje entre el simulador y el consumidor (Lambda).

Debe coincidir con PAYLOAD_OBLIGATORIO de lambda/consumidor/handler.py del proyecto.
Mensaje: {"evento_id": uuid, "tipo": str, "ts_evento": ISO-8601, "version": 1, "payload": {...}}
"""
import uuid
from datetime import datetime

VERSION = 1

# Campos obligatorios del payload por tipo de evento ("a.b" = campo anidado)
PAYLOAD_OBLIGATORIO = {
    "pedido_creado": ["pedido.order_id", "pedido.order_purchase_timestamp",
                      "cliente.customer_id", "cliente.customer_unique_id", "items"],
    "pedido_aprobado": ["order_id", "fecha"],
    "pedido_enviado": ["order_id", "fecha"],
    "pedido_entregado": ["order_id", "fecha"],
    "pedido_cancelado": ["order_id"],
    "resena_creada": ["review_id", "order_id", "review_score"],
    "cliente_actualizado": ["customer_id", "customer_unique_id"],
}

TIPOS = tuple(PAYLOAD_OBLIGATORIO)


def _obtener(dic, ruta):
    for parte in ruta.split("."):
        if not isinstance(dic, dict) or dic.get(parte) in (None, ""):
            return None
        dic = dic[parte]
    return dic


def validar(mensaje):
    """Devuelve el motivo de rechazo, o None si el mensaje cumple el contrato."""
    if not isinstance(mensaje, dict):
        return "El mensaje no es un objeto JSON"
    faltantes = {"evento_id", "tipo", "ts_evento", "payload"} - mensaje.keys()
    if faltantes:
        return f"Faltan campos: {sorted(faltantes)}"
    if mensaje["tipo"] not in PAYLOAD_OBLIGATORIO:
        return f"Tipo desconocido: {mensaje['tipo']}"
    try:
        uuid.UUID(str(mensaje["evento_id"]))
    except ValueError:
        return "evento_id no es un UUID"
    try:
        datetime.fromisoformat(str(mensaje["ts_evento"]).replace("Z", "+00:00"))
    except ValueError:
        return "ts_evento no es una fecha ISO-8601"
    if not isinstance(mensaje["payload"], dict):
        return "payload no es un objeto"
    faltantes = [c for c in PAYLOAD_OBLIGATORIO[mensaje["tipo"]]
                 if _obtener(mensaje["payload"], c) is None]
    if faltantes:
        return f"Faltan campos en payload: {faltantes}"
    return None
