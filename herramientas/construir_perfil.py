"""Construye el perfil estadístico de Olist que usa el simulador de ventas.

Uso:
    python herramientas/construir_perfil.py <carpeta_csv_olist> [--salida olist_simulador/datos/perfil.json.gz]

Lee los CSV originales del dataset público de Olist y resume, sin datos personales,
las distribuciones necesarias para generar ventas sintéticas coherentes con el histórico:
catálogo de productos y vendedores, geografía de clientes, estacionalidad, pagos, flete,
tiempos logísticos por ruta y reseñas.
"""
import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

DESDE, HASTA = "2017-01-01", "2018-09-01"  # periodo estable del dataset
SEMILLA = 2026
MUESTRAS_POR_RUTA = 120
MIN_MUESTRAS_RUTA = 25
CUANTILES = [round(q, 2) for q in np.linspace(0, 1, 41)]


# ==========================================
# 1. LECTURA
# ==========================================
def leer(carpeta):
    def csv(nombre):
        return pd.read_csv(Path(carpeta) / nombre, dtype=str, keep_default_na=False)

    t = {
        "orders": csv("olist_orders_dataset.csv"),
        "items": csv("olist_order_items_dataset.csv"),
        "payments": csv("olist_order_payments_dataset.csv"),
        "reviews": csv("olist_order_reviews_dataset.csv"),
        "customers": csv("olist_customers_dataset.csv"),
        "products": csv("olist_products_dataset.csv"),
        "sellers": csv("olist_sellers_dataset.csv"),
    }
    o = t["orders"]
    for c in ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
              "order_delivered_customer_date", "order_estimated_delivery_date"]:
        o[c] = pd.to_datetime(o[c].replace("", None))
    t["orders"] = o[(o.order_purchase_timestamp >= DESDE) & (o.order_purchase_timestamp < HASTA)]
    it = t["items"]
    it["price"] = it["price"].astype(float)
    it["freight_value"] = it["freight_value"].astype(float)
    it["shipping_limit_date"] = pd.to_datetime(it["shipping_limit_date"])
    pg = t["payments"]
    pg["payment_value"] = pg["payment_value"].astype(float)
    pg["payment_installments"] = pg["payment_installments"].astype(int)
    pg["payment_sequential"] = pg["payment_sequential"].astype(int)
    rv = t["reviews"]
    rv["review_score"] = rv["review_score"].astype(int)
    for c in ["review_creation_date", "review_answer_timestamp"]:
        rv[c] = pd.to_datetime(rv[c].replace("", None))
    return t


def numero(v):
    return None if v in ("", None) else int(float(v))


def cuantiles(serie):
    serie = pd.Series(serie).dropna()
    return [round(float(v), 4) for v in serie.quantile(CUANTILES)]


# ==========================================
# 2. COMPONENTES DEL PERFIL
# ==========================================
def catalogo(t):
    it, pr, se = t["items"], t["products"], t["sellers"]
    it = it[it.order_id.isin(t["orders"].order_id)]
    precio = it.groupby("product_id").price.agg(
        n="size", p50="median", sd=lambda s: float(np.log(s).std()) if len(s) > 1 else 0.0)
    pr = pr.merge(precio, left_on="product_id", right_index=True)
    vendedores = se.reset_index(drop=True)
    indice_vendedor = {s: i for i, s in enumerate(vendedores.seller_id)}
    pares = it.groupby(["product_id", "seller_id"]).size().rename("n").reset_index()
    pares_por_producto = {
        p: [[indice_vendedor[s], int(n)] for s, n in zip(g.seller_id, g.n)]
        for p, g in pares.groupby("product_id")}
    productos = []
    for r in pr.itertuples(index=False):
        productos.append([
            r.product_id, r.product_category_name or None,
            numero(r.product_name_lenght), numero(r.product_description_lenght), numero(r.product_photos_qty),
            numero(r.product_weight_g), numero(r.product_length_cm), numero(r.product_height_cm),
            numero(r.product_width_cm), int(r.n), round(float(r.p50), 2), round(min(float(r.sd), 0.35), 4),
            pares_por_producto[r.product_id]])
    return {
        "productos_campos": ["product_id", "product_category_name", "product_name_lenght",
                             "product_description_lenght", "product_photos_qty", "product_weight_g",
                             "product_length_cm", "product_height_cm", "product_width_cm",
                             "ventas", "precio_mediano", "precio_log_sd", "vendedores"],
        "productos": productos,
        "vendedores_campos": ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"],
        "vendedores": vendedores[["seller_id", "seller_zip_code_prefix", "seller_city",
                                  "seller_state"]].values.tolist(),
    }


