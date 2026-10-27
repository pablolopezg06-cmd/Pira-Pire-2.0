"""
Módulo: validaciones
====================

Núcleo de reglas de negocio del Mini ERP Contable (secciones 13, 14, 15,
16 y 18 del enunciado).

Este módulo es el **único** lugar donde se decide si una operación es
válida. Los repositorios (`plan_de_cuentas`, `libro_diario`,
`libro_mayor`, `balance`) preguntan acá; de esa forma las reglas no se
dispersan ni se duplican.

Controles implementados (sección 18)
------------------------------------
C1  Un asiento no se confirma si Debe != Haber.
C2  No se puede utilizar una cuenta inexistente o inactiva.
C3  Las cuentas deben estar clasificadas correctamente (código coherente
    con su clasificación y con su cuenta padre).
C4  El Libro Mayor obtiene su información del Libro Diario.
C5  Los balances se generan de la información contabilizada (no hay
    carga manual paralela).
C6  Se diferencia la naturaleza deudora (D) y acreedora (A).

C4 y C5 no se "validan" contra una base de datos: se garantizan por
arquitectura, porque el Libro Mayor y el Balance **no tienen tablas
propias**; son consultas (`SELECT`) sobre las partidas ya contabilizadas.
`verificar_trazabilidad()` expone esa garantía de forma auditable.
"""

from __future__ import annotations

import re

from .excepciones import ErrorValidacion

# ---------------------------------------------------------------------------
# Catálogos
# ---------------------------------------------------------------------------

#: Las 6 clasificaciones del plan de cuentas. La clave es el dígito raíz del
#: código, con la etiqueta y la naturaleza que les corresponde por definición.
CLASIFICACIONES: dict[int, dict] = {
    1: {"nombre": "ACTIVO", "naturaleza": "D", "tipo": "ACTIVO"},
    2: {"nombre": "PASIVO", "naturaleza": "A", "tipo": "PASIVO"},
    3: {"nombre": "PATRIMONIO NETO", "naturaleza": "A", "tipo": "PATRIMONIO NETO"},
    4: {"nombre": "INGRESOS", "naturaleza": "A", "tipo": "INGRESOS"},
    5: {"nombre": "COSTOS", "naturaleza": "D", "tipo": "COSTOS"},
    6: {"nombre": "GASTOS", "naturaleza": "D", "tipo": "GASTOS"},
}

#: Naturaleza por defecto según clasificación (Control 6).
NATURALEZA_POR_CLASIFICACION = {k: v["naturaleza"] for k, v in CLASIFICACIONES.items()}

NATURALEZAS = {"D": "Deudora", "A": "Acreedora"}

ESTADOS_CUENTA = {"ACTIVA", "INACTIVA"}

ESTADOS_ASIENTO = {"BORRADOR", "CONFIRMADO", "ANULADO"}

#: Formato de código: 1, 1.1, 1.1.01  (raíz de 1 a 6, hasta dos niveles hijos)
FORMATO_CODIGO = re.compile(r"^[1-6](?:\.\d{1,2}){0,2}$")


def descripcion_clasificaciones() -> str:
    """Texto '1 - ACTIVO (Deudora)' para usar en <select> de la interfaz."""
    return " · ".join(
        f"{k} - {v['nombre']} ({NATURALEZAS[v['naturaleza']]})"
        for k, v in CLASIFICACIONES.items()
    )


# ---------------------------------------------------------------------------
# Catálogo de controles (sección 18) — se muestra en la pestaña "Controles"
# ---------------------------------------------------------------------------

