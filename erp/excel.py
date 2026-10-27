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
from datetime import date, timedelta
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


def escribir(encabezados: list[str], filas: list[list], hoja: str = "Hoja1") -> bytes:
    """
    Arma un .xlsx con una sola hoja.

    Las celdas ``int``, ``float`` y ``Decimal`` se escriben como números con
    formato de importe; todo lo demás, como texto. La fila de encabezados va
    en negrita y queda inmovilizada.
    """
    todas = [list(encabezados)] + [list(f) for f in filas]
    anchos = [
        min(60, max(8, *(len(str(f[i])) if i < len(f) and f[i] is not None else 0
                         for f in todas)) + 2)
        for i in range(len(encabezados))
    ]

    filas_xml: list[str] = []
    for numero, fila in enumerate(todas, start=1):
        celdas: list[str] = []
        for columna, valor in enumerate(fila):
            if valor is None or valor == "":
                continue
            ref = f"{_letra_columna(columna)}{numero}"
            if numero == 1:
                celdas.append(f'<c r="{ref}" t="inlineStr" s="1"><is><t>{escape(str(valor))}</t></is></c>')
            elif isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool):
                celdas.append(f'<c r="{ref}" s="2"><v>{valor}</v></c>')
            else:
                celdas.append(
                    f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                    f"{escape(str(valor))}</t></is></c>"
                )
        filas_xml.append(f'<row r="{numero}">{"".join(celdas)}</row>')

    columnas_xml = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{ancho}" customWidth="1"/>'
        for i, ancho in enumerate(anchos)
    )
    hoja_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{_NS["m"]}">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        f"<cols>{columnas_xml}</cols>"
        f'<sheetData>{"".join(filas_xml)}</sheetData>'
        "</worksheet>"
    )
    nombre_hoja = escape(re.sub(r"[\[\]:*?/\\]", "", hoja)[:31] or "Hoja1")

    archivos = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
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
            f'<workbook xmlns="{_NS["m"]}" xmlns:r="{_NS_REL}">'
            f'<sheets><sheet name="{nombre_hoja}" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{_NS["m"]}">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="3">'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
            '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            "</cellXfs></styleSheet>"
        ),
        "xl/worksheets/sheet1.xml": hoja_xml,
    }

    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as zip_:
        for nombre, contenido in archivos.items():
            zip_.writestr(nombre, contenido)
    return salida.getvalue()


def desde_csv(texto: str, hoja: str = "Hoja1") -> bytes:
    """
    Convierte un CSV (como los de las plantillas o el que propone Pira Pires)
    en .xlsx. Las columnas de importe se guardan como números.
    """
    from .dinero import a_centavos, desde_centavos

    texto = (texto or "").lstrip("﻿").strip()
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
