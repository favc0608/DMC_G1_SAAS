"""Ejecución de una simulación: ritmo según el reloj simulado, envío en paralelo y métricas."""
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor

MAX_LOTES_EN_VUELO = 32
MAX_FALLOS_SEGUIDOS = 5
VENTANA_CAUDAL_S = 5


class Metricas:

    def __init__(self, objetivo):
        self._lock = threading.Lock()
        self.objetivo = objetivo
        self.estado = "preparada"
        self.enviados = 0
        self.fallidos = 0
        self.reintentos = 0
        self.por_tipo = Counter()
        self.fecha_simulada = None
        self.inicio = None
        self.fin = None
        self.error = None
        self._ventana = deque()
        self._fallos_seguidos = 0

    def registrar(self, mensajes, reintentos):
        ahora = time.monotonic()
        with self._lock:
            self.enviados += len(mensajes)
            self.reintentos += reintentos
            self.por_tipo.update(m["tipo"] for m in mensajes)
            self._ventana.append((ahora, len(mensajes)))
            self._fallos_seguidos = 0

    def fallo(self, cantidad, error):
        with self._lock:
            self.fallidos += cantidad
            self.error = str(error)
            self._fallos_seguidos += 1
            return self._fallos_seguidos

    def caudal(self):
        limite = time.monotonic() - VENTANA_CAUDAL_S
        with self._lock:
            while self._ventana and self._ventana[0][0] < limite:
                self._ventana.popleft()
            return sum(n for _, n in self._ventana) / VENTANA_CAUDAL_S

    def transcurrido(self):
        if self.inicio is None:
            return 0.0
        return (self.fin or time.monotonic()) - self.inicio

    def copia(self):
        with self._lock:
            return {"estado": self.estado, "enviados": self.enviados, "fallidos": self.fallidos,
                    "reintentos": self.reintentos, "por_tipo": dict(self.por_tipo),
                    "fecha_simulada": self.fecha_simulada, "error": self.error, "objetivo": self.objetivo}


class Ejecucion:
    """Corre el generador en un hilo propio para que la interfaz siga respondiendo."""

    def __init__(self, generador, emisor, sin_ritmo=False, hilos=4):
        self.generador = generador
        self.emisor = emisor
        self.sin_ritmo = sin_ritmo
        self.hilos = hilos
        self.metricas = Metricas(generador.plan().total_eventos)
        self._detener = threading.Event()
        self._hilo = None

    @property
    def activa(self):
        return self._hilo is not None and self._hilo.is_alive()

    def iniciar(self):
        if self.activa:
            raise RuntimeError("La simulación ya está en curso.")
        self._hilo = threading.Thread(target=self._correr, name="simulador-olist", daemon=True)
        self._hilo.start()

    def detener(self):
        self._detener.set()

    def esperar(self, timeout=None):
        if self._hilo:
            self._hilo.join(timeout)

    # ==========================================
    # 1. ENVÍO DE UN LOTE
    # ==========================================
    def _enviar(self, lote):
        try:
            reintentos = self.emisor.enviar(lote)
            self.metricas.registrar(lote, reintentos)
        except Exception as error:  # se informa en el panel; varios fallos seguidos detienen la simulación
            if self.metricas.fallo(len(lote), error) >= MAX_FALLOS_SEGUIDOS:
                self._detener.set()

    # ==========================================
    # 2. BUCLE PRINCIPAL
    # ==========================================
    def _correr(self):
        m, p = self.metricas, self.generador.p
        m.estado, m.inicio = "en curso", time.monotonic()
        segundos_simulados = p.dias * 86400
        duracion = p.duracion_min * 60
        en_vuelo = threading.BoundedSemaphore(MAX_LOTES_EN_VUELO)
        pool = ThreadPoolExecutor(max_workers=self.hilos, thread_name_prefix="sqs")

        def despachar(lote):
            en_vuelo.acquire()
            pool.submit(self._enviar, lote).add_done_callback(lambda _: en_vuelo.release())

        lote = []
        try:
            for momento, mensaje in self.generador.eventos():
                if self._detener.is_set():
                    break
                if not self.sin_ritmo:
                    objetivo = (momento - p.inicio).total_seconds() / segundos_simulados * duracion
                    restante = objetivo - (time.monotonic() - m.inicio)
                    if restante > 0:
                        if lote:
                            despachar(lote)
                            lote = []
                        if self._detener.wait(restante):
                            break
                lote.append(mensaje)
                m.fecha_simulada = momento
                if len(lote) == 10:
                    despachar(lote)
                    lote = []
            if lote:
                despachar(lote)
        except Exception as error:
            m.error = f"{type(error).__name__}: {error}"
            m.estado = "error"
        finally:
            pool.shutdown(wait=True)
            m.fin = time.monotonic()
            if m.estado != "error":
                fallo_grave = m._fallos_seguidos >= MAX_FALLOS_SEGUIDOS
                m.estado = "error" if fallo_grave else "detenida" if self._detener.is_set() else "finalizada"
