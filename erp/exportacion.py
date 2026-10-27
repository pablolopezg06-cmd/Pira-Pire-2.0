"""
Módulo: exportacion
===================

Exportación del plan de cuentas y del Libro Diario a **CSV** o **Excel**.

Los archivos salen con las mismas columnas que las plantillas del
importador (``erp.importacion``), así que se pueden editar en una planilla y
volver a importar en otra base sin tocar los encabezados.

Del Libro Diario se exportan sólo los asientos CONFIRMADOS: son los únicos
que cuentan para el Mayor y el Balance, y los que el importador sabe crear.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from . import excel
from .base_datos import conectar, ruta_base_datos
from .dinero import desde_centavos, formato_monto
from .importacion import COLUMNAS_ASIENTOS, COLUMNAS_CUENTAS


#: Plantilla del plan de cuentas (con las cuentas de la cátedra).
PLANTILLA_CUENTAS = Path(__file__).resolve().parent.parent / "web" / "plantillas" / "cuentas.csv"


def filas_cuentas(ruta: str | None = None, *, completar: bool = False) -> list[list]:
    """
    Plan de cuentas completo, ordenado por jerarquía.

    Con ``completar=True`` se agregan las cuentas de la plantilla que la
    empresa todavía no tiene: al importar ese archivo, las existentes se
    omiten y solo se crean las que faltan.
    """
    with conectar(ruta_base_datos(ruta)) as conexion:
        filas = conexion.execute(
            """SELECT codigo, nombre, clasificacion, naturaleza,
                      recibe_movimiento, estado
                 FROM cuenta ORDER BY orden, codigo"""
        ).fetchall()
    resultado = [
        [
            f["codigo"], f["nombre"], f["clasificacion"], f["naturaleza"],
            "detalle" if f["recibe_movimiento"] else "agrupadora", f["estado"],
        ]
        for f in filas
    ]
    if completar:
        existentes = {fila[0] for fila in resultado}
        lector = csv.reader(io.StringIO(PLANTILLA_CUENTAS.read_text(encoding="utf-8")), delimiter=";")
        next(lector, None)
        resultado += [fila for fila in lector if fila and fila[0] not in existentes]
        resultado.sort(key=lambda fila: tuple(int(p) for p in str(fila[0]).split(".")))
    return resultado


def filas_asientos(
    ruta: str | None = None,
    *,
    desde: str | None = None,
    hasta: str | None = None,
) -> list[list]:
    """Líneas de los asientos confirmados; los importes quedan en centavos."""
    condiciones = ["a.estado = 'CONFIRMADO'"]
    parametros: list = []
    if desde:
        condiciones.append("a.fecha >= ?")
        parametros.append(desde)
    if hasta:
        condiciones.append("a.fecha <= ?")
        parametros.append(hasta)

    with conectar(ruta_base_datos(ruta)) as conexion:
        filas = conexion.execute(
            f"""SELECT a.numero, a.fecha, a.concepto,
                       p.cuenta, p.detalle, p.debe, p.haber
                  FROM asiento a
                  JOIN partida p ON p.asiento_id = a.id
                 WHERE {' AND '.join(condiciones)}
                 ORDER BY a.fecha, a.numero, p.linea""",
            parametros,
        ).fetchall()
    return [
        [f["numero"], f["fecha"], f["concepto"], f["cuenta"],
         f["detalle"] or "", f["debe"], f["haber"]]
        for f in filas
    ]


def _csv(encabezados: list[str], filas: list[list]) -> bytes:
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(encabezados)
    escritor.writerows(filas)
    # Con BOM, Excel abre el CSV en UTF-8 y respeta los acentos.
    return ("﻿" + salida.getvalue()).encode("utf-8")


def exportar(
    tipo: str,
    formato: str,
    ruta: str | None = None,
    **filtros,
) -> tuple[bytes, str, str]:
    """
    Devuelve ``(contenido, tipo_mime, nombre_de_archivo)``.

    ``tipo`` es ``"cuentas"`` o ``"asientos"``; ``formato``, ``"csv"`` o
    ``"xlsx"``.
    """
    if tipo == "cuentas":
        encabezados, hoja = COLUMNAS_CUENTAS, "Plan de cuentas"
        filas = filas_cuentas(ruta, completar=bool(filtros.get("completar")))
        importes: tuple[int, ...] = ()
    else:
        encabezados, hoja = COLUMNAS_ASIENTOS, "Libro Diario"
        filas = filas_asientos(ruta, **filtros)
        importes = (5, 6)

    if formato == "xlsx":
        for fila in filas:
            for i in importes:
                fila[i] = desde_centavos(fila[i]) if fila[i] else ""
        return (
            excel.escribir(encabezados, filas, hoja),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"{tipo}.xlsx",
        )

    for fila in filas:
        for i in importes:
            fila[i] = formato_monto(fila[i], con_simbolo=False) if fila[i] else ""
    return _csv(encabezados, filas), "text/csv; charset=utf-8", f"{tipo}.csv"
