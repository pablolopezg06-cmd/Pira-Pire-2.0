"""
Módulo: libro_diario
====================

Etapa 1 del Mini ERP Contable — módulo **Libro Diario** (sección 14).

Es la **única fuente de carga** del sistema. Todo lo demás (Libro Mayor y
Balance) se calcula a partir de las partidas registradas acá, nunca se
carga a mano (controles 4 y 5 del enunciado).

Ciclo de vida de un asiento
---------------------------
    BORRADOR  ──confirmar──►  CONFIRMADO  ──anular──►  ANULADO
       │                                                   ▲
       └──────────────────── editar ─────────────────────────┘

    · BORRADOR   : se puede editar o borrar sin más. No influye en los
                   saldos de las cuentas ni en los reportes.
    · CONFIRMADO : contabilizado. Entra al Libro Mayor y al Balance. Ya no
                   se puede editar: si hay un error se anula y se vuelve a
                   registrar (como en un libro contable real).
    · ANULADO    : queda registrado por auditoría, pero no suma en ningún
                   reporte.

Control 1: un asiento no se confirma si total Debe != total Haber.
"""

from __future__ import annotations

import sqlite3

from . import validaciones
from .base_datos import conectar, error_sql_a_mensaje, ruta_base_datos
from .dinero import a_centavos
from .excepciones import (
    ErrorNoEncontrado,
    ErrorOperacionInvalida,
    ErrorValidacion,
)

#: Asientos ya contabilizados que no admiten edición de partidas.
ESTADOS_EDITABLES = {"BORRADOR"}


# ---------------------------------------------------------------------------
# Normalización de la entrada
# ---------------------------------------------------------------------------

def _normalizar_partidas(partidas: list[dict]) -> list[dict]:
    """
    Limpia la lista de partidas recibida del cuerpo de la petición:
    convierte los importes a centavos enteros y descarta filas vacías.
    """
    limpias: list[dict] = []
    for partida in partidas or []:
        debe = a_centavos(partida.get("debe") or 0)
        haber = a_centavos(partida.get("haber") or 0)
        cuenta = str(partida.get("cuenta") or "").strip()

        # Una fila totalmente vacía no es una partida: se descarta.
        if not cuenta and not detalle_presente(partida):
            continue
        if not cuenta and debe == 0 and haber == 0:
            continue

        limpias.append(
            {
                "cuenta": cuenta,
                "debe": debe,
                "haber": haber,
                "detalle": str(partida.get("detalle") or "").strip() or None,
            }
        )
    return limpias


def detalle_presente(partida: dict) -> bool:
    """True si la partida trae algún dato escrito (aunque no tenga importe)."""
    return bool(str(partida.get("detalle") or "").strip())


def _siguiente_numero(conexion) -> int:
    """Número correlativo del asiento (1, 2, 3...)."""
    fila = conexion.execute("SELECT COALESCE(MAX(numero), 0) AS n FROM asiento").fetchone()
    return int(fila["n"]) + 1


# ---------------------------------------------------------------------------
# Consulta y lectura
# ---------------------------------------------------------------------------

def listar_asientos(
    ruta: str | None = None,
    *,
    desde: str | None = None,
    hasta: str | None = None,
    estado: str | None = None,
    busqueda: str | None = None,
    limite: int | None = None,
) -> list[dict]:
    """
    Lista los asientos del Libro Diario con sus totales y una marca de
    cuadre (columna `cuadra`), que es el control 1 mostrado en pantalla.
    """
    condiciones = []
    parametros: list = []

    if desde:
        condiciones.append("fecha >= ?")
        parametros.append(desde)
    if hasta:
        condiciones.append("fecha <= ?")
        parametros.append(hasta)
    if estado and estado.upper() != "TODOS":
        condiciones.append("estado = ?")
        parametros.append(estado.upper())
    if busqueda:
        condiciones.append("(concepto LIKE ? OR observaciones LIKE ?)")
        patron = f"%{busqueda.strip()}%"
        parametros += [patron, patron]

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    limite_sql = f" LIMIT {int(limite)}" if limite else ""

    with conectar(ruta_base_datos(ruta)) as conexion:
        filas = conexion.execute(
            f"""SELECT a.*,
                       (a.total_debe = a.total_haber AND a.total_debe > 0) AS cuadra,
                       (SELECT COUNT(*) FROM partida p WHERE p.asiento_id = a.id) AS lineas
                  FROM asiento a
                  {where}
                 ORDER BY a.fecha, a.numero{limite_sql}""",
            parametros,
        ).fetchall()

    return [dict(f) for f in filas]


