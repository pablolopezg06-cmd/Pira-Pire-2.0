"""
Módulo: balance
===============

Etapa 3 del Mini ERP Contable — **Balance de Comprobación automático**
(sección 16).

Al igual que el Libro Mayor, el balance **no tiene tabla propia**: se calcula
con un `SELECT` que suma los débitos y créditos ya registrados en el Libro
Diario (control 5).

Salida esperada por el enunciado:

    Cuenta | Débitos | Créditos | Saldo Deudor | Saldo Acreedor

Verificaciones que realiza:
  · Total de débitos = total de créditos.
  · Suma de saldos deudores = suma de saldos acreedores.
  · Los saldos respetan la naturaleza de cada cuenta (control 6).
"""

from __future__ import annotations

from .base_datos import conectar, ruta_base_datos
from .dinero import formato_monto
from .validaciones import CLASIFICACIONES, nombre_naturaleza

ESTADO_CONTABILIZADO = "CONFIRMADO"

#: Trae, por cuenta de detalle, los débitos y créditos contabilizados en el
#: período. Las partidas se filtran primero en una subconsulta (solo asientos
#: CONFIRMADO y dentro del rango de fechas) y recién después se combinan con
#: el plan de cuentas. Así una partida de un asiento no contabilizado no se
#: suma, y una cuenta sin movimiento aparece igual con totales en cero.
SQL_SALDOS = """
    SELECT c.codigo             AS codigo,
           c.nombre             AS nombre,
           c.clasificacion      AS clasificacion,
           c.naturaleza         AS naturaleza,
           c.nivel              AS nivel,
           c.estado             AS estado,
           COALESCE(SUM(p.debe),  0) AS debe,
           COALESCE(SUM(p.haber), 0) AS haber,
           COUNT(p.id)              AS movimientos
      FROM cuenta c
      LEFT JOIN (
            SELECT pa.id, pa.cuenta, pa.debe, pa.haber
              FROM partida pa
              JOIN asiento a ON a.id = pa.asiento_id
             WHERE a.estado = :estado
               AND (:desde IS NULL OR a.fecha >= :desde)
               AND (:hasta IS NULL OR a.fecha <= :hasta)
           ) p ON p.cuenta = c.codigo
     WHERE c.recibe_movimiento = 1
       AND (:incluir_inactivas = 1 OR c.estado = 'ACTIVA')
     GROUP BY c.codigo
     ORDER BY c.codigo
"""


def balance_comprobacion(
    ruta: str | None = None,
    *,
    desde: str | None = None,
    hasta: str | None = None,
    incluir_cuentas_sin_movimiento: bool = False,
    incluir_inactivas: bool = True,
    agrupar_por_clasificacion: bool = True,
) -> dict:
    """
    Genera el Balance de Comprobación a partir de lo contabilizado.

    Parámetros
    ----------
    incluir_cuentas_sin_movimiento:
        Si es True, listan también las cuentas de detalle que no tienen
        movimientos en el período (con totales en cero).
    agrupar_por_clasificacion:
        Si es True, agrega subtotales y totales por clasificación, lo que
        hace visible el control 6 (naturaleza deudora o acreedora).
    """
    with conectar(ruta_base_datos(ruta)) as conexion:
        filas = conexion.execute(
            SQL_SALDOS,
            {
                "estado": ESTADO_CONTABILIZADO,
                "desde": desde,
                "hasta": hasta,
                "incluir_inactivas": 1 if incluir_inactivas else 0,
            },
        ).fetchall()

    cuentas: list[dict] = []
    for fila in filas:
        codigo = fila["codigo"]
        debe = int(fila["debe"])
        haber = int(fila["haber"])

        if not incluir_cuentas_sin_movimiento and debe == 0 and haber == 0:
            continue

        saldo_deudor = debe - haber if debe > haber else 0
        saldo_acreedor = haber - debe if haber > debe else 0

        cuentas.append(
            {
                "codigo": codigo,
                "nombre": fila["nombre"],
                "nivel": int(fila["nivel"]),
                "clasificacion": int(fila["clasificacion"]),
                "clasificacion_nombre": CLASIFICACIONES[int(fila["clasificacion"])]["nombre"],
                "naturaleza": fila["naturaleza"],
                "naturaleza_nombre": nombre_naturaleza(fila["naturaleza"]),
                "estado": fila["estado"],
                "debe": debe,
                "haber": haber,
                "saldo_deudor": saldo_deudor,
                "saldo_acreedor": saldo_acreedor,
                "movimientos": int(fila["movimientos"]),
            }
        )

    total_debe = sum(c["debe"] for c in cuentas)
    total_haber = sum(c["haber"] for c in cuentas)
    total_deudor = sum(c["saldo_deudor"] for c in cuentas)
    total_acreedor = sum(c["saldo_acreedor"] for c in cuentas)

    resultado = {
        "periodo": {"desde": desde, "hasta": hasta},
        "cuentas": cuentas,
        "totales": {
            "debe": total_debe,
            "haber": total_haber,
            "diferencia": total_debe - total_haber,
            "saldo_deudor": total_deudor,
            "saldo_acreedor": total_acreedor,
            "diferencia_saldos": total_deudor - total_acreedor,
        },
        "verificaciones": {
            "debitos_igual_creditos": {
                "cumple": total_debe == total_haber,
                "mensaje": (
                    f"Total de débitos {formato_monto(total_debe)} = "
                    f"total de créditos {formato_monto(total_haber)}"
                    if total_debe == total_haber
                    else (
                        f"El total de débitos ({formato_monto(total_debe)}) no es igual "
                        f"al de créditos ({formato_monto(total_haber)}). "
                        "Diferencia: " + formato_monto(total_debe - total_haber)
                    )
                ),
                "regla": "C5",
            },
            "saldos_iguales": {
                "cumple": total_deudor == total_acreedor,
                "mensaje": (
                    f"Suma de saldos deudores {formato_monto(total_deudor)} = "
                    f"suma de saldos acreedores {formato_monto(total_acreedor)}"
                    if total_deudor == total_acreedor
                    else (
                        f"Las sumas de saldos no coinciden: deudores "
                        f"{formato_monto(total_deudor)} vs acreedores "
                        f"{formato_monto(total_acreedor)}"
                    )
                ),
                "regla": "C5",
            },
        },
        "origen": "Suma de débitos y créditos de los asientos CONFIRMADOS del Libro Diario",
        "regla_aplicada": "C5",
    }

    if agrupar_por_clasificacion:
        resultado["por_clasificacion"] = _subtotos_por_clasificacion(cuentas)

    resultado["cuadrado"] = (
        resultado["verificaciones"]["debitos_igual_creditos"]["cumple"]
        and resultado["verificaciones"]["saldos_iguales"]["cumple"]
    )
    return resultado


