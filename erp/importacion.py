"""
Módulo: importacion
===================

Importación de datos propios desde archivos **CSV**.

Se eligió CSV porque es el formato que exporta cualquier planilla de cálculo
y no requiere dependencias externas. El módulo tiene dos importadores:

* **Plan de cuentas**: código, nombre, clasificación, naturaleza, tipo y estado.
* **Libro Diario**: líneas agrupadas por número de asiento.

La importación es **todo o nada**: primero se valida el archivo completo y
recién después se escribe, dentro de una única transacción. Si una fila tiene
un error, no se guarda ninguna cuenta ni ningún asiento y se informa el número
de fila del archivo para corregirlo.

Los importes se interpretan con el formato paraguayo (``1.500.000,50``) usando
``erp.dinero.a_centavos``. El separador del CSV puede ser coma, punto y coma,
tabulador o barra vertical; se detecta automáticamente.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import datetime

from . import validaciones
from .base_datos import conectar
from .dinero import ErrorMonto, a_centavos
from .excepciones import ErrorValidacion
from .libro_diario import insertar_asiento_confirmado

#: Columnas que entiende cada tipo de archivo. El orden es el de la plantilla.
COLUMNAS_CUENTAS = [
    "codigo", "nombre", "clasificacion", "naturaleza", "tipo", "estado",
]
COLUMNAS_ASIENTOS = [
    "asiento", "fecha", "concepto", "cuenta", "detalle", "debe", "haber",
]

#: Nombres alternativos de las columnas. Todos se normalizan sin acentos,
#: espacios ni signos antes de comparar.
_ALIAS_CUENTAS = {
    "codigo": (
        "codigo", "codigocuenta", "cod", "code", "nrocuenta",
        "numerocuenta", "cuentacodigo", "cta",
    ),
    "nombre": (
        "nombre", "nombrecuenta", "denominacion", "descripcion", "cuenta",
        "descripcioncuenta",
    ),
    "clasificacion": (
        "clasificacion", "rubro", "grupo", "clase", "clasificacioncuenta",
    ),
    "naturaleza": (
        "naturaleza", "naturalezacuenta", "tiponaturaleza", "da", "natura",
    ),
    "tipo": (
        "tipo", "tipocuenta", "recibemovimiento", "movimiento", "imputable",
    ),
    "estado": (
        "estado", "situacion", "estadocuenta", "activa",
    ),
}

_ALIAS_ASIENTOS = {
    "asiento": (
        "asiento", "numeroasiento", "nroasiento", "nro", "numero",
        "comprobante", "nrocomprobante", "idasiento", "referencia",
    ),
    "fecha": ("fecha", "date", "fechacontable", "fechaasiento"),
    "concepto": (
        "concepto", "descripcion", "detalleasiento", "leyenda", "glosa",
        "conceptoasiento",
    ),
    "cuenta": ("cuenta", "codigocuenta", "codigo", "cuentacodigo", "cod", "cta"),
    "detalle": (
        "detalle", "detallelinea", "descripcionlinea", "descripciondetalle",
        "auxiliar",
    ),
    "debe": ("debe", "debito", "cargo", "importedebe", "debegs"),
    "haber": ("haber", "credito", "abono", "importehaber", "habergs"),
}

_ALIAS_POR_TIPO = {"cuentas": _ALIAS_CUENTAS, "asientos": _ALIAS_ASIENTOS}

#: Delimitadores que se prueban, en orden de preferencia.
_DELIMITADORES = (",", ";", "\t", "|")


# ---------------------------------------------------------------------------
# Lectura y normalización del CSV
# ---------------------------------------------------------------------------

def _sin_acentos(texto: str) -> str:
    """'Clasificación' -> 'clasificacion'."""
    normalizado = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _norm(texto: str) -> str:
    """Normaliza un encabezado: minúsculas y solo letras y números."""
    return re.sub(r"[^a-z0-9]+", "", _sin_acentos(texto).lower())


_ALIAS_NORMALIZADOS = {
    tipo: {
        campo: {_norm(alias) for alias in aliases}
        for campo, aliases in campos.items()
    }
    for tipo, campos in _ALIAS_POR_TIPO.items()
}


def _mapear_cabecera(celdas: list[str], tipo: str) -> dict[str, int]:
    """Devuelve ``{campo: índice}`` para una fila de encabezados."""
    aliases = _ALIAS_NORMALIZADOS[tipo]
    mapa: dict[str, int] = {}
    for indice, celda in enumerate(celdas):
        clave = _norm(celda)
        for campo, nombres in aliases.items():
            if campo not in mapa and clave in nombres:
                mapa[campo] = indice
                break
    return mapa


def _leer_tabla(texto: str, tipo: str) -> list[tuple[int, dict]]:
    """
    Lee el CSV y devuelve ``[(línea_física, {campo: valor}), ...]``.

    Detecta el separador y acepta una o dos líneas de título antes de la fila
    de encabezados. Las filas completamente vacías y las que empiezan con
    ``#`` se ignoran.
    """
    if not texto or not texto.strip():
        raise ErrorValidacion("El archivo CSV está vacío.")

    texto = texto.lstrip("\ufeff")
    columnas = COLUMNAS_CUENTAS if tipo == "cuentas" else COLUMNAS_ASIENTOS
    requeridas = ["codigo", "nombre"] if tipo == "cuentas" else [
        "fecha", "concepto", "cuenta", "debe", "haber",
    ]

    mejor: dict | None = None

    for delimitador in _DELIMITADORES:
        try:
            lector = csv.reader(io.StringIO(texto), delimiter=delimitador)
            filas_crudas: list[tuple[int, list[str]]] = []
            for celdas in lector:
                if not any(str(c).strip() for c in celdas):
                    continue
                if celdas and str(celdas[0]).lstrip().startswith("#"):
                    continue
                filas_crudas.append((lector.line_num, celdas))
        except csv.Error:
            continue

        if not filas_crudas:
            continue

        # La cabecera puede venir después de un título. Se prueban las
        # primeras filas y se elige la que reconoce más columnas.
        for posicion in range(min(10, len(filas_crudas))):
            _, celdas = filas_crudas[posicion]
            mapa = _mapear_cabecera(celdas, tipo)
            faltantes = [campo for campo in requeridas if campo not in mapa]
            if faltantes:
                continue
            puntaje = len(mapa) - len(faltantes) * 10
            if mejor is None or puntaje > mejor["puntaje"]:
                mejor = {
                    "puntaje": puntaje,
                    "delimitador": delimitador,
                    "posicion": posicion,
                    "mapa": mapa,
                    "filas": filas_crudas,
                    "ancho": len(celdas),
                }
            break

    if mejor is None:
        raise ErrorValidacion(
            "No se encontró una fila de encabezados reconocible. "
            f"Para {tipo} se esperan las columnas: {', '.join(columnas)}. "
            "Se aceptan coma, punto y coma, tabulador o barra vertical como separador."
        )

    delimitador = mejor["delimitador"]
    mapa = mejor["mapa"]
    filas_crudas = mejor["filas"][mejor["posicion"] + 1 :]
    ancho = mejor["ancho"]

    if not filas_crudas:
        raise ErrorValidacion(
            "El CSV tiene encabezados pero no tiene filas de datos."
        )

    datos: list[tuple[int, dict]] = []
    for linea, celdas in filas_crudas:
        # Un CSV con coma decimal sin comillas puede generar columnas de más.
        # En vez de recortar el importe en silencio, se avisa cómo corregirlo.
        if len(celdas) > ancho and any(str(c).strip() for c in celdas[ancho:]):
            raise ErrorValidacion(
                f"La fila {linea} tiene más columnas que el encabezado. "
                f"Revisá que los textos con {delimitador!r} estén entre comillas y, "
                "si usás coma decimal, guardá el CSV con punto y coma (;) como separador."
            )

        fila = {
            campo: (str(celdas[indice]).strip() if indice < len(celdas) else "")
            for campo, indice in mapa.items()
        }
        datos.append((linea, fila))

    return datos


# ---------------------------------------------------------------------------
# Conversión de valores
# ---------------------------------------------------------------------------

def _error(fila: int | None, mensaje: str, regla: str | None = None) -> dict:
    return {"fila": fila, "mensaje": str(mensaje), "regla": regla}


def _clasificacion(valor: str, codigo: str) -> int | None:
    """Convierte 1..6 o el nombre de la clasificación; si está vacío, usa la raíz."""
    texto = _norm(valor)
    if not texto:
        if validaciones.FORMATO_CODIGO.fullmatch(codigo or ""):
            return validaciones.raiz_de_codigo(codigo)
        return None

    if texto in {"1", "2", "3", "4", "5", "6"}:
        return int(texto)

    mapa = {
        "activo": 1, "activos": 1,
        "pasivo": 2, "pasivos": 2,
        "patrimonio": 3, "patrimonioneto": 3,
        "ingreso": 4, "ingresos": 4,
        "costo": 5, "costos": 5,
        "gasto": 6, "gastos": 6,
    }
    if texto in mapa:
        return mapa[texto]

    # "Activo corriente", "Gastos administrativos", etc. también son válidos.
    for nombre, numero in (
        ("activo", 1), ("pasivo", 2), ("patrimonio", 3),
        ("ingreso", 4), ("costo", 5), ("gasto", 6),
    ):
        if texto.startswith(nombre):
            return numero
    return None


def _naturaleza(valor: str) -> str | None:
    texto = _norm(valor)
    if not texto:
        return None
    mapa = {
        "d": "D", "deudora": "D", "deudor": "D", "debe": "D", "debito": "D",
        "a": "A", "acreedora": "A", "acreedor": "A", "haber": "A", "credito": "A",
    }
    return mapa.get(texto)


def _tipo_cuenta(valor: str) -> bool | None:
    """True = detalle (recibe movimientos), False = agrupadora."""
    texto = _norm(valor)
    if not texto:
        return None
    if texto in {"detalle", "1", "si", "true", "movimiento", "imputable"}:
        return True
    if texto in {"agrupadora", "agrupador", "colectora", "0", "no", "false"}:
        return False
    return None


def _estado_cuenta(valor: str) -> str | None:
    texto = _norm(valor)
    if not texto:
        return None
    if texto in {"activa", "activo", "1", "si", "true"}:
        return "ACTIVA"
    if texto in {"inactiva", "inactivo", "0", "no", "false"}:
        return "INACTIVA"
    return None


def _clave_orden_codigo(codigo: str) -> tuple:
    """Ordena '1.2' antes que '1.10' y antes que sus subcuentas."""
    # Un código inválido (p. ej. una fecha que puso Excel) va al final en vez
    # de romper el orden: el error ya se informa en la validación.
    try:
        return (0, *(int(parte) for parte in str(codigo).split(".")))
    except ValueError:
        return (1, str(codigo))


def _fecha_iso(valor: str) -> tuple[str | None, str | None]:
    """Devuelve ``(fecha_iso, error)``. Acepta ISO y DD/MM/AAAA."""
    texto = str(valor or "").strip()
    if not texto:
        return None, None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(texto, formato).date().isoformat(), None
        except ValueError:
            continue
    return None, f"La fecha '{texto}' no es válida. Usá AAAA-MM-DD o DD/MM/AAAA."


# ---------------------------------------------------------------------------
# Plan de cuentas
# ---------------------------------------------------------------------------

def importar_cuentas(
    texto: str,
    ruta: str | None = None,
    *,
    confirmar: bool = False,
) -> dict:
    """
    Valida (y opcionalmente guarda) un plan de cuentas desde CSV.

    Con ``confirmar=False`` sólo devuelve la vista previa. Con
    ``confirmar=True`` escribe en una sola transacción si no hay errores.
    Las cuentas cuyo código ya existe se omiten; no se actualizan.
    """
    filas = _leer_tabla(texto, "cuentas")

    with conectar(ruta) as conexion:
        existentes = {
            fila["codigo"]: dict(fila)
            for fila in conexion.execute(
                """SELECT codigo, nombre, clasificacion, nivel,
                          recibe_movimiento, naturaleza, estado
                     FROM cuenta"""
            ).fetchall()
        }

        errores: list[dict] = []
        candidatas: list[dict] = []
        vistos: dict[str, int] = {}

        for linea, fila in filas:
            codigo = str(fila.get("codigo") or "").strip()
            if not codigo:
                errores.append(_error(linea, "Falta el código de la cuenta.", "C3"))
                continue
            if codigo in vistos:
                errores.append(_error(
                    linea,
                    f"El código '{codigo}' está repetido en el CSV "
                    f"(también aparece en la fila {vistos[codigo]}).",
                    "C3",
                ))
                continue
            vistos[codigo] = linea

            nombre = str(fila.get("nombre") or "").strip()
            clasificacion = _clasificacion(fila.get("clasificacion"), codigo)
            naturaleza = _naturaleza(fila.get("naturaleza"))
            tipo = _tipo_cuenta(fila.get("tipo"))
            estado = _estado_cuenta(fila.get("estado"))

            if clasificacion is None:
                if _norm(fila.get("clasificacion")):
                    errores.append(_error(
                        linea,
                        f"La clasificación '{fila.get('clasificacion')}' no es válida. "
                        "Usá un número del 1 al 6 o el nombre de la clasificación.",
                        "C3",
                    ))
                elif validaciones.FORMATO_CODIGO.fullmatch(codigo):
                    errores.append(_error(
                        linea,
                        f"No se pudo deducir la clasificación del código '{codigo}'.",
                        "C3",
                    ))
                else:
                    errores.append(_error(
                        linea,
                        f"El código '{codigo}' no tiene un formato válido "
                        "(se espera 1, 1.1 o 1.1.01)."
                        + (" Excel lo convirtió en fecha: poné la columna del "
                           "código con formato Texto y volvé a escribirlo."
                           if re.fullmatch(r"\d{4}-\d{2}-\d{2}", codigo) else ""),
                        "C3",
                    ))

            if _norm(fila.get("naturaleza")) and naturaleza is None:
                errores.append(_error(
                    linea,
                    f"La naturaleza '{fila.get('naturaleza')}' no es válida. "
                    "Usá D (deudora) o A (acreedora).",
                    "C6",
                ))
            if _norm(fila.get("tipo")) and tipo is None:
                errores.append(_error(
                    linea,
                    f"El tipo de cuenta '{fila.get('tipo')}' no es válido. "
                    "Usá detalle o agrupadora.",
                    "C3",
                ))
            if _norm(fila.get("estado")) and estado is None:
                errores.append(_error(
                    linea,
                    f"El estado '{fila.get('estado')}' no es válido. "
                    "Usá ACTIVA o INACTIVA.",
                ))

            # Si el tipo viene vacío, se completa después con la jerarquía.
            candidatas.append({
                "fila": linea,
                "codigo": codigo,
                "nombre": nombre,
                "clasificacion": clasificacion,
                "naturaleza": naturaleza,
                "recibe_movimiento": tipo,
                "estado": estado or "ACTIVA",
            })

        todos_los_codigos = set(existentes) | set(vistos)

        # Completar naturaleza y tipo cuando no se informaron.
        for cuenta in candidatas:
            if cuenta["naturaleza"] is None and cuenta["clasificacion"] is not None:
                cuenta["naturaleza"] = validaciones.naturaleza_de_cuenta(
                    cuenta["clasificacion"]
                )

            if cuenta["recibe_movimiento"] is None:
                codigo = cuenta["codigo"]
                if validaciones.FORMATO_CODIGO.fullmatch(codigo):
                    nivel = validaciones.nivel_de_codigo(codigo)
                    if nivel == 1:
                        # Las cuentas raíz siempre son agrupadoras.
                        cuenta["recibe_movimiento"] = False
                    else:
                        tiene_hijas = any(
                            otro != codigo and otro.startswith(codigo + ".")
                            for otro in todos_los_codigos
                        )
                        cuenta["recibe_movimiento"] = not tiene_hijas
                else:
                    cuenta["recibe_movimiento"] = True

        # Control 3: se valida cada cuenta nueva. Las que ya existen se omiten.
        for cuenta in candidatas:
            if cuenta["codigo"] in existentes:
                cuenta["omitida"] = True
                continue
            cuenta["omitida"] = False

            if cuenta["clasificacion"] is None:
                continue

            problemas = validaciones.validar_cuenta(
                cuenta["codigo"],
                cuenta["nombre"],
                cuenta["clasificacion"],
                recibe_movimiento=bool(cuenta["recibe_movimiento"]),
                existe_codigo=lambda codigo: (
                    codigo in existentes or codigo in vistos
                ),
                obtener_cuenta_padre=lambda codigo: (
                    existentes.get(codigo)
                    or ({"codigo": codigo} if codigo in vistos else None)
                ),
                codigo_original=cuenta["codigo"],
            )
            for problema in problemas:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cuenta["codigo"]):
                    problema += (" Excel lo convirtió en fecha: poné la columna del "
                                 "código con formato Texto y volvé a escribirlo.")
                errores.append(_error(cuenta["fila"], problema, "C3"))

        nuevas = [cuenta for cuenta in candidatas if not cuenta["omitida"]]
        valido = not errores
        confirmado = bool(confirmar and valido)
        creadas = 0

        if confirmado:
            for cuenta in sorted(nuevas, key=lambda c: _clave_orden_codigo(c["codigo"])):
                try:
                    conexion.execute(
                        """INSERT INTO cuenta (codigo, nombre, clasificacion, nivel,
                                                recibe_movimiento, naturaleza, estado, orden)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            cuenta["codigo"],
                            cuenta["nombre"],
                            cuenta["clasificacion"],
                            validaciones.nivel_de_codigo(cuenta["codigo"]),
                            int(bool(cuenta["recibe_movimiento"])),
                            cuenta["naturaleza"],
                            cuenta["estado"],
                            validaciones.nivel_de_codigo(cuenta["codigo"]) * 100000
                            + int(cuenta["codigo"].replace(".", "") or 0),
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - se traduce al usuario
                    raise ErrorValidacion(
                        f"No se pudo guardar la cuenta '{cuenta['codigo']}': {exc}",
                        regla="C3",
                    ) from exc
                creadas += 1

    muestra = [
        {
            "fila": cuenta["fila"],
            "codigo": cuenta["codigo"],
            "nombre": cuenta["nombre"],
            "clasificacion": cuenta["clasificacion"],
            "naturaleza": cuenta["naturaleza"],
            "recibe_movimiento": bool(cuenta["recibe_movimiento"]),
            "estado": cuenta["estado"],
        }
        for cuenta in sorted(nuevas, key=lambda c: _clave_orden_codigo(c["codigo"]))[:12]
    ]

    return {
        "tipo": "cuentas",
        "valido": valido,
        "confirmado": confirmado,
        "filas": len(filas),
        "a_crear": len(nuevas),
        "omitidas": len(candidatas) - len(nuevas),
        "creadas": creadas,
        "errores": errores,
        "muestra": muestra,
    }