REGLAS: list[dict] = [
    {
        "codigo": "C1",
        "titulo": "Cuadre del asiento",
        "requisito": "Un asiento no puede registrarse definitivamente si Debe ≠ Haber.",
        "implementacion": (
            "Al confirmar un asiento se suman los débitos y los créditos de todas sus "
            "partidas. Si los totales no son iguales, o el total es cero, la operación "
            "se rechaza. Además cada partida individual debe tener un solo importe: "
            "no puede llevar Debe y Haber a la vez, ni ambos en cero."
        ),
        "modulo": "libro_diario",
    },
    {
        "codigo": "C2",
        "titulo": "Existencia y estado de la cuenta",
        "requisito": "No se podrá utilizar una cuenta inexistente.",
        "implementacion": (
            "Cada partida se valida contra el plan de cuentas: la cuenta debe existir, "
            "estar ACTIVA y ser una cuenta de detalle (una cuenta agrupadora como "
            "'1 ACTIVO' no admite movimientos). El código está declarado como clave "
            "foránea en la base, así que el motor también impide guardar lo inexistente."
        ),
        "modulo": "validaciones / base de datos",
    },
    {
        "codigo": "C3",
        "titulo": "Clasificación correcta de las cuentas",
        "requisito": "Las cuentas deberán estar clasificadas correctamente.",
        "implementacion": (
            "El código debe respetar el formato jerárquico y su raíz debe coincidir con "
            "la clasificación declarada; toda cuenta de nivel 2 o 3 debe tener una cuenta "
            "padre existente, de la misma clasificación y coherente con ella. El sistema "
            "ofrece además la naturaleza 'esperada' según la clasificación."
        ),
        "modulo": "plan_de_cuentas",
    },
    {
        "codigo": "C4",
        "titulo": "Libro Mayor derivado del Libro Diario",
        "requisito": "El Libro Mayor deberá obtener la información directamente del Libro Diario.",
        "implementacion": (
            "El Libro Mayor no tiene tabla propia: se genera con un SELECT sobre las "
            "partidas de los asientos CONFIRMADOS del Libro Diario, y el saldo se "
            "acumula en el orden de las fechas. No existe forma de cargar un mayor 'a mano'."
        ),
        "modulo": "libro_mayor",
    },
    {
        "codigo": "C5",
        "titulo": "Balance generado de lo contabilizado",
        "requisito": "Los balances se generan con la información contabilizada, sin una segunda carga manual.",
        "implementacion": (
            "El balance suma los débitos y créditos ya registrados en las partidas. "
            "El ERP verifica que el total de débitos sea igual al total de créditos y "
            "que la suma de saldos deudores sea igual a la suma de saldos acreedores."
        ),
        "modulo": "balance",
    },
    {
        "codigo": "C6",
        "titulo": "Naturaleza deudora o acreedora",
        "requisito": "El sistema deberá diferenciar cuentas de naturaleza deudora y acreedora.",
        "implementacion": (
            "Cada cuenta guarda su naturaleza, que se valida contra la clasificación "
            "(un pasivo siempre es acreedor, un activo siempre es deudor). El Libro "
            "Mayor expresa el saldo según esa naturaleza y el Balance la usa para los "
            "subtotales por clasificación."
        ),
        "modulo": "validaciones / libro_mayor / balance",
    },
]


# ---------------------------------------------------------------------------
# Utilidades de código de cuenta
# ---------------------------------------------------------------------------

def nivel_de_codigo(codigo: str) -> int:
    """'1' -> 1, '1.1' -> 2, '1.1.01' -> 3."""
    return str(codigo).count(".") + 1


def raiz_de_codigo(codigo: str) -> int:
    """'1.1.01' -> 1 (la clasificación a la que pertenece)."""
    return int(str(codigo).split(".")[0])


def padre_de_codigo(codigo: str) -> str | None:
    """'1.1.01' -> '1.1';  '1' -> None."""
    partes = str(codigo).split(".")
    return ".".join(partes[:-1]) if len(partes) > 1 else None


def jerarquia(codigo: str) -> list[str]:
    """'1.1.01' -> ['1', '1.1', '1.1.01'] (la ruta completa en la jerarquía)."""
    partes = str(codigo).split(".")
    return [".".join(partes[: i + 1]) for i in range(len(partes))]


