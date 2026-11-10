"""Envío de mensajes a SQS en lotes de hasta 10 con reintentos."""
import json
import time

from .contrato import validar

MAX_LOTE = 10
MAX_BYTES_LOTE = 250_000
INTENTOS = 5


def serializar(mensaje):
    return json.dumps(mensaje, ensure_ascii=False, separators=(",", ":"))


def lotes(mensajes):
    """Divide mensajes en lotes que respetan el límite de cantidad y de tamaño de SQS."""
    lote, tamano = [], 0
    for m in mensajes:
        cuerpo = serializar(m)
        if lote and (len(lote) == MAX_LOTE or tamano + len(cuerpo.encode()) > MAX_BYTES_LOTE):
            yield lote
            lote, tamano = [], 0
        lote.append(cuerpo)
        tamano += len(cuerpo.encode())
    if lote:
        yield lote


class EmisorSQS:

    def __init__(self, conexion, hilos=4):
        from botocore.config import Config

        config = Config(max_pool_connections=hilos * 2, retries={"max_attempts": 5, "mode": "standard"})
        self.sqs = conexion.session.client("sqs", config=config)
        self.url = conexion.url_cola

    def enviar(self, mensajes):
        """Envía mensajes; devuelve la cantidad de reintentos. Lanza RuntimeError si no puede enviar."""
        reintentos = 0
        for cuerpos in lotes(mensajes):
            entradas = [{"Id": str(i), "MessageBody": c} for i, c in enumerate(cuerpos)]
            for intento in range(INTENTOS):
                respuesta = self.sqs.send_message_batch(QueueUrl=self.url, Entries=entradas)
                fallidos = respuesta.get("Failed", [])
                if not fallidos:
                    break
                permanentes = [f for f in fallidos if f.get("SenderFault")]
                if permanentes:
                    raise RuntimeError(f"SQS rechazó mensajes: {permanentes[0].get('Message')}")
                ids = {f["Id"] for f in fallidos}
                entradas = [e for e in entradas if e["Id"] in ids]
                reintentos += len(entradas)
                time.sleep(0.2 * 2 ** intento)
            else:
                raise RuntimeError(f"No se pudieron enviar {len(entradas)} mensajes tras {INTENTOS} intentos.")
        return reintentos

    def estado_cola(self):
        a = self.sqs.get_queue_attributes(
            QueueUrl=self.url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return int(a["ApproximateNumberOfMessages"]), int(a["ApproximateNumberOfMessagesNotVisible"])


class EmisorPrueba:
    """No envía nada: valida cada mensaje contra el contrato y mide su tamaño."""

    def __init__(self):
        self.max_bytes = 0

    def enviar(self, mensajes):
        for m in mensajes:
            motivo = validar(m)
            if motivo:
                raise RuntimeError(f"Mensaje fuera de contrato ({m.get('tipo')}): {motivo}")
            self.max_bytes = max(self.max_bytes, len(serializar(m).encode()))
        return 0

    def estado_cola(self):
        return None
