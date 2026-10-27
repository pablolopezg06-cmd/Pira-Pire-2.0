"""
Módulo: iva
===========

Impuesto al Valor Agregado de **Paraguay** (Ley N.º 1259/97).

QUÉ CUBRE
---------
Este módulo resuelve las tres operaciones con IVA que un ERP contable
necesita y que el enunciado no traía:

    1. **Discriminar** un importe que ya incluye IVA, separando la base
       imponible del impuesto (``discriminar``).

    2. **Calcular** el IVA sobre una base imponible conocida y armar las
       partidas del asiento de venta o de compra (``liquidar_venta``,
       ``liquidar_compra``).

    3. **Liquidar** el período: cruzar Débito Fiscal contra Crédito Fiscal
       y determinar si queda un saldo a pagar o a recuperar
       (``liquidar_periodo``).

ALÍCUOTAS VIGENTES
------------------
La ley paraguaya tiene **dos** tasas desde la Ley N.º 1259/97:

    * **10 %** — la tasa general. Alcanza a la mayoría de los bienes y
      servicios (Art. 10).
    * **5 %** — tasa reducida para determinados productos de la canasta
      (Art. 11), por ejemplo algunos alimentos y medicamentos.

Las operaciones **exentas** (Art. 15 y siguientes) no llevan impuesto:
van a la cuenta "Ventas Exentas" sin partida de IVA.

CÓMO SE GUARDAN LOS IMPORTES
----------------------------
Igual que el resto del ERP: **enteros en centavos**, nunca ``float``.
El redondeo del impuesto es *comercial* (medio centavo sube), que es el
criterio que usa la SET para la declaración jurada.

DOS FORMAS DE CARGAR EL MISMO ASIENTO
-------------------------------------
En la práctica, el usuario puede conocer:

    * el **importe total** que cobró o pagó (IVA incluido), o
    * la **base imponible** más el IVA por separado.

Por eso hay dos caminos y ambos llegan al mismo asiento:

    >>> # Camino A: sé la base (1.000.000 G.) y quiero el total
    >>> liquidar_venta(base=1000000, alicuota=10)
    {'base': 100000000, 'iva': 10000000, 'total': 110000000, ...}

    >>> # Camino B: sé el total (1.100.000 G.) y quiero discriminar
    >>> discriminar(total=1100000, alicuota=10)
    {'base': 100000000, 'iva': 10000000, 'total': 110000000, ...}
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .dinero import a_centavos, desde_centavos
from .excepciones import ErrorValidacion

# ---------------------------------------------------------------------------
# Alícuotas vigentes en Paraguay
# ---------------------------------------------------------------------------

#: Tasa general de la Ley N.º 1259/97 (Art. 10).
ALICUOTA_GENERAL = 10

#: Tasa reducida de la Ley N.º 1259/97 (Art. 11).
ALICUOTA_REDUCIDA = 5

#: Alícuota nula: operación exenta o no gravada (Art. 15 y sgtes.).
ALICUOTA_EXENTA = 0

#: Catálogo de alícuotas usables, con su etiqueta para la interfaz.
#: «exenta» no se guarda como número en la partida: simplemente no se
#: genera la línea de IVA.
ALICUOTAS = {
    ALICUOTA_GENERAL: "IVA 10% (tasa general, Art. 10)",
    ALICUOTA_REDUCIDA: "IVA 5% (tasa reducida, Art. 11)",
    ALICUOTA_EXENTA: "Exenta / no gravada (Art. 15 y sgtes.)",
}

#: Cuentas del bloque de IVA, tal como están en ``PLAN_MINIMO``.
CUENTA_DEBITO_FISCAL = "2.1.02"
CUENTA_CREDITO_FISCAL = "1.1.05"
CUENTA_IVA_POR_PAGAR = "2.1.03"
CUENTA_IVA_A_RECUPERAR = "1.1.06"
CUENTA_VENTAS_GRAVADAS = "4.1.01"
CUENTA_VENTAS_EXENTAS = "4.1.02"


class ErrorIVA(ErrorValidacion):
    """
    Datos de IVA incoherentes (alícuota inexistente, base negativa, etc.).

    Hereda de ``ErrorValidacion`` para que el servidor lo devuelva como
    **422** con el mismo formato que el resto de las validaciones del ERP,
    en lugar de caer al 500 genérico.
    """

    def __init__(self, mensaje: str, regla: str | None = "IVA"):
        super().__init__(mensaje, regla)


# ---------------------------------------------------------------------------
# Validación de la alícuota
# ---------------------------------------------------------------------------

def normalizar_alicuota(valor) -> int:
    """
    Convierte lo que llega del formulario en una alícuota válida (10, 5 o 0).

    Acepta tanto el número (``10``) como el texto con el signo (``"10%"``)
    y el decimal (``"0.10"``). Cualquier otra cosa se rechaza: es
    preferible un error claro a contabilizar un impuesto equivocado.
    """
    if valor is None or valor == "":
        return ALICUOTA_GENERAL

    if isinstance(valor, bool):
        raise ErrorIVA("La alícuota no puede ser verdadero o falso.")

    if isinstance(valor, str):
        texto = valor.strip().replace("%", "").replace(",", ".")
        if not texto:
            return ALICUOTA_GENERAL
        try:
            numero = Decimal(texto)
        except Exception as exc:  # noqa: BLE001 - se reemplaza por error propio
            raise ErrorIVA(f"La alícuota '{valor}' no es un número válido.") from exc
    else:
        try:
            numero = Decimal(str(valor))
        except Exception as exc:  # noqa: BLE001
            raise ErrorIVA(f"La alícuota '{valor}' no es un número válido.") from exc

    # "0.10" (fracción) se interpreta como "10" (porcentaje).
    if 0 < numero <= 1:
        numero *= 100

    numero = numero.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if int(numero) not in ALICUOTAS:
        vigentes = ", ".join(f"{a}%" for a in sorted(ALICUOTAS) if a)
        raise ErrorIVA(
            f"La alícuota {numero}% no es válida en Paraguay. "
            f"Las tasas vigentes son {vigentes} (Ley N.º 1259/97)."
        )
    return int(numero)


def factor_alicuota(alicuota) -> Decimal:
    """Devuelve la alícuota como fracción: 10 -> Decimal('0.10')."""
    return Decimal(normalizar_alicuota(alicuota)) / Decimal(100)


# ---------------------------------------------------------------------------
# Cálculo del impuesto
# ---------------------------------------------------------------------------

def _redondear(centavos: Decimal) -> int:
    """Redondeo comercial a centavos enteros (medio centavo sube)."""
    return int(centavos.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calcular_iva(base, alicuota=ALICUOTA_GENERAL) -> int:
    """
    Devuelve el IVA **en centavos** de una base imponible en unidades.

    La ``base`` se recibe en la misma unidad que en todo el ERP (pesos o
    guaraníes, no centavos): ``a_centavos`` la convierte. El resultado sale
    en centavos enteros, listo para una partida.

    >>> calcular_iva(1000000)          # G. 1.000.000 al 10%
    10000000
    >>> calcular_iva(1000000, 5)       # G. 1.000.000 al 5%
    5000000
    >>> calcular_iva(1000000, 0)       # exenta
    0
    """
    base_centavos = a_centavos(base)
    if base_centavos < 0:
        raise ErrorIVA("La base imponible no puede ser negativa.")

    factor = factor_alicuota(alicuota)
    if factor == 0:
        return 0

    # Se calcula en Decimal y se redondea una sola vez al final: así no se
    # arrastra el error de redondear base e impuesto por separado.
    return _redondear(Decimal(base_centavos) * factor)


def discriminar(total, alicuota=ALICUOTA_GENERAL) -> dict:
    """
    Separa un importe **con IVA incluido** en base imponible + impuesto.

    Se usa cuando el usuario conoce el total que cobró o pagó, que es lo
    habitual en una factura: el precio final ya trae el impuesto adentro.
    El ``total`` llega **en unidades** (como en todo el ERP) y el
    diccionario devuelve tanto centavos (``base``, ``iva``, ``total``)
    como el importe en unidades (``*_unidades``, un ``Decimal``).

    >>> r = discriminar(1100000)        # G. 1.100.000 final al 10%
    >>> r['base'], r['iva'], r['total']
    (100000000, 10000000, 110000000)

    El impuesto se obtiene dividiendo por (1 + alícuota), no restando un
    porcentaje del total: así la base reconstruida siempre vuelve a dar el
    total exacto y el asiento nunca queda descuadrado por un centavo.
    """
    total_centavos = a_centavos(total)
    if total_centavos < 0:
        raise ErrorIVA("El importe total no puede ser negativo.")

    factor = factor_alicuota(alicuota)
    if factor == 0:
        return {
            "base": total_centavos,
            "iva": 0,
            "total": total_centavos,
            "alicuota": 0,
            "base_unidades": desde_centavos(total_centavos),
            "iva_unidades": Decimal("0.00"),
            "total_unidades": desde_centavos(total_centavos),
        }

    divisor = Decimal(1) + factor
    base_exacta = Decimal(total_centavos) / divisor
    base_centavos = _redondear(base_exacta)
    iva_centavos = total_centavos - base_centavos

    return {
        "base": base_centavos,
        "iva": iva_centavos,
        "total": total_centavos,
        "alicuota": normalizar_alicuota(alicuota),
        "base_unidades": desde_centavos(base_centavos),
        "iva_unidades": desde_centavos(iva_centavos),
        "total_unidades": desde_centavos(total_centavos),
    }


def liquidar_venta(base=None, total=None, alicuota=ALICUOTA_GENERAL) -> dict:
    """
    Arma el importe de una **venta**, aceptando la base o el total.

    Si se pasa ``total``, se discrimina; si se pasa ``base``, se calcula el
    impuesto hacia adelante. Exactamente uno de los dos debe venir.

    Devuelve el mismo diccionario que ``discriminar``.
    """
    if (base is None) == (total is None):
        raise ErrorIVA("Indicá la base imponible o el importe total, pero no ambos.")

    if total is not None:
        return discriminar(total, alicuota)

    base_centavos = a_centavos(base)
    # Ojo: calcular_iva espera la base en UNIDADES (le aplica a_centavos),
    # por eso se le pasa `base` y no `base_centavos`, que ya fue convertida.
    iva_centavos = calcular_iva(base, alicuota)
    return {
        "base": base_centavos,
        "iva": iva_centavos,
        "total": base_centavos + iva_centavos,
        "alicuota": normalizar_alicuota(alicuota),
        "base_unidades": desde_centavos(base_centavos),
        "iva_unidades": desde_centavos(iva_centavos),
        "total_unidades": desde_centavos(base_centavos + iva_centavos),
    }


def liquidar_compra(base=None, total=None, alicuota=ALICUOTA_GENERAL) -> dict:
    """
    Igual que ``liquidar_venta`` pero del lado de la compra.

    La aritmética es idéntica (el IVA no cambia de valor según quién lo
    pague); se separa en dos funciones para que el código que arma el
    asiento se lea en el idioma del negocio y no en el del cálculo.
    """
    return liquidar_venta(base=base, total=total, alicuota=alicuota)


# ---------------------------------------------------------------------------
# Armado de las partidas del asiento
# ---------------------------------------------------------------------------
#
# CONTRATO DE LAS PARTIDAS
# ------------------------
# Todas las funciones de esta sección devuelven las partidas con los
# importes **en unidades** (no en centavos), que es lo que espera
# `libro_diario.crear_asiento`: ese módulo aplica `a_centavos` a cada
# importe antes de guardarlo. Devolver centavos aquí los multiplicaría
# por 100 dos veces y descuadraría el asiento.

def _unidades(centavos: int):
    """Centavos enteros -> Decimal en unidades (150000050 -> 1500000.50)."""
    return desde_centavos(centavos)


def partidas_venta(
    *,
    cuenta_cobro: str,
    base=None,
    total=None,
    alicuota=ALICUOTA_GENERAL,
    cuenta_ingreso: str | None = None,
    detalle: str | None = None,
) -> list[dict]:
    """
    Devuelve las **partidas del asiento de venta** listas para
    ``libro_diario.crear_asiento``.

    Con IVA del 10 %, una venta de G. 1.000.000 cobrada en efectivo genera:

        Debe   1.1.01 Caja              1.100.000
        Haber  4.1.01 Ventas Gravadas   1.000.000
        Haber  2.1.02 IVA Débito Fiscal   100.000

    Con una operación **exenta** (alícuota 0) se usa la cuenta de ventas
    exentas y **no** se agrega la línea de IVA:

        Debe   1.1.01 Caja              1.000.000
        Haber  4.1.02 Ventas Exentas    1.000.000
    """
    calculo = liquidar_venta(base=base, total=total, alicuota=alicuota)

    if cuenta_ingreso is None:
        cuenta_ingreso = (
            CUENTA_VENTAS_EXENTAS if calculo["alicuota"] == 0 else CUENTA_VENTAS_GRAVADAS
        )

    partidas: list[dict] = [
        {
            "cuenta": cuenta_cobro,
            "debe": _unidades(calculo["total"]),
            "haber": 0,
            "detalle": detalle or "Cobro de la venta",
        },
        {
            "cuenta": cuenta_ingreso,
            "debe": 0,
            "haber": _unidades(calculo["base"]),
            "detalle": detalle or "Venta",
        },
    ]

    if calculo["iva"]:
        partidas.append(
            {
                "cuenta": CUENTA_DEBITO_FISCAL,
                "debe": 0,
                "haber": _unidades(calculo["iva"]),
                "detalle": f"IVA {calculo['alicuota']}%",
            }
        )

    return partidas


def partidas_compra(
    *,
    cuenta_pago: str,
    cuenta_gasto_o_bien: str,
    base=None,
    total=None,
    alicuota=ALICUOTA_GENERAL,
    detalle: str | None = None,
) -> list[dict]:
    """
    Devuelve las **partidas del asiento de compra** (o gasto) con IVA.

    Con IVA del 10 %, una compra de G. 500.000 pagada en efectivo genera:

        Debe   5.1   Costo de Mercaderías   500.000
        Debe   1.1.05 IVA Crédito Fiscal     50.000
        Haber  1.1.01 Caja                  550.000

    El IVA de la compra se debita en Crédito Fiscal y después se descuenta
    del Débito Fiscal en la liquidación del período.
    """
    calculo = liquidar_compra(base=base, total=total, alicuota=alicuota)

    partidas: list[dict] = [
        {
            "cuenta": cuenta_gasto_o_bien,
            "debe": _unidades(calculo["base"]),
            "haber": 0,
            "detalle": detalle or "Compra",
        }
    ]

    if calculo["iva"]:
        partidas.append(
            {
                "cuenta": CUENTA_CREDITO_FISCAL,
                "debe": _unidades(calculo["iva"]),
                "haber": 0,
                "detalle": f"IVA {calculo['alicuota']}%",
            }
        )

    partidas.append(
        {
            "cuenta": cuenta_pago,
            "debe": 0,
            "haber": _unidades(calculo["total"]),
            "detalle": detalle or "Pago de la compra",
        }
    )

    return partidas


# ---------------------------------------------------------------------------
# Liquidación del período
# ---------------------------------------------------------------------------

def liquidar_periodo(
    *,
    debito_fiscal: int = 0,
    credito_fiscal: int = 0,
) -> dict:
    """
    Cruza el Débito Fiscal contra el Crédito Fiscal del período.

    Es el paso previo a la declaración jurada (Formulario 120 de la SET).
    Devuelve el saldo y **con qué cuenta se contabiliza**:

        * Débito > Crédito -> saldo a favor del fisco: **IVA por Pagar**
          (pasivo). Se acredita la cuenta 2.1.03.
        * Crédito > Débito -> saldo a favor del contribuyente:
          **IVA a Recuperar** (activo). Se debita la cuenta 1.1.06.
        * Débito = Crédito -> no hay nada que liquidar.

    También devuelve las partidas del asiento de liquidación cuando
    corresponde (lista vacía si el período cierra exacto).
    """
    debito = int(debito_fiscal)
    credito = int(credito_fiscal)
    if debito < 0 or credito < 0:
        raise ErrorIVA("El débito y el crédito fiscal no pueden ser negativos.")

    diferencia = debito - credito

    if diferencia > 0:
        situacion = "A_PAGAR"
        cuenta_saldo = CUENTA_IVA_POR_PAGAR
        monto = diferencia
        partidas = [
            {
                "cuenta": CUENTA_DEBITO_FISCAL,
                "debe": _unidades(debito),
                "haber": 0,
                "detalle": "Cierre del Débito Fiscal",
            },
            {
                "cuenta": CUENTA_CREDITO_FISCAL,
                "debe": 0,
                "haber": _unidades(credito),
                "detalle": "Cierre del Crédito Fiscal",
            },
            {
                "cuenta": CUENTA_IVA_POR_PAGAR,
                "debe": 0,
                "haber": _unidades(diferencia),
                "detalle": "IVA a pagar del período",
            },
        ]
    elif diferencia < 0:
        situacion = "A_RECUPERAR"
        cuenta_saldo = CUENTA_IVA_A_RECUPERAR
        monto = -diferencia
        partidas = [
            {
                "cuenta": CUENTA_DEBITO_FISCAL,
                "debe": _unidades(debito),
                "haber": 0,
                "detalle": "Cierre del Débito Fiscal",
            },
            {
                "cuenta": CUENTA_CREDITO_FISCAL,
                "debe": 0,
                "haber": _unidades(credito),
                "detalle": "Cierre del Crédito Fiscal",
            },
            {
                "cuenta": CUENTA_IVA_A_RECUPERAR,
                "debe": _unidades(-diferencia),
                "haber": 0,
                "detalle": "IVA a recuperar del período",
            },
        ]
    else:
        situacion = "SIN_SALDO"
        cuenta_saldo = None
        monto = 0
        partidas = [
            {
                "cuenta": CUENTA_DEBITO_FISCAL,
                "debe": _unidades(debito),
                "haber": 0,
                "detalle": "Cierre del Débito Fiscal",
            },
            {
                "cuenta": CUENTA_CREDITO_FISCAL,
                "debe": 0,
                "haber": _unidades(credito),
                "detalle": "Cierre del Crédito Fiscal",
            },
        ] if debito else []

    return {
        "debito_fiscal": debito,
        "credito_fiscal": credito,
        "diferencia": diferencia,
        "situacion": situacion,
        "monto": monto,
        "cuenta_saldo": cuenta_saldo,
        "partidas": partidas,
    }