def obtener_asiento(asiento_id: int, ruta: str | None = None) -> dict:
    """Devuelve un asiento con todas sus partidas."""
    with conectar(ruta_base_datos(ruta)) as conexion:
        cabecera = conexion.execute(
            "SELECT * FROM asiento WHERE id = ?", (asiento_id,)
        ).fetchone()
        if cabecera is None:
            raise ErrorNoEncontrado(f"No existe el asiento N.º {asiento_id}.")

        partidas = conexion.execute(
            """SELECT p.*, c.nombre AS cuenta_nombre, c.naturaleza AS cuenta_naturaleza,
                      c.clasificacion AS cuenta_clasificacion
                 FROM partida p
                 JOIN cuenta c ON c.codigo = p.cuenta
                WHERE p.asiento_id = ?
                ORDER BY p.linea""",
            (asiento_id,),
        ).fetchall()

    asiento = dict(cabecera)
    asiento["partidas"] = [dict(p) for p in partidas]
    asiento["cuadra"] = asiento["total_debe"] == asiento["total_haber"] and asiento[
        "total_debe"
    ] > 0
    return asiento


# ---------------------------------------------------------------------------
# Alta y edición
# ---------------------------------------------------------------------------

def crear_asiento(
    *,
    fecha: str,
    concepto: str,
    partidas: list[dict],
    confirmar: bool = True,
    observaciones: str | None = None,
    ruta: str | None = None,
) -> dict:
    """
    Registra un asiento en el Libro Diario.

    Si `confirmar` es True (por defecto) el asiento se guarda directamente
    como CONFIRMADO, lo que implica pasar **antes** por el control 1. Si el
    asiento no cuadra, se guarda como BORRADOR y se devuelve el detalle de
    la falla para que el usuario lo corrija — nunca se contabiliza descuadrado.
    """
    fecha = str(fecha or "").strip()
    concepto = str(concepto or "").strip()

    if not fecha:
        raise ErrorValidacion("La fecha del asiento es obligatoria.")
    if len(fecha) != 10 or fecha[4] != "-" or fecha[7] != "-":
        raise ErrorValidacion("La fecha debe tener el formato AAAA-MM-DD.")
    if not concepto:
        raise ErrorValidacion("El concepto del asiento es obligatorio.")

    partidas = _normalizar_partidas(partidas)
    if not partidas:
        raise ErrorValidacion(
            "El asiento debe tener al menos dos partidas con importe.",
            regla="C1",
        )

    total_debe = sum(p["debe"] for p in partidas)
    total_haber = sum(p["haber"] for p in partidas)

    with conectar(ruta_base_datos(ruta)) as conexion:
        problemas = validaciones.validar_partidas(
            partidas,
            obtener_cuenta=lambda codigo: _cuenta_minima(conexion, codigo),
        )

        # Si hay un problema estructural la línea ni se puede guardar (la base
        # lo impide por sus restricciones), así que no se crea el asiento:
        # se informa el error indicando la línea exacta.
        graves = validaciones.bloqueantes(problemas)
        if graves:
            validaciones.lanzar_si_hay_problemas(
                graves,
                "El asiento no puede registrarse porque tiene líneas inválidas",
            )

        # Control 1: un asiento descuadrado jamás pasa a CONFIRMADO.
        estado = "BORRADOR"
        if confirmar and not problemas:
            estado = "CONFIRMADO"

        numero = _siguiente_numero(conexion)
        try:
            cursor = conexion.execute(
                """INSERT INTO asiento (numero, fecha, concepto, estado,
                                        total_debe, total_haber, observaciones)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (numero, fecha, concepto, estado, total_debe, total_haber,
                 str(observaciones or "").strip() or None),
            )
        except sqlite3.Error as exc:
            raise error_sql_a_mensaje(exc) from exc

        asiento_id = cursor.lastrowid
        _grabar_partidas(conexion, asiento_id, partidas)

        if estado == "CONFIRMADO":
            conexion.execute(
                "UPDATE asiento SET confirmado_en = datetime('now') WHERE id = ?",
                (asiento_id,),
            )

    asiento = obtener_asiento(asiento_id, ruta)
    if estado == "CONFIRMADO":
        return asiento

    # No se confirmó: se informa por qué, para que el usuario corrija.
    validaciones.lanzar_si_hay_problemas(
        problemas,
        f"El asiento N.º {numero} se guardó como BORRADOR y no pudo contabilizarse",
    )
    return asiento


def insertar_asiento_confirmado(
    conexion,
    *,
    fecha: str,
    concepto: str,
    partidas: list[dict],
    observaciones: str | None = None,
) -> dict:
    """
    Inserta un asiento ya validado como **CONFIRMADO** en una transacción abierta.

    Está pensado para la importación masiva: recibe la conexión para que todas
    las filas del CSV se guarden juntas. La validación de las partidas (C1 y
    C2) y de la fecha debe hacerla quien llama; acá solo se persiste.

    Devuelve ``{id, numero, total_debe, total_haber}``.
    """
    partidas = [
        {
            "cuenta": str(p.get("cuenta") or "").strip(),
            "detalle": str(p.get("detalle") or "").strip() or None,
            "debe": int(p.get("debe") or 0),
            "haber": int(p.get("haber") or 0),
        }
        for p in partidas
    ]
    total_debe = sum(p["debe"] for p in partidas)
    total_haber = sum(p["haber"] for p in partidas)
    numero = _siguiente_numero(conexion)

    try:
        cursor = conexion.execute(
            """INSERT INTO asiento (numero, fecha, concepto, estado,
                                    total_debe, total_haber, observaciones,
                                    confirmado_en)
               VALUES (?, ?, ?, 'CONFIRMADO', ?, ?, ?, datetime('now'))""",
            (
                numero,
                fecha,
                concepto,
                total_debe,
                total_haber,
                str(observaciones or "").strip() or None,
            ),
        )
    except sqlite3.Error as exc:
        raise error_sql_a_mensaje(exc) from exc

    asiento_id = cursor.lastrowid
    _grabar_partidas(conexion, asiento_id, partidas)
    return {
        "id": asiento_id,
        "numero": numero,
        "total_debe": total_debe,
        "total_haber": total_haber,
    }


def actualizar_asiento(
    asiento_id: int,
    *,
    fecha: str | None = None,
    concepto: str | None = None,
    partidas: list[dict] | None = None,
    observaciones: str | None = None,
    ruta: str | None = None,
) -> dict:
    """
    Edita un asiento que está en BORRADOR.

    Un asiento CONFIRMADO no se edita: se anula y se vuelve a registrar.
    """
    actual = obtener_asiento(asiento_id, ruta)
    if actual["estado"] not in ESTADOS_EDITABLES:
        raise ErrorOperacionInvalida(
            f"El asiento N.º {actual['numero']} está {actual['estado']}: "
            "no se puede editar. Si hay un error, primero debe anularlo y registrar uno nuevo."
        )

    fecha = str(fecha or actual["fecha"]).strip()
    concepto = str(concepto or actual["concepto"]).strip()
    partidas = _normalizar_partidas(partidas) if partidas is not None else [
        {"cuenta": p["cuenta"], "debe": p["debe"], "haber": p["haber"],
         "detalle": p.get("detalle")}
        for p in actual["partidas"]
    ]

    if not fecha or len(fecha) != 10:
        raise ErrorValidacion("La fecha del asiento es obligatoria (AAAA-MM-DD).")
    if not concepto:
        raise ErrorValidacion("El concepto del asiento es obligatorio.")

    total_debe = sum(p["debe"] for p in partidas)
    total_haber = sum(p["haber"] for p in partidas)

    with conectar(ruta_base_datos(ruta)) as conexion:
        # Se validan también las líneas antes de escribir: las restricciones de
        # la base rechazarían (y con ellas todas) una edición inválida.
        problemas = validaciones.validar_partidas(
            partidas,
            obtener_cuenta=lambda codigo: _cuenta_minima(conexion, codigo),
        )
        graves = validaciones.bloqueantes(problemas)
        if graves:
            validaciones.lanzar_si_hay_problemas(
                graves,
                f"El asiento N.º {actual['numero']} no puede guardarse",
            )

        conexion.execute(
            """UPDATE asiento
                  SET fecha = ?, concepto = ?, total_debe = ?, total_haber = ?,
                      observaciones = ?
                WHERE id = ?""",
            (fecha, concepto, total_debe, total_haber,
             str(observaciones or "").strip() or None, asiento_id),
        )
        conexion.execute("DELETE FROM partida WHERE asiento_id = ?", (asiento_id,))
        _grabar_partidas(conexion, asiento_id, partidas)

    return obtener_asiento(asiento_id, ruta)


def _grabar_partidas(conexion, asiento_id: int, partidas: list[dict]) -> None:
    """Inserta las partidas de un asiento, renumerando las líneas."""
    for linea, partida in enumerate(partidas, start=1):
        try:
            conexion.execute(
                """INSERT INTO partida (asiento_id, linea, cuenta, detalle, debe, haber)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (asiento_id, linea, partida["cuenta"], partida.get("detalle"),
                 partida["debe"], partida["haber"]),
            )
        except sqlite3.Error as exc:
            raise error_sql_a_mensaje(exc) from exc