def _subtotos_por_clasificacion(cuentas: list[dict]) -> list[dict]:
    """
    Agrupa el balance por clasificación (Activo, Pasivo, Patrimonio Neto,
    Ingresos, Costos y Gastos) mostrando los subtotales.

    Aquí es donde se nota el control 6: cada clasificación tiene una
    naturaleza (deudora o acreedora) que define cómo se lee su saldo.
    """
    grupos: dict[int, dict] = {}

    for clave, datos in CLASIFICACIONES.items():
        grupos[clave] = {
            "clasificacion": clave,
            "nombre": datos["nombre"],
            "tipo": datos["tipo"],
            "naturaleza": datos["naturaleza"],
            "naturaleza_nombre": nombre_naturaleza(datos["naturaleza"]),
            "debe": 0,
            "haber": 0,
            "saldo_deudor": 0,
            "saldo_acreedor": 0,
            "cuentas": 0,
        }

    for cuenta in cuentas:
        grupo = grupos[cuenta["clasificacion"]]
        grupo["debe"] += cuenta["debe"]
        grupo["haber"] += cuenta["haber"]
        grupo["saldo_deudor"] += cuenta["saldo_deudor"]
        grupo["saldo_acreedor"] += cuenta["saldo_acreedor"]
        grupo["cuentas"] += 1

    resultado = []
    for clave in sorted(grupos):
        grupo = grupos[clave]
        # Saldo con la naturaleza de la clasificación: positivo si la
        # clasificación se comporta como es (activo deudor, pasivo acreedor).
        if grupo["naturaleza"] == "D":
            grupo["saldo_natural"] = grupo["saldo_deudor"] - grupo["saldo_acreedor"]
        else:
            grupo["saldo_natural"] = grupo["saldo_acreedor"] - grupo["saldo_deudor"]
        grupo["saldo_natural_formateado"] = formato_monto(grupo["saldo_natural"])
        resultado.append(grupo)

    return resultado


# ---------------------------------------------------------------------------
# Verificación independiente contra el Libro Mayor
# ---------------------------------------------------------------------------

def cruzar_con_libro_mayor(ruta: str | None = None, *, desde=None, hasta=None) -> dict:
    """
    Control cruzado: vuelve a sumar los saldos desde el Libro Mayor y los
    compara con el Balance.

    Sirve para demostrar en la práctica que el balance se construye con la
    misma información del Libro Diario (controles 4 y 5). Si ambos
    coinciden, el balance está bien generado.
    """
    from .libro_mayor import libro_mayor_completo

    balance = balance_comprobacion(ruta, desde=desde, hasta=hasta)
    mayor = libro_mayor_completo(ruta, desde=desde, hasta=hasta)

    saldos_balance = {
        c["codigo"]: c["saldo_deudor"] - c["saldo_acreedor"]
        for c in balance["cuentas"]
    }
    saldos_mayor = {
        c["cuenta"]["codigo"]: (
            c["totales"]["saldo_final"]
            if c["cuenta"]["naturaleza"] == "D"
            else -c["totales"]["saldo_final"]
        )
        for c in mayor["cuentas"]
    }

    diferencias = []
    for codigo in sorted(set(saldos_balance) | set(saldos_mayor)):
        a = saldos_balance.get(codigo, 0)
        b = saldos_mayor.get(codigo, 0)
        if a != b:
            diferencias.append(
                {"cuenta": codigo, "saldo_balance": a, "saldo_libro_mayor": b}
            )

    return {
        "coincide": not diferencias,
        "diferencias": diferencias,
        "mensaje": (
            "El Balance y el Libro Mayor provienen de la misma información "
            "contabilizada y sus saldos coinciden cuenta por cuenta."
            if not diferencias
            else f"Se detectaron {len(diferencias)} diferencias entre ambos reportes."
        ),
    }