# ---------------------------------------------------------------------------
# Libro Diario
# ---------------------------------------------------------------------------

def _cuenta_minima(conexion, codigo: str) -> dict | None:
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


def _agrupar_asientos(
    filas: list[tuple[int, dict]],
    errores: list[dict],
) -> list[dict]:
    """Agrupa las filas por número de asiento, o por fecha + concepto."""
    usa_asiento = any(str(fila.get("asiento") or "").strip() for _, fila in filas)
    grupos: list[dict] = []
    por_id: dict[str, dict] = {}
    actual: dict | None = None

    for linea, fila in filas:
        if usa_asiento:
            etiqueta = str(fila.get("asiento") or "").strip()
            if etiqueta:
                grupo = por_id.get(etiqueta)
                if grupo is None:
                    grupo = {
                        "etiqueta": etiqueta,
                        "inicio": linea,
                        "lineas": [],
                        "fecha_ref": "",
                        "concepto_ref": "",
                    }
                    por_id[etiqueta] = grupo
                    grupos.append(grupo)
                grupo["lineas"].append((linea, fila))
                actual = grupo
            elif actual is None:
                errores.append(_error(
                    linea,
                    "Falta el número de asiento en esta fila y no hay un asiento "
                    "anterior al cual continuar.",
                    "C1",
                ))
            else:
                actual["lineas"].append((linea, fila))
            continue

        fecha = str(fila.get("fecha") or "").strip()
        concepto = str(fila.get("concepto") or "").strip()
        if actual is not None:
            fecha = fecha or actual["fecha_ref"]
            concepto = concepto or actual["concepto_ref"]

        if (
            actual is None
            or (fecha, concepto) != (actual["fecha_ref"], actual["concepto_ref"])
        ):
            actual = {
                "etiqueta": str(len(grupos) + 1),
                "inicio": linea,
                "lineas": [],
                "fecha_ref": fecha,
                "concepto_ref": concepto,
            }
            grupos.append(actual)

        actual["lineas"].append((linea, fila))

    return grupos


