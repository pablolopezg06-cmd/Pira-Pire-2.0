"""
Módulo: libro_mayor
===================

Etapa 2 del Mini ERP Contable — **Libro Mayor automático** (sección 15).

Punto central del enunciado:

    "El estudiante deberá comprender que el Libro Mayor no constituye una
     nueva carga de datos. Debe generarse utilizando los movimientos
     existentes en el Libro Diario."

Por eso este módulo **no tiene ninguna tabla propia**. Todo lo que hace es
un `SELECT` sobre las partidas de los asientos CONFIRMADOS y, en Python, el
cálculo del saldo acumulado (running balance) de cada cuenta.

Los asientos en BORRADOR y ANULADOS no se consideran: solo lo contabilizado
genera movimiento en el Libro Mayor.
"""

from __future__ import annotations

from .base_datos import conectar, ruta_base_datos
from .dinero import formato_monto
from .validaciones import CLASIFICACIONES, nombre_naturaleza

#: Solo los asientos en este estado generan Libro Mayor.
ESTADO_CONTABILIZADO = "CONFIRMADO"


# ---------------------------------------------------------------------------
# Consulta base: movimientos del Libro Diario
# ---------------------------------------------------------------------------

SQL_MOVIMIENTOS = """
    SELECT a.id        AS asiento_id,
           a.numero    AS numero,
           a.fecha     AS fecha,
           a.concepto  AS concepto,
           p.linea     AS linea,
           p.cuenta    AS cuenta,
           c.nombre    AS cuenta_nombre,
           c.naturaleza AS naturaleza,
           c.clasificacion AS clasificacion,
           p.detalle   AS detalle,
           p.debe      AS debe,
           p.haber     AS haber
      FROM partida p
      JOIN asiento a ON a.id = p.asiento_id
      JOIN cuenta  c ON c.codigo = p.cuenta
     WHERE a.estado = :estado
       AND (:desde  IS NULL OR a.fecha >= :desde)
       AND (:hasta  IS NULL OR a.fecha <= :hasta)
       AND (:cuenta IS NULL OR p.cuenta = :cuenta)
     ORDER BY a.fecha, a.numero, p.linea
"""


def _movimientos(
    conexion,
    desde: str | None,
    hasta: str | None,
    cuenta: str | None = None,
) -> list[dict]:
    """Ejecuta la consulta de movimientos del Libro Diario."""
    filas = conexion.execute(
        SQL_MOVIMIENTOS,
        {"estado": ESTADO_CONTABILIZADO, "desde": desde, "hasta": hasta, "cuenta": cuenta},
    ).fetchall()
    return [dict(f) for f in filas]


def saldo_anterior(conexion, cuenta: str, desde: str | None) -> int:
    """
    Saldo de la cuenta **anterior al período** solicitado.

    Es la suma de todos los movimientos contabilizados con fecha anterior a
    `desde`. Si `desde` es None el saldo inicial es 0.
    """
    if not desde:
        return 0
    fila = conexion.execute(
        """SELECT COALESCE(SUM(p.debe), 0) AS debe, COALESCE(SUM(p.haber), 0) AS haber
             FROM partida p
             JOIN asiento a ON a.id = p.asiento_id
            WHERE a.estado = ? AND a.fecha < ? AND p.cuenta = ?""",
        (ESTADO_CONTABILIZADO, desde, cuenta),
    ).fetchone()
    return int(fila["debe"]) - int(fila["haber"])


# ---------------------------------------------------------------------------
# Libro Mayor por cuenta
# ---------------------------------------------------------------------------

def mayor_de_cuenta(
    codigo: str,
    ruta: str | None = None,
    *,
    desde: str | None = None,
    hasta: str | None = None,
) -> dict:
    """
    Genera el Libro Mayor de **una** cuenta (ejemplo de la sección 15):

        Fecha | Concepto        | Debe   | Haber  | Saldo

    Devuelve la cuenta, sus movimientos con saldo acumulado y los totales.
    """
    codigo = str(codigo).strip()

    with conectar(ruta_base_datos(ruta)) as conexion:
        cabecera = conexion.execute(
            "SELECT * FROM cuenta WHERE codigo = ?", (codigo,)
        ).fetchone()
        if cabecera is None:
            from .excepciones import ErrorNoEncontrado

            raise ErrorNoEncontrado(f"La cuenta '{codigo}' no existe.")

        cuenta = dict(cabecera)
        movimientos = _movimientos(conexion, desde, hasta, codigo)
        inicial = saldo_anterior(conexion, codigo, desde)

    filas, saldo = _acumular(movimientos, cuenta["naturaleza"], inicial)

    return {
        "cuenta": {
            **cuenta,
            "naturaleza_nombre": nombre_naturaleza(cuenta["naturaleza"]),
            "clasificacion_nombre": CLASIFICACIONES[cuenta["clasificacion"]]["nombre"],
        },
        "periodo": {"desde": desde, "hasta": hasta},
        "saldo_anterior": inicial,
        "movimientos": filas,
        "totales": {
            "debe": sum(f["debe"] for f in filas),
            "haber": sum(f["haber"] for f in filas),
            "saldo_final": saldo,
        },
        "regla_aplicada": "C4",
    }


