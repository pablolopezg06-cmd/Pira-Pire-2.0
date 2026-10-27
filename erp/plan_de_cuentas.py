"""
Módulo: plan_de_cuentas
=======================

Etapa 1 del Mini ERP Contable — módulo **Plan de cuentas** (sección 13).

Permite registrar cuentas jerárquicas con los datos mínimos exigidos:

    código · nombre · clasificación · naturaleza · estado

Además controla la **clasificación correcta** (control 3), que es donde
se Decide si una cuenta es de detalle o agrupadora y si su naturaleza es
coherente con la clasificación (control 6).
"""

from __future__ import annotations

import sqlite3

from . import validaciones
from .base_datos import conectar, error_sql_a_mensaje, ruta_base_datos
from .excepciones import ErrorNoEncontrado, ErrorOperacionInvalida, ErrorValidacion

#: Plan de cuentas mínimo exigido por el enunciado (sección 13), ampliado con
#: las cuentas que necesitan las 15 operaciones de prueba de la sección 19 y
#: con el bloque completo de IVA de Paraguay (Ley N.º 1259/97).
#:
#: Cada fila es: (código, nombre, clasificación, recibe_movimiento, naturaleza)
#: El quinto elemento es opcional: si se omite, la naturaleza se deduce de la
#: clasificación (control 6). Solo se indica en la cuenta contrapasiva
#: "Depreciación Acumulada": aunque es un ACTIVO (naturaleza deudora por
#: definición), se contabiliza con saldo acreedor porque resta del valor del
#: bien. Es el mejor ejemplo para mostrar que el ERP diferencia las dos
#: naturalezas.
#:
#: BLOQUE DE IVA (Paraguay). Seis cuentas que cubren el ciclo completo del
#: impuesto al valor agregado:
#:
#:   Débito Fiscal (2.1.02)   -> IVA que se cobra en las ventas. Acreedora.
#:   Crédito Fiscal (1.1.05)  -> IVA que se paga en las compras. Deudora.
#:   IVA por Pagar (2.1.03)   -> se acredita al liquidar (débito > crédito).
#:   IVA a Recuperar (1.1.06) -> se debita cuando el crédito supera al débito.
#:   Ventas Gravadas (4.1.01) -> ventas alcanzadas por el impuesto.
#:   Ventas Exentas (4.1.02)  -> ventas no alcanzadas (Art. 15 y sgtes.).
#:
#: «Ventas» (4.1) queda como cuenta agrupadora de las dos anteriores, igual
#: que ACTIVO agrupa a sus subdivisiones: así se puede ver el total vendido
#: sin perder el detalle de cuánto fue gravado y cuánto exento.
PLAN_MINIMO = [
    ("1",       "ACTIVO",                        1, False),
    ("1.1",     "ACTIVO CORRIENTE",              1, False),
    ("1.1.01",  "Caja",                          1, True),
    ("1.1.02",  "Banco",                         1, True),
    ("1.1.03",  "Clientes",                      1, True),
    ("1.1.04",  "Mercaderías",                   1, True),
    ("1.1.05",  "IVA Crédito Fiscal",            1, True),
    ("1.1.06",  "IVA a Recuperar",               1, True),
    ("1.2",     "ACTIVO NO CORRIENTE",           1, False),
    ("1.2.01",  "Muebles y Equipos",             1, True),
    ("1.2.02",  "Depreciación Acumulada",        1, True, "A"),
    ("2",       "PASIVO",                        2, False),
    ("2.1",     "PASIVO CORRIENTE",              2, False),
    ("2.1.01",  "Proveedores",                   2, True),
    ("2.1.02",  "IVA Débito Fiscal",             2, True),
    ("2.1.03",  "IVA por Pagar",                 2, True),
    ("3",       "PATRIMONIO NETO",               3, False),
    ("3.1",     "Capital",                       3, True),
    ("4",       "INGRESOS",                      4, False),
    ("4.1",     "Ventas",                        4, True),
    ("4.1.01",  "Ventas Gravadas",               4, True),
    ("4.1.02",  "Ventas Exentas",                4, True),
    ("5",       "COSTOS",                        5, False),
    ("5.1",     "Costo de Mercaderías Vendidas", 5, True),
    ("6",       "GASTOS",                        6, False),
    ("6.1",     "Gastos Administrativos",        6, True),
    ("6.2",     "Gastos de Ventas",              6, True),
]


# ---------------------------------------------------------------------------
# Consultas de lectura
# ---------------------------------------------------------------------------

