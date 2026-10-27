"""
Módulo: dinero
==============

Manejo de importes monetarios del Mini ERP Contable.

DECISIÓN DE DISEÑO
-----------------
Los importes **nunca** se guardan como número decimal de punto flotante
(`float`), porque 0.1 + 0.2 != 0.3 en IEEE 754 y en contabilidad eso
produce diferencias de un centavo que hacen fallar el control de cuadre.

Por eso todos los importes se manejan y persisten como **enteros en
centavos** (la unidad mínima monetaria). Ejemplo:

    G. 1.500.000,50  ->  150000050 centavos

La conversión ocurre únicamente en los bordes del sistema: al recibir
datos del usuario y al mostrar resultados.

FORMATO DE PRESENTACIÓN
----------------------
Se usa el formato habitual de Paraguay y Argentina:
punto para los miles y coma para los decimales  ->  "1.500.000,50"
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

#: Cantidad de centavos que forman una unidad monetaria (1 G. = 100 centavos).
CENTAVOS_POR_UNIDAD = 100

#: Símbolo de la moneda por defecto.
SIMBOLO_MONEDA = "G."


class ErrorMonto(ValueError):
    """Se lanza cuando un importe no puede interpretarse como número válido."""


def _interpretar(valor) -> Decimal:
    """
    Convierte la entrada del usuario en `Decimal` aceptando tanto notación
    numérica (números, cadenas simples) como notación contable con
    separadores de miles.

    Formatos aceptados:
        1500000.5   -> 1500000.50
        "1500000.5" -> 1500000.50
        "1.500.000,50" -> 1500000.50   (punto = miles, coma = decimal)
        "1,500,000.50" -> 1500000.50   (coma = miles, punto = decimal)
        "1.000"     -> 1000.00         (un punto con 3 dígitos = miles)
        "G. 1.500.000" -> 1500000.00
    """
    if isinstance(valor, bool):  # bool es subclase de int: se rechaza
        raise ErrorMonto("Un importe no puede ser verdadero o falso.")

    if isinstance(valor, Decimal):
        return valor

    if isinstance(valor, int):
        return Decimal(valor)

    if isinstance(valor, float):
        # Se pasa por str para no arrastrar el error binario de float.
        return Decimal(repr(valor))

    if valor is None or valor == "":
        return Decimal("0")

    if not isinstance(valor, str):
        raise ErrorMonto(f"Tipo de importe no soportado: {type(valor).__name__}")

    texto = valor.strip()
    if not texto:
        return Decimal("0")

    # Se quitan el símbolo de moneda y los espacios (incluye el espacio fino).
    texto = texto.replace(" ", "").replace(" ", "")
    texto = texto.upper().replace("G.", "").replace("GS.", "").replace("$", "")
    texto = re.sub(r"[A-Z]+", "", texto)  # por si viene "PYG. 1.000"
    if texto in {"", "-", "+"}:
        return Decimal("0")

    negativo = texto.startswith("-")
    texto = texto.lstrip("+-")

    punto = texto.count(".")
    coma = texto.count(",")

    if punto and coma:
        # El separador que aparece más a la derecha es el decimal.
        if texto.rfind(".") > texto.rfind(","):
            decimal = texto.replace(",", "")       # coma = miles
        else:
            decimal = texto.replace(".", "").replace(",", ".")  # punto = miles
    elif coma:
        # Una sola coma: decimal. Varias comas: separador de miles.
        decimal = texto.replace(",", ".") if coma == 1 else texto.replace(",", "")
    elif punto:
        # Convención de Paraguay y Argentina: un único punto seguido de
        # exactamente tres dígitos es separador de miles ("1.000" = mil),
        # no decimal. Con más de un punto también es miles. Un punto con
        # otra cantidad de dígitos detrás se interpreta como decimal.
        digitos_detras = len(texto) - texto.rfind(".") - 1
        decimal = texto.replace(".", "") if punto > 1 or digitos_detras == 3 else texto
    else:
        decimal = texto

    if not re.fullmatch(r"\d+(\.\d+)?", decimal):
        raise ErrorMonto(f"El importe '{valor}' no es un número válido.")

    numero = Decimal(decimal)
    if negativo:
        numero = -numero
    return numero.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def a_centavos(valor) -> int:
    """
    Convierte cualquier importe recibido a **centavos enteros**.

    Lanza `ErrorMonto` si el valor no es numérico o si tiene más de dos
    decimales (no tiene sentido guarda centavos).
    """
    numero = _interpretar(valor)

    # Redondeo contable: 0.005 -> 1 centavo.
    centavos = (numero * CENTAVOS_POR_UNIDAD).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )

    if centavos != (numero * CENTAVOS_POR_UNIDAD):
        raise ErrorMonto(
            f"El importe '{valor}' tiene más de dos decimales y no puede registrarse."
        )
    return int(centavos)


def desde_centavos(centavos: int) -> Decimal:
    """Convierte centavos enteros a `Decimal` en unidades monetarias."""
    return (Decimal(int(centavos)) / CENTAVOS_POR_UNIDAD).quantize(Decimal("0.01"))


def _miles(entero: int) -> str:
    """Formatea un entero con punto como separador de miles: 1500000 -> '1.500.000'."""
    return f"{abs(entero):,}".replace(",", ".")


def formato_monto(centavos: int, con_simbolo: bool = True) -> str:
    """
    Devuelve el importe ya formateado para mostrar en pantalla o en reportes.

    >>> formato_monto(150000050)
    '1.500.000,50'
    >>> formato_monto(0, con_simbolo=False)
    '0,00'
    """
    centavos = int(centavos)
    unidades, resto = divmod(abs(centavos), CENTAVOS_POR_UNIDAD)
    negativo = centavos < 0

    texto = f"{_miles(unidades)},{resto:02d}"
    if negativo:
        texto = f"-{texto}"
    if con_simbolo:
        texto = f"{SIMBOLO_MONEDA} {texto}"
    return texto


def formato_numero_miles(entero: int) -> str:
    """Formato de miles sin decimales, para cantidades de asientos/filas."""
    return f"{int(entero):,}".replace(",", ".")