def _cuenta_minima(conexion, codigo: str) -> dict | None:
    """Datos de la cuenta necesarios para validar una partida (control 2)."""
    fila = conexion.execute(
        "SELECT codigo, nombre, estado, recibe_movimiento FROM cuenta WHERE codigo = ?",
        (codigo,),
    ).fetchone()
    if fila is None:
        return None
    return {
        "codigo": fila["codigo"],
        "nombre": fila["nombre"],
        "estado": fila["estado"],
        "recibe_movimiento": bool(fila["recibe_movimiento"]),
    }


# ---------------------------------------------------------------------------
# Confirmación, anulación y borrado
# ---------------------------------------------------------------------------

def confirmar_asiento(asiento_id: int, ruta: str | None = None) -> dict:
    """
    Contabiliza un asiento: pasa de BORRADOR a CONFIRMADO.

    Acá es donde se exige el **Control 1**: si el asiento no cuadra, o usa
    cuentas inexistentes/inactivas (control 2), la operación se rechaza y el
    asiento sigue en BORRADOR.
    """
    actual = obtener_asiento(asiento_id, ruta)

    if actual["estado"] == "CONFIRMADO":
        raise ErrorOperacionInvalida(
            f"El asiento N.º {actual['numero']} ya está confirmado."
        )
    if actual["estado"] == "ANULADO":
        raise ErrorOperacionInvalida(
            f"El asiento N.º {actual['numero']} está anulado: no se puede confirmar."
        )

    partidas = [
        {"cuenta": p["cuenta"], "debe": p["debe"], "haber": p["haber"]}
        for p in actual["partidas"]
    ]

    with conectar(ruta_base_datos(ruta)) as conexion:
        problemas = validaciones.validar_partidas(
            partidas,
            obtener_cuenta=lambda codigo: _cuenta_minima(conexion, codigo),
        )
        validaciones.lanzar_si_hay_problemas(
            problemas,
            f"El asiento N.º {actual['numero']} no puede contabilizarse",
        )
        conexion.execute(
            """UPDATE asiento
                  SET estado = 'CONFIRMADO', confirmado_en = datetime('now'),
                      total_debe = ?, total_haber = ?
                WHERE id = ?""",
            (actual["total_debe"], actual["total_haber"], asiento_id),
        )

    return obtener_asiento(asiento_id, ruta)


