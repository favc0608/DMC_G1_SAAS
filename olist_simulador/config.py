"""Parámetros de la simulación, validación y cálculo del plan de envío."""
from dataclasses import dataclass, field
from datetime import datetime

from .contrato import TIPOS

# ==========================================
# 1. LÍMITES Y VALORES POR DEFECTO
# ==========================================
INICIO_POR_DEFECTO = datetime(2026, 9, 4)   # fin del histórico de Olist desplazado 418 semanas
MAX_CAUDAL = 200                            # eventos por segundo (promedio)
MAX_DURACION_MIN = 240
MAX_DIAS = 365
BLOQUES = ("pagos", "producto", "vendedor", "textos_resena")
COLA_POR_DEFECTO = "g1-olist-eventos"
REGION_POR_DEFECTO = "us-east-2"


@dataclass
class ParametrosSimulacion:
    inicio: datetime = INICIO_POR_DEFECTO
    dias: float = 30
    duracion_min: float = 10
    caudal: float = 50
    tipos: tuple = TIPOS
    bloques: tuple = ("pagos", "producto", "vendedor")
    semilla: int = 42
    tasa_recompra: float | None = None          # None = valor del perfil
    tasa_cambio_direccion: float | None = None  # None = valor del perfil
    prueba_sin_envio: bool = False
    cola: str = COLA_POR_DEFECTO
    region: str = REGION_POR_DEFECTO

    @property
    def total_eventos(self):
        return int(round(self.caudal * self.duracion_min * 60))

    @property
    def segundos_por_dia(self):
        return self.duracion_min * 60 / self.dias

    def errores(self):
        """Lista de errores de validación (vacía si los parámetros son válidos)."""
        e = []
        if not 0 < self.dias <= MAX_DIAS:
            e.append(f"El periodo simulado debe estar entre 1 y {MAX_DIAS} días.")
        if not 0 < self.duracion_min <= MAX_DURACION_MIN:
            e.append(f"La duración debe estar entre 1 y {MAX_DURACION_MIN} minutos.")
        if not 0 < self.caudal <= MAX_CAUDAL:
            e.append(f"El caudal debe estar entre 1 y {MAX_CAUDAL} eventos por segundo.")
        if not self.tipos:
            e.append("Seleccione al menos un tipo de evento.")
        elif "pedido_creado" not in self.tipos:
            e.append("pedido_creado es obligatorio: los demás eventos se refieren a pedidos creados.")
        desconocidos = set(self.tipos) - set(TIPOS)
        if desconocidos:
            e.append(f"Tipos de evento desconocidos: {sorted(desconocidos)}")
        desconocidos = set(self.bloques) - set(BLOQUES)
        if desconocidos:
            e.append(f"Bloques desconocidos: {sorted(desconocidos)}")
        for nombre in ("tasa_recompra", "tasa_cambio_direccion"):
            valor = getattr(self, nombre)
            if valor is not None and not 0 <= valor <= 1:
                e.append(f"{nombre} debe estar entre 0 y 1.")
        return e


@dataclass
class Plan:
    total_eventos: int
    ventas_estimadas: int
    ventas_por_dia: float
    eventos_por_venta: float
    segundos_por_dia: float
    ventas_dia_referencia: int
    archivos_por_minuto: float
    costo_usd: float
    fin_simulado: datetime
    avisos: list = field(default_factory=list)