def nombre_naturaleza(naturaleza: str) -> str:
    return NATURALEZAS.get(naturaleza, naturaleza)


def naturaleza_de_cuenta(clasificacion: int) -> str:
    """Naturaleza que corresponde a una clasificación (Control 6)."""
    return NATURALEZA_POR_CLASIFICACION[clasificacion]


# ---------------------------------------------------------------------------
# Control 3 - Clasificación correcta de las cuentas
# ---------------------------------------------------------------------------

def validar_cuenta(
    codigo: str,
    nombre: str,
    clasificacion: int,
    *,
    recibe_movimiento: bool,
    existe_codigo=None,
    obtener_cuenta_padre=None,
    codigo_original: str | None = None,
) -> list[str]:
    """
    Valida los datos de una cuenta del plan de cuentas.

    Devuelve la lista de errores encontrados (vacía si todo está bien).
    Cada error es una cadena ya legible por el usuario.

    Parámetros
    ----------
    recibe_movimiento:
        True si la cuenta admite asientos; False si es una cuenta agrupadora
        (colectora) que solo sirve para subtotales.
    existe_codigo:
        Callable(codigo) -> bool. Indica si el código ya está en uso.
    obtener_cuenta_padre:
        Callable(codigo) -> dict | None. Devuelve la cuenta padre.
    codigo_original:
        Código previo, si se está editando (para no autoconflictuarse).
    """
    errores: list[str] = []

    codigo = (codigo or "").strip()
    nombre = (nombre or "").strip()

    # --- Formato y jerarquía del código -------------------------------
    if not codigo:
        errores.append("El código de la cuenta es obligatorio.")
    elif not FORMATO_CODIGO.fullmatch(codigo):
        errores.append(
            f"El código '{codigo}' no tiene un formato válido. Se espera un código "
            "jerárquico como '1', '1.1' o '1.1.01'."
        )

    # --- Nombre ---------------------------------------------------------
    if not nombre:
        errores.append("El nombre de la cuenta es obligatorio.")
    elif len(nombre) < 3:
        errores.append("El nombre de la cuenta es demasiado corto (mínimo 3 caracteres).")

    # --- Coherencia entre código y clasificación -----------------------
    if codigo and FORMATO_CODIGO.fullmatch(codigo):
        raiz = raiz_de_codigo(codigo)
        if raiz != clasificacion:
            errores.append(
                f"El código '{codigo}' pertenece a la clasificación {raiz} "
                f"({CLASIFICACIONES[raiz]['nombre']}) pero la cuenta está clasificada "
                f"como {clasificacion} ({CLASIFICACIONES[clasificacion]['nombre']})."
            )
        elif nivel_de_codigo(codigo) == 1 and recibe_movimiento:
            errores.append(
                f"'{codigo} {CLASIFICACIONES[raiz]['nombre']}' es una cuenta agrupadora "
                "de primer nivel: no puede recibir movimientos."
            )

    # --- Cuenta padre (jerarquía sin saltos ni huérfanas) ---------------
    if codigo and FORMATO_CODIGO.fullmatch(codigo):
        padre = padre_de_codigo(codigo)
        if padre and obtener_cuenta_padre is not None:
            cuenta_padre = obtener_cuenta_padre(padre)
            if cuenta_padre is None:
                errores.append(
                    f"La cuenta padre '{padre}' no existe. Primero se debe crear."
                )
            elif padre != codigo_original and recibe_movimiento:
                # Debe existir la cadena completa hasta la raíz.
                faltante = siguiente_codigo_inexistente(codigo, obtener_cuenta_padre)
                if faltante:
                    errores.append(
                        f"Falta crear la cuenta intermedia '{faltante}' en la jerarquía."
                    )

    # --- Unicidad del código --------------------------------------------
    if codigo and existe_codigo is not None and codigo != codigo_original:
        if existe_codigo(codigo):
            errores.append(f"Ya existe una cuenta con el código '{codigo}'.")

    return errores