def anular_asiento(asiento_id: int, motivo: str | None = None, ruta: str | None = None) -> dict:
    """
    Anula un asiento: deja de sumar en el Libro Mayor y en el Balance pero
    permanece en la base para conservar la trazabilidad.
    """
    actual = obtener_asiento(asiento_id, ruta)
    if actual["estado"] == "ANULADO":
        raise ErrorOperacionInvalida(
            f"El asiento N.º {actual['numero']} ya está anulado."
        )

    texto = (motivo or "").strip() or "Anulado por el usuario"
    with conectar(ruta_base_datos(ruta)) as conexion:
        conexion.execute(
            """UPDATE asiento
                  SET estado = 'ANULADO',
                      observaciones = TRIM(COALESCE(observaciones, '') || ' | ANULADO: ' || ?)
                WHERE id = ?""",
            (texto, asiento_id),
        )
    return obtener_asiento(asiento_id, ruta)


def borrar_asiento(asiento_id: int, ruta: str | None = None) -> None:
    """
    Elimina físicamente un asiento. Solo se permite si está en BORRADOR o
    ANULADO: un asiento contabilizado no se borra, se anula.
    """
    actual = obtener_asiento(asiento_id, ruta)
    if actual["estado"] == "CONFIRMADO":
        raise ErrorOperacionInvalida(
            f"El asiento N.º {actual['numero']} está contabilizado: no se puede borrar, "
            "se debe anular. Así el Libro Diario conserva la trazabilidad."
        )
    with conectar(ruta_base_datos(ruta)) as conexion:
        conexion.execute("DELETE FROM asiento WHERE id = ?", (asiento_id,))


