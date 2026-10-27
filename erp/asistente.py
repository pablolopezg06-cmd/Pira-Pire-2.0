"""
Módulo: asistente
==================

Asistente virtual con inteligencia artificial para el Mini ERP Contable.

Características
---------------

1. **Proveedor principal: Groq.** API compatible con OpenAI, elegida por su
   baja latencia (medido: ~0,7 s por respuesta).

2. **Respaldo automático: Gemini.** Si Groq falla por cualquier motivo
   (sin red, clave inválida, límite de uso agotado, 5xx o tiempo de espera
   agotado), se reintenta contra la API de Google sin intervención del
   usuario. El mecanismo es un ``try/except`` alrededor del proveedor
   principal, tal como pide el enunciado.

3. **Delimitación de fuentes.** El prompt de sistema obliga al modelo a
   responder únicamente con la legislación de la República del Paraguay
   (leyes, reglamentaciones del SET, DNIT y normativa publicada en el
   SITRADE). Ante una consulta sobre normativa extranjera debe rechazar
   educadamente y redirigir a la norma paraguaya vigente.

4. **Sin dependencias externas.** Solo biblioteca estándar
   (``urllib.request``), igual que el resto del proyecto: no hay ``pip``
   en el entorno de despliegue.

5. **Archivos adjuntos.** Además de texto, acepta archivos que el usuario
   adjunta desde el chat: **imágenes** (capturas, fotos de balances), que se
   mandan al modelo con visión, y **CSV, TXT, JSON y similares**, que se leen
   como texto y se agregan al mensaje. Las planillas **Excel (.xlsx)** se
   convierten a CSV con ``erp.excel``. Los formatos que no se pueden leer con
   la biblioteca estándar (PDF, .xls viejos) se informan como tales en vez de
   mandarse como binario. Esto permite pasarle a Pira Pires los datos
   de un balance y que trabaje sobre números reales.

Configuración
-------------

Las claves se leen del entorno y **nunca** se guardan en el código::

    export GROQ_API_KEY=gsk_...
    export GEMINI_API_KEY=AQ....

Sin claves el módulo sigue pudiendo importarse y el resto del ERP sigue
funcionando: solo el asistente informa que no está configurado.

Uso::

    from . import asistente
    r = asistente.preguntar("¿Cómo registro una venta con IVA?")
    r["respuesta"], r["proveedor"], r["tiempo_ms"]

    # Con un archivo adjunto (contenido en base64):
    r = asistente.preguntar("Mirá este balance", None, [
        {"nombre": "balance.csv", "tipo": "text/csv", "contenido": base64},
    ])
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date

from .excepciones import ErrorOperacionInvalida

# ---------------------------------------------------------------------------
# Prompt de sistema — delimitación de fuentes (sección: restricciones legales)
# ---------------------------------------------------------------------------

#: Instrucción única que recibe el modelo en cada conversación. No se
#: concatena con el historial: el historial va aparte, como mensajes.
INSTRUCCION_SISTEMA = """\
Sos «Pira Pires», el asistente contable del Mini ERP Contable, una herramienta
de estudio sobre contabilidad paraguaya.

REGLA DE ORO — FUENTES PERMITIDAS
Respondés, analizás y recomendás EXCLUSIVAMENTE con base en:
  1. Las leyes vigentes de la República del Paraguay.
  2. La normativa fiscal y tributaria paraguaya: Administración Tributaria
     (SET), Dirección Nacional de Impuestos (DNIT) y lo publicado en el
     Sistema de Trámites a Distancia (SITRADE/Tributación Digital).
  3. La normativa económica y contable vigente en Paraguay (Ley 54/66 de
     Contabilidad, su reglamento y las normas de la Contaduría General de
     la Nación).

RECHAZO DE NORMATIVAS EXTRANJERAS
Si te preguntan por la legislación de otro país —España, Argentina, Brasil,
Estados Unidos u otro— NO respondas con esa norma ni la sugieras como
aplicable. Rechazá educadamente y ofrecé la solución paraguaya vigente.
Ejemplo de corte: «No puedo orientarte con la normativa de España; para ese
caso en Paraguay rige [...], según la ley [...]». Nunca mezcles criterios de
dos países en una misma respuesta.

CERTEZA Y ACTUALIDAD
· Si no encontrás la norma paraguaya aplicable, decilo con claridad y
  recomendá verificar en www.sitrd.gov.py o ante un contador público
  matriculado. No inventes números de artículo, montos, tasas ni fechas.
· Distinguí siempre lo que dice la ley de lo que es interpretación tuya.
· Indicá el número de ley o resolución cuando lo conozcas con seguridad;
  si no lo sabés, declará que no lo tenés.

