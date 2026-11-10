"""Interfaz del simulador para Google Colab / Jupyter (ipywidgets)."""
import html
import json
import threading
import time
from datetime import datetime, timezone

import ipywidgets as w
from IPython.display import display

from . import __version__
from .config import BLOQUES, MAX_CAUDAL, MAX_DIAS, MAX_DURACION_MIN, ParametrosSimulacion
from .contrato import TIPOS
from .credenciales import conectar, leer_csv
from .ejecucion import Ejecucion
from .emisor import EmisorPrueba, EmisorSQS
from .generador import Generador
from .perfil import cargar_perfil

ETIQUETAS_TIPO = {
    "pedido_creado": "Pedido creado", "pedido_aprobado": "Pedido aprobado",
    "pedido_enviado": "Pedido enviado", "pedido_entregado": "Pedido entregado",
    "pedido_cancelado": "Pedido cancelado", "resena_creada": "Reseña creada",
    "cliente_actualizado": "Cliente actualizado",
}
ETIQUETAS_CORTAS = {
    "pedido_creado": "creados", "pedido_aprobado": "aprobados", "pedido_enviado": "enviados",
    "pedido_entregado": "entregados", "pedido_cancelado": "cancelados", "resena_creada": "reseñas",
    "cliente_actualizado": "clientes actualizados",
}
ETIQUETAS_BLOQUE = {"pagos": "Pagos", "producto": "Detalle de producto",
                    "vendedor": "Detalle de vendedor", "textos_resena": "Textos de reseña"}
ESTILO = {"description_width": "150px"}
ANCHO = w.Layout(width="320px")

CSS = """
<style>
.olsim {font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; font-size: 13px; color: inherit;}
.olsim-titulo {font-size: 17px; font-weight: 600;}
.olsim-sub {opacity: .65; font-size: 12px; margin-top: 2px;}
.olsim-seccion {font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
                opacity: .6; margin: 14px 0 4px 0; padding-bottom: 3px;
                border-bottom: 1px solid rgba(128,128,128,.25);}
.olsim table {border-collapse: collapse;}
.olsim td {padding: 2px 18px 2px 0; vertical-align: top;}
.olsim td.k {opacity: .65; white-space: nowrap;}
.olsim .num {font-variant-numeric: tabular-nums;}
.olsim .ok {color: #1a7f37;} .olsim .aviso {color: #b35900;} .olsim .error {color: #cf222e;}
.olsim pre {font-size: 11px; max-height: 320px; overflow: auto; padding: 8px;
            border: 1px solid rgba(128,128,128,.25); border-radius: 4px;}
</style>
"""


def _html(contenido):
    return f"{CSS}<div class='olsim'>{contenido}</div>"


def _seccion(texto):
    return w.HTML(_html(f"<div class='olsim-seccion'>{texto}</div>"))


def _tabla(filas):
    cuerpo = "".join(f"<tr><td class='k'>{k}</td><td class='num'>{v}</td></tr>" for k, v in filas)
    return f"<table>{cuerpo}</table>"


def _duracion(segundos):
    segundos = max(0, int(segundos))
    m, s = divmod(segundos, 60)
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s"


def _contenido_subido(widget):
    """Bytes del archivo subido, compatible con ipywidgets 7 y 8."""
    valor = widget.value
    if isinstance(valor, dict):
        archivo = next(iter(valor.values()), None)
    else:
        archivo = valor[0] if valor else None
    return None if archivo is None else bytes(archivo["content"])


def _limpiar_subida(widget):
    try:
        if isinstance(widget.value, dict):
            widget.value.clear()
            widget._counter = 0
        else:
            widget.value = ()
    except Exception:
        pass


