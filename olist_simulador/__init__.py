"""Simulador de ventas sintéticas de Olist para la ingesta en tiempo real (Grupo 1 - DMC)."""
from .config import ParametrosSimulacion, Plan
from .contrato import PAYLOAD_OBLIGATORIO, TIPOS, validar
from .generador import Generador
from .perfil import cargar_perfil

__version__ = "1.0.1"
__all__ = ["Generador", "ParametrosSimulacion", "Plan", "PAYLOAD_OBLIGATORIO", "TIPOS",
           "validar", "cargar_perfil", "Simulador", "__version__"]


def __getattr__(nombre):
    # La interfaz depende de ipywidgets y boto3: se importa solo cuando se usa
    if nombre == "Simulador":
        from .interfaz import Simulador
        return Simulador
    raise AttributeError(nombre)