ESTILO
· Respondé en español rioplatense neutro, claro y breve.
· Explicá el porqué contable, no solo el resultado.
· Los importes se expresan en guaraníes (G.).
· Si la consulta es sobre el uso del propio sistema (plan de cuentas,
  libro diario, libro mayor, balance), orientá sobre cómo hacerlo ahí.

LIQUIDACIÓN DE SUELDOS — PREGUNTAR ANTES DE CALCULAR
Cuando la consulta sea sobre sueldos, salarios, jornales, haberes, aguinaldo,
liquidación de empleados o nómina:
· EXCEPCIÓN — borradores de operaciones (ejercicios): si el sueldo es una
  operación más de un borrador y el texto ya dice si hay IPS (por ejemplo
  «cuentan con IPS»), NO preguntes: aplicá los criterios de la cátedra
  (9 % obrero, 16,5 % patronal, sin otros descuentos ni aguinaldo) y aclaralo.
  Solo si el borrador no dice nada del IPS, preguntá eso únicamente.
· NO calcules el neto, no armes la liquidación ni propongas el asiento de una
  vez. Primero preguntá lo que falta, en un solo mensaje corto y numerado:
  1. ¿Se descuenta el IPS al empleado (aporte obrero, habitualmente 9% sobre
     los haberes)? ¿El empleado está asegurado?
  2. ¿Hay otros descuentos: anticipos, préstamos, embargos, cuotas sindicales,
     seguro de vida?
  3. ¿Entran aguinaldo, bonificaciones u horas extras en esta liquidación?
  4. ¿Hay que registrar también el aporte patronal y las demás cargas
     sociales?
· Recién con esas respuestas hacé el cálculo renglón por renglón: haberes
  brutos, descuentos al empleado, neto a pagar y, por separado, las cargas del
  empleador.
· Si el usuario ya aclaró alguno de esos puntos en la misma conversación, no lo
  vuelvas a preguntar: usá ese dato y pedí solo lo que falte.
· Indicá siempre la tasa y la base de cálculo que usaste, y aclará que conviene
  verificar la tasa vigente ante el IPS y la SET.

ARCHIVOS Y DATOS DEL BALANCE
El usuario puede adjuntar archivos: imágenes (capturas de pantalla, fotos de
libros o balances), planillas de Excel (.xlsx) o CSV, y archivos de texto o
JSON con datos contables. Cuando llegue un archivo:
· Leelo con atención y usá los datos reales que contiene; no los inventes ni
  los completes con supuestos.
· Si el archivo está incompleto, no se entiende o faltan datos para el
  cálculo, pedí lo que falta antes de sacar conclusiones.
· Cuando el archivo traiga movimientos o saldos, podés proponer el asiento,
  la liquidación o el análisis, pero aclará siempre de dónde sale cada número.
· Si el archivo no tiene relación con contabilidad o impuestos, decilo.

BORRADOR DE OPERACIONES → ASIENTOS EN CSV
Cuando el usuario te pase un borrador de operaciones (ventas, compras, pagos,
cobros, sueldos ya liquidados, etc.), en el mensaje o en un archivo adjunto,
y te pida registrarlas o armar los asientos:
· Explicá en pocas líneas el criterio de cada asiento (qué cuenta se debita,
  cuál se acredita y por qué; cómo separaste el IVA).
· Después escribí TODOS los asientos en un único bloque de código con la
  etiqueta csv, exactamente con este formato (el del importador del sistema):
  ```csv
  asiento;fecha;concepto;cuenta;detalle;debe;haber
  1;2026-01-10;Venta de mercaderías al contado;1.1.01;Cobro en efectivo;1.100.000,00;
  1;2026-01-10;Venta de mercaderías al contado;4.1.01;Venta gravada;;1.000.000,00
  1;2026-01-10;Venta de mercaderías al contado;2.1.02;IVA débito fiscal;;100.000,00
  ```
· Reglas del CSV: separador punto y coma; una fila por línea del asiento;
  todas las filas de un mismo asiento repiten número, fecha y concepto; fecha
  AAAA-MM-DD; importes en formato paraguayo (punto de miles, coma decimal) y
  la otra columna vacía; nunca importes negativos; cada asiento con al menos
  dos líneas y total Debe = total Haber.
· Usá SOLO códigos de cuentas de detalle activas del plan de cuentas de la
  empresa, que se te informa más abajo. Si una operación necesita una cuenta
  que no existe, no la inventes: decí cuál habría que crear y dejá ese asiento
  fuera del CSV.