class Simulador:
    """Formulario, plan y panel de ejecución del simulador de ventas."""

    def __init__(self):
        self.perfil = cargar_perfil()
        self.conexion = None
        self.ejecucion = None
        self.generador = None
        self._construir()
        self._actualizar_plan()

    # ==========================================
    # 1. CONTROLES
    # ==========================================
    def _construir(self):
        pf = self.perfil
        self.w_encabezado = w.HTML(_html(
            "<div class='olsim-titulo'>Simulador de ventas Olist</div>"
            f"<div class='olsim-sub'>Grupo 1 · DMC · Ingesta en tiempo real · v{__version__}</div>"))

        self.w_archivo = w.FileUpload(accept=".csv", multiple=False, description="Subir CSV",
                                      layout=w.Layout(width="140px"))
        self.w_estado_cred = w.HTML(_html("<span class='aviso'>Sin credenciales. Suba el CSV de access keys.</span>"))
        self.w_archivo.observe(self._al_subir, names="value")

        self.w_tipos = {t: w.Checkbox(value=True, description=ETIQUETAS_TIPO[t], indent=False,
                                      disabled=(t == "pedido_creado"), layout=w.Layout(width="190px"))
                        for t in TIPOS}
        self.w_bloques = {b: w.Checkbox(value=(b != "textos_resena"), description=ETIQUETAS_BLOQUE[b],
                                        indent=False, layout=w.Layout(width="190px")) for b in BLOQUES}

        self.w_inicio = w.DatePicker(description="Inicio simulado", value=ParametrosSimulacion.inicio.date(),
                                     style=ESTILO, layout=ANCHO)
        self.w_dias = w.BoundedIntText(value=30, min=1, max=MAX_DIAS, description="Periodo (días)",
                                       style=ESTILO, layout=ANCHO)
        self.w_duracion = w.BoundedIntText(value=10, min=1, max=MAX_DURACION_MIN, description="Duración (min)",
                                           style=ESTILO, layout=ANCHO)
        self.w_caudal = w.BoundedIntText(value=50, min=1, max=MAX_CAUDAL, description="Caudal (eventos/s)",
                                         style=ESTILO, layout=ANCHO)

        self.w_semilla = w.IntText(value=42, description="Semilla", style=ESTILO, layout=ANCHO)
        self.w_recompra = w.BoundedFloatText(value=round(pf.tasa_recompra * 100, 2), min=0, max=100, step=0.5,
                                             description="Recompra (%)", style=ESTILO, layout=ANCHO)
        self.w_cambio = w.BoundedFloatText(value=round(pf.tasa_cambio_direccion * 100, 2), min=0, max=100,
                                           step=0.5, description="Cambio dirección (%)", style=ESTILO, layout=ANCHO)
        self.w_prueba = w.Checkbox(value=False, description="Prueba sin envío (valida y cuenta, no envía)",
                                   indent=False)
        avanzado = w.VBox([self.w_semilla, self.w_recompra, self.w_cambio, self.w_prueba])
        self.w_avanzado = w.Accordion(children=[avanzado], selected_index=None, layout=w.Layout(width="520px"))
        self.w_avanzado.set_title(0, "Avanzado")

        self.w_plan = w.HTML()

        self.b_vista = w.Button(description="Vista previa", icon="eye", layout=w.Layout(width="130px"))
        self.b_iniciar = w.Button(description="Iniciar", button_style="primary", icon="play",
                                  layout=w.Layout(width="130px"))
        self.b_detener = w.Button(description="Detener", icon="stop", disabled=True, layout=w.Layout(width="130px"))
        self.b_vista.on_click(self._al_vista_previa)
        self.b_iniciar.on_click(self._al_iniciar)
        self.b_detener.on_click(self._al_detener)

        self.w_progreso = w.FloatProgress(value=0, min=0, max=1, layout=w.Layout(width="520px"))
        self.w_panel = w.HTML()
        self.w_detalle = w.HTML()

        for control in [*self.w_tipos.values(), *self.w_bloques.values(), self.w_inicio, self.w_dias,
                        self.w_duracion, self.w_caudal, self.w_semilla, self.w_recompra, self.w_cambio,
                        self.w_prueba]:
            control.observe(lambda _: self._actualizar_plan(), names="value")

        tipos = list(self.w_tipos.values())
        bloques = list(self.w_bloques.values())
        self.vista = w.VBox([
            self.w_encabezado,
            _seccion("Credenciales"), w.HBox([self.w_archivo, self.w_estado_cred],
                                             layout=w.Layout(align_items="center", grid_gap="12px")),
            _seccion("Eventos"),
            w.HBox(tipos[:4]), w.HBox(tipos[4:]),
            _seccion("Bloques del payload"), w.HBox(bloques),
            _seccion("Simulación"),
            w.HBox([w.VBox([self.w_inicio, self.w_dias]), w.VBox([self.w_duracion, self.w_caudal])]),
            self.w_avanzado,
            _seccion("Plan"), self.w_plan,
            w.HBox([self.b_vista, self.b_iniciar, self.b_detener], layout=w.Layout(margin="10px 0 0 0")),
            _seccion("Ejecución"), self.w_progreso, self.w_panel, self.w_detalle,
        ], layout=w.Layout(max_width="900px"))

    def mostrar(self):
        display(self.vista)

    # ==========================================
    # 2. PARÁMETROS Y PLAN
    # ==========================================
    def _parametros(self):
        fecha = self.w_inicio.value or ParametrosSimulacion.inicio.date()
        return ParametrosSimulacion(
            inicio=datetime(fecha.year, fecha.month, fecha.day),
            dias=self.w_dias.value, duracion_min=self.w_duracion.value, caudal=self.w_caudal.value,
            tipos=tuple(t for t, c in self.w_tipos.items() if c.value),
            bloques=tuple(b for b, c in self.w_bloques.items() if c.value),
            semilla=self.w_semilla.value, tasa_recompra=self.w_recompra.value / 100,
            tasa_cambio_direccion=self.w_cambio.value / 100, prueba_sin_envio=self.w_prueba.value)

    def _actualizar_plan(self):
        if self.ejecucion and self.ejecucion.activa:
            return
        p = self._parametros()
        errores = p.errores()
        if errores:
            self.generador = None
            self.w_plan.value = _html("".join(f"<div class='error'>{html.escape(e)}</div>" for e in errores))
            self._habilitar()
            return
        self.generador = Generador(p, self.perfil)
        pl = self.generador.plan()
        filas = [
            ("Eventos a enviar", f"{pl.total_eventos:,}"),
            ("Ventas estimadas", f"{pl.ventas_estimadas:,} ({pl.eventos_por_venta:.1f} eventos por venta)"),
            ("Ventas por día simulado", f"{pl.ventas_por_dia:,.0f} (referencia Olist 2018: {pl.ventas_dia_referencia:,})"),
            ("Reloj", f"1 día simulado = {pl.segundos_por_dia:,.1f} s reales"),
            ("Periodo", f"{p.inicio:%Y-%m-%d} a {pl.fin_simulado:%Y-%m-%d}"),
            ("Archivos en S3", f"~{pl.archivos_por_minuto:,.0f} por minuto"),
            ("Costo estimado", "&lt; USD 0.01" if pl.costo_usd < 0.01 else f"USD {pl.costo_usd:,.2f}"),
        ]
        avisos = "".join(f"<div class='aviso'>{html.escape(a)}</div>" for a in pl.avisos)
        self.w_plan.value = _html(_tabla(filas) + avisos)
        self._habilitar()

    def _habilitar(self):
        activa = bool(self.ejecucion and self.ejecucion.activa)
        listo = self.generador is not None and (self.conexion is not None or self.w_prueba.value)
        self.b_iniciar.disabled = activa or not listo
        self.b_vista.disabled = activa or self.generador is None
        self.b_detener.disabled = not activa

    # ==========================================
    # 3. CREDENCIALES
    # ==========================================
    def _al_subir(self, _):
        contenido = _contenido_subido(self.w_archivo)
        if contenido is None:
            return
        _limpiar_subida(self.w_archivo)
        self.conexion = None
        self.w_estado_cred.value = _html("Validando credenciales...")
        try:
            credenciales = leer_csv(contenido)
            p = self._parametros()
            self.conexion = conectar(credenciales, p.region, p.cola)
            usuario = self.conexion.identidad.split("/")[-1]
            self.w_estado_cred.value = _html(
                f"<span class='ok'>Conectado como {html.escape(usuario)}</span> · cola {html.escape(p.cola)} "
                f"({html.escape(p.region)})")
        except Exception as error:
            self.w_estado_cred.value = _html(f"<span class='error'>{html.escape(str(error))}</span>")
        finally:
            del contenido
            self._habilitar()

    # ==========================================
    # 4. ACCIONES
    # ==========================================
    def _al_vista_previa(self, _):
        if self.generador is None:
            return
        ejemplos = {}
        for _, mensaje in self.generador.eventos():
            ejemplos.setdefault(mensaje["tipo"], mensaje)
            if len(ejemplos) == len(self.generador.p.tipos):
                break
        bloques = "".join(
            f"<div class='olsim-seccion'>{t}</div><pre>{html.escape(json.dumps(m, indent=2, ensure_ascii=False))}</pre>"
            for t, m in ejemplos.items())
        self.w_detalle.value = _html(bloques)

    def _al_iniciar(self, _):
        if self.generador is None or (self.ejecucion and self.ejecucion.activa):
            return
        prueba = self.w_prueba.value
        if not prueba and self.conexion is None:
            return
        emisor = EmisorPrueba() if prueba else EmisorSQS(self.conexion)
        self.w_detalle.value = ""
        self.ejecucion = Ejecucion(self.generador, emisor, sin_ritmo=prueba)
        self.ejecucion.iniciar()
        self._habilitar()
        threading.Thread(target=self._refrescar, name="panel-simulador", daemon=True).start()

    def _al_detener(self, _):
        if self.ejecucion:
            self.ejecucion.detener()
            self.b_detener.disabled = True

    # ==========================================
    # 5. PANEL
    # ==========================================
    def _refrescar(self):
        cola, ultima_consulta = None, 0.0
        while True:
            activa = self.ejecucion.activa
            if activa and time.monotonic() - ultima_consulta > 5:
                ultima_consulta = time.monotonic()
                try:
                    cola = self.ejecucion.emisor.estado_cola()
                except Exception:
                    cola = None
            self._pintar(cola)
            if not activa:
                break
            time.sleep(1)
        self._habilitar()
        self._resumen_final()

    def _pintar(self, cola):
        ej = self.ejecucion
        m = ej.metricas.copia()
        p = ej.generador.p
        transcurrido = ej.metricas.transcurrido()
        duracion = p.duracion_min * 60
        avance = min(1.0, transcurrido / duracion) if not ej.sin_ritmo else (
            min(1.0, m["enviados"] / max(m["objetivo"], 1)))
        if m["estado"] == "finalizada":
            avance = 1.0
        self.w_progreso.value = avance
        self.w_progreso.bar_style = {"error": "danger", "finalizada": "success"}.get(m["estado"], "info")
        clase = {"error": "error", "finalizada": "ok", "detenida": "aviso"}.get(m["estado"], "")
        por_tipo = " · ".join(f"{ETIQUETAS_CORTAS[t]} {n:,}"
                              for t, n in sorted(m["por_tipo"].items(), key=lambda x: -x[1]))
        caudal = f"{ej.metricas.caudal():,.1f} eventos/s"
        filas = [
            ("Estado", f"<span class='{clase}'>{m['estado'].capitalize()}</span>"
                       + (" (prueba sin envío)" if ej.sin_ritmo else "")),
            ("Fecha simulada", f"{m['fecha_simulada']:%Y-%m-%d %H:%M}" if m["fecha_simulada"] else "-"),
            ("Validados" if ej.sin_ritmo else "Enviados", f"{m['enviados']:,} de ~{m['objetivo']:,} ({avance:.0%})"),
            ("Caudal", caudal if ej.sin_ritmo else f"{caudal} (objetivo {p.caudal:,})"),
            ("Tiempo", f"{_duracion(transcurrido)}" + ("" if ej.sin_ritmo else
                       f" de {_duracion(duracion)} · restante {_duracion(duracion - transcurrido)}")),
            ("Fallidos / reintentos", f"{m['fallidos']:,} / {m['reintentos']:,}"),
            ("Por tipo", por_tipo or "-"),
        ]
        if cola is not None:
            filas.append(("Cola SQS", f"en espera {cola[0]:,} · en proceso {cola[1]:,}"))
        if m["error"]:
            filas.append(("Último error", f"<span class='error'>{html.escape(m['error'][:300])}</span>"))
        self.w_panel.value = _html(_tabla(filas))

    def _resumen_final(self):
        ej = self.ejecucion
        m = ej.metricas.copia()
        if ej.sin_ritmo:
            tamano = getattr(ej.emisor, "max_bytes", 0)
            texto = (f"Prueba completada: {m['enviados']:,} eventos válidos según el contrato. "
                     f"Mensaje más grande: {tamano / 1024:,.1f} KB (límite SQS: 256 KB).")
            self.w_detalle.value = _html(f"<div class='ok'>{texto}</div>")
            return
        hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        consulta = (
            "SELECT tipo, count(*) AS eventos, count(DISTINCT evento_id) AS unicos\n"
            "FROM g1_olist_lakehouse.landing_eventos_streaming\n"
            f"WHERE fecha = '{hoy}'\nGROUP BY tipo\nORDER BY eventos DESC;")
        self.w_detalle.value = _html(
            "<div class='olsim-seccion'>Verificación en Athena (workgroup g1-olist, 1-2 min después)</div>"
            f"<pre>{html.escape(consulta)}</pre>")
