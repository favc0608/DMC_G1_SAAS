# Simulador de ventas Olist - Grupo 1

Generador de **ventas sintéticas** de e-commerce para la capa de ingesta en tiempo real del proyecto
integrador del Diplomado de Ingeniería de Datos (DMC). Las ventas se generan a partir de un perfil
estadístico del dataset público de Olist y se envían como eventos a una cola de Amazon SQS.

```
Simulador (Colab) ──► SQS g1-olist-eventos ──► Lambda ──► S3 landing ──► bronze ──► silver ──► gold
```

[Abrir en Colab](https://colab.research.google.com/github/favc0608/DMC_G1_SAAS/blob/main/notebooks/simulador_olist_g1.ipynb)

## Uso en Google Colab

1. Abrir `notebooks/simulador_olist_g1.ipynb` en Colab y ejecutar las dos celdas.
2. En **Credenciales**, subir el CSV de access keys del usuario productor (descargado desde IAM).
   El archivo se lee en memoria: no se guarda en disco ni se muestra.
3. Elegir eventos, bloques del payload y parámetros. El plan se recalcula antes de enviar.
4. **Vista previa** muestra un evento de cada tipo; **Prueba sin envío** valida todo sin tocar AWS.
5. **Iniciar** comienza el envío; **Detener** lo corta de forma ordenada.

Fuera de Colab:

```python
from olist_simulador import Generador, ParametrosSimulacion

g = Generador(ParametrosSimulacion(dias=30, duracion_min=10, caudal=50))
print(g.plan())
for momento, mensaje in g.eventos():
    ...
```

## Parámetros

| Parámetro | Descripción | Rango |
|---|---|---|
| Inicio simulado | Fecha en que arranca el reloj simulado | Por defecto 2026-09-04 |
| Periodo | Días de negocio que se simulan | 1 a 365 |
| Duración | Minutos reales que dura el envío | 1 a 240 |
| Caudal | Eventos por segundo (promedio) | 1 a 200 |
| Semilla | Misma semilla y parámetros = mismos eventos | Entero |
| Recompra / cambio de dirección | Probabilidades del cliente (por defecto, las de Olist) | 0 a 100 % |

Caudal × duración determina cuántos eventos se envían; el periodo determina la velocidad del reloj.
Dentro del día, las compras siguen la estacionalidad por hora y día de la semana de Olist.

## Eventos

Contrato: `{"evento_id": uuid, "tipo": str, "ts_evento": ISO-8601, "version": 1, "payload": {...}}`.

| Tipo | Payload | Campos obligatorios |
|---|---|---|
| `pedido_creado` | pedido, cliente, items (producto y vendedor), pagos | pedido.order_id, pedido.order_purchase_timestamp, cliente.customer_id, cliente.customer_unique_id, items |
| `pedido_aprobado` / `pedido_enviado` / `pedido_entregado` | order_id, order_status, fecha | order_id, fecha |
| `pedido_cancelado` | order_id, order_status (`canceled` o `unavailable`), fecha | order_id |
| `resena_creada` | review_id, order_id, puntaje, título, mensaje, fechas | review_id, order_id, review_score |
| `cliente_actualizado` | cliente con su nueva dirección, motivo | customer_id, customer_unique_id |

- `ts_evento` es la fecha simulada de negocio (UTC).
- `evento_id` es determinista (uuid5 de semilla, tipo y clave): reenviar no genera duplicados después de la
  deduplicación en bronze.
- La entrega de SQS Standard es "al menos una vez" y sin orden garantizado.

## Realismo

El perfil (`olist_simulador/datos/perfil.json.gz`) resume el periodo 2017-01 a 2018-08: catálogo de
productos y vendedores reales, geografía de clientes, estacionalidad, medios de pago y cuotas, flete por
ruta y peso, tiempos logísticos por ruta (aprobación, despacho, entrega, fecha estimada), cancelaciones y
reseñas según puntualidad. Comparación con el histórico:

| Indicador | Olist | Simulador |
|---|---|---|
| Entregas tardías | 6.8 % | 6.3 % |
| Días de entrega promedio | 12.5 | 12.2 |
| Reseña a tiempo / tarde | 4.29 / 2.27 | 4.29 / 2.36 |
| Pagos con tarjeta de crédito | 76.7 % | 76.7 % |
| Ticket medio (BRL) | 137.7 | 136.0 |
| Flete / precio | 16.6 % | 16.7 % |

Los identificadores de productos y vendedores existen en Olist, de modo que las ventas sintéticas se unen
con las dimensiones del lakehouse.

## Desarrollo

```
olist_simulador/   paquete (contrato, config, perfil, generador, emisor, ejecucion, credenciales, interfaz)
herramientas/      construir_perfil.py: regenera el perfil desde los CSV de Olist
notebooks/         notebook de Colab
tests/             pruebas unitarias (sin AWS)
```

```
python -m unittest discover -s tests -v
python herramientas/construir_perfil.py <carpeta_csv_olist>
```

## Datos

Perfil derivado de [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce),
licencia CC BY-NC-SA 4.0. Uso académico y sin fines comerciales.