# ---------------------------------------------------------------------------
# Verificaciones
# ---------------------------------------------------------------------------

def verificar_cuadre(ruta: str | None = None) -> dict:
    """
    Control 1 a nivel de todo el Libro Diario: comprueba que ningún asiento
    confirmado esté descuadrado y que el total general cuadre.
    """
    with conectar(ruta_base_datos(ruta)) as conexion:
        fila = conexion.execute(
            """SELECT
                  COALESCE(SUM(CASE WHEN estado = 'CONFIRMADO' THEN total_debe  END), 0) AS debe,
                  COALESCE(SUM(CASE WHEN estado = 'CONFIRMADO' THEN total_haber END), 0) AS haber,
                  COUNT(CASE WHEN estado = 'CONFIRMADO' THEN 1 END) AS confirmados,
                  COUNT(CASE WHEN estado = 'BORRADOR'   THEN 1 END) AS borradores,
                  COUNT(CASE WHEN estado = 'ANULADO'    THEN 1 END) AS anulados,
                  COUNT(CASE WHEN estado = 'CONFIRMADO'
                                 AND (total_debe <> total_haber OR total_debe = 0)
                             THEN 1 END) AS descuadrados
                FROM asiento"""
        ).fetchone()

    return {
        "total_debe": int(fila["debe"]),
        "total_haber": int(fila["haber"]),
        "diferencia": int(fila["debe"]) - int(fila["haber"]),
        "cuadra": int(fila["debe"]) == int(fila["haber"]) and int(fila["descuadrados"]) == 0,
        "asientos_confirmados": int(fila["confirmados"]),
        "asientos_borrador": int(fila["borradores"]),
        "asientos_anulados": int(fila["anulados"]),
        "asientos_descuadrados": int(fila["descuadrados"]),
        "regla": "C1",
    }