#: Plantilla del plan de cuentas de la cátedra (para reconocer sus nombres).
_PLANTILLA_CUENTAS = (
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "web" / "plantillas" / "cuentas.csv"
)


def _codigos_por_nombre(ruta: str | None) -> dict[str, str]:
    """
    ``{nombre normalizado: código}`` de las cuentas de detalle del plan. Se
    suman los nombres de la plantilla de la cátedra para los códigos que la
    empresa tiene con otro nombre (por ejemplo, si renombró la cuenta).
    """
    with conectar(ruta) as conexion:
        filas = conexion.execute(
            "SELECT codigo, nombre FROM cuenta WHERE recibe_movimiento = 1"
        ).fetchall()
    codigos = {f["codigo"] for f in filas}
    resultado: dict[str, str] = {}
    try:
        for linea in _PLANTILLA_CUENTAS.read_text(encoding="utf-8").splitlines()[1:]:
            partes = linea.split(";")
            if len(partes) > 1 and partes[0] in codigos:
                resultado[_norm(partes[1])] = partes[0]
    except OSError:
        pass
    resultado.update({_norm(f["nombre"]): f["codigo"] for f in filas})
    return resultado


def ajustar_codigo(codigo: str, detalle: set[str]) -> str:
    """
    Corrige un código que cuelga de una cuenta de detalle existente.

    Una cuenta de detalle no puede tener subcuentas, así que «6.3.01» cuando
    «6.3» es de detalle solo puede referirse a «6.3» (el modelo a veces
    agrega «.01» por su cuenta). Si no hay tal cuenta, devuelve el código
    sin cambios y la validación informa el error.
    """
    codigo = str(codigo or "").strip()
    if codigo in detalle:
        return codigo
    partes = codigo.split(".")
    for largo in range(len(partes) - 1, 0, -1):
        padre = ".".join(partes[:largo])
        if padre in detalle:
            return padre
    return codigo