def _acumular(movimientos: list[dict], naturaleza: str, saldo_inicial: int):
    """
    Calcula el saldo acumulado línea a línea del Libro Mayor.

    Control 6: el saldo se expresa **según la naturaleza de la cuenta**.

        · Cuenta deudora  : el saldo es positivo si Debe > Haber.
        · Cuenta acreedora : el saldo es positivo si Haber > Debe.

    En ambos casos el valor devuelto es el saldo con signo contable
    (positivo = del lado de su naturaleza), y `lado` indica en cuál de los
    dos lados se ubica.
    """
    filas = []
    saldo = saldo_inicial

    for movimiento in movimientos:
        debe = int(movimiento["debe"])
        haber = int(movimiento["haber"])

        # Saldo con signo deudor: + Debe - Haber.
        saldo += debe - haber

        fila = {
            **movimiento,
            "debe": debe,
            "haber": haber,
            "saldo_neto": saldo,
        }

        if naturaleza == "A":
            # Cuenta acreedora: el saldo contable es el inverso.
            fila["saldo"] = -saldo
            fila["lado"] = "A" if saldo < 0 else ("=" if saldo == 0 else "D")
        else:
            fila["saldo"] = saldo
            fila["lado"] = "D" if saldo > 0 else ("=" if saldo == 0 else "A")

        filas.append(fila)

    saldo_final = -saldo if naturaleza == "A" else saldo
    return filas, saldo_final


# ---------------------------------------------------------------------------
# Libro Mayor completo
# ---------------------------------------------------------------------------

def libro_mayor_completo(
    ruta: str | None = None,
    *,
    desde: str | None = None,
    hasta: str | None = None,
    solo_cuentas_con_movimiento: bool = True,
) -> dict:
    """
    Genera el Libro Mayor de **todas** las cuentas de detalle, agrupadas por
    código, con sus totales y saldos.

    Estructura devuelta:

        {
          "periodo": {...},
          "cuentas": [
            {"cuenta": {...}, "saldo_anterior": n, "movimientos": [...],
             "totales": {...}},
            ...
          ],
          "totales": {...}
        }
    """
    with conectar(ruta_base_datos(ruta)) as conexion:
        movimientos = _movimientos(conexion, desde, hasta)

        cuentas_por_codigo: dict[str, list[dict]] = {}
        for movimiento in movimientos:
            cuentas_por_codigo.setdefault(movimiento["cuenta"], []).append(movimiento)

        if solo_cuentas_con_movimiento:
            codigos = list(cuentas_por_codigo.keys())
        else:
            codigos = [
                fila["codigo"]
                for fila in conexion.execute(
                    "SELECT codigo FROM cuenta WHERE recibe_movimiento = 1 ORDER BY codigo"
                ).fetchall()
            ]

        # Orden jerárquico por código, no por orden de aparición.
        codigos = sorted(codigos)

        cuentas: list[dict] = []
        for codigo in codigos:
            cabecera = conexion.execute(
                "SELECT * FROM cuenta WHERE codigo = ?", (codigo,)
            ).fetchone()
            if cabecera is None:
                continue

            cuenta = dict(cabecera)
            sus_movimientos = sorted(
                cuentas_por_codigo.get(codigo, []),
                key=lambda m: (m["fecha"], m["numero"], m["linea"]),
            )
            inicial = saldo_anterior(conexion, codigo, desde)
            filas, saldo = _acumular(sus_movimientos, cuenta["naturaleza"], inicial)

            cuentas.append(
                {
                    "cuenta": {
                        **cuenta,
                        "naturaleza_nombre": nombre_naturaleza(cuenta["naturaleza"]),
                        "clasificacion_nombre": CLASIFICACIONES[cuenta["clasificacion"]]["nombre"],
                    },
                    "saldo_anterior": inicial,
                    "saldo_inicial_formateado": formato_monto(inicial),
                    "movimientos": filas,
                    "totales": {
                        "debe": sum(f["debe"] for f in filas),
                        "haber": sum(f["haber"] for f in filas),
                        "saldo_final": saldo,
                    },
                }
            )

    total_debe = sum(c["totales"]["debe"] for c in cuentas)
    total_haber = sum(c["totales"]["haber"] for c in cuentas)

    return {
        "periodo": {"desde": desde, "hasta": hasta},
        "cuentas": cuentas,
        "totales": {
            "debe": total_debe,
            "haber": total_haber,
            "diferencia": total_debe - total_haber,
            "cuadra": total_debe == total_haber,
        },
        "origen": "Partidas de los asientos CONFIRMADOS del Libro Diario",
        "regla_aplicada": "C4",
    }