def composicion(t):
    it = t["items"][t["items"].order_id.isin(t["orders"].order_id)]
    distintos = it.groupby("order_id").product_id.nunique().value_counts().sort_index()
    cantidad = it.groupby(["order_id", "product_id"]).size().value_counts().sort_index()
    limite = (it.shipping_limit_date - it.order_id.map(
        t["orders"].set_index("order_id").order_purchase_timestamp)).dt.total_seconds() / 86400
    return {
        "productos_por_pedido": [[int(k), int(v)] for k, v in distintos.items() if k <= 6],
        "unidades_por_producto": [[int(k), int(v)] for k, v in cantidad.items() if k <= 6],
        "dias_limite_envio": cuantiles(limite[(limite >= 0) & (limite < 60)]),
    }


def geografia(t):
    o, c = t["orders"], t["customers"]
    c = c[c.customer_id.isin(o.customer_id)]
    zips = (c.groupby(["customer_zip_code_prefix", "customer_city", "customer_state"]).size()
            .rename("n").reset_index().sort_values("n", ascending=False)
            .drop_duplicates("customer_zip_code_prefix"))
    rng = np.random.default_rng(SEMILLA)
    unicos = c.drop_duplicates("customer_unique_id")
    pool = unicos.iloc[rng.choice(len(unicos), size=min(30000, len(unicos)), replace=False)]
    # cambios de dirección reales entre pedidos del mismo cliente
    oc = o.merge(c, on="customer_id").sort_values("order_purchase_timestamp")
    oc["dir"] = oc.customer_zip_code_prefix + "|" + oc.customer_state
    rec = oc.groupby("customer_unique_id").filter(lambda g: len(g) > 1)
    cambios = rec.groupby("customer_unique_id").agg(dirs=("dir", "nunique"),
                                                     estados=("customer_state", "nunique"))
    con_cambio = cambios[cambios.dirs > 1]
    return {
        "zips_campos": ["zip", "ciudad", "estado", "peso"],
        "zips": zips[["customer_zip_code_prefix", "customer_city", "customer_state", "n"]].values.tolist(),
        "clientes_campos": ["customer_unique_id", "zip", "ciudad", "estado"],
        "clientes": pool[["customer_unique_id", "customer_zip_code_prefix", "customer_city",
                          "customer_state"]].values.tolist(),
        "tasa_recompra": round(float((oc.groupby("customer_unique_id").size() > 1).mean()), 4),
        "tasa_cambio_direccion": round(float(len(con_cambio) / max(len(cambios), 1)), 4),
        "cambio_mismo_estado": round(float((con_cambio.estados == 1).mean()), 4),
    }


def estacionalidad(t):
    ts = t["orders"].order_purchase_timestamp
    m = pd.crosstab(ts.dt.dayofweek, ts.dt.hour).reindex(index=range(7), columns=range(24), fill_value=0)
    m = m / m.values.sum() * 7  # promedio por día = 1
    diario = t["orders"][t["orders"].order_purchase_timestamp >= "2018-01-01"].groupby(ts.dt.date).size()
    return {"semana_hora": m.round(5).values.tolist(), "ventas_dia_referencia": int(diario.mean())}


def pagos(t):
    pg = t["payments"][t["payments"].order_id.isin(t["orders"].order_id)]
    n = pg.groupby("order_id").size()
    simples = pg[pg.order_id.isin(n[n == 1].index)]
    tipos = simples[simples.payment_type != "not_defined"].payment_type.value_counts(normalize=True)
    tarjeta = simples[simples.payment_type == "credit_card"].copy()
    tramos = [0, 50, 100, 200, 500, 1e9]
    tarjeta["tramo"] = pd.cut(tarjeta.payment_value, tramos, labels=False, right=False)
    cuotas = {}
    for tramo, g in tarjeta.groupby("tramo"):
        dist = g.payment_installments.clip(1, 24).value_counts().sort_index()
        cuotas[str(int(tramo))] = [[int(k), int(v)] for k, v in dist.items()]
    return {
        "tipo_pago": [[k, round(float(v), 5)] for k, v in tipos.items()],
        "tasa_pago_combinado": round(float((n > 1).mean()), 4),
        "tramos_valor": tramos[:-1],
        "cuotas_tarjeta": cuotas,
    }