def codigos_de_detalle(ruta: str | None) -> set[str]:
    """Códigos de las cuentas de detalle activas del plan."""
    with conectar(ruta) as conexion:
        return {
            f["codigo"] for f in conexion.execute(
                "SELECT codigo FROM cuenta WHERE recibe_movimiento = 1 AND estado = 'ACTIVA'"
            ).fetchall()
        }


def _codigo_de_nombre(nombre: str, codigos: dict[str, str]) -> str:
    """
    Busca la cuenta por nombre: primero exacto (sin acentos ni signos) y si
    no, la única cuenta cuyo nombre contiene al buscado o viceversa. Si no la
    encuentra devuelve el nombre tal cual, y la validación avisa que no existe.
    """
    texto = str(nombre or "").strip()
    if validaciones.FORMATO_CODIGO.fullmatch(texto):
        return texto
    clave = _norm(texto)
    if clave in codigos:
        return codigos[clave]
    parecidas = {c for n, c in codigos.items() if clave and (clave in n or n in clave)}
    return parecidas.pop() if len(parecidas) == 1 else texto


def _desde_formato_clase(texto: str, ruta: str | None) -> str:
    """
    Convierte un Libro Diario con el formato de las planillas de clase
    (NRO · FECHA · CUENTAS CONTABLES · DEBE · HABER, el concepto en una fila
    sin importes debajo de cada asiento y «SUMAS IGUALES» al final) al CSV del
    importador. Si el texto no tiene ese formato, lo devuelve sin cambios.
    """
    texto = (texto or "").lstrip("\ufeff")
    try:
        delimitador = csv.Sniffer().sniff(texto[:2000], delimiters=";,\t|").delimiter
    except csv.Error:
        delimitador = ";"
    filas = list(csv.reader(io.StringIO(texto), delimiter=delimitador))

    cabecera = None
    for indice, celdas in enumerate(filas[:15]):
        nombres = [_norm(c) for c in celdas]
        if "nro" in nombres and "cuentascontables" in nombres and "debe" in nombres:
            cabecera = (indice, {n: i for i, n in enumerate(nombres) if n})
            break
    if cabecera is None:
        return texto

    inicio, columnas = cabecera
    c_nro, c_fecha = columnas.get("nro"), columnas.get("fecha")
    c_cuenta, c_debe, c_haber = columnas["cuentascontables"], columnas["debe"], columnas.get("haber")
    c_codigo = columnas.get("codigo")
    codigos = _codigos_por_nombre(ruta)
    detalle = codigos_de_detalle(ruta)

    def celda(celdas: list[str], i: int | None) -> str:
        return celdas[i].strip() if i is not None and i < len(celdas) else ""

    asientos: list[dict] = []
    actual: dict | None = None
    for celdas in filas[inicio + 1:]:
        nro, cuenta = celda(celdas, c_nro), celda(celdas, c_cuenta)
        debe, haber = celda(celdas, c_debe), celda(celdas, c_haber)
        if _norm(cuenta).startswith("sumasiguales"):
            break
        if nro:
            actual = {"nro": nro, "fecha": celda(celdas, c_fecha), "concepto": "", "lineas": []}
            asientos.append(actual)
        if actual is None or not cuenta:
            continue
        if not debe and not haber:
            # Fila sin importes debajo del asiento: es el concepto.
            actual["concepto"] = actual["concepto"] or cuenta
            continue
        # El código, si la planilla lo trae, es exacto; si no, se busca por nombre.
        codigo = celda(celdas, c_codigo)
        if codigo not in detalle:
            # Código ausente o que no existe: se prueba por nombre y, si no,
            # se corrige «6.3.01» -> «6.3» cuando 6.3 es de detalle.
            por_nombre = _codigo_de_nombre(cuenta, codigos)
            codigo = por_nombre if por_nombre in detalle else ajustar_codigo(codigo or por_nombre, detalle)
        actual["lineas"].append((codigo, cuenta, debe, haber))

    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";", lineterminator="\n")
    escritor.writerow(COLUMNAS_ASIENTOS)
    for a in asientos:
        concepto = a["concepto"] or f"Asiento {a['nro']}"
        for codigo, _nombre, debe, haber in a["lineas"]:
            escritor.writerow([a["nro"], a["fecha"], concepto, codigo, "", debe, haber])
    return salida.getvalue()