def siguiente_codigo_inexistente(codigo: str, obtener_cuenta_padre) -> str | None:
    """Revisa la ruta de la jerarquía y devuelve el primer código que falta."""
    ruta = jerarquia(codigo)
    for antecesor in ruta[:-1]:
        if obtener_cuenta_padre(antecesor) is None:
            return antecesor
    return None


# ---------------------------------------------------------------------------
# Control 1 y Control 2 - Validación de las partidas de un asiento
# ---------------------------------------------------------------------------

def validar_partidas(
    partidas: list[dict],
    *,
    obtener_cuenta=None,
) -> list[dict]:
    """
    Valida el detalle (partidas) de un asiento.

    Aplica el **Control 1** (cada partida con un solo importe y el total
    del asiento cuadrado) y el **Control 2** (cuentas existentes, activas y
    aptas para recibir movimientos).

    Cada problema trae una marca ``bloqueante``:

    · ``True``  -> la línea es estructuralmente imposible de guardar (importe
      en los dos lados, línea vacía, importe negativo o cuenta inexistente).
      El asiento ni siquiera se guarda como borrador: no se puede persistir.
    · ``False`` -> el asiento podría guardarse como borrador pero no se
      contabiliza (por ejemplo, que no cuadre o que la cuenta esté inactiva).

    Parámetros
    ----------
    partidas:
        Lista de diccionarios con las claves: cuenta, detalle, debe, haber.
    obtener_cuenta:
        Callable(codigo) -> dict | None que busca la cuenta en el plan.

    Devuelve una lista de problemas: ``{regla, linea, mensaje, bloqueante}``.
    """
    problemas: list[dict] = []
    total_debe = 0
    total_haber = 0

    if not partidas:
        problemas.append(
            {
                "regla": "C1",
                "linea": None,
                "bloqueante": True,
                "mensaje": "El asiento debe tener al menos dos partidas.",
            }
        )
        return problemas

    for numero_linea, partida in enumerate(partidas, start=1):
        etiqueta = f"Línea {numero_linea}"

        # --- Importes ---------------------------------------------------
        debe = int(partida.get("debe") or 0)
        haber = int(partida.get("haber") or 0)

        if debe < 0 or haber < 0:
            problemas.append(
                {
                    "regla": "C1",
                    "linea": numero_linea,
                    "bloqueante": True,
                    "mensaje": (
                        f"{etiqueta}: los importes no pueden ser negativos "
                        "(un importe negativo se registra invirtiendo los signos de Debe/Haber)."
                    ),
                }
            )
        if debe > 0 and haber > 0:
            problemas.append(
                {
                    "regla": "C1",
                    "linea": numero_linea,
                    "bloqueante": True,
                    "mensaje": (
                        f"{etiqueta}: la cuenta '{partida.get('cuenta')}' tiene importe "
                        "en Debe y en Haber a la vez. Cada línea debe ir solo por un lado."
                    ),
                }
            )
        elif debe == 0 and haber == 0:
            problemas.append(
                {
                    "regla": "C1",
                    "linea": numero_linea,
                    "bloqueante": True,
                    "mensaje": (
                        f"{etiqueta}: la cuenta '{partida.get('cuenta')}' no tiene importe "
                        "en Debe ni en Haber. Una línea vacía no se puede contabilizar."
                    ),
                }
            )

        total_debe += debe
        total_haber += haber

        # --- Control 2: cuenta existente, activa y de detalle ----------
        codigo = (partida.get("cuenta") or "").strip()
        if not codigo:
            problemas.append(
                {
                    "regla": "C2",
                    "linea": numero_linea,
                    "bloqueante": True,
                    "mensaje": f"{etiqueta}: se debe indicar una cuenta.",
                }
            )
        elif obtener_cuenta is not None:
            cuenta = obtener_cuenta(codigo)
            if cuenta is None:
                # Bloqueante: la base tiene clave foránea contra el plan.
                problemas.append(
                    {
                        "regla": "C2",
                        "linea": numero_linea,
                        "bloqueante": True,
                        "mensaje": (
                            f"{etiqueta}: la cuenta '{codigo}' no existe en el plan de cuentas."
                        ),
                    }
                )
            else:
                if cuenta["estado"] == "INACTIVA":
                    problemas.append(
                        {
                            "regla": "C2",
                            "linea": numero_linea,
                            "bloqueante": False,
                            "mensaje": (
                                f"{etiqueta}: la cuenta '{codigo} {cuenta['nombre']}' está "
                                "INACTIVA y no admite movimientos."
                            ),
                        }
                    )
                if not cuenta["recibe_movimiento"]:
                    problemas.append(
                        {
                            "regla": "C2",
                            "linea": numero_linea,
                            "bloqueante": False,
                            "mensaje": (
                                f"{etiqueta}: '{codigo} {cuenta['nombre']}' es una cuenta "
                                "agrupadora: el movimiento debe registrarse en una cuenta "
                                "de detalle."
                            ),
                        }
                    )

    # --- Control 1: cuadre del asiento ---------------------------------
    if total_debe != total_haber:
        problemas.append(
            {
                "regla": "C1",
                "linea": None,
                "bloqueante": False,
                "mensaje": (
                    f"El asiento no cuadra: total Debe {_d(total_debe)} vs "
                    f"total Haber {_d(total_haber)}. "
                    f"Diferencia: {_d(total_debe - total_haber)}."
                ),
            }
        )

    return problemas


