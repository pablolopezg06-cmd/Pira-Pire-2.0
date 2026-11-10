"""
Módulo: excel
=============

Lectura y escritura de planillas **Excel (.xlsx)** con la biblioteca estándar.

Un ``.xlsx`` es un ZIP con archivos XML (formato Office Open XML). Para no
sumar dependencias externas (el proyecto no instala nada con ``pip``), acá se
lee y se escribe ese XML directamente con ``zipfile`` y ``xml.etree``. Se
cubre lo que necesita el ERP:

* **Leer** la primera hoja y devolverla como CSV con punto y coma, para que
  los importadores existentes (``erp.importacion``) la procesen igual que un
  CSV: mismas reglas, mismos mensajes de error, misma vista previa.
* **Escribir** una hoja con encabezado en negrita, importes como números con
  formato ``#,##0.00`` y anchos de columna razonables.

Las fechas guardadas como fecha de Excel (número de serie) se convierten a
``AAAA-MM-DD``. Los importes numéricos se pasan con punto decimal, que
``erp.dinero.a_centavos`` interpreta sin ambigüedad.

No se soporta el formato binario viejo ``.xls`` (Excel 97-2003).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .excepciones import ErrorValidacion

#: Espacio de nombres principal de SpreadsheetML.
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

#: Formatos numéricos integrados de Excel que son fechas.
_FORMATOS_FECHA = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))

#: Día cero del calendario de Excel (con el error histórico de 1900).
_ORIGEN_EXCEL = date(1899, 12, 30)

#: Columnas que, al escribir, se guardan como número y no como texto.
COLUMNAS_IMPORTE = {"debe", "haber", "saldo", "importe", "monto"}

#: Firma de un ZIP: todo .xlsx empieza así.
FIRMA_ZIP = b"PK\x03\x04"


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def _texto_nodo(nodo) -> str:
    """Texto de un <si> o <is>: concatena todos los <t> (incluye texto enriquecido)."""
    return "".join(t.text or "" for t in nodo.iter(f"{{{_NS['m']}}}t"))


def _estilos_fecha(zip_: zipfile.ZipFile) -> set[int]:
    """Índices de estilo de celda (atributo ``s``) que muestran fechas."""
    try:
        raiz = ET.fromstring(zip_.read("xl/styles.xml"))
    except KeyError:
        return set()

    personalizados: set[int] = set()
    for formato in raiz.iterfind("m:numFmts/m:numFmt", _NS):
        codigo = re.sub(r'"[^"]*"|\[[^\]]*\]', "", formato.get("formatCode", "")).lower()
        if re.search(r"[dy]", codigo) or ("m" in codigo and "h" not in codigo and "s" not in codigo):
            personalizados.add(int(formato.get("numFmtId", "0")))

    estilos: set[int] = set()
    for indice, xf in enumerate(raiz.iterfind("m:cellXfs/m:xf", _NS)):
        id_formato = int(xf.get("numFmtId", "0"))
        if id_formato in _FORMATOS_FECHA or id_formato in personalizados:
            estilos.add(indice)
    return estilos


def _ruta_primera_hoja(zip_: zipfile.ZipFile) -> str:
    """Ruta dentro del ZIP de la primera hoja del libro."""
    try:
        libro = ET.fromstring(zip_.read("xl/workbook.xml"))
        hoja = libro.find("m:sheets/m:sheet", _NS)
        id_rel = hoja.get(f"{{{_NS_REL}}}id") if hoja is not None else None
        relaciones = ET.fromstring(zip_.read("xl/_rels/workbook.xml.rels"))
        for rel in relaciones:
            if rel.get("Id") == id_rel:
                destino = rel.get("Target", "").lstrip("/")
                return destino if destino.startswith("xl/") else f"xl/{destino}"
    except (KeyError, ET.ParseError):
        pass
    return "xl/worksheets/sheet1.xml"


def _indice_columna(referencia: str) -> int:
    """'A1' -> 0, 'C7' -> 2, 'AA3' -> 26."""
    letras = re.match(r"[A-Z]+", referencia or "")
    if not letras:
        return -1
    indice = 0
    for letra in letras.group(0):
        indice = indice * 26 + (ord(letra) - 64)
    return indice - 1


def _numero_a_texto(texto: str) -> str:
    """
    Pasa un número de Excel a texto que ``a_centavos`` lea sin ambigüedad.

    Los enteros van sin decimales. Los demás, con punto decimal; si quedan
    justo tres decimales se agrega un cero, porque ``1.234`` se leería como
    mil doscientos treinta y cuatro (punto de miles) en vez de dar error.
    """
    valor = Decimal(texto)
    if valor == valor.to_integral_value():
        return str(int(valor))
    plano = format(valor.normalize(), "f")
    if len(plano.split(".")[1]) == 3:
        plano += "0"
    return plano


def leer_filas(contenido: bytes) -> list[list[str]]:
    """Devuelve las filas de la primera hoja como listas de textos."""
    if not contenido.startswith(FIRMA_ZIP):
        raise ErrorValidacion(
            "El archivo no es un Excel .xlsx válido. Si es un .xls (Excel 97-2003), "
            "abrilo y guardalo como .xlsx o como CSV."
        )
    try:
        zip_ = zipfile.ZipFile(io.BytesIO(contenido))
    except zipfile.BadZipFile as exc:
        raise ErrorValidacion("El archivo Excel está dañado o incompleto.") from exc

    with zip_:
        compartidas: list[str] = []
        if "xl/sharedStrings.xml" in zip_.namelist():
            raiz = ET.fromstring(zip_.read("xl/sharedStrings.xml"))
            compartidas = [_texto_nodo(si) for si in raiz.iterfind("m:si", _NS)]

        estilos_fecha = _estilos_fecha(zip_)
        try:
            hoja = ET.fromstring(zip_.read(_ruta_primera_hoja(zip_)))
        except (KeyError, ET.ParseError) as exc:
            raise ErrorValidacion("No se encontró ninguna hoja en el archivo Excel.") from exc

    filas: list[list[str]] = []
    for fila in hoja.iterfind("m:sheetData/m:row", _NS):
        valores: dict[int, str] = {}
        for posicion, celda in enumerate(fila.iterfind("m:c", _NS)):
            columna = _indice_columna(celda.get("r", ""))
            if columna < 0:
                columna = posicion
            tipo = celda.get("t", "n")
            nodo_valor = celda.find("m:v", _NS)
            crudo = nodo_valor.text if nodo_valor is not None and nodo_valor.text else ""

            if tipo == "s":
                texto = compartidas[int(crudo)] if crudo.isdigit() and int(crudo) < len(compartidas) else ""
            elif tipo == "inlineStr":
                nodo = celda.find("m:is", _NS)
                texto = _texto_nodo(nodo) if nodo is not None else ""
            elif tipo == "b":
                texto = "VERDADERO" if crudo == "1" else "FALSO"
            elif tipo in {"str", "e"}:
                texto = crudo
            elif crudo:
                if int(celda.get("s", "0")) in estilos_fecha:
                    texto = (_ORIGEN_EXCEL + timedelta(days=int(float(crudo)))).isoformat()
                else:
                    texto = _numero_a_texto(crudo)
            else:
                texto = ""
            valores[columna] = texto.strip()

        if valores:
            ancho = max(valores) + 1
            filas.append([valores.get(i, "") for i in range(ancho)])
    return filas


def a_csv(contenido: bytes) -> str:
    """Convierte la primera hoja de un .xlsx en CSV separado por punto y coma."""
    filas = leer_filas(contenido)
    if not any(any(celda for celda in fila) for fila in filas):
        raise ErrorValidacion("La planilla de Excel está vacía.")
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";", lineterminator="\n")
    escritor.writerows(filas)
    return salida.getvalue()


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

def _letra_columna(indice: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'."""
    letras = ""
    indice += 1
    while indice:
        indice, resto = divmod(indice - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


#: Fuentes, rellenos y bordes de las planillas, por nombre.
_FUENTES = {
    "normal": '<font><sz val="11"/><name val="Calibri"/></font>',
    "negrita": '<font><b/><sz val="11"/><name val="Calibri"/></font>',
    "cursiva": '<font><i/><sz val="11"/><name val="Calibri"/></font>',
    "cursiva_negrita": '<font><b/><i/><sz val="11"/><name val="Calibri"/></font>',
    "titulo": '<font><b/><sz val="16"/><name val="Calibri"/></font>',
}
_RELLENOS = {
    None: '<fill><patternFill patternType="none"/></fill>',
    "gray125": '<fill><patternFill patternType="gray125"/></fill>',
    **{
        nombre: f'<fill><patternFill patternType="solid"><fgColor rgb="{color}"/>'
                '<bgColor indexed="64"/></patternFill></fill>'
        for nombre, color in {
            "azul": "FFBDD7EE", "naranja": "FFF8CBAD", "amarillo": "FFFFE699",
            "celeste": "FFDDEBF7", "verde": "FFE2EFDA", "gris": "FFD9D9D9",
            "crema": "FFFFF2CC",
        }.items()
    },
}


def _borde(izquierda="", derecha="", arriba="", abajo="") -> str:
    def lado(etiqueta: str, estilo: str) -> str:
        return f'<{etiqueta} style="{estilo}"><color indexed="64"/></{etiqueta}>' if estilo else f"<{etiqueta}/>"
    return (f"<border>{lado('left', izquierda)}{lado('right', derecha)}"
            f"{lado('top', arriba)}{lado('bottom', abajo)}<diagonal/></border>")


_BORDES = {
    None: _borde(),
    "caja": _borde("thin", "thin", "thin", "thin"),
    "caja_gruesa": _borde("medium", "medium", "medium", "medium"),
    "abajo": _borde(abajo="thin"),
    "arriba": _borde(arriba="thin"),
    "izquierda": _borde(izquierda="thin"),
    "arriba_izquierda": _borde(izquierda="thin", arriba="thin"),
}

#: Estilos de celda con nombre: (fuente, relleno, borde, alineación).
#: En las filas, una celda ``(valor, "estilo")`` usa uno de estos.
ESTILOS = {
    "normal": ("normal", None, None, None),
    "negrita": ("negrita", None, None, None),
    "titulo": ("titulo", None, None, "center"),
    "caja": ("normal", None, "caja", None),
    "caja_centro": ("normal", None, "caja", "center"),
    "caja_negrita": ("negrita", None, "caja", None),
    "encabezado": ("negrita", "azul", "caja", "center"),
    "seccion": ("negrita", "azul", "caja", None),
    "concepto": ("cursiva", None, "caja", None),
    "separador": ("normal", "gris", "caja", None),
    "subtotal": ("negrita", "gris", "caja", None),
    "total": ("negrita", "crema", "caja", None),
    "etiqueta": ("cursiva_negrita", None, None, None),
    "nombre_cuenta": ("negrita", None, "abajo", "center"),
    "t_izquierda": ("normal", None, "izquierda", None),
    "t_total": ("negrita", None, "arriba", None),
    "t_total_izquierda": ("negrita", None, "arriba_izquierda", None),
    "grupo_folio": ("negrita", "gris", "caja", "center"),
    "grupo_sumas": ("negrita", "naranja", "caja", "center"),
    "grupo_saldos": ("negrita", "amarillo", "caja", "center"),
    "grupo_patrimoniales": ("negrita", "celeste", "caja", "center"),
    "grupo_resultados": ("negrita", "verde", "caja", "center"),
}


class _Estilos:
    """Registro de los estilos usados en el libro (el <cellXfs> de styles.xml)."""

    def __init__(self) -> None:
        self.lista: list[tuple] = [("normal", None, None, None, 0)]

    def indice(self, nombre: str, formato: int) -> int:
        """Índice del estilo; ``formato`` es 4 (importe) o 0 (general)."""
        clave = (*ESTILOS[nombre], formato)
        if clave not in self.lista:
            self.lista.append(clave)
        return self.lista.index(clave)

    def xml(self) -> str:
        fuentes, rellenos, bordes = list(_FUENTES), list(_RELLENOS), list(_BORDES)
        xfs = []
        for fuente, relleno, borde, alineacion, formato in self.lista:
            atributos = (
                f'numFmtId="{formato}" fontId="{fuentes.index(fuente)}" '
                f'fillId="{rellenos.index(relleno)}" borderId="{bordes.index(borde)}" xfId="0" '
                'applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1"'
            )
            xfs.append(
                f'<xf {atributos} applyAlignment="1"><alignment horizontal="{alineacion}" vertical="center"/></xf>'
                if alineacion else f"<xf {atributos}/>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{_NS["m"]}">'
            f'<fonts count="{len(fuentes)}">{"".join(_FUENTES.values())}</fonts>'
            f'<fills count="{len(rellenos)}">{"".join(_RELLENOS.values())}</fills>'
            f'<borders count="{len(bordes)}">{"".join(_BORDES.values())}</borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs></styleSheet>'
        )


def _hoja_xml(
    encabezados: list,
    filas: list[list],
    estilos: _Estilos,
    *,
    filas_negrita: set[int] | tuple = (),
    inmovilizar: int = 1,
    combinar: list[str] | tuple = (),
    anchos: list[float] | None = None,
    altos: dict[int, float] | None = None,
) -> str:
    """
    XML de una hoja. Cada celda es un valor simple o ``(valor, "estilo")``
    (ver ``ESTILOS``); una celda con estilo se escribe aunque esté vacía,
    para que se vean sus bordes y su color. Los números llevan formato de
    importe. ``combinar`` son rangos como ``"A1:F1"``; ``anchos``, el ancho
    de cada columna; ``altos``, ``{número de fila: alto}``.
    """
    todas = [list(encabezados)] + [list(f) for f in filas]

    def valor_de(celda):
        return celda[0] if isinstance(celda, tuple) else celda

    ancho_tabla = max(len(f) for f in todas)
    if anchos is None:
        anchos = [
            min(60, max(8, *(len(str(valor_de(f[i]))) if i < len(f) and valor_de(f[i]) is not None else 0
                             for f in todas)) + 2)
            for i in range(ancho_tabla)
        ]

    filas_xml: list[str] = []
    for numero, fila in enumerate(todas, start=1):
        celdas: list[str] = []
        for columna, celda in enumerate(fila):
            valor, estilo = celda if isinstance(celda, tuple) else (celda, None)
            vacio = valor is None or valor == ""
            if vacio and estilo is None:
                continue
            if estilo is None:
                estilo = "negrita" if numero == 1 or (numero - 2) in filas_negrita else "normal"
            es_numero = isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool)
            ref = f"{_letra_columna(columna)}{numero}"
            # Los enteros (folios, números de asiento) van sin decimales.
            es_importe = es_numero and not isinstance(valor, int)
            s = estilos.indice(estilo, 4 if es_importe else 0)
            if vacio:
                celdas.append(f'<c r="{ref}" s="{s}"/>')
            elif es_numero:
                celdas.append(f'<c r="{ref}" s="{s}"><v>{valor}</v></c>')
            else:
                celdas.append(
                    f'<c r="{ref}" t="inlineStr" s="{s}">'
                    f'<is><t xml:space="preserve">{escape(str(valor))}</t></is></c>'
                )
        alto = (altos or {}).get(numero)
        atributos = f' ht="{alto}" customHeight="1"' if alto else ""
        filas_xml.append(f'<row r="{numero}"{atributos}>{"".join(celdas)}</row>')

    columnas_xml = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{ancho}" customWidth="1"/>'
        for i, ancho in enumerate(anchos)
    )
    panel = (
        f'<pane ySplit="{inmovilizar}" topLeftCell="A{inmovilizar + 1}" '
        'activePane="bottomLeft" state="frozen"/>'
    ) if inmovilizar else ""
    combinadas = (
        f'<mergeCells count="{len(combinar)}">'
        + "".join(f'<mergeCell ref="{rango}"/>' for rango in combinar)
        + "</mergeCells>"
    ) if combinar else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{_NS["m"]}">'
        f'<sheetViews><sheetView workbookViewId="0" showGridLines="0">{panel}</sheetView></sheetViews>'
        f"<cols>{columnas_xml}</cols>"
        f'<sheetData>{"".join(filas_xml)}</sheetData>'
        f"{combinadas}"
        "</worksheet>"
    )


