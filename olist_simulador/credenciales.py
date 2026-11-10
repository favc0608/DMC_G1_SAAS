"""Lectura del CSV de access keys de IAM y conexión a AWS.

Las claves se leen en memoria: no se escriben en disco, en variables de entorno ni en la salida.
"""
import csv
import io
import re
from dataclasses import dataclass

_ID = {"access key id", "accesskeyid", "aws_access_key_id"}
_SECRETO = {"secret access key", "secretaccesskey", "aws_secret_access_key"}
_PATRON_ID = re.compile(r"^(AKIA|ASIA)[A-Z0-9]{16}$")


@dataclass(frozen=True, repr=False)
class Credenciales:
    access_key_id: str
    secret_access_key: str

    def __repr__(self):
        return f"Credenciales(access_key_id='{self.access_key_id[:4]}...{self.access_key_id[-4:]}')"


@dataclass
class Conexion:
    session: object
    identidad: str
    url_cola: str
    region: str


def leer_csv(contenido: bytes) -> Credenciales:
    """Lee el CSV descargado de la consola de IAM (columnas 'Access key ID' y 'Secret access key')."""
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("El archivo no es un CSV de texto.") from error
    filas = [f for f in csv.reader(io.StringIO(texto)) if any(c.strip() for c in f)]
    if len(filas) < 2:
        raise ValueError("El archivo no tiene encabezado y datos.")
    encabezado = [c.strip().lower() for c in filas[0]]
    i_id = next((i for i, c in enumerate(encabezado) if c in _ID), None)
    i_secreto = next((i for i, c in enumerate(encabezado) if c in _SECRETO), None)
    if i_id is None or i_secreto is None:
        raise ValueError("El CSV no tiene las columnas 'Access key ID' y 'Secret access key'. "
                         "Use el archivo de access keys descargado desde IAM.")
    fila = filas[1]
    if len(fila) <= max(i_id, i_secreto):
        raise ValueError("La fila de datos del CSV está incompleta.")
    clave, secreto = fila[i_id].strip(), fila[i_secreto].strip()
    if not _PATRON_ID.match(clave) or len(secreto) != 40:
        raise ValueError("El formato del access key no es válido.")
    return Credenciales(clave, secreto)


def conectar(credenciales: Credenciales, region: str, cola: str) -> Conexion:
    """Valida las credenciales y obtiene la URL de la cola por su nombre."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    session = boto3.Session(aws_access_key_id=credenciales.access_key_id,
                            aws_secret_access_key=credenciales.secret_access_key, region_name=region)
    try:
        identidad = session.client("sts").get_caller_identity()["Arn"]
    except (ClientError, BotoCoreError) as error:
        raise ValueError("AWS rechazó las credenciales. Verifique que el access key esté activo.") from error
    try:
        url = session.client("sqs").get_queue_url(QueueName=cola)["QueueUrl"]
    except (ClientError, BotoCoreError) as error:
        raise ValueError(f"Las credenciales no tienen acceso a la cola '{cola}' en {region}.") from error
    return Conexion(session=session, identidad=identidad, url_cola=url, region=region)