def importar_asientos(
    texto: str,
    ruta: str | None = None,
    *,
    confirmar: bool = False,
) -> dict:
    """
    Valida (y opcionalmente guarda) asientos desde CSV.

    Cada grupo debe tener al menos dos líneas, cuentas existentes/activas/de
    detalle y total Debe = total Haber. Se guardan como CONFIRMADO en una sola
    transacción. Con ``confirmar=False`` sólo se devuelve la vista previa.

    También acepta el Libro Diario con el formato de las planillas de clase
    (ver ``_desde_formato_clase``), con las cuentas por nombre.
    """
    texto = _desde_formato_clase(texto, ruta)
    filas = _leer_tabla(texto, "asientos")
    errores: list[dict] = []

    with conectar(ruta) as conexion:
        grupos = _agrupar_asientos(filas, errores)

        for grupo in grupos:
            lineas = grupo["lineas"]
            fecha_ref = next(
                (str(f.get("fecha") or "").strip() for _, f in lineas
                 if str(f.get("fecha") or "").strip()),
                "",
            )
            concepto_ref = next(
                (str(f.get("concepto") or "").strip() for _, f in lineas
                 if str(f.get("concepto") or "").strip()),
                "",
            )

            if not fecha_ref:
                errores.append(_error(
                    grupo["inicio"],
                    f"El asiento {grupo['etiqueta']} no tiene fecha.",
                    "C1",
                ))
            if not concepto_ref:
                errores.append(_error(
                    grupo["inicio"],
                    f"El asiento {grupo['etiqueta']} no tiene concepto.",
                    "C1",
                ))

            fecha_iso, error_fecha = _fecha_iso(fecha_ref)
            if error_fecha:
                errores.append(_error(grupo["inicio"], error_fecha, "C1"))

            # Todas las líneas del mismo asiento deben compartir fecha y concepto.
            for linea, fila in lineas:
                fecha_linea = str(fila.get("fecha") or "").strip()
                concepto_linea = str(fila.get("concepto") or "").strip()
                if fecha_linea:
                    fecha_linea_iso, error_linea = _fecha_iso(fecha_linea)
                    if error_linea:
                        # La fecha de referencia ya se validó arriba; no repetir
                        # el mismo mensaje por cada línea del asiento.
                        if fecha_linea != fecha_ref:
                            errores.append(_error(linea, error_linea, "C1"))
                    elif fecha_iso and fecha_linea_iso != fecha_iso:
                        errores.append(_error(
                            linea,
                            f"El asiento {grupo['etiqueta']} tiene fechas distintas "
                            f"({fecha_iso} y {fecha_linea_iso}).",
                            "C1",
                        ))
                if concepto_linea and concepto_ref and concepto_linea != concepto_ref:
                    errores.append(_error(
                        linea,
                        f"El asiento {grupo['etiqueta']} tiene conceptos distintos.",
                        "C1",
                    ))

            partidas: list[dict] = []
            for linea, fila in lineas:
                try:
                    debe = a_centavos(fila.get("debe"))
                    haber = a_centavos(fila.get("haber"))
                except ErrorMonto as exc:
                    errores.append(_error(linea, str(exc), "C1"))
                    continue
                partidas.append({
                    "cuenta": str(fila.get("cuenta") or "").strip(),
                    "detalle": str(fila.get("detalle") or "").strip() or None,
                    "debe": debe,
                    "haber": haber,
                    "_linea": linea,
                })

            if len(partidas) < 2:
                errores.append(_error(
                    grupo["inicio"],
                    f"El asiento {grupo['etiqueta']} debe tener al menos dos líneas.",
                    "C1",
                ))

            problemas = validaciones.validar_partidas(
                [
                    {"cuenta": p["cuenta"], "debe": p["debe"], "haber": p["haber"]}
                    for p in partidas
                ],
                obtener_cuenta=lambda codigo: _cuenta_minima(conexion, codigo),
            )
            for problema in problemas:
                indice = problema.get("linea")
                if indice and 1 <= int(indice) <= len(partidas):
                    fila_error = partidas[int(indice) - 1]["_linea"]
                else:
                    fila_error = grupo["inicio"]
                errores.append(_error(
                    fila_error,
                    f"Asiento {grupo['etiqueta']}: {problema['mensaje']}",
                    problema.get("regla"),
                ))

            total_debe = sum(p["debe"] for p in partidas)
            total_haber = sum(p["haber"] for p in partidas)
            if total_debe == 0 and total_haber == 0:
                errores.append(_error(
                    grupo["inicio"],
                    f"El asiento {grupo['etiqueta']} no tiene importes.",
                    "C1",
                ))

            grupo["fecha"] = fecha_iso or fecha_ref
            grupo["concepto"] = concepto_ref
            grupo["partidas"] = [
                {clave: valor for clave, valor in p.items() if clave != "_linea"}
                for p in partidas
            ]
            # Para la vista previa: el nombre de cada cuenta, si existe.
            grupo["vista"] = [
                {
                    "cuenta": p["cuenta"],
                    "nombre": (_cuenta_minima(conexion, p["cuenta"]) or {}).get("nombre"),
                    "detalle": p["detalle"],
                    "debe": p["debe"],
                    "haber": p["haber"],
                }
                for p in partidas
            ]
            grupo["total_debe"] = total_debe
            grupo["total_haber"] = total_haber

        valido = not errores
        confirmado = bool(confirmar and valido)
        creados = 0

        if confirmado:
            for grupo in grupos:
                insertar_asiento_confirmado(
                    conexion,
                    fecha=grupo["fecha"],
                    concepto=grupo["concepto"],
                    partidas=grupo["partidas"],
                    observaciones="Importado desde CSV",
                )
                creados += 1

    muestra = [
        {
            "asiento": grupo["etiqueta"],
            "fila": grupo["inicio"],
            "fecha": grupo.get("fecha", ""),
            "concepto": grupo.get("concepto", ""),
            "lineas": len(grupo["lineas"]),
            "debe": grupo.get("total_debe", 0),
            "haber": grupo.get("total_haber", 0),
            "partidas": grupo.get("vista", []),
        }
        for grupo in grupos[:12]
    ]

    return {
        "tipo": "asientos",
        "valido": valido,
        "confirmado": confirmado,
        "filas": len(filas),
        "asientos": len(grupos),
        "lineas": len(filas),
        "total_debe": sum(g.get("total_debe", 0) for g in grupos),
        "total_haber": sum(g.get("total_haber", 0) for g in grupos),
        "creados": creados,
        "errores": errores,
        "muestra": muestra,
    }