def escribir_libro(hojas: list[dict]) -> bytes:
    """
    Arma un .xlsx con varias hojas. Cada hoja es un diccionario con
    ``nombre``, ``encabezados``, ``filas`` y, opcionales, ``filas_negrita``,
    ``inmovilizar``, ``combinar``, ``anchos`` y ``altos`` (ver ``_hoja_xml``).
    """
    cantidad = len(hojas)
    nombres = [escape(re.sub(r"[\[\]:*?/\\]", "", h["nombre"])[:31] or f"Hoja{i + 1}")
               for i, h in enumerate(hojas)]
    estilos = _Estilos()
    hojas_xml = [
        _hoja_xml(
            hoja["encabezados"], hoja["filas"], estilos,
            filas_negrita=hoja.get("filas_negrita", ()),
            inmovilizar=hoja.get("inmovilizar", 1),
            combinar=hoja.get("combinar", ()),
            anchos=hoja.get("anchos"),
            altos=hoja.get("altos"),
        )
        for hoja in hojas
    ]
    archivos = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, cantidad + 1)
            )
            + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_NS["m"]}" xmlns:r="{_NS_REL}"><sheets>'
            + "".join(
                f'<sheet name="{nombre}" sheetId="{i}" r:id="rId{i}"/>'
                for i, nombre in enumerate(nombres, start=1)
            )
            + "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, cantidad + 1)
            )
            + f'<Relationship Id="rId{cantidad + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": estilos.xml(),
    }
    for i, hoja_xml in enumerate(hojas_xml, start=1):
        archivos[f"xl/worksheets/sheet{i}.xml"] = hoja_xml

    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as zip_:
        for nombre, contenido in archivos.items():
            zip_.writestr(nombre, contenido)
    return salida.getvalue()