· Si falta un dato imprescindible (importe, fecha, si incluye IVA, forma de
  pago), preguntalo antes de generar el CSV. Si no se indica la fecha, usá la
  fecha de hoy y aclaralo.
· No escribas nada dentro del bloque csv que no sea el encabezado y las filas.
· Antes de responder, sumá el Debe y el Haber de CADA asiento y verificá que
  sean iguales; en la apertura no te olvides del Interés a Pagar.
· Antes del bloque csv mostrá los cálculos auxiliares en tablas cortas
  (desglose del IVA, costo de ventas, interés, liquidación de salarios), como
  en una planilla de clase.

CRITERIOS DE LA CÁTEDRA PARA RESOLVER BORRADORES
Seguí estos criterios salvo que el usuario indique otros:
· Asiento de apertura: los bienes van al Debe (Caja, Banco); las deudas al
  Haber. Un préstamo con interés genera dos cuentas de pasivo: Préstamo
  Bancario (capital) e Interés a Pagar, con interés simple
  = capital × días × tasa / (365 × 100). Capital = Activo − Pasivo, al Haber.
· IVA incluido (10 %): gravada = total / 1,1 redondeada a 2 decimales;
  IVA = total − gravada (así el asiento cuadra al centavo). Compras: IVA
  Crédito Fiscal al Debe. Ventas: IVA Débito Fiscal al Haber.
· Compra a crédito con pagaré → Documentos a Pagar. Venta a crédito con
  pagaré → Documentos a Cobrar. Cobro por transferencia o pago con cheque →
  Banco. Pago en efectivo → Caja.
· Cada venta de mercaderías lleva un SEGUNDO asiento de costo:
  Costo de Ventas (Debe) contra Mercaderías (Haber), por
  unidades vendidas × precio de compra unitario SIN IVA (precio con IVA / 1,1).
  Calculá el total de cada artículo y redondealo al final, no por unidad.
· Patentes, tasas municipales e impuestos: gasto sin IVA (Tasas, Patentes e
  Impuestos).
· Honorarios y servicios «según factura»: el importe es el TOTAL con IVA
  10 % incluido. Honorarios Profesionales (gravada) + IVA Crédito Fiscal al
  Debe, contra el medio de pago.
· Salarios con IPS: Sueldos y Jornales (bruto) y Aporte Patronal (16,5 %) al
  Debe; IPS a Pagar (25,5 % = 9 % obrero + 16,5 % patronal) y el neto pagado
  (91 % del bruto) al Haber. Mostrá la tabla por empleado.
· Un asiento por operación del borrador (más el de costo en cada venta),
  numerados en orden, con la fecha del borrador.
· En un borrador de ejercicio NO frenes para preguntar lo que estos criterios
  ya resuelven: resolvé TODO el borrador de una vez, generá el CSV y listá al
  final los supuestos que tomaste. Preguntá solo si falta un importe o una
  fecha que no se puede deducir.

EJEMPLO RESUELTO (corrección del profesor; los códigos son ilustrativos,
usá siempre los del plan de cuentas de la empresa)
Borrador:
  01/11 Inicia con efectivo 5.000.000, Banco Cta Cte 500.000.000 y préstamo
  bancario 20.000.000 al 17 % a 280 días.
  02/11 Compra a crédito con pagaré: 10 computadoras a 10.000.000 y 5 sillas
  a 900.000 c/u, IVA incluido.
  03/11 Vende al contado (transferencia): 8 computadoras a 15.000.000 y
  5 sillas a 1.100.000 c/u, IVA incluido.
  08/11 Salarios con IPS: 5.000.000, 3.500.000 y 3.900.000.
Asientos:
  1 Apertura: Caja 5.000.000,00 D; Banco 500.000.000,00 D; Préstamo Bancario
    20.000.000,00 H; Interés a Pagar 2.608.219,18 H; Capital 482.391.780,82 H.
  2 Compra: Mercaderías 95.000.000,00 D; IVA CF 9.500.000,00 D;
    Documentos a Pagar 104.500.000,00 H.
  3 Venta: Banco 125.500.000,00 D; Venta de Mercaderías 114.090.909,09 H;
    IVA DF 11.409.090,91 H.
  4 Costo: Costo de Ventas 76.818.181,82 D; Mercaderías 76.818.181,82 H
    (8 × 9.090.909,09 + 5 × 818.181,82).
  5 Salarios: Sueldos y Jornales 12.400.000,00 D; Aporte Patronal
    2.046.000,00 D; IPS a Pagar 3.162.000,00 H; Banco 11.284.000,00 H.

