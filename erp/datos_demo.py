"""
Módulo: datos_demo
==================

Empresa ficticia con las **15 operaciones económicas mínimas** que pide la
sección 19 del enunciado, para poder mostrar en cadena:

    Libro Diario → Libro Mayor → Balance de Comprobación

Los importes están en la unidad de la tabla de la sección 19 y son todos
redondos para que la lectura sea fácil.

Verificación aritmética de la carga (calculada por el propio sistema):

    Total de débitos  = Total de créditos  = G. 189.650.000
    Suma saldos deudores = Suma saldos acreedores = G. 132.250.000

Distribución de las 15 operaciones por tipo de operación:

    · Aportes de los socios ........................ 2
    · Compras (contado y crédito) .................. 2
    · Ventas (contado y crédito) ................... 4
    · Cobros y pagos a terceros .................... 2
    · Gastos (administrativos y de ventas) ......... 2
    · Bienes de uso y depreciación ................. 2
    · Costo de mercaderías vendidas ................ 1
"""

from __future__ import annotations

from . import base_datos, libro_diario, plan_de_cuentas
from .dinero import formato_monto

#: Datos de la empresa que acompañan a los movimientos de prueba.
#:
#: El **nombre va vacío a propósito**: en pantalla lo pone el usuario que entró
#: desde el inicio de sesión, así los datos de prueba no le imponen una razón
#: social ficticia. El CUIT, la actividad y el ejercicio sí sirven de ejemplo,
#: para que el panel no arranque sin contexto.
EMPRESA = {
    "nombre": "",
    "cuit": "",
    "actividad": "Comercio mayorista de mercaderías",
    "ejercicio_inicio": "2026-01-01",
    "ejercicio_fin": "2026-12-31",
    "moneda": "G.",
}

#: Las 15 operaciones. Cada entrada es:
#:     (fecha, concepto, [(cuenta, "debe"|"haber", importe)], [observaciones])
OPERACIONES = [
    (
        "2026-04-01",
        "Aporte inicial de los socios en cuenta bancaria",
        [("1.1.02", "debe", 50_000_000), ("3.1", "haber", 50_000_000)],
        "Aporte de capital en efectivo depositado en el banco.",
    ),
    (
        "2026-04-02",
        "Aporte de fondos para la caja chica",
        [("1.1.01", "debe", 12_000_000), ("1.1.02", "haber", 12_000_000)],
        "Retiro del banco para workings de efectivo.",
    ),
    (
        "2026-04-03",
        "Compra de mercaderías pagada al contado con efectivo",
        [("1.1.04", "debe", 20_000_000), ("1.1.01", "haber", 20_000_000)],
        "Mercaderías compradas y pagadas en el momento.",
    ),
    (
        "2026-04-05",
        "Compra de mercaderías a crédito",
        [("1.1.04", "debe", 15_000_000), ("2.1.01", "haber", 15_000_000)],
        "Compra a proveedores, se genera deuda comercial.",
    ),
    (
        "2026-04-06",
        "Venta de mercaderías pagada al contado con efectivo",
        [("1.1.01", "debe", 12_000_000), ("4.1", "haber", 12_000_000)],
        "Venta cobrada en efectivo.",
    ),
    (
        "2026-04-07",
        "Venta de mercaderías a crédito",
        [("1.1.03", "debe", 18_000_000), ("4.1", "haber", 18_000_000)],
        "Venta a clientes: nace la cuenta por cobrar.",
    ),
    (
        "2026-04-08",
        "Cobro a clientes",
        [("1.1.02", "debe", 9_000_000), ("1.1.03", "haber", 9_000_000)],
        "Cobro parcial de cuentas por cobrar.",
    ),
    (
        "2026-04-10",
        "Pago a proveedores",
        [("1.1.02", "debe", 7_000_000), ("2.1.01", "haber", 7_000_000)],
        "Pago parcial de la deuda con proveedores.",
    ),
    (
        "2026-04-11",
        "Pago de gastos administrativos del período",
        [("6.1", "debe", 1_500_000), ("1.1.01", "haber", 1_500_000)],
        "Servicios básicos, papelería y útiles de oficina.",
    ),
    (
        "2026-04-12",
        "Pago de gastos de ventas del período",
        [("6.2", "debe", 900_000), ("1.1.01", "haber", 900_000)],
        "Fletes y comisiones de vendedores.",
    ),
    (
        "2026-04-14",
        "Compra de muebles y equipos a crédito",
        [("1.2.01", "debe", 5_000_000), ("2.1.01", "haber", 5_000_000)],
        "Bienes de uso incorporados al activo no corriente.",
    ),
    (
        "2026-04-15",
        "Depreciación de muebles y equipos del período",
        [("6.1", "debe", 250_000), ("1.2.02", "haber", 250_000)],
        "Cuenta contrapasiva del activo; muestra el control de naturaleza.",
    ),
    (
        "2026-04-18",
        "Venta de mercaderías con depósito directo en el banco",
        [("1.1.02", "debe", 15_000_000), ("4.1", "haber", 15_000_000)],
        "Venta cobrada por transferencia bancaria.",
    ),
    (
        "2026-04-20",
        "Venta de mercaderías a crédito",
        [("1.1.03", "debe", 10_000_000), ("4.1", "haber", 10_000_000)],
        "Segunda venta del período a clientes.",
    ),
    (
        "2026-04-25",
        "Costo de mercaderías vendidas del período",
        [("5.1", "debe", 14_000_000), ("1.1.04", "haber", 14_000_000)],
        "Baja de mercaderías por las ventas realizadas.",
    ),
]