def escribir(
    encabezados: list[str],
    filas: list[list],
    hoja: str = "Hoja1",
    *,
    filas_negrita: set[int] | tuple = (),
    inmovilizar: int = 1,
) -> bytes:
    """
    Arma un .xlsx con una sola hoja, en casillas: el encabezado con fondo y
    en negrita, y cada dato con borde. Las celdas ``int``, ``float`` y
    ``Decimal`` se escriben como números con formato de importe; todo lo
    demás, como texto. Las filas de ``filas_negrita`` (índices de ``filas``)
    van en negrita y las primeras ``inmovilizar`` quedan fijas.
    """
    ancho = max([len(encabezados), *(len(f) for f in filas)])
    return escribir_libro([{
        "nombre": hoja,
        "encabezados": [(e, "encabezado") for e in encabezados],
        "filas": [
            [(valor, "caja_negrita" if i in filas_negrita else "caja")
             for valor in list(f) + [""] * (ancho - len(f))]
            for i, f in enumerate(filas)
        ],
        "inmovilizar": inmovilizar,
    }])


def desde_csv(texto: str, hoja: str = "Hoja1") -> bytes:
    """
    Convierte un CSV (como los de las plantillas o el que propone Pira Pires)
    en .xlsx. Las columnas de importe se guardan como números.
    """
    from .dinero import a_centavos, desde_centavos

    texto = (texto or "").lstrip("\ufeff").strip()
    if not texto:
        raise ErrorValidacion("No hay datos para exportar.")
    try:
        delimitador = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=";,\t|").delimiter
    except csv.Error:
        delimitador = ";"
    filas = [f for f in csv.reader(io.StringIO(texto), delimiter=delimitador) if any(f)]
    encabezados, datos = filas[0], filas[1:]
    importes = {i for i, nombre in enumerate(encabezados) if nombre.strip().lower() in COLUMNAS_IMPORTE}

    convertidas: list[list] = []
    for fila in datos:
        nueva: list = []
        for i, valor in enumerate(fila):
            if i in importes and valor.strip():
                try:
                    nueva.append(desde_centavos(a_centavos(valor)))
                    continue
                except Exception:  # noqa: BLE001 - si no es un importe, queda como texto
                    pass
            nueva.append(valor)
        convertidas.append(nueva)
    return escribir(encabezados, convertidas, hoja)