def flete(t):
    o, it = t["orders"], t["items"]
    d = (it.merge(o[["order_id", "customer_id"]], on="order_id")
         .merge(t["customers"][["customer_id", "customer_state"]], on="customer_id")
         .merge(t["sellers"][["seller_id", "seller_state"]], on="seller_id")
         .merge(t["products"][["product_id", "product_weight_g"]], on="product_id"))
    d = d[(d.freight_value > 0) & (d.product_weight_g != "")]
    peso = np.log(d.product_weight_g.astype(float) / 1000 + 0.1)
    valor = np.log(d.freight_value)
    pendiente = float(np.polyfit(peso, valor, 1)[0])
    d["base"] = valor - pendiente * peso
    d["ruta"] = d.seller_state + ">" + d.customer_state
    rutas = d.groupby("ruta").base.agg(["median", "size"])
    destino = d.groupby("customer_state").base.median()
    return {
        "pendiente_peso": round(pendiente, 4),
        "base_ruta": {k: round(float(v), 4) for k, v in rutas[rutas["size"] >= MIN_MUESTRAS_RUTA]["median"].items()},
        "base_destino": {k: round(float(v), 4) for k, v in destino.items()},
        "base_global": round(float(d.base.median()), 4),
        "ruido_sd": round(float((d.base - d.ruta.map(rutas["median"])).std()), 4),
    }


def logistica(t):
    o = t["orders"]
    primer_vendedor = t["items"].sort_values("order_item_id").drop_duplicates("order_id")[["order_id", "seller_id"]]
    d = (o[o.order_status == "delivered"].dropna(subset=[
        "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date"])
         .merge(t["customers"][["customer_id", "customer_state"]], on="customer_id")
         .merge(primer_vendedor, on="order_id")
         .merge(t["sellers"][["seller_id", "seller_state"]], on="seller_id"))
    compra = d.order_purchase_timestamp
    d["aprob_h"] = (d.order_approved_at - compra).dt.total_seconds() / 3600
    d["despacho_d"] = (d.order_delivered_carrier_date - compra).dt.total_seconds() / 86400
    d["entrega_d"] = (d.order_delivered_customer_date - compra).dt.total_seconds() / 86400
    d["estimada_d"] = (d.order_estimated_delivery_date - compra.dt.normalize()).dt.days
    d = d[(d.aprob_h >= 0) & (d.aprob_h < 24 * 15) & (d.despacho_d * 24 >= d.aprob_h)
          & (d.entrega_d > d.despacho_d) & (d.entrega_d < 120) & (d.estimada_d > 0)]
    d["ruta"] = d.seller_state + ">" + d.customer_state
    campos = ["aprob_h", "despacho_d", "entrega_d", "estimada_d"]

    def muestras(g):
        g = g.sample(n=min(len(g), MUESTRAS_POR_RUTA), random_state=SEMILLA)
        return [[round(float(v), 3) for v in fila] for fila in g[campos].values]

    tamanos = d.groupby("ruta").size()
    rutas = {r: muestras(g) for r, g in d.groupby("ruta") if tamanos[r] >= MIN_MUESTRAS_RUTA}
    destinos = {e: muestras(g) for e, g in d.groupby("customer_state")}
    tarde = (d.order_delivered_customer_date.dt.normalize() > d.order_estimated_delivery_date)
    cancelados = o.order_status.isin(["canceled", "unavailable"])
    return {
        "campos": campos, "rutas": rutas, "destinos": destinos, "global": muestras(d),
        "tasa_tarde_referencia": round(float(tarde.mean()), 4),
        "tasa_cancelacion": round(float(cancelados.mean()), 4),
        "cancelacion_unavailable": round(float((o.order_status == "unavailable").sum() / max(cancelados.sum(), 1)), 4),
    }