def listar_cuentas(
    ruta: str | None = None,
    *,
    incluir_inactivas: bool = True,
    solo_detalle: bool = False,
    busqueda: str | None = None,
) -> list[dict]:
    """Devuelve el plan de cuentas completo, ordenado por código (jerarquía)."""
    condiciones = []
    parametros: list = []

    if not incluir_inactivas:
        condiciones.append("estado = 'ACTIVA'")
    if solo_detalle:
        condiciones.append("recibe_movimiento = 1")
    if busqueda:
        condiciones.append("(codigo LIKE ? OR nombre LIKE ?)")
        patron = f"%{busqueda.strip()}%"
        parametros += [patron, patron]

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    with conectar(ruta_base_datos(ruta)) as conexion:
        filas = conexion.execute(
            f"SELECT * FROM cuenta {where} ORDER BY orden, codigo", parametros
        ).fetchall()

    cuentas = [_enriquecer(conexion_fila_a_dict(f)) for f in filas]

    # Si hay filtro de búsqueda o solo detalle, se recalcula el nivel de
    # existencia de las subcuentas para no mostrar huérfanas.
    if busqueda or solo_detalle:
        codigos = {c["codigo"] for c in cuentas}
        for cuenta in cuentas:
            padre = validaciones.padre_de_codigo(cuenta["codigo"])
            cuenta["tiene_padre_en_resultado"] = padre in codigos
    return cuentas


def obtener_cuenta(codigo: str, ruta: str | None = None) -> dict | None:
    """Devuelve una cuenta por código, o None si no existe."""
    with conectar(ruta_base_datos(ruta)) as conexion:
        fila = conexion.execute(
            "SELECT * FROM cuenta WHERE codigo = ?", (str(codigo).strip(),)
        ).fetchone()
    return _enriquecer(dict(fila)) if fila else None


def _enriquecer(cuenta: dict) -> dict:
    """Agrega a la cuenta los datos derivados que usa la interfaz."""
    cuenta["recibe_movimiento"] = bool(cuenta["recibe_movimiento"])
    cuenta["naturaleza_nombre"] = validaciones.nombre_naturaleza(cuenta["naturaleza"])
    cuenta["clasificacion_nombre"] = validaciones.CLASIFICACIONES[
        cuenta["clasificacion"]
    ]["nombre"]
    cuenta["ruta"] = validaciones.jerarquia(cuenta["codigo"])
    cuenta["nivel"] = validaciones.nivel_de_codigo(cuenta["codigo"])
    return cuenta


def subcuentas(codigo: str, ruta: str | None = None) -> list[dict]:
    """Cuentas hijas directas de una cuenta."""
    with conectar(ruta_base_datos(ruta)) as conexion:
        filas = conexion.execute(
            "SELECT * FROM cuenta WHERE codigo LIKE ? AND codigo <> ? ORDER BY codigo",
            (f"{codigo}.%", codigo),
        ).fetchall()
    return [_enriquecer(dict(f)) for f in filas]


def existe_codigo(codigo: str, ruta: str | None = None) -> bool:
    return obtener_cuenta(codigo, ruta) is not None


# ---------------------------------------------------------------------------
# Alta y modificación
# ---------------------------------------------------------------------------