def libro_diario_de_clase(texto_csv: str, nombres: dict[str, str]) -> bytes:
    """Libro Diario de clase en un .xlsx de una hoja (ver ``_hoja_libro_diario``)."""
    return escribir_libro([_hoja_libro_diario(texto_csv, nombres)])


def _hoja_libro_diario(texto_csv: str, nombres: dict[str, str]) -> dict:
    """
    Arma el Libro Diario con el formato de las planillas de clase, en
    casillas: NRO · FECHA · CUENTAS CONTABLES · DEBE · HABER, una fila por
    cuenta, el concepto en cursiva debajo de cada asiento, una fila gris entre
    asientos y «SUMAS IGUALES» al final. A la derecha va el CÓDIGO de cada
    cuenta, para que el archivo se pueda volver a importar sin depender de
    cómo se llama la cuenta.

    ``texto_csv`` es el CSV del importador (el que propone Pira Pires) y
    ``nombres`` traduce cada código de cuenta a su nombre en el plan.
    """
    from .dinero import a_centavos, desde_centavos

    lineas = [
        f for f in csv.reader(io.StringIO((texto_csv or "").lstrip("﻿")), delimiter=";")
        if any(c.strip() for c in f)
    ]
    if len(lineas) < 2:
        raise ErrorValidacion("No hay asientos para exportar.")

    def importe(valor: str):
        return desde_centavos(a_centavos(valor)) if valor.strip() else ""

    def caja(*valores) -> list:
        return [(v, "caja") for v in valores]

    def cierre(concepto: str) -> list[list]:
        return [
            [("", "caja"), ("", "caja"), (concepto, "concepto"), ("", "caja"), ("", "caja"), ("", "caja")],
            [("", "separador")] * 6,
        ]

    filas: list[list] = [[(t, "encabezado") for t in ("NRO", "FECHA", "CUENTAS CONTABLES", "DEBE", "HABER", "CÓDIGO")]]
    total_debe = total_haber = 0
    anterior = None
    concepto = ""
    for celdas in lineas[1:]:
        asiento, fecha, concepto_linea, cuenta, detalle, debe, haber = (celdas + [""] * 7)[:7]
        if asiento != anterior:
            if anterior is not None:
                filas += cierre(concepto)
            try:
                fecha = datetime.strptime(fecha.strip(), "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                pass
            primera, anterior, concepto = True, asiento, concepto_linea
        else:
            primera = False
        nombre = nombres.get(cuenta.strip()) or detalle or cuenta
        numero = int(asiento) if asiento.strip().isdigit() else asiento
        fila = caja(numero if primera else "", fecha if primera else "",
                    nombre.upper(), importe(debe), importe(haber), cuenta.strip())
        fila[0] = (fila[0][0], "caja_centro")
        fila[1] = (fila[1][0], "caja_centro")
        fila[5] = (fila[5][0], "caja_centro")
        filas.append(fila)
        total_debe += a_centavos(debe or "0")
        total_haber += a_centavos(haber or "0")
    filas += cierre(concepto)[:1]
    filas.append([("", "total"), ("", "total"), ("SUMAS IGUALES", "total"),
                  (desde_centavos(total_debe), "total"), (desde_centavos(total_haber), "total"), ("", "total")])
    return {
        "nombre": "LIBRO DIARIO",
        "encabezados": [("LIBRO DIARIO", "titulo")] + [("", "titulo")] * 5,
        "filas": filas, "inmovilizar": 2,
        "combinar": ["A1:F1"], "anchos": [7, 12, 44, 18, 18, 10], "altos": {1: 26, 2: 18},
    }


def libros_contables(texto_csv: str, nombres: dict[str, str]) -> bytes:
    """
    El ejercitario completo en un .xlsx, con el formato de la plantilla de
    la cátedra y todo en casillas: LIBRO DIARIO, LIBRO MAYOR (cuentas T por
    folio, tres por banda), BALANCE de comprobación de sumas y saldos, e
    IRE Y RATIOS.

    La primera hoja es el Diario, así el archivo se puede volver a importar.
    """
    from . import estados
    from .dinero import desde_centavos

    analisis = estados.analizar(texto_csv, nombres)

    def g(centavos: int):
        return desde_centavos(centavos) if centavos else ""

    def g0(centavos: int):
        """Como ``g`` pero muestra 0,00 en vez de dejar la celda vacía."""
        return desde_centavos(centavos)

    def fecha_corta(texto: str) -> str:
        try:
            return datetime.strptime(texto, "%Y-%m-%d").strftime("%d/%m")
        except ValueError:
            return texto

    def letra(columna: int) -> str:
        return _letra_columna(columna)

    # --- Libro Mayor: bandas de tres cuentas T, como en la plantilla ------
    # Cada cuenta ocupa 4 columnas (asiento | DEBE | HABER | asiento) y una
    # de separación. La línea vertical de la T es el borde izquierdo del Haber.
    mayor: list[list] = [[""] * 15]
    combinar_mayor = ["A1:N1"]
    cuentas = analisis["mayor"]
    for inicio in range(0, len(cuentas), 3):
        banda = cuentas[inicio:inicio + 3]
        alto = max(max(sum(1 for m in t["movimientos"] if m["debe"]),
                       sum(1 for m in t["movimientos"] if m["haber"])) for t in banda)
        base = len(mayor) + 2  # número de fila en Excel de la primera fila del bloque
        bloque: list[list] = [[""] * 15 for _ in range(alto + 6)]
        for posicion, t in enumerate(banda):
            x = posicion * 5
            bloque[0][x] = (f"Folio: {t['folio']:02d}", "etiqueta")
            bloque[1][x] = ("Cuenta:", "etiqueta")
            bloque[1][x + 1:x + 4] = [(t["nombre"].upper(), "nombre_cuenta"), ("", "nombre_cuenta"), ("", "nombre_cuenta")]
            combinar_mayor.append(f"{letra(x + 1)}{base + 1}:{letra(x + 3)}{base + 1}")
            bloque[2][x:x + 4] = [("DEBE", "encabezado"), ("", "encabezado"), ("HABER", "encabezado"), ("", "encabezado")]
            combinar_mayor += [f"{letra(x)}{base + 2}:{letra(x + 1)}{base + 2}",
                               f"{letra(x + 2)}{base + 2}:{letra(x + 3)}{base + 2}"]
            debe = [m for m in t["movimientos"] if m["debe"]]
            haber = [m for m in t["movimientos"] if m["haber"]]
            for fila in range(alto):
                bloque[3 + fila][x + 2] = ("", "t_izquierda")
            for fila, m in enumerate(debe):
                bloque[3 + fila][x] = (f"As. {m['asiento']} ({fecha_corta(m['fecha'])})", "normal")
                bloque[3 + fila][x + 1] = (g(m["debe"]), "normal")
            for fila, m in enumerate(haber):
                bloque[3 + fila][x + 2] = (g(m["haber"]), "t_izquierda")
                bloque[3 + fila][x + 3] = (f"As. {m['asiento']} ({fecha_corta(m['fecha'])})", "normal")
            bloque[alto + 3][x:x + 4] = [("Totales", "t_total"), (g0(t["debe"]), "t_total"),
                                         (g0(t["haber"]), "t_total_izquierda"), ("", "t_total")]
            saldo = t["saldo"]
            fila_saldo = [("Saldo deudor" if saldo >= 0 else "Saldo acreedor", "total"),
                          ("", "total"), ("", "total"), ("", "total")]
            fila_saldo[1 if saldo >= 0 else 2] = (g0(abs(saldo)), "total")
            bloque[alto + 4][x:x + 4] = fila_saldo
        mayor += bloque

    # --- Balance de comprobación de sumas y saldos --------------------------
    columnas = ("debe", "haber", "deudor", "acreedor", "activo", "pasivo_pn", "negativo", "positivo")
    grupos = ["grupo_sumas"] * 2 + ["grupo_saldos"] * 2 + ["grupo_patrimoniales"] * 2 + ["grupo_resultados"] * 2
    balance: list[list] = [
        [""] * 10,
        [("Nro. Folio", "grupo_folio"), ("Cuenta Contable", "grupo_folio"),
         ("SUMAS", "grupo_sumas"), ("", "grupo_sumas"), ("SALDOS", "grupo_saldos"), ("", "grupo_saldos"),
         ("PATRIMONIALES", "grupo_patrimoniales"), ("", "grupo_patrimoniales"),
         ("RESULTADOS", "grupo_resultados"), ("", "grupo_resultados")],
        [("", "grupo_folio"), ("", "grupo_folio")] + [
            (texto, grupo) for texto, grupo in zip(
                ("Debe", "Haber", "Deudor", "Acreedor", "Activo", "Pasivo/PNeto", "Negativo", "Positivo"), grupos)
        ],
    ]
    for f in analisis["balance"]:
        balance.append([(f["folio"], "caja_centro"), (f["nombre"].upper(), "caja"),
                        *((g(f[c]), "caja") for c in columnas)])
    sumas = analisis["sumas"]
    balance.append([("", "subtotal"), ("SUMAS", "subtotal"),
                    *((desde_centavos(sumas[c]), "subtotal") for c in columnas)])
    resultado = analisis["resultado"]
    etiqueta = "Resultado del Ejercicio" + (" (ganancia)" if resultado > 0 else " (pérdida)" if resultado < 0 else "")
    fila_resultado = [("", "caja"), (etiqueta, "concepto")] + [("", "caja")] * 8
    for c, valor in analisis["sumas_iguales"].items():
        if valor != sumas[c]:
            fila_resultado[2 + columnas.index(c)] = (desde_centavos(valor - sumas[c]), "caja")
    balance.append(fila_resultado)
    balance.append([("", "total"), ("SUMAS IGUALES", "total"),
                    *((desde_centavos(analisis["sumas_iguales"][c]), "total") for c in columnas)])

    # --- IRE y ratios --------------------------------------------------------
    r = analisis["ratios"]

    def dato(etiqueta: str, valor, formula: str) -> list:
        return [(etiqueta, "caja"), (valor if valor is not None else "—", "caja"), (formula, "concepto")]

    def seccion(titulo: str) -> list:
        return [(titulo, "seccion"), ("", "seccion"), ("", "seccion")]

    ratios: list[list] = [
        [""] * 3,
        seccion("RESULTADO DEL EJERCICIO"),
        dato("Ingresos (cuentas 4)", desde_centavos(sumas["positivo"]), "Saldos acreedores de resultados"),
        dato("Costos y gastos (cuentas 5 y 6)", desde_centavos(sumas["negativo"]), "Saldos deudores de resultados"),
        dato("Resultado antes del IRE", desde_centavos(resultado), "Ingresos − costos y gastos"),
        [""] * 3,
        seccion("IMPUESTO A LA RENTA EMPRESARIAL (IRE)"),
        dato("IRE (10 % de la renta neta)", desde_centavos(analisis["ire"]),
             "Ley N.º 6380/2019; solo si hay ganancia" if resultado > 0 else "No aplica: no hay ganancia"),
        dato("Utilidad neta después del IRE", desde_centavos(analisis["utilidad_neta"]), "Resultado − IRE"),
        [""] * 3,
        seccion("DATOS DEL BALANCE"),
        dato("Activo total", desde_centavos(analisis["activo_total"]), "Cuentas 1"),
        dato("Activo corriente", desde_centavos(analisis["activo_corriente"]), "Cuentas 1.1"),
        dato("Pasivo total", desde_centavos(analisis["pasivo_total"]), "Cuentas 2"),
        dato("Pasivo corriente", desde_centavos(analisis["pasivo_corriente"]), "Cuentas 2.1"),
        dato("Patrimonio neto (sin el resultado)", desde_centavos(analisis["patrimonio"]), "Cuentas 3"),
        [""] * 3,
        seccion("RATIOS FINANCIEROS"),
        dato("ROI (%)", r["roi"], "Utilidad neta (después del IRE) / Activo total × 100"),
        dato("ROI antes del IRE (%)", r["roi_antes_ire"], "Resultado antes del IRE / Activo total × 100"),
        dato("Liquidez corriente", r["liquidez"], "Activo corriente / Pasivo corriente"),
        dato("Solvencia", r["solvencia"], "Activo total / Pasivo total"),
    ]
    secciones = [i + 2 for i, fila in enumerate(ratios) if fila and isinstance(fila[0], tuple) and fila[0][1] == "seccion"]

    return escribir_libro([
        _hoja_libro_diario(texto_csv, nombres),
        {"nombre": "LIBRO MAYOR", "encabezados": [("LIBRO MAYOR", "titulo")] + [("", "titulo")] * 13,
         "filas": mayor, "inmovilizar": 0, "combinar": combinar_mayor,
         "anchos": [14, 16, 16, 14, 3] * 3, "altos": {1: 26}},
        {"nombre": "BALANCE",
         "encabezados": [("BALANCE DE COMPROBACIÓN DE SUMAS Y SALDOS", "titulo")] + [("", "titulo")] * 9,
         "filas": balance, "inmovilizar": 4,
         "combinar": ["A1:J1", "A3:A4", "B3:B4", "C3:D3", "E3:F3", "G3:H3", "I3:J3"],
         "anchos": [11, 34] + [16] * 8, "altos": {1: 26}},
        {"nombre": "IRE Y RATIOS",
         "encabezados": [("IRE Y RATIOS FINANCIEROS", "titulo"), ("", "titulo"), ("", "titulo")],
         "filas": ratios, "inmovilizar": 0,
         "combinar": ["A1:C1"] + [f"A{n}:C{n}" for n in secciones],
         "anchos": [38, 20, 55], "altos": {1: 26}},
    ])