def resenas(t):
    o, rv = t["orders"], t["reviews"]
    rv = rv[rv.order_id.isin(o.order_id)].merge(
        o[["order_id", "order_status", "order_delivered_customer_date", "order_estimated_delivery_date"]], on="order_id")
    entregado = rv.order_delivered_customer_date.notna()
    atraso = (rv.order_delivered_customer_date.dt.normalize() - rv.order_estimated_delivery_date).dt.days
    rv["grupo"] = np.select(
        [rv.order_status.isin(["canceled", "unavailable"]), ~entregado, atraso <= 0, atraso <= 3, atraso <= 7],
        ["cancelado", "no_entregado", "a_tiempo", "tarde_1_3", "tarde_4_7"], "tarde_8_mas")
    puntajes = {g: [[int(k), int(v)] for k, v in x.review_score.value_counts().sort_index().items()]
                for g, x in rv.groupby("grupo")}
    base = rv.order_delivered_customer_date.dt.normalize()
    demora = (rv.review_creation_date - base).dt.days
    respuesta = (rv.review_answer_timestamp - rv.review_creation_date).dt.total_seconds() / 3600
    textos = {}
    rng = np.random.default_rng(SEMILLA)
    for s, g in rv.groupby("review_score"):
        titulos = g.review_comment_title[g.review_comment_title.str.strip() != ""].unique()
        mensajes = g.review_comment_message[g.review_comment_message.str.strip() != ""].unique()
        textos[str(s)] = {
            "p_titulo": round(float((g.review_comment_title.str.strip() != "").mean()), 4),
            "p_mensaje": round(float((g.review_comment_message.str.strip() != "").mean()), 4),
            "titulos": [str(x) for x in rng.choice(titulos, size=min(80, len(titulos)), replace=False)],
            "mensajes": [str(x).replace("\r", " ").replace("\n", " ")
                         for x in rng.choice(mensajes, size=min(150, len(mensajes)), replace=False)],
        }
    total = o.order_id.nunique()
    return {
        "tasa_resena": round(float(rv.order_id.nunique() / total), 4),
        "puntajes": puntajes,
        "dias_tras_entrega": cuantiles(demora[(demora >= 0) & (demora < 30)]),
        "horas_respuesta": cuantiles(respuesta[(respuesta >= 0) & (respuesta < 24 * 30)]),
        "textos": textos,
    }


# ==========================================
# 3. EJECUCIÓN
# ==========================================
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("carpeta", help="Carpeta con los CSV originales de Olist")
    parser.add_argument("--salida", default=str(Path(__file__).resolve().parent.parent
                                               / "olist_simulador" / "datos" / "perfil.json.gz"))
    args = parser.parse_args()

    t = leer(args.carpeta)
    perfil = {
        "meta": {
            "fuente": "Brazilian E-Commerce Public Dataset by Olist (Kaggle), CC BY-NC-SA 4.0",
            "periodo": [DESDE, HASTA],
            "generado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": 1,
        },
        "catalogo": catalogo(t),
        "composicion": composicion(t),
        "geografia": geografia(t),
        "estacionalidad": estacionalidad(t),
        "pagos": pagos(t),
        "flete": flete(t),
        "logistica": logistica(t),
        "resenas": resenas(t),
    }
    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(salida, "wt", encoding="utf-8") as f:
        json.dump(perfil, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Perfil: {salida} ({salida.stat().st_size / 1e6:.2f} MB)")
    print(f"Productos {len(perfil['catalogo']['productos']):,} | vendedores {len(perfil['catalogo']['vendedores']):,} | "
          f"zips {len(perfil['geografia']['zips']):,} | rutas {len(perfil['logistica']['rutas'])}")
    print(f"Tardanza ref {perfil['logistica']['tasa_tarde_referencia']:.2%} | cancelación "
          f"{perfil['logistica']['tasa_cancelacion']:.2%} | recompra {perfil['geografia']['tasa_recompra']:.2%} | "
          f"cambio dirección {perfil['geografia']['tasa_cambio_direccion']:.2%} | "
          f"ventas/día ref {perfil['estacionalidad']['ventas_dia_referencia']}")
    print("Tipos de pago:", perfil["pagos"]["tipo_pago"])


if __name__ == "__main__":
    main()