def cargar(ruta: str | None = None, recrear: bool = False) -> dict:
    """
    Carga la empresa, el plan de cuentas mínimo y las 15 operaciones.

    Si `recrear` es True, borra previamente los asientos y las cuentas para
    empezar de cero (útil para repetir la demostración).
    """
    if recrear:
        base_datos.reiniciar(ruta)
        with base_datos.conectar(ruta) as conexion:
            base_datos.asegurar_empresa(conexion)

    with base_datos.conectar(ruta) as conexion:
        base_datos.asegurar_empresa(conexion)
        conexion.execute(
            """UPDATE empresa SET nombre = ?, cuit = ?, actividad = ?,
                                ejercicio_inicio = ?, ejercicio_fin = ?, moneda = ?
                WHERE id = 1""",
            tuple(EMPRESA.values()),
        )

    cuentas_creadas = plan_de_cuentas.cargar_plan_minimo(ruta)

    # Los asientos se registran directamente como CONFIRMADOS: se bypassa
    # la creación en BORRADOR pero NO la validación, porque
    # `crear_asiento` corre los controles 1 y 2 antes de contabilizar.
    asientos = []
    errores = []
    for fecha, concepto, lineas, observaciones in OPERACIONES:
        partidas = [
            {
                "cuenta": cuenta,
                "debe": importe if lado == "debe" else 0,
                "haber": importe if lado == "haber" else 0,
                "detalle": concepto,
            }
            for cuenta, lado, importe in lineas
        ]
        try:
            asientos.append(
                libro_diario.crear_asiento(
                    fecha=fecha,
                    concepto=concepto,
                    partidas=partidas,
                    confirmar=True,
                    observaciones=observaciones,
                    ruta=ruta,
                )
            )
        except Exception as exc:  # pragma: no cover - datos de prueba
            errores.append({"fecha": fecha, "concepto": concepto, "error": str(exc)})

    cuadre = libro_diario.verificar_cuadre(ruta)

    return {
        "empresa": EMPRESA,
        "cuentas_creadas": cuentas_creadas,
        "asientos_creados": len(asientos),
        "errores": errores,
        "cuadre_libro_diario": cuadre,
        "mensaje": (
            f"Se cargaron {cuentas_creadas} cuentas y {len(asientos)} asientos. "
            f"Total Debe {formato_monto(cuadre['total_debe'])} = "
            f"Total Haber {formato_monto(cuadre['total_haber'])}."
        ),
    }