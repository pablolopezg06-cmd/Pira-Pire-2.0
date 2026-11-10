"""
Módulo: estados
===============

Libro Mayor, Balance de comprobación, IRE y ratios **calculados a partir de
un Libro Diario en CSV** (el formato del importador, el mismo que propone
Pira Pires), sin pasar por la base de datos.

Sirve para resolver los ejercitarios de clase («Elabora el Libro Diario,
Libro Mayor y Balances; calcula el IRE y los ratios de ROI, Liquidez y
Solvencia») antes de importar nada: la IA arma el Diario y estos cálculos,
que son exactos, los hace el código.

Criterios (los de la plantilla de la cátedra):

* **Mayor:** una cuenta T por cuenta, numeradas por folio en el orden en que
  aparecen en el Diario.
* **Balance de comprobación de sumas y saldos:** sumas Debe/Haber, saldo
  deudor o acreedor, y el saldo repartido en patrimoniales (Activo |
  Pasivo/PNeto, cuentas 1 a 3) o resultados (Negativo | Positivo, cuentas
  4 a 6). La diferencia entre Positivo y Negativo es el resultado del
  ejercicio, que se suma del lado menor para llegar a «sumas iguales».
* **IRE:** 10 % de la renta neta, solo si hay ganancia (Ley N.º 6380/2019).
* **ROI** = utilidad / activo total × 100, con la utilidad después del IRE
  (y también antes del IRE, porque las dos definiciones se usan).
* **Liquidez corriente** = activo corriente (1.1) / pasivo corriente (2.1).
* **Solvencia** = activo total / pasivo total.

Todos los importes se manejan en centavos enteros, como en el resto del ERP.
"""

from __future__ import annotations

import csv
import io
from decimal import ROUND_HALF_UP, Decimal

from .dinero import a_centavos
from .excepciones import ErrorValidacion

#: Tasa general del Impuesto a la Renta Empresarial (Ley N.º 6380/2019).
TASA_IRE = Decimal("0.10")


def _lineas(texto_csv: str) -> list[list[str]]:
    filas = [
        f for f in csv.reader(io.StringIO((texto_csv or "").lstrip("﻿")), delimiter=";")
        if any(c.strip() for c in f)
    ]
    if len(filas) < 2:
        raise ErrorValidacion("No hay asientos para analizar.")
    return [(f + [""] * 7)[:7] for f in filas[1:]]


def _cociente(numerador: int, denominador: int) -> float | None:
    return round(numerador / denominador, 4) if denominador else None


def analizar(texto_csv: str, nombres: dict[str, str]) -> dict:
    """
    Calcula Mayor, Balance, resultado, IRE y ratios a partir del Diario.

    ``nombres`` traduce cada código de cuenta a su nombre. Devuelve un
    diccionario con importes en centavos, listo para mostrar o exportar.
    """
    mayor: dict[str, dict] = {}
    for asiento, fecha, _concepto, cuenta, detalle, debe, haber in _lineas(texto_csv):
        codigo = cuenta.strip()
        if not codigo:
            continue
        t = mayor.setdefault(codigo, {
            "codigo": codigo,
            "nombre": nombres.get(codigo) or detalle.strip() or codigo,
            "folio": len(mayor) + 1,
            "movimientos": [],
        })
        t["movimientos"].append({
            "asiento": asiento.strip(),
            "fecha": fecha.strip(),
            "debe": a_centavos(debe),
            "haber": a_centavos(haber),
        })

    balance: list[dict] = []
    for t in mayor.values():
        debe = sum(m["debe"] for m in t["movimientos"])
        haber = sum(m["haber"] for m in t["movimientos"])
        deudor, acreedor = max(debe - haber, 0), max(haber - debe, 0)
        patrimonial = t["codigo"][:1] in {"1", "2", "3"}
        fila = {
            "folio": t["folio"], "codigo": t["codigo"], "nombre": t["nombre"],
            "debe": debe, "haber": haber, "deudor": deudor, "acreedor": acreedor,
            "activo": deudor if patrimonial else 0,
            "pasivo_pn": acreedor if patrimonial else 0,
            "negativo": 0 if patrimonial else deudor,
            "positivo": 0 if patrimonial else acreedor,
        }
        t.update(debe=debe, haber=haber, saldo=debe - haber)
        balance.append(fila)

    columnas = ("debe", "haber", "deudor", "acreedor", "activo", "pasivo_pn", "negativo", "positivo")
    sumas = {c: sum(f[c] for f in balance) for c in columnas}
    resultado = sumas["positivo"] - sumas["negativo"]

    # El resultado va del lado menor en cada par para cerrar el balance:
    # una ganancia suma en Negativo y en Pasivo/PNeto; una pérdida, al revés.
    ajuste = dict.fromkeys(columnas, 0)
    if resultado > 0:
        ajuste.update(negativo=resultado, pasivo_pn=resultado)
    elif resultado < 0:
        ajuste.update(positivo=-resultado, activo=-resultado)
    sumas_iguales = {c: sumas[c] + ajuste[c] for c in columnas}

    ire = int((Decimal(resultado) * TASA_IRE).quantize(Decimal("1"), ROUND_HALF_UP)) if resultado > 0 else 0
    utilidad_neta = resultado - ire

    activo_total = sum(f["deudor"] - f["acreedor"] for f in balance if f["codigo"].startswith("1"))
    activo_corriente = sum(f["deudor"] - f["acreedor"] for f in balance if f["codigo"].startswith("1.1"))
    pasivo_total = sum(f["acreedor"] - f["deudor"] for f in balance if f["codigo"].startswith("2"))
    pasivo_corriente = sum(f["acreedor"] - f["deudor"] for f in balance if f["codigo"].startswith("2.1"))
    patrimonio = sum(f["acreedor"] - f["deudor"] for f in balance if f["codigo"].startswith("3"))

    roi = _cociente(utilidad_neta * 100, activo_total)
    return {
        "mayor": list(mayor.values()),
        "balance": balance,
        "sumas": sumas,
        "resultado": resultado,
        "sumas_iguales": sumas_iguales,
        "ire": ire,
        "utilidad_neta": utilidad_neta,
        "activo_total": activo_total,
        "activo_corriente": activo_corriente,
        "pasivo_total": pasivo_total,
        "pasivo_corriente": pasivo_corriente,
        "patrimonio": patrimonio,
        "ratios": {
            "roi": roi,
            "roi_antes_ire": _cociente(resultado * 100, activo_total),
            "liquidez": _cociente(activo_corriente, pasivo_corriente),
            "solvencia": _cociente(activo_total, pasivo_total),
        },
    }


def resumen(analisis: dict) -> dict:
    """Lo que muestra la tarjeta del chat (sin el Mayor ni el Balance completos)."""
    return {
        clave: analisis[clave] for clave in (
            "resultado", "ire", "utilidad_neta", "activo_total", "activo_corriente",
            "pasivo_total", "pasivo_corriente", "patrimonio", "ratios",
        )
    } | {"cuentas": len(analisis["balance"]), "sumas_iguales": analisis["sumas_iguales"]["debe"]}