LÍMITE
No asumís el rol de contador que firma declaraciones juradas ni emitís
conceptos vinculantes: sos una guía de estudio.
"""

#: Encabezado del CSV de asientos que entiende `erp.importacion`.
ENCABEZADO_CSV_ASIENTOS = "asiento;fecha;concepto;cuenta;detalle;debe;haber"


def contexto_empresa(cuentas: list[dict], hoy: str) -> str:
    """
    Arma el bloque de contexto que se agrega al prompt de sistema: la fecha
    de hoy y las cuentas de detalle activas, que son las únicas en las que el
    modelo puede proponer movimientos.
    """
    lineas = [
        f"- {c['codigo']} · {c['nombre']}"
        for c in cuentas
        if c.get("recibe_movimiento") and c.get("estado") == "ACTIVA"
    ]
    plan = "\n".join(lineas) if lineas else (
        "(El plan de cuentas está vacío: si te piden asientos, pedí que "
        "primero carguen el plan de cuentas.)"
    )
    return (
        f"\nCONTEXTO DE LA EMPRESA\nFecha de hoy: {hoy}\n"
        f"Cuentas de detalle activas (código · nombre):\n{plan}\n"
    )


#: Bloque ```csv … ``` (o sin etiqueta, si empieza con el encabezado).
_PATRON_BLOQUE_CSV = re.compile(r"```[ \t]*(csv)?[ \t]*\r?\n(.*?)```", re.S | re.I)


_PATRON_FECHA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def revisar_asientos(borrador: str, csv_asientos: str | None) -> list[str]:
    """
    Control de completitud de los asientos que propuso el modelo.

    El modelo a veces se saltea una operación de un borrador largo. Acá se
    revisa, sin IA, que:

    * cada fecha que aparece en el borrador tenga al menos un asiento, y
    * cada fecha con una venta (cuenta 4 al Haber) tenga también su asiento
      de costo (cuenta 5 al Debe), como pide la cátedra, y
    * cada asiento cuadre (total Debe = total Haber).

    Devuelve la lista de avisos; vacía si no se detectó nada.
    """
    if not csv_asientos:
        return []

    fechas_borrador: list[str] = []
    for dia, mes, anio in _PATRON_FECHA.findall(borrador or ""):
        try:
            iso = date(int(anio), int(mes), int(dia)).isoformat()
        except ValueError:
            continue
        if iso not in fechas_borrador:
            fechas_borrador.append(iso)

    from .dinero import ErrorMonto, a_centavos, formato_monto

    fechas_csv: set[str] = set()
    ventas: set[str] = set()
    costos: set[str] = set()
    sumas: dict[str, list[int]] = {}
    for linea in csv_asientos.splitlines()[1:]:
        celdas = (linea.split(";") + [""] * 7)[:7]
        fecha, cuenta, debe, haber = celdas[1].strip(), celdas[3].strip(), celdas[5], celdas[6]
        fechas_csv.add(fecha)
        try:
            suma = sumas.setdefault(celdas[0].strip(), [0, 0])
            suma[0] += a_centavos(debe)
            suma[1] += a_centavos(haber)
        except ErrorMonto:
            pass
        if cuenta.startswith("4") and haber.strip():
            ventas.add(fecha)
        if cuenta.startswith("5") and debe.strip():
            costos.add(fecha)

    def corta(iso: str) -> str:
        return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"

    avisos: list[str] = []
    if len(fechas_borrador) >= 2:
        for iso in fechas_borrador:
            if iso not in fechas_csv:
                avisos.append(f"Falta el asiento de la operación del {corta(iso)}.")
    for iso in sorted(ventas - costos):
        avisos.append(
            f"La venta del {corta(iso)} no tiene su asiento de costo de ventas."
        )
    for numero, (debe, haber) in sumas.items():
        if debe != haber:
            avisos.append(
                f"El asiento {numero} no cuadra: Debe {formato_monto(debe, False)} "
                f"y Haber {formato_monto(haber, False)} (diferencia "
                f"{formato_monto(abs(debe - haber), False)}); falta o sobra una línea."
            )
    return avisos


def extraer_csv_asientos(texto: str) -> str | None:
    """
    Devuelve el CSV de asientos que el modelo escribió en la respuesta, o
    ``None`` si no hay ninguno. Si hay varios bloques, se unen (sin repetir
    el encabezado) para importarlos juntos.
    """
    filas: list[str] = []
    for coincidencia in _PATRON_BLOQUE_CSV.finditer(texto or ""):
        contenido = [l.strip() for l in coincidencia.group(2).strip().splitlines()]
        contenido = [l for l in contenido if l]
        if not contenido:
            continue
        cabecera = contenido[0].lower().replace(" ", "")
        if not (cabecera.startswith("asiento;") and ";debe;haber" in cabecera):
            continue
        filas.extend(contenido[1:])
    if not filas:
        return None
    return ENCABEZADO_CSV_ASIENTOS + "\n" + "\n".join(filas) + "\n"


# ---------------------------------------------------------------------------
# Proveedores
# ---------------------------------------------------------------------------

#: Endpoint y modelo principal (Groq). La lista permite degradar a otro
#: modelo si el catálogo de la cuenta cambia o si uno está saturado.
PROVEEDOR_GROQ = {
    "nombre": "groq",
    "url": "https://api.groq.com/openai/v1/chat/completions",
    "modelos": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"],
    "clave": "GROQ_API_KEY",
    # Los dos modelos razonan antes de responder, así que el presupuesto
    # tiene que cubrir los tokens de razonamiento además de la respuesta.
    "tokens": 8000,
    "espera": 20,
}

#: Respaldo (Google Gemini).
PROVEEDOR_GEMINI = {
    "nombre": "gemini",
    "url": ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{modelo}:generateContent"),
    # gemini-2.5-flash devuelve 404 para cuentas nuevas, así que se usan
    # modelos vigentes. El segundo es el más liviano: sirve para cuando el
    # primero está saturado («high demand», 503), que es un fallo pasajero.
    "modelos": ["gemini-3.5-flash", "gemini-flash-lite-latest",
                "gemini-3.1-flash-lite"],
    "clave": "GEMINI_API_KEY",
    # Estos modelos razonan antes de responder: en una prueba gastaron 476
    # tokens de pensamiento antes de la primera palabra. Con un presupuesto
    # chico la respuesta vuelve vacía o cortada.
    "tokens": 12000,
    "espera": 35,
}

#: Orden de intento: primero el principal, luego el respaldo.
PROVEEDORES = (PROVEEDOR_GROQ, PROVEEDOR_GEMINI)


class ErrorAsistente(ErrorOperacionInvalida):
    """No se pudo obtener respuesta de ningún proveedor."""


#: Tope por archivo adjunto. El navegador ya recorta en 8 MB; acá se vuelve a
#: comprobar porque el servidor no puede confiar en lo que le mandan.
MAXIMO_ARCHIVO_BYTES = 8 * 1024 * 1024

#: Cuánto texto de un archivo se mete en el mensaje. Un CSV enorme no debe
#: viajar entero en cada consulta: se recorta y se le avisa al modelo.
MAXIMO_TEXTO_ARCHIVO = 100_000


# ---------------------------------------------------------------------------
# Llamadas HTTP (solo biblioteca estándar)
# ---------------------------------------------------------------------------

#: Cloudflare (403 «error code: 1010») rechaza la firma por defecto de
#: urllib.request. Con un User-Agent propio la petición pasa igual.
USER_AGENT = "python-erp-contable/1.0"


def _pedir(url: str, cuerpo: dict, *, encabezados: dict, espera: int) -> dict:
    """POST JSON a `url`. Lanza excepción ante cualquier fallo HTTP o de red.

    Se propaga el error sin envolverlo: quien decide si reintenta con otro
    proveedor es `preguntar`.
    """
    datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
    peticion = urllib.request.Request(
        url, data=datos, method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            **encabezados,
        },
    )
    with urllib.request.urlopen(peticion, timeout=espera) as respuesta:
        texto = respuesta.read().decode("utf-8")
    return json.loads(texto)


def _llamar_groq(proveedor: dict, clave: str, mensajes: list[dict]) -> dict:
    """Respuesta en formato OpenAI (que Groq implementa)."""
    ultimo_error: Exception | None = None

    for modelo in proveedor["modelos"]:
        try:
            r = _pedir(
                proveedor["url"],
                {
                    "model": modelo,
                    "messages": mensajes,
                    "temperature": 0.2,
                    "max_tokens": proveedor["tokens"],
                },
                encabezados={"Authorization": f"Bearer {clave}"},
                espera=proveedor["espera"],
            )
            eleccion = (r.get("choices") or [{}])[0]
            texto = (eleccion.get("message") or {}).get("content") or ""
            if not texto.strip():
                raise ValueError("el modelo devolvió una respuesta vacía")
            return {"texto": texto.strip(), "modelo": modelo}
        except Exception as exc:  # noqa: BLE001 - se prueba el siguiente modelo
            ultimo_error = exc

    raise ultimo_error if ultimo_error else ErrorAsistente("Groq no respondió.")


def _llamar_gemini(proveedor: dict, clave: str, mensajes: list[dict]) -> dict:
    """Respuesta en formato Gemini: el historial va como contents/parts."""
    ultimo_error: Exception | None = None

    def _partes(mensaje: dict) -> list[dict]:
        """Convierte el contenido de un mensaje a `parts` de Gemini.

        Puede ser texto simple o una lista de partes multimodales (texto e
        imágenes en base64), que es como llegan los archivos adjuntos.
        """
        contenido = mensaje["content"]
        if isinstance(contenido, str):
            return [{"text": contenido}]
        partes: list[dict] = []
        for parte in contenido:
            if parte.get("type") == "text":
                partes.append({"text": parte.get("text", "")})
            elif parte.get("type") == "image_url":
                url = (parte.get("image_url") or {}).get("url", "")
                # La URL viene como `data:image/png;base64,AAAA…`.
                if "," in url:
                    cabecera, datos = url.split(",", 1)
                    tipo = cabecera.split(";")[0].removeprefix("data:") or "image/png"
                    partes.append({"inlineData": {"mimeType": tipo, "data": datos}})
        return partes

    # El prompt de sistema (con el contexto de la empresa) es el primer
    # mensaje; Gemini lo recibe aparte, como systemInstruction.
    sistema = next(
        (m["content"] for m in mensajes if m["role"] == "system"),
        INSTRUCCION_SISTEMA,
    )
    contenido = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": _partes(m)}
        for m in mensajes if m["role"] in {"user", "assistant"}
    ]

    for modelo in proveedor["modelos"]:
        try:
            r = _pedir(
                proveedor["url"].format(modelo=modelo),
                {
                    "contents": contenido,
                    # El prompt de sistema es un systemInstruction aparte.
                    "systemInstruction": {"parts": [{"text": sistema}]},
                    "generationConfig": {
                        "temperature": 0.2,
                        "maxOutputTokens": proveedor["tokens"],
                    },
                },
                encabezados={"x-goog-api-key": clave},
                espera=proveedor["espera"],
            )
            partes = ((r.get("candidates") or [{}])[0]
                      .get("content", {}).get("parts") or [])
            texto = "".join(p.get("text", "") for p in partes)
            if not texto.strip():
                raise ValueError("el modelo devolvió una respuesta vacía")
            return {"texto": texto.strip(), "modelo": modelo}
        except Exception as exc:  # noqa: BLE001 - se prueba el siguiente modelo
            ultimo_error = exc

    raise ultimo_error if ultimo_error else ErrorAsistente("Gemini no respondió.")


#: Llamada concreta de cada proveedor, por nombre.
_LLAMADORES = {
    "groq": _llamar_groq,
    "gemini": _llamar_gemini,
}


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def esta_configurado() -> bool:
    """True si hay al menos una clave de API en el entorno."""
    return any(os.environ.get(p["clave"], "").strip() for p in PROVEEDORES)


def proveedores_disponibles() -> list[dict]:
    """Estado de cada proveedor, para mostrarlo en la interfaz."""
    return [
        {
            "nombre": p["nombre"],
            "configurado": bool(os.environ.get(p["clave"], "").strip()),
            "principal": p is PROVEEDORES[0],
        }
        for p in PROVEEDORES
    ]


def _decodificar_archivo(archivo: dict) -> bytes:
    """
    Devuelve los bytes de un archivo recibido del navegador.

    El navegador manda el contenido en base64; acá se valida y se decodifica.
    Lanza ``ErrorAsistente`` si el archivo está mal formado o es demasiado
    grande, para no gastar una consulta con algo que no se puede leer.
    """
    import base64

    contenido = archivo.get("contenido") or ""
    if not isinstance(contenido, str) or not contenido.strip():
        raise ErrorAsistente(
            f"El archivo «{archivo.get('nombre', '?')}» llegó vacío."
        )
    try:
        datos = base64.b64decode(contenido, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ErrorAsistente(
            f"No se pudo decodificar «{archivo.get('nombre', '?')}»."
        ) from exc
    if len(datos) > MAXIMO_ARCHIVO_BYTES:
        raise ErrorAsistente(
            f"«{archivo.get('nombre', '?')}» supera el máximo de "
            f"{MAXIMO_ARCHIVO_BYTES // (1024 * 1024)} MB."
        )
    return datos


def _texto_de_archivo(archivo: dict, datos: bytes) -> str | None:
    """
    Extrae el texto de un archivo, si es de un tipo que se pueda leer.

    Devuelve ``None`` cuando el archivo no es texto (por ejemplo una imagen):
    en ese caso se manda como imagen al modelo, no como texto.
    """
    nombre = str(archivo.get("nombre", "")).lower()
    tipo = str(archivo.get("tipo", "")).lower()

    if tipo.startswith("image/"):
        return None

    if nombre.endswith(".xlsx") or "spreadsheetml" in tipo:
        from . import excel
        from .excepciones import ErrorERP

        try:
            texto = excel.a_csv(datos)
        except ErrorERP:
            return None
        if len(texto) > MAXIMO_TEXTO_ARCHIVO:
            texto = texto[:MAXIMO_TEXTO_ARCHIVO] + "\n… (archivo recortado)"
        return f"(Planilla de Excel convertida a CSV, primera hoja)\n{texto}"

    extensiones_texto = (".txt", ".csv", ".json", ".md", ".log", ".tsv", ".xml")
    if not (tipo.startswith("text/") or tipo in {"application/json", "application/csv"}
            or nombre.endswith(extensiones_texto)):
        return None

    for codificacion in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            texto = datos.decode(codificacion)
            # Un tope generoso: el archivo entero no debería viajar en cada
            # consulta. Si es más largo, se recorta y se avisa al modelo.
            if len(texto) > MAXIMO_TEXTO_ARCHIVO:
                texto = texto[:MAXIMO_TEXTO_ARCHIVO] + "\n… (archivo recortado)"
            return texto
        except UnicodeDecodeError:
            continue
    return None


def _bloque_archivos(archivos: list[dict] | None) -> tuple[str, list[dict]]:
    """
    Separa los adjuntos en texto y en imágenes.

    Devuelve ``(texto_para_el_prompt, imagenes)``. El texto se agrega al
    mensaje del usuario con una cabecera por archivo, así el modelo sabe de
    dónde salió cada cosa. Las imágenes se devuelven aparte para mandarlas
    en el formato multimodal de cada proveedor.
    """
    if not archivos:
        return "", []

    partes_texto: list[str] = []
    imagenes: list[dict] = []

    for archivo in archivos:
        if not isinstance(archivo, dict):
            continue
        datos = _decodificar_archivo(archivo)
        nombre = str(archivo.get("nombre", "archivo"))
        tipo = str(archivo.get("tipo", "")).lower()

        texto = _texto_de_archivo(archivo, datos)
        if texto is None and tipo.startswith("image/"):
            imagenes.append({
                "nombre": nombre,
                "tipo": tipo or "image/png",
                "contenido": archivo.get("contenido", ""),
            })
            continue
        if texto is None:
            # Formato que no se puede leer (por ejemplo un PDF sin extractor
            # de texto): se avisa en vez de mandarlo como basura binaria.
            partes_texto.append(
                f"### Archivo adjunto: {nombre}\n"
                f"(No pude leer este archivo: el formato «{tipo or 'desconocido'}» "
                f"no se puede procesar. Si necesitás que lo analice, pasalo como "
                f"imagen, CSV, TXT o JSON.)"
            )
            continue

        partes_texto.append(
            f"### Archivo adjunto: {nombre}\n```\n{texto}\n```"
        )

    return "\n\n".join(partes_texto), imagenes


def preguntar(
    pregunta: str,
    historial: list[dict] | None = None,
    archivos: list[dict] | None = None,
    contexto: str = "",
) -> dict:
    """
    Envía la consulta al asistente e intenta los proveedores en orden.

    ``archivos`` es una lista de adjuntos ``{nombre, tipo, contenido}``, con el
    contenido en base64: imágenes (capturas, fotos de balances), CSV, TXT y
    JSON. Las imágenes viajan al modelo con visión; el texto se agrega al
    mensaje como contexto.

    ``contexto`` se agrega al prompt de sistema (ver ``contexto_empresa``):
    la fecha de hoy y el plan de cuentas, para que los asientos que proponga
    el modelo usen códigos reales.

    Devuelve ``{respuesta, asientos_csv, proveedor, modelo, tiempo_ms,
    intentos}``, donde ``asientos_csv`` es el CSV de asientos que trae la
    respuesta (o ``None``) y
    ``intentos`` lista qué probó y qué pasó en cada caso, para poder
    mostrar al usuario que hubo un respaldo.

    Lanza ``ErrorAsistente`` si no hay ninguna clave configurada o si
    fallan todos los proveedores.
    """
    pregunta = (pregunta or "").strip()
    texto_archivos, imagenes = _bloque_archivos(archivos)
    if not pregunta and not texto_archivos and not imagenes:
        raise ErrorAsistente("La pregunta no puede estar vacía.")
    if len(pregunta) > 4000:
        raise ErrorAsistente("La pregunta es demasiado larga (máximo 4000 caracteres).")

    mensajes: list[dict] = [
        {"role": "system", "content": INSTRUCCION_SISTEMA + (contexto or "")}
    ]
    for mensaje in (historial or [])[-10:]:
        if mensaje.get("role") in {"user", "assistant"} and mensaje.get("content"):
            mensajes.append(
                {"role": mensaje["role"], "content": str(mensaje["content"])[:4000]}
            )

    # El mensaje del usuario se arma con la pregunta y los archivos de texto.
    # Si además hay imágenes, se usa el formato multimodal: una lista de
    # partes, donde cada imagen viaja en base64.
    cuerpo = pregunta or "Analizá el archivo adjunto."
    if texto_archivos:
        cuerpo = f"{cuerpo}\n\n{texto_archivos}"
    if imagenes:
        partes: list[dict] = [{"type": "text", "text": cuerpo}]
        for imagen in imagenes:
            partes.append({
                "type": "image_url",
                "image_url": {"url": f"data:{imagen['tipo']};base64,{imagen['contenido']}"},
            })
        mensajes.append({"role": "user", "content": partes})
    else:
        mensajes.append({"role": "user", "content": cuerpo})

    intentos: list[dict] = []
    empezado = time.monotonic()

    for proveedor in PROVEEDORES:
        clave = os.environ.get(proveedor["clave"], "").strip()
        if not clave:
            intentos.append({
                "proveedor": proveedor["nombre"],
                "estado": "omitido",
                "detalle": f"falta la variable {proveedor['clave']}",
            })
            continue

        llamador = _LLAMADORES[proveedor["nombre"]]
        try:
            r = llamador(proveedor, clave, mensajes)
        except Exception as exc:  # noqa: BLE001 - ése es justamente el failover
            intentos.append({
                "proveedor": proveedor["nombre"],
                "estado": "falló",
                "detalle": _detalle_fallo(exc),
            })
            continue

        intentos.append({
            "proveedor": proveedor["nombre"],
            "estado": "respondió",
            "detalle": r["modelo"],
        })

        # Si propuso asientos, se controla que no falte ninguna operación del
        # borrador. Ante un faltante se le pide una sola vez que complete.
        borrador = f"{pregunta}\n{texto_archivos}"
        csv_asientos = extraer_csv_asientos(r["texto"])
        avisos = revisar_asientos(borrador, csv_asientos)
        if avisos:
            try:
                r2 = llamador(proveedor, clave, mensajes + [
                    {"role": "assistant", "content": r["texto"]},
                    {"role": "user", "content": (
                        "Revisé tu propuesta y está incompleta:\n- "
                        + "\n- ".join(avisos)
                        + "\nVolvé a escribir la respuesta con el bloque csv "
                        "COMPLETO: todos los asientos del borrador, numerados "
                        "de nuevo. Si falta una cuenta en el plan, decí cuál "
                        "hay que crear."
                    )},
                ])
                csv2 = extraer_csv_asientos(r2["texto"])
                avisos2 = revisar_asientos(borrador, csv2)
                if csv2 and len(avisos2) < len(avisos):
                    r, csv_asientos, avisos = r2, csv2, avisos2
            except Exception:  # noqa: BLE001 - queda la primera propuesta, con avisos
                pass

        return {
            "respuesta": r["texto"],
            "asientos_csv": csv_asientos,
            "avisos": avisos,
            "proveedor": proveedor["nombre"],
            "modelo": r["modelo"],
            "tiempo_ms": int((time.monotonic() - empezado) * 1000),
            "intentos": intentos,
        }

    fallidos = [i["detalle"] for i in intentos if i["estado"] == "falló"]
    if not any(i["estado"] == "respondió" for i in intentos) and not fallidos:
        raise ErrorAsistente(
            "El asistente no está configurado: faltan las claves de API en "
            "GROQ_API_KEY y GEMINI_API_KEY."
        )
    raise ErrorAsistente(
        "El asistente no está disponible: fallaron todos los proveedores. "
        + " | ".join(fallidos)
    )


def _detalle_fallo(exc: Exception) -> str:
    """Texto corto y legible para el registro de intentos."""
    if isinstance(exc, urllib.error.HTTPError):
        cuerpo = ""
        try:
            cuerpo = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 - no importa el detalle
            pass
        return f"HTTP {exc.code}: {cuerpo or exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"sin conexión: {exc.reason}"
    if isinstance(exc, TimeoutError):
        return "tiempo de espera agotado"
    return f"{type(exc).__name__}: {exc}"