def bloqueantes(problemas: list[dict]) -> list[dict]:
    """Filtra los problemas que impiden siquiera guardar el asiento."""
    return [p for p in problemas if p.get("bloqueante")]


def _d(centavos: int) -> str:
    """Formato corto para mensajes de error (delega en `dinero`)."""
    from .dinero import formato_monto

    return formato_monto(centavos)


def lanzar_si_hay_problemas(problemas: list[dict], contexto: str):
    """Convierte la lista de problemas devuelta por los validadores en una excepción."""
    if not problemas:
        return
    primero = problemas[0]
    raise ErrorValidacion(
        f"{contexto}: {primero['mensaje']}",
        regla=primero.get("regla"),
        detalles=[p["mensaje"] for p in problemas],
    )


# ---------------------------------------------------------------------------
# Control 4 y Control 5 - Trazabilidad (garantía por arquitectura)
# ---------------------------------------------------------------------------

def verificar_trazabilidad() -> list[dict]:
    """
    Control 4 y 5: el Libro Mayor y el Balance no tienen tablas propias en
    la base de datos, por lo que es imposible que se carguen a mano.

    Esta función se expone en la interfaz para demostrarlo: informa qué
    tablas alimentan cada reporte y de dónde lee sus datos.
    """
    return [
        {
            "reporte": "Libro Diario",
            "tablas": ["asiento", "partida", "cuenta"],
            "descripcion": "Única fuente de carga. Los asientos nacen en BORRADOR y "
                           "solo pasan a CONFIRMADO si cuadran.",
            "carga_manual": False,
        },
        {
            "reporte": "Libro Mayor",
            "tablas": ["partida JOIN asiento"],
            "descripcion": "SELECT sobre las partidas de los asientos CONFIRMADOS, "
                           "con saldo acumulado por cuenta.",
            "carga_manual": False,
        },
        {
            "reporte": "Balance de Comprobación",
            "tablas": ["partida JOIN asiento"],
            "descripcion": "SUM de débitos y créditos por cuenta sobre la misma "
                           "información del Libro Diario.",
            "carga_manual": False,
        },
    ]