def crear_cuenta(
    *,
    codigo: str,
    nombre: str,
    clasificacion: int,
    recibe_movimiento: bool = True,
    naturaleza: str | None = None,
    estado: str = "ACTIVA",
    ruta: str | None = None,
) -> dict:
    """
    Da de alta una cuenta en el plan de cuentas.

    Valida el código jerárquico, la coherencia con la clasificación y la
    existencia de la cuenta padre (control 3).

    `naturaleza` es opcional: si no se informa, se deduce de la
    clasificación (control 6).
    """
    codigo = str(codigo or "").strip()
    nombre = str(nombre or "").strip()

    if not clasificacion or int(clasificacion) not in validaciones.CLASIFICACIONES:
        raise ErrorValidacion(
            "La clasificación debe ser una de: "
            + ", ".join(f"{k}-{v['nombre']}" for k, v in validaciones.CLASIFICACIONES.items()),
            regla="C3",
        )

    clasificacion = int(clasificacion)
    naturaleza = (naturaleza or validaciones.naturaleza_de_cuenta(clasificacion)).upper()
    if naturaleza not in validaciones.NATURALEZAS:
        raise ErrorValidacion("La naturaleza debe ser 'D' (deudora) o 'A' (acreedora).",
                              regla="C6")

    if estado not in validaciones.ESTADOS_CUENTA:
        raise ErrorValidacion("El estado debe ser ACTIVA o INACTIVA.")

    # -- Control 3: validación completa antes de tocar la base ------------
    with conectar(ruta_base_datos(ruta)) as conexion:
        errores = validaciones.validar_cuenta(
            codigo,
            nombre,
            clasificacion,
            recibe_movimiento=recibe_movimiento,
            existe_codigo=lambda c: conexion.execute(
                "SELECT 1 FROM cuenta WHERE codigo = ?", (c,)
            ).fetchone()
            is not None,
            obtener_cuenta_padre=lambda c: conexion.execute(
                "SELECT 1 FROM cuenta WHERE codigo = ?", (c,)
            ).fetchone(),
        )
        if errores:
            validaciones.lanzar_si_hay_problemas(
                [{"regla": "C3", "linea": None, "mensaje": e} for e in errores],
                "No se puede crear la cuenta",
            )

        try:
            conexion.execute(
                """INSERT INTO cuenta (codigo, nombre, clasificacion, nivel,
                                        recibe_movimiento, naturaleza, estado, orden)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    codigo,
                    nombre,
                    clasificacion,
                    validaciones.nivel_de_codigo(codigo),
                    int(recibe_movimiento),
                    naturaleza,
                    estado,
                    validaciones.nivel_de_codigo(codigo) * 100000
                    + int(codigo.replace(".", "") or 0),
                ),
            )
        except sqlite3.Error as exc:
            raise error_sql_a_mensaje(exc) from exc

    return obtener_cuenta(codigo, ruta)


def actualizar_cuenta(
    codigo: str,
    *,
    nombre: str | None = None,
    clasificacion: int | None = None,
    recibe_movimiento: bool | None = None,
    naturaleza: str | None = None,
    estado: str | None = None,
    ruta: str | None = None,
) -> dict:
    """
    Modifica una cuenta existente.

    No se puede cambiar el código: en contabilidad el código es la identidad
    de la cuenta y cambiarlo rompería los asientos históricos.
    """
    cuenta = obtener_cuenta(codigo, ruta)
    if cuenta is None:
        raise ErrorNoEncontrado(f"La cuenta '{codigo}' no existe.")

    nuevos = {
        "nombre": cuenta["nombre"] if nombre is None else str(nombre).strip(),
        "clasificacion": cuenta["clasificacion"] if clasificacion is None else int(clasificacion),
        "recibe_movimiento": cuenta["recibe_movimiento"]
        if recibe_movimiento is None
        else bool(recibe_movimiento),
        "naturaleza": cuenta["naturaleza"] if naturaleza is None else naturaleza.upper(),
        "estado": cuenta["estado"] if estado is None else estado,
    }

    if nuevos["clasificacion"] not in validaciones.CLASIFICACIONES:
        raise ErrorValidacion("La clasificación indicada no es válida.", regla="C3")

    # Control 3 y 6: si es cuenta de detalle no puede recibir movimientos
    # una cuenta agrupadora, y su naturaleza debe ser la de su clasificación.
    if not nuevos["recibe_movimiento"] and cuenta["codigo"] in _codigos_con_movimientos(ruta):
        raise ErrorOperacionInvalida(
            f"La cuenta '{codigo} {cuenta['nombre']}' ya tiene movimientos contabilizados: "
            "no puede pasar a ser una cuenta agrupadora."
        )

    # Control 6: la naturaleza se valida contra la clasificación en el ERP,
    # pero se permite ajustarla (caso de las cuentas contrapasivas como la
    # Depreciación Acumulada, que siendo activo tiene naturaleza acreedora).
    if nuevos["naturaleza"] not in validaciones.NATURALEZAS:
        raise ErrorValidacion("La naturaleza debe ser 'D' (deudora) o 'A' (acreedora).",
                              regla="C6")

    with conectar(ruta_base_datos(ruta)) as conexion:
        errores = validaciones.validar_cuenta(
            cuenta["codigo"],
            nuevos["nombre"],
            nuevos["clasificacion"],
            recibe_movimiento=nuevos["recibe_movimiento"],
            existe_codigo=lambda c: c == cuenta["codigo"]
            or conexion.execute("SELECT 1 FROM cuenta WHERE codigo = ?", (c,)).fetchone()
            is not None,
            obtener_cuenta_padre=lambda c: conexion.execute(
                "SELECT 1 FROM cuenta WHERE codigo = ?", (c,)
            ).fetchone(),
            codigo_original=cuenta["codigo"],
        )
        if errores:
            validaciones.lanzar_si_hay_problemas(
                [{"regla": "C3", "linea": None, "mensaje": e} for e in errores],
                "No se puede actualizar la cuenta",
            )

        conexion.execute(
            """UPDATE cuenta
                  SET nombre = ?, clasificacion = ?, recibe_movimiento = ?,
                      naturaleza = ?, estado = ?
                WHERE codigo = ?""",
            (
                nuevos["nombre"],
                nuevos["clasificacion"],
                int(nuevos["recibe_movimiento"]),
                nuevos["naturaleza"],
                nuevos["estado"],
                codigo,
            ),
        )

    return obtener_cuenta(codigo, ruta)


def _codigos_con_movimientos(ruta: str | None = None) -> set[str]:
    """Códigos de cuenta que ya aparecen en alguna partida del Libro Diario."""
    with conectar(ruta_base_datos(ruta)) as conexion:
        filas = conexion.execute(
            "SELECT DISTINCT cuenta FROM partida"
        ).fetchall()
    return {f["cuenta"] for f in filas}


# ---------------------------------------------------------------------------
# Baja y estado
# ---------------------------------------------------------------------------

def eliminar_cuenta(codigo: str, ruta: str | None = None) -> None:
    """
    Elimina una cuenta del plan de cuentas.

    Solo se permite si no tiene movimientos contabilizados ni cuentas hijas:
    de lo contrario habría información contable huérfana.
    """
    cuenta = obtener_cuenta(codigo, ruta)
    if cuenta is None:
        raise ErrorNoEncontrado(f"La cuenta '{codigo}' no existe.")

    if codigo in _codigos_con_movimientos(ruta):
        raise ErrorOperacionInvalida(
            f"La cuenta '{codigo} {cuenta['nombre']}' tiene movimientos en el Libro "
            "Diario: no se puede eliminar, solo pasar a INACTIVA."
        )

    hijas = subcuentas(codigo, ruta)
    if hijas:
        raise ErrorOperacionInvalida(
            f"La cuenta '{codigo} {cuenta['nombre']}' tiene {len(hijas)} subcuenta(s): "
            "primero se deben eliminar."
        )

    with conectar(ruta_base_datos(ruta)) as conexion:
        conexion.execute("DELETE FROM cuenta WHERE codigo = ?", (codigo,))


def cambiar_estado(codigo: str, estado: str, ruta: str | None = None) -> dict:
    """Activa o desactiva una cuenta (control 2: las inactivas no se usan)."""
    if estado not in validaciones.ESTADOS_CUENTA:
        raise ErrorValidacion("El estado debe ser ACTIVA o INACTIVA.")
    if obtener_cuenta(codigo, ruta) is None:
        raise ErrorNoEncontrado(f"La cuenta '{codigo}' no existe.")

    with conectar(ruta_base_datos(ruta)) as conexion:
        conexion.execute("UPDATE cuenta SET estado = ? WHERE codigo = ?", (estado, codigo))
    return obtener_cuenta(codigo, ruta)


# ---------------------------------------------------------------------------
# Carga del plan mínimo
# ---------------------------------------------------------------------------

def cargar_plan_minimo(ruta: str | None = None) -> int:
    """
    Carga el plan de cuentas mínimo del enunciado (sección 13).

    Devuelve la cantidad de cuentas creadas. No duplica códigos existentes.
    """
    creadas = 0
    with conectar(ruta_base_datos(ruta)) as conexion:
        for fila in PLAN_MINIMO:
            codigo, nombre, clasificacion, recibe_movimiento = fila[:4]
            # El quinto elemento es la naturaleza explícita (cuentas
            # contrapasivas); si no viene, se deduce de la clasificación.
            naturaleza = fila[4] if len(fila) > 4 else validaciones.naturaleza_de_cuenta(
                clasificacion
            )

            existe = conexion.execute(
                "SELECT 1 FROM cuenta WHERE codigo = ?", (codigo,)
            ).fetchone()
            if existe:
                continue
            try:
                conexion.execute(
                    """INSERT INTO cuenta (codigo, nombre, clasificacion, nivel,
                                            recibe_movimiento, naturaleza, estado, orden)
                       VALUES (?, ?, ?, ?, ?, ?, 'ACTIVA', ?)""",
                    (
                        codigo,
                        nombre,
                        clasificacion,
                        validaciones.nivel_de_codigo(codigo),
                        int(recibe_movimiento),
                        naturaleza,
                        validaciones.nivel_de_codigo(codigo) * 100000
                        + int(codigo.replace(".", "")),
                    ),
                )
                creadas += 1
            except sqlite3.Error:
                continue
    return creadas


def conexion_fila_a_dict(fila) -> dict:
    """Convierte una fila de sqlite3 en diccionario."""
    return dict(fila) if hasattr(fila, "keys") else dict(zip(fila.keys(), fila))