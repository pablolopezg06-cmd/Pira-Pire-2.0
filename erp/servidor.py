"""
Módulo: servidor
================

Servidor web del Mini ERP Contable.

Expone:
  · Una API JSON en ``/api/...`` que usa la interfaz.
  · Los archivos estáticos de la carpeta ``web/``.

Sólo se usa la biblioteca estándar de Python (``http.server``), sin
dependencias externas. Al ser una herramienta de estudio, escucha
únicamente en ``127.0.0.1``.

Uso::

    python3 main.py            # http://127.0.0.1:8000
    python3 main.py --puerto 9000 --datos mi_base.db
"""

from __future__ import annotations

import json
import mimetypes
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import asistente
from . import balance as modulo_balance
from . import base_datos
from . import iva
from . import libro_diario
from . import libro_mayor
from . import plan_de_cuentas
from . import excel
from . import exportacion
from . import importacion
from . import validaciones
from .dinero import formato_monto
from .dinero import ErrorMonto
from .excepciones import (
    ErrorERP,
    ErrorNoEncontrado,
    ErrorOperacionInvalida,
    ErrorValidacion,
)

#: Carpeta con los archivos de la interfaz web.
RAIZ_WEB = Path(__file__).resolve().parent.parent / "web"

#: Tamaño máximo admitido para el cuerpo de una petición.
TAMANO_MAXIMO_CUERPO = 3 * 1024 * 1024


# ---------------------------------------------------------------------------
# Tabla de rutas
# ---------------------------------------------------------------------------
# Cada ruta es (método, patrón con grupos de captura, nombre del manejador).

RUTAS: list[tuple[str, re.Pattern, str]] = [
    # -- Empresa ---------------------------------------------------------
    ("GET",    r"^/api/empresa$",                                "empresa_obtener"),
    ("PUT",    r"^/api/empresa$",                                "empresa_guardar"),
    ("GET",    r"^/api/resumen$",                                "resumen"),

    # -- Plan de cuentas (sección 13) ------------------------------------
    ("GET",    r"^/api/cuentas$",                                "cuentas_listar"),
    ("POST",   r"^/api/cuentas$",                                "cuentas_crear"),
    ("GET",    r"^/api/cuentas/(?P<codigo>[^/]+)$",              "cuentas_obtener"),
    ("PUT",    r"^/api/cuentas/(?P<codigo>[^/]+)$",              "cuentas_actualizar"),
    ("DELETE", r"^/api/cuentas/(?P<codigo>[^/]+)$",              "cuentas_eliminar"),
    ("POST",   r"^/api/cuentas/(?P<codigo>[^/]+)/estado$",       "cuentas_estado"),
    ("GET",    r"^/api/cuentas/(?P<codigo>[^/]+)/mayor$",        "cuentas_mayor"),

    # -- Libro Diario (sección 14) --------------------------------------
    ("GET",    r"^/api/asientos$",                               "asientos_listar"),
    ("POST",   r"^/api/asientos$",                               "asientos_crear"),
    ("GET",    r"^/api/asientos/(?P<asiento_id>\d+)$",           "asientos_obtener"),
    ("PUT",    r"^/api/asientos/(?P<asiento_id>\d+)$",           "asientos_actualizar"),
    ("DELETE", r"^/api/asientos/(?P<asiento_id>\d+)$",           "asientos_borrar"),
    ("POST",   r"^/api/asientos/(?P<asiento_id>\d+)/confirmar$", "asientos_confirmar"),
    ("POST",   r"^/api/asientos/(?P<asiento_id>\d+)/anular$",    "asientos_anular"),
    ("GET",    r"^/api/verificacion/cuadre$",                    "diario_verificar"),

    # -- Libro Mayor (sección 15) ----------------------------------------
    ("GET",    r"^/api/libro-mayor$",                            "mayor_completo"),

    # -- Balance de comprobación (sección 16) ----------------------------
    ("GET",    r"^/api/balance$",                                "balance_generar"),
    ("GET",    r"^/api/balance/cruce$",                          "balance_cruce"),

    # -- Controles (sección 18) ------------------------------------------
    ("GET",    r"^/api/controles$",                              "controles_listar"),

    # -- IVA de Paraguay (Ley N.º 1259/97) --------------------------------
    ("GET",    r"^/api/iva/alicuotas$",                          "iva_alicuotas"),
    ("POST",   r"^/api/iva/calcular$",                           "iva_calcular"),
    ("POST",   r"^/api/iva/asiento$",                            "iva_asiento"),
    ("GET",    r"^/api/iva/resumen$",                         "iva_resumen"),
    ("POST",   r"^/api/iva/liquidar$",                           "iva_liquidar"),

    # -- Asistente virtual (Groq con respaldo en Gemini) -----------------
    ("GET",    r"^/api/asistente/configuracion$",                "asistente_configuracion"),
    ("POST",   r"^/api/asistente$",                              "asistente_preguntar"),

    # -- Utilidades -------------------------------------------------------
    ("POST",   r"^/api/importar/cuentas$",                         "importar_cuentas"),
    ("POST",   r"^/api/importar/asientos$",                        "importar_asientos"),
    ("GET",    r"^/api/exportar/(?P<tipo>cuentas|asientos)$",     "exportar"),
    ("GET",    r"^/api/plantillas/(?P<tipo>cuentas|asientos)\.xlsx$", "plantilla_xlsx"),
    ("POST",   r"^/api/excel/desde-csv$",                         "excel_desde_csv"),
    ("POST",   r"^/api/datos-demo$",                              "datos_demo"),
    ("POST",   r"^/api/reiniciar$",                              "reiniciar"),
]

#: Los patrones se compilan una sola vez al importar el módulo.
RUTAS = [(metodo, re.compile(patron), manejador) for metodo, patron, manejador in RUTAS]


class Manejador(BaseHTTPRequestHandler):
    """Traduce peticiones HTTP a llamadas de los módulos contables."""

    server_version = "MiniERPContable/1.0"

    # -- utilidades de respuesta ----------------------------------------

    def _enviar_json(self, datos, codigo: int = 200) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _enviar_archivo(self, contenido: bytes, tipo: str, nombre: str) -> None:
        """Respuesta binaria que el navegador guarda como archivo."""
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(contenido)))
        self.send_header("Content-Disposition", f'attachment; filename="{nombre}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(contenido)

    def _enviar_error(self, excepcion: Exception) -> None:
        """Traduce una excepción del dominio a una respuesta HTTP con sentido."""
        if isinstance(excepcion, ErrorValidacion):
            self._enviar_json(excepcion.como_diccionario(), 422)
        elif isinstance(excepcion, ErrorMonto):
            # Un importe mal escrito es un error de validación (422), no un
            # fallo interno: sin esta rama el servidor respondería 500.
            self._enviar_json(
                {
                    "error": str(excepcion),
                    "regla": None,
                    "titulo_regla": "Importe inválido",
                    "detalles": [],
                },
                422,
            )
        elif isinstance(excepcion, ErrorNoEncontrado):
            self._enviar_json({"error": str(excepcion), "detalles": []}, 404)
        elif isinstance(excepcion, ErrorOperacionInvalida):
            self._enviar_json(
                {"error": str(excepcion), "titulo_regla": "Operación no permitida",
                 "detalles": []},
                409,
            )
        elif isinstance(excepcion, ErrorERP):
            self._enviar_json({"error": str(excepcion), "detalles": []}, 400)
        else:
            traceback.print_exc()
            self._enviar_json(
                {"error": f"Error interno: {excepcion}", "detalles": []}, 500
            )

    def _leer_cuerpo(self) -> dict:
        longitud = int(self.headers.get("Content-Length") or 0)
        if longitud <= 0:
            return {}
        if longitud > TAMANO_MAXIMO_CUERPO:
            raise ErrorValidacion("El cuerpo de la petición es demasiado grande.")
        crudo = self.rfile.read(longitud)
        try:
            return json.loads(crudo.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ErrorValidacion(f"El cuerpo no es un JSON válido: {exc}") from exc

    def _parametros(self) -> dict:
        consulta = parse_qs(urlparse(self.path).query)
        # Se devuelven valores simples (no listas) para simplificar el uso.
        return {clave: valores[0] for clave, valores in consulta.items()}

    # -- despacho ---------------------------------------------------------

    def do_GET(self) -> None:
        self._despachar("GET")

    def do_POST(self) -> None:
        self._despachar("POST")

    def do_PUT(self) -> None:
        self._despachar("PUT")

    def do_DELETE(self) -> None:
        self._despachar("DELETE")

    def _despachar(self, metodo: str) -> None:
        ruta = unquote(urlparse(self.path).path).rstrip("/") or "/"

        if not ruta.startswith("/api"):
            if metodo == "GET":
                self._servir_estatico(ruta)
            else:
                self._enviar_error(ErrorOperacionInvalida("Método no permitido en esa ruta."))
            return

        manejador = None
        argumentos: dict = {}

        for metodo_ruta, patron, nombre in RUTAS:
            coincidencia = patron.match(ruta)
            if coincidencia and metodo_ruta == metodo:
                manejador = getattr(self, f"_manejar_{nombre}", None)
                argumentos = {
                    clave: (unquote(valor) if isinstance(valor, str) else valor)
                    for clave, valor in coincidencia.groupdict().items()
                }
                break

        if manejador is None:
            self._enviar_json(
                {"error": f"No existe el recurso {metodo} {ruta}", "detalles": []}, 404
            )
            return

        try:
            manejador(argumentos)
        except Exception as exc:  # noqa: BLE001 - se traduce abajo
            self._enviar_error(exc)

    # -- archivos estáticos -----------------------------------------------

    def _servir_estatico(self, ruta: str) -> None:
        relativa = "index.html" if ruta == "/" else ruta.lstrip("/")
        destino = (RAIZ_WEB / relativa).resolve()

        # Protección contra recorridos fuera de la carpeta web.
        if not str(destino).startswith(str(RAIZ_WEB.resolve())) or not destino.is_file():
            destino = RAIZ_WEB / "index.html"
            if not destino.is_file():
                self._enviar_json({"error": "No se encontró la interfaz web."}, 404)
                return

        tipo = mimetypes.guess_type(str(destino))[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in {"application/javascript", "application/json"}:
            tipo += "; charset=utf-8"

        contenido = destino.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(contenido)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(contenido)

    # ------------------------------------------------------------------
    # Manejadores: Empresa
    # ------------------------------------------------------------------

    def _manejar_empresa_obtener(self, _: dict) -> None:
        with base_datos.conectar(self.ruta_base) as conexion:
            base_datos.asegurar_empresa(conexion)
            fila = conexion.execute("SELECT * FROM empresa WHERE id = 1").fetchone()
        self._enviar_json(dict(fila))

    def _manejar_empresa_guardar(self, _: dict) -> None:
        datos = self._leer_cuerpo()

        campos = {
            "nombre": str(datos.get("nombre") or "").strip(),
            "cuit": str(datos.get("cuit") or "").strip(),
            "actividad": str(datos.get("actividad") or "").strip(),
            "ejercicio_inicio": str(datos.get("ejercicio_inicio") or "").strip(),
            "ejercicio_fin": str(datos.get("ejercicio_fin") or "").strip(),
            "moneda": str(datos.get("moneda") or "G.").strip() or "G.",
        }
        if not campos["nombre"]:
            raise ErrorValidacion("El nombre de la empresa es obligatorio.")

        with base_datos.conectar(self.ruta_base) as conexion:
            base_datos.asegurar_empresa(conexion)
            conexion.execute(
                """UPDATE empresa
                      SET nombre = ?, cuit = ?, actividad = ?,
                          ejercicio_inicio = ?, ejercicio_fin = ?, moneda = ?
                    WHERE id = 1""",
                tuple(campos.values()),
            )
            fila = conexion.execute("SELECT * FROM empresa WHERE id = 1").fetchone()
        self._enviar_json(dict(fila))

    def _manejar_resumen(self, _: dict) -> None:
        """Indicadores del panel: totales, cantidad de asientos y controles."""
        cuadre = libro_diario.verificar_cuadre(self.ruta_base)
        balance = modulo_balance.balance_comprobacion(self.ruta_base)

        with base_datos.conectar(self.ruta_base) as conexion:
            cuentas = conexion.execute(
                "SELECT COUNT(*) AS total FROM cuenta"
            ).fetchone()["total"]
            cuentas_detalle = conexion.execute(
                "SELECT COUNT(*) AS total FROM cuenta WHERE recibe_movimiento = 1"
            ).fetchone()["total"]
            empresa = conexion.execute("SELECT * FROM empresa WHERE id = 1").fetchone()

        return self._enviar_json(
            {
                "empresa": dict(empresa) if empresa else base_datos.empresa_por_defecto(),
                "cuadre_libro_diario": cuadre,
                "totales_balance": balance["totales"],
                "balance_cuadrado": balance["cuadrado"],
                "verificaciones": balance["verificaciones"],
                "cuentas": int(cuentas),
                "cuentas_detalle": int(cuentas_detalle),
                "moneda": (dict(empresa)["moneda"] if empresa else "G."),
            }
        )

    # ------------------------------------------------------------------
    # Manejadores: Plan de cuentas
    # ------------------------------------------------------------------

    def _manejar_cuentas_listar(self, _: dict) -> None:
        parametros = self._parametros()
        cuentas = plan_de_cuentas.listar_cuentas(
            self.ruta_base,
            incluir_inactivas=parametros.get("inactivas", "1") != "0",
            solo_detalle=parametros.get("detalle") == "1",
            busqueda=parametros.get("q"),
        )
        self._enviar_json({"cuentas": cuentas, "total": len(cuentas)})

    def _manejar_cuentas_crear(self, _: dict) -> None:
        datos = self._leer_cuerpo()
        cuenta = plan_de_cuentas.crear_cuenta(
            codigo=datos.get("codigo"),
            nombre=datos.get("nombre"),
            clasificacion=datos.get("clasificacion"),
            recibe_movimiento=bool(datos.get("recibe_movimiento", True)),
            naturaleza=datos.get("naturaleza") or None,
            estado=datos.get("estado") or "ACTIVA",
            ruta=self.ruta_base,
        )
        self._enviar_json({"cuenta": cuenta}, 201)

    def _manejar_cuentas_obtener(self, argumentos: dict) -> None:
        cuenta = plan_de_cuentas.obtener_cuenta(argumentos["codigo"], self.ruta_base)
        if cuenta is None:
            raise ErrorNoEncontrado(f"La cuenta '{argumentos['codigo']}' no existe.")
        self._enviar_json({"cuenta": cuenta})

    def _manejar_cuentas_actualizar(self, argumentos: dict) -> None:
        datos = self._leer_cuerpo()
        cuenta = plan_de_cuentas.actualizar_cuenta(
            argumentos["codigo"],
            nombre=datos.get("nombre"),
            clasificacion=datos.get("clasificacion"),
            recibe_movimiento=datos.get("recibe_movimiento"),
            naturaleza=datos.get("naturaleza") or None,
            estado=datos.get("estado"),
            ruta=self.ruta_base,
        )
        self._enviar_json({"cuenta": cuenta})

    def _manejar_cuentas_eliminar(self, argumentos: dict) -> None:
        plan_de_cuentas.eliminar_cuenta(argumentos["codigo"], self.ruta_base)
        self._enviar_json({"eliminado": argumentos["codigo"]})

    def _manejar_cuentas_estado(self, argumentos: dict) -> None:
        datos = self._leer_cuerpo()
        cuenta = plan_de_cuentas.cambiar_estado(
            argumentos["codigo"], str(datos.get("estado") or "").upper(), self.ruta_base
        )
        self._enviar_json({"cuenta": cuenta})

    def _manejar_cuentas_mayor(self, argumentos: dict) -> None:
        parametros = self._parametros()
        self._enviar_json(
            libro_mayor.mayor_de_cuenta(
                argumentos["codigo"],
                self.ruta_base,
                desde=parametros.get("desde"),
                hasta=parametros.get("hasta"),
            )
        )

    # ------------------------------------------------------------------
    # Manejadores: Libro Diario
    # ------------------------------------------------------------------

    def _manejar_asientos_listar(self, _: dict) -> None:
        parametros = self._parametros()
        asientos = libro_diario.listar_asientos(
            self.ruta_base,
            desde=parametros.get("desde"),
            hasta=parametros.get("hasta"),
            estado=parametros.get("estado"),
            busqueda=parametros.get("q"),
        )
        totales = {
            "debe": sum(a["total_debe"] for a in asientos),
            "haber": sum(a["total_haber"] for a in asientos),
        }
        self._enviar_json({"asientos": asientos, "totales": totales, "total": len(asientos)})

    def _manejar_asientos_crear(self, _: dict) -> None:
        datos = self._leer_cuerpo()
        asiento = libro_diario.crear_asiento(
            fecha=datos.get("fecha"),
            concepto=datos.get("concepto"),
            partidas=datos.get("partidas") or [],
            confirmar=bool(datos.get("confirmar", True)),
            observaciones=datos.get("observaciones"),
            ruta=self.ruta_base,
        )
        self._enviar_json({"asiento": asiento}, 201)

    def _manejar_asientos_obtener(self, argumentos: dict) -> None:
        self._enviar_json(
            {"asiento": libro_diario.obtener_asiento(int(argumentos["asiento_id"]), self.ruta_base)}
        )

    def _manejar_asientos_actualizar(self, argumentos: dict) -> None:
        datos = self._leer_cuerpo()
        asiento = libro_diario.actualizar_asiento(
            int(argumentos["asiento_id"]),
            fecha=datos.get("fecha"),
            concepto=datos.get("concepto"),
            partidas=datos.get("partidas"),
            observaciones=datos.get("observaciones"),
            ruta=self.ruta_base,
        )
        self._enviar_json({"asiento": asiento})

    def _manejar_asientos_confirmar(self, argumentos: dict) -> None:
        self._enviar_json(
            {"asiento": libro_diario.confirmar_asiento(int(argumentos["asiento_id"]), self.ruta_base)}
        )

    def _manejar_asientos_anular(self, argumentos: dict) -> None:
        datos = self._leer_cuerpo()
        self._enviar_json(
            {
                "asiento": libro_diario.anular_asiento(
                    int(argumentos["asiento_id"]), datos.get("motivo"), self.ruta_base
                )
            }
        )

    def _manejar_asientos_borrar(self, argumentos: dict) -> None:
        libro_diario.borrar_asiento(int(argumentos["asiento_id"]), self.ruta_base)
        self._enviar_json({"eliminado": argumentos["asiento_id"]})

    def _manejar_diario_verificar(self, _: dict) -> None:
        self._enviar_json(libro_diario.verificar_cuadre(self.ruta_base))

    # ------------------------------------------------------------------
    # Manejadores: Libro Mayor y Balance
    # ------------------------------------------------------------------

    def _manejar_mayor_completo(self, _: dict) -> None:
        parametros = self._parametros()
        self._enviar_json(
            libro_mayor.libro_mayor_completo(
                self.ruta_base,
                desde=parametros.get("desde"),
                hasta=parametros.get("hasta"),
                solo_cuentas_con_movimiento=parametros.get("vacias") != "1",
            )
        )

    def _manejar_balance_generar(self, _: dict) -> None:
        parametros = self._parametros()
        balance = modulo_balance.balance_comprobacion(
            self.ruta_base,
            desde=parametros.get("desde"),
            hasta=parametros.get("hasta"),
            incluir_cuentas_sin_movimiento=parametros.get("vacias") == "1",
            agrupar_por_clasificacion=parametros.get("agrupado", "1") != "0",
        )
        cruce = modulo_balance.cruzar_con_libro_mayor(
            self.ruta_base, desde=parametros.get("desde"), hasta=parametros.get("hasta")
        )
        balance["cruce_libro_mayor"] = cruce
        balance["moneda"] = self._moneda()
        self._enviar_json(balance)

    def _manejar_balance_cruce(self, _: dict) -> None:
        parametros = self._parametros()
        self._enviar_json(
            modulo_balance.cruzar_con_libro_mayor(
                self.ruta_base, desde=parametros.get("desde"), hasta=parametros.get("hasta")
            )
        )

    def _moneda(self) -> str:
        with base_datos.conectar(self.ruta_base) as conexion:
            fila = conexion.execute("SELECT moneda FROM empresa WHERE id = 1").fetchone()
        return fila["moneda"] if fila else "G."

    # ------------------------------------------------------------------
    # Manejadores: controles y utilidades
    # ------------------------------------------------------------------

    def _manejar_controles_listar(self, _: dict) -> None:
        cuadre = libro_diario.verificar_cuadre(self.ruta_base)
        cruce = modulo_balance.cruzar_con_libro_mayor(self.ruta_base)

        # Estado de cada control evaluado en caliente sobre los datos reales.
        estado = {
            "C1": {
                "cumple": cuadre["cuadra"],
                "detalle": (
                    f"{cuadre['asientos_confirmados']} asientos confirmados; "
                    f"total Debe {formato_monto(cuadre['total_debe'])} = "
                    f"total Haber {formato_monto(cuadre['total_haber'])}"
                    + (
                        ""
                        if cuadre["asientos_descuadrados"] == 0
                        else f"; {cuadre['asientos_descuadrados']} descuadrados"
                    )
                ),
            },
            "C2": {
                "cumple": True,
                "detalle": "Validación al crear el asiento y clave foránea en la base de datos.",
            },
            "C3": {
                "cumple": True,
                "detalle": "Código jerárquico validado contra la clasificación y la cuenta padre.",
            },
            "C4": {
                "cumple": True,
                "detalle": "El Libro Mayor se calcula con un SELECT sobre el Libro Diario.",
            },
            "C5": {
                "cumple": cruce["coincide"],
                "detalle": cruce["mensaje"],
            },
            "C6": {
                "cumple": True,
                "detalle": "Naturaleza validada contra la clasificación en cada cuenta.",
            },
        }

        self._enviar_json(
            {
                "controles": [
                    {**regla, **estado.get(regla["codigo"], {})} for regla in validaciones.REGLAS
                ],
                "trazabilidad": validaciones.verificar_trazabilidad(),
                "cuadre_libro_diario": cuadre,
                "cruce": cruce,
            }
        )

    # ------------------------------------------------------------------
    # Manejadores: IVA de Paraguay
    # ------------------------------------------------------------------

    def _manejar_iva_alicuotas(self, _: dict) -> None:
        """Catálogo de alícuotas vigentes, para poblar el selector."""
        self._enviar_json(
            {
                "alicuotas": [
                    {"valor": valor, "etiqueta": etiqueta}
                    for valor, etiqueta in sorted(iva.ALICUOTAS.items(), reverse=True)
                ],
                "general": iva.ALICUOTA_GENERAL,
                "reducida": iva.ALICUOTA_REDUCIDA,
                "exenta": iva.ALICUOTA_EXENTA,
                "marco_legal": "Ley N.º 1259/97 (Arts. 10, 11 y 15 y sgtes.)",
                "cuentas": {
                    "debito_fiscal": iva.CUENTA_DEBITO_FISCAL,
                    "credito_fiscal": iva.CUENTA_CREDITO_FISCAL,
                    "por_pagar": iva.CUENTA_IVA_POR_PAGAR,
                    "a_recuperar": iva.CUENTA_IVA_A_RECUPERAR,
                    "ventas_gravadas": iva.CUENTA_VENTAS_GRAVADAS,
                    "ventas_exentas": iva.CUENTA_VENTAS_EXENTAS,
                },
            }
        )

    def _manejar_iva_calcular(self, _: dict) -> None:
        """
        Calcula el IVA de una operación sin contabilizarla.

        El cuerpo admite ``base`` o ``total`` (excluyentes) y ``alicuota``.
        Sirve para que la interfaz muestre el desglose antes de confirmar.
        """
        cuerpo = self._leer_cuerpo()
        alicuota = cuerpo.get("alicuota", iva.ALICUOTA_GENERAL)
        if cuerpo.get("total") is not None:
            calculo = iva.discriminar(cuerpo["total"], alicuota)
        else:
            calculo = iva.liquidar_venta(base=cuerpo.get("base"), alicuota=alicuota)

        calculo["base_formateado"] = formato_monto(calculo["base"])
        calculo["iva_formateado"] = formato_monto(calculo["iva"])
        calculo["total_formateado"] = formato_monto(calculo["total"])
        self._enviar_json(calculo)

    def _manejar_iva_asiento(self, _: dict) -> None:
        """
        Arma y —si se pide— contabiliza el asiento de una operación con IVA.

        Cuerpo esperado::

            {
              "tipo": "venta" | "compra",
              "fecha": "2026-04-20",
              "concepto": "Venta de mercaderías",
              "cuenta": "1.1.01",          # contrapartida (caja, banco, proveedor)
              "cuenta_ingreso": "4.1.01",  # solo ventas (opcional)
              "alicuota": 10,
              "base": 1000000,             # o "total"
              "confirmar": true
            }
        """
        cuerpo = self._leer_cuerpo()
        tipo = str(cuerpo.get("tipo") or "venta").strip().lower()
        alicuota = cuerpo.get("alicuota", iva.ALICUOTA_GENERAL)
        base = cuerpo.get("base")
        total = cuerpo.get("total")

        if tipo == "venta":
            partidas = iva.partidas_venta(
                cuenta_cobro=cuerpo.get("cuenta") or "",
                base=base,
                total=total,
                alicuota=alicuota,
                cuenta_ingreso=cuerpo.get("cuenta_ingreso"),
                detalle=cuerpo.get("concepto"),
            )
        elif tipo == "compra":
            partidas = iva.partidas_compra(
                cuenta_pago=cuerpo.get("cuenta") or "",
                cuenta_gasto_o_bien=cuerpo.get("cuenta_gasto") or "",
                base=base,
                total=total,
                alicuota=alicuota,
                detalle=cuerpo.get("concepto"),
            )
        else:
            raise ErrorValidacion(
                "El tipo de operación debe ser 'venta' o 'compra'.",
                "IVA",
            )

        calculo = iva.liquidar_venta(
            base=base, total=total, alicuota=alicuota
        )

        # Si no se pide contabilizar, se devuelve solo la previsualización
        # del asiento (útil para el «vista previa» de la interfaz).
        if not cuerpo.get("confirmar", True):
            self._enviar_json(
                {
                    "contabilizado": False,
                    "calculo": {**calculo, "base_formateado": formato_monto(calculo["base"]),
                                "iva_formateado": formato_monto(calculo["iva"]),
                                "total_formateado": formato_monto(calculo["total"])},
                    "partidas": partidas,
                }
            )
            return

        asiento = libro_diario.crear_asiento(
            fecha=cuerpo.get("fecha"),
            concepto=cuerpo.get("concepto") or "Operación con IVA",
            partidas=partidas,
            confirmar=bool(cuerpo.get("confirmar_contable", True)),
            observaciones=f"Generado por el módulo de IVA ({calculo.get('alicuota')}%).",
            ruta=self.ruta_base,
        )
        self._enviar_json(
            {
                "contabilizado": True,
                "calculo": {**calculo, "base_formateado": formato_monto(calculo["base"]),
                            "iva_formateado": formato_monto(calculo["iva"]),
                            "total_formateado": formato_monto(calculo["total"])},
                "asiento": asiento,
            },
            201,
        )

    def _manejar_iva_resumen(self, _: dict) -> None:
        """
        Débito y Crédito Fiscal acumulados en un período (desde/hasta).

        Es la antesala de la liquidación: suma los saldos de las dos
        cuentas de IVA de los asientos CONFIRMADO del rango.
        """
        parametros = self._parametros()
        desde = (parametros.get("desde") or [None])[0]
        hasta = (parametros.get("hasta") or [None])[0]

        with base_datos.conectar(self.ruta_base) as conexion:
            filas = conexion.execute(
                """
                SELECT p.cuenta,
                       COALESCE(SUM(p.debe), 0)  AS debe,
                       COALESCE(SUM(p.haber), 0) AS haber
                  FROM partida p
                  JOIN asiento a ON a.id = p.asiento_id
                 WHERE a.estado = 'CONFIRMADO'
                   AND p.cuenta IN (?, ?)
                   AND (? IS NULL OR a.fecha >= ?)
                   AND (? IS NULL OR a.fecha <= ?)
                 GROUP BY p.cuenta
                """,
                (
                    iva.CUENTA_DEBITO_FISCAL,
                    iva.CUENTA_CREDITO_FISCAL,
                    desde, desde,
                    hasta, hasta,
                ),
            ).fetchall()

        movimientos = {fila["cuenta"]: dict(fila) for fila in filas}
        debito_fila = movimientos.get(iva.CUENTA_DEBITO_FISCAL, {"debe": 0, "haber": 0})
        credito_fila = movimientos.get(iva.CUENTA_CREDITO_FISCAL, {"debe": 0, "haber": 0})

        # El Débito Fiscal es acreedor: su saldo es haber - debe.
        debito = int(debito_fila["haber"]) - int(debito_fila["debe"])
        # El Crédito Fiscal es deudor: su saldo es debe - haber.
        credito = int(credito_fila["debe"]) - int(credito_fila["haber"])

        liquidacion = iva.liquidar_periodo(
            debito_fiscal=max(debito, 0), credito_fiscal=max(credito, 0)
        )

        self._enviar_json(
            {
                "periodo": {"desde": desde, "hasta": hasta},
                "debito_fiscal": debito,
                "credito_fiscal": credito,
                "debito_formateado": formato_monto(debito),
                "credito_formateado": formato_monto(credito),
                "liquidacion": {
                    **liquidacion,
                    "monto_formateado": formato_monto(liquidacion["monto"]),
                },
            }
        )

    def _manejar_iva_liquidar(self, _: dict) -> None:
        """
        Contabiliza el asiento de liquidación del IVA del período.

        Cierra las cuentas de Débito y Crédito Fiscal y deja el saldo en
        «IVA por Pagar» o «IVA a Recuperar», según corresponda.
        """
        cuerpo = self._leer_cuerpo()
        debito = cuerpo.get("debito_fiscal")
        credito = cuerpo.get("credito_fiscal")

        # Si no vienen los saldos, se calculan del período indicado.
        if debito is None and credito is None:
            with base_datos.conectar(self.ruta_base) as conexion:
                filas = conexion.execute(
                    """
                    SELECT p.cuenta,
                           COALESCE(SUM(p.debe), 0)  AS debe,
                           COALESCE(SUM(p.haber), 0) AS haber
                      FROM partida p
                      JOIN asiento a ON a.id = p.asiento_id
                     WHERE a.estado = 'CONFIRMADO'
                       AND p.cuenta IN (?, ?)
                       AND (? IS NULL OR a.fecha >= ?)
                       AND (? IS NULL OR a.fecha <= ?)
                     GROUP BY p.cuenta
                    """,
                    (
                        iva.CUENTA_DEBITO_FISCAL,
                        iva.CUENTA_CREDITO_FISCAL,
                        cuerpo.get("desde"), cuerpo.get("desde"),
                        cuerpo.get("hasta"), cuerpo.get("hasta"),
                    ),
                ).fetchall()
            mov = {f["cuenta"]: dict(f) for f in filas}
            df = mov.get(iva.CUENTA_DEBITO_FISCAL, {"debe": 0, "haber": 0})
            cf = mov.get(iva.CUENTA_CREDITO_FISCAL, {"debe": 0, "haber": 0})
            debito = int(df["haber"]) - int(df["debe"])
            credito = int(cf["debe"]) - int(cf["haber"])

        liquidacion = iva.liquidar_periodo(
            debito_fiscal=int(debito or 0), credito_fiscal=int(credito or 0)
        )

        if liquidacion["situacion"] == "SIN_SALDO":
            self._enviar_json(
                {
                    "contabilizado": False,
                    "motivo": "El Débito y el Crédito Fiscal del período son iguales: no hay nada que liquidar.",
                    "liquidacion": liquidacion,
                }
            )
            return

        asiento = libro_diario.crear_asiento(
            fecha=cuerpo.get("fecha"),
            concepto=cuerpo.get("concepto") or "Liquidación de IVA del período",
            partidas=liquidacion["partidas"],
            confirmar=bool(cuerpo.get("confirmar", True)),
            observaciones="Liquidación de IVA (Ley N.º 1259/97).",
            ruta=self.ruta_base,
        )
        self._enviar_json(
            {
                "contabilizado": True,
                "liquidacion": {
                    **liquidacion,
                    "monto_formateado": formato_monto(liquidacion["monto"]),
                },
                "asiento": asiento,
            },
            201,
        )

    # ------------------------------------------------------------------
    # Manejadores: Asistente virtual
    # ------------------------------------------------------------------

    def _manejar_asistente_configuracion(self, _: dict) -> None:
        """Estado de los proveedores, para informarlo en la interfaz."""
        self._enviar_json(
            {
                "configurado": asistente.esta_configurado(),
                "proveedores": asistente.proveedores_disponibles(),
                "instruccion": asistente.INSTRUCCION_SISTEMA,
            }
        )

    def _manejar_asistente_preguntar(self, _: dict) -> None:
        cuerpo = self._leer_cuerpo()
        from datetime import date

        cuentas = plan_de_cuentas.listar_cuentas(self.ruta_base)
        plantilla = "" if cuentas else (
            RAIZ_WEB / "plantillas" / "cuentas.csv"
        ).read_text(encoding="utf-8")
        contexto = asistente.contexto_empresa(cuentas, date.today().isoformat(), plantilla)
        resultado = asistente.preguntar(
            cuerpo.get("pregunta", ""),
            cuerpo.get("historial"),
            cuerpo.get("archivos"),
            contexto,
        )

        # Cuentas que el modelo usó y que no están en el plan de la empresa:
        # se avisa en la tarjeta para que el usuario las cree antes de importar.
        if resultado.get("asientos_csv"):
            validas = {
                c["codigo"] for c in cuentas
                if c.get("estado") == "ACTIVA" and c.get("recibe_movimiento")
            }
            propuestas = {
                linea.split(";")[0].strip()
                for linea in (resultado.get("cuentas_csv") or "").splitlines()[1:]
            }
            usadas = {
                (linea.split(";") + [""] * 4)[3].strip()
                for linea in resultado["asientos_csv"].splitlines()[1:]
            }

            # Si el modelo usó cuentas de la plantilla de la cátedra que la
            # empresa no tiene y no las propuso, el plan se arma acá (sin IA):
            # esas cuentas más sus cuentas padre que falten.
            existentes = {c["codigo"] for c in cuentas}
            plantilla_filas = [
                linea for linea in (RAIZ_WEB / "plantillas" / "cuentas.csv")
                .read_text(encoding="utf-8").splitlines()[1:] if linea.strip()
            ]
            por_codigo = {linea.split(";")[0]: linea for linea in plantilla_filas}
            sin_plan = (usadas - validas - propuestas) & set(por_codigo)
            if sin_plan:
                necesarias = set()
                for codigo in sin_plan:
                    partes = codigo.split(".")
                    necesarias |= {".".join(partes[:i]) for i in range(1, len(partes) + 1)}
                nuevas = [
                    por_codigo[c] for c in sorted(
                        necesarias - existentes - propuestas,
                        key=lambda c: tuple(int(p) for p in c.split(".")),
                    ) if c in por_codigo
                ]
                if nuevas:
                    previas = (resultado.get("cuentas_csv") or "").splitlines()[1:]
                    resultado["cuentas_csv"] = "\n".join(
                        [asistente.ENCABEZADO_CSV_CUENTAS, *previas, *nuevas]
                    ) + "\n"
                    propuestas |= {linea.split(";")[0] for linea in nuevas}

            faltan = sorted(c for c in usadas - validas - propuestas if c)
            if (usadas & propuestas) - validas:
                resultado.setdefault("avisos", []).append(
                    "Los asientos usan cuentas nuevas del plan propuesto: importá "
                    "primero el plan de cuentas y después los asientos."
                )
            if faltan:
                resultado.setdefault("avisos", []).append(
                    "Estas cuentas no existen (o no son de detalle activas) en tu "
                    f"plan de cuentas: {', '.join(faltan)}. Crealas o importá la "
                    "plantilla de cuentas antes de importar los asientos."
                )
        self._enviar_json(resultado)

    # ------------------------------------------------------------------
    # Manejadores: importación desde CSV
    # ------------------------------------------------------------------

    @staticmethod
    def _detalles_importacion(errores: list[dict], limite: int = 40) -> list[str]:
        """Convierte los errores del importador en textos para la API."""
        detalles = [
            (f"Fila {e['fila']}: {e['mensaje']}" if e.get("fila") else str(e["mensaje"]))
            for e in errores[:limite]
        ]
        if len(errores) > limite:
            detalles.append(f"… y {len(errores) - limite} error(es) más.")
        return detalles

    @staticmethod
    def _texto_a_importar(datos: dict) -> str:
        """
        CSV a importar. Un Excel llega en base64 (campo ``xlsx``) y se
        convierte acá a CSV, así pasa por exactamente las mismas validaciones.
        """
        if datos.get("xlsx"):
            import base64
            import binascii

            try:
                contenido = base64.b64decode(str(datos["xlsx"]), validate=False)
            except (binascii.Error, ValueError) as exc:
                raise ErrorValidacion("El archivo Excel llegó dañado.") from exc
            return excel.a_csv(contenido)

        texto = datos.get("csv")
        if not isinstance(texto, str) or not texto.strip():
            raise ErrorValidacion("Seleccioná un archivo CSV o Excel con datos.")
        return texto

    def _manejar_importar_cuentas(self, _: dict) -> None:
        datos = self._leer_cuerpo()
        confirmar = bool(datos.get("confirmar", False))

        try:
            texto = self._texto_a_importar(datos)
            resultado = importacion.importar_cuentas(
                texto, self.ruta_base, confirmar=confirmar
            )
        except ErrorValidacion as exc:
            if confirmar:
                raise
            # En la vista previa los errores de formato se muestran dentro del
            # diálogo de importación, no como un toast genérico.
            self._enviar_json(
                {
                    "tipo": "cuentas",
                    "valido": False,
                    "confirmado": False,
                    "filas": 0,
                    "a_crear": 0,
                    "omitidas": 0,
                    "creadas": 0,
                    "errores": [{
                        "fila": None, "mensaje": exc.mensaje, "regla": exc.regla,
                    }],
                    "muestra": [],
                }
            )
            return

        if confirmar and not resultado["valido"]:
            raise ErrorValidacion(
                f"El CSV tiene {len(resultado['errores'])} error(es): "
                "no se importó ninguna cuenta.",
                regla=(resultado["errores"][0].get("regla")
                       if resultado["errores"] else None),
                detalles=self._detalles_importacion(resultado["errores"]),
            )

        self._enviar_json(resultado, 201 if resultado["confirmado"] else 200)

    def _manejar_importar_asientos(self, _: dict) -> None:
        datos = self._leer_cuerpo()
        confirmar = bool(datos.get("confirmar", False))

        try:
            texto = self._texto_a_importar(datos)
            resultado = importacion.importar_asientos(
                texto, self.ruta_base, confirmar=confirmar
            )
        except ErrorValidacion as exc:
            if confirmar:
                raise
            self._enviar_json(
                {
                    "tipo": "asientos",
                    "valido": False,
                    "confirmado": False,
                    "filas": 0,
                    "asientos": 0,
                    "lineas": 0,
                    "total_debe": 0,
                    "total_haber": 0,
                    "creados": 0,
                    "errores": [{
                        "fila": None, "mensaje": exc.mensaje, "regla": exc.regla,
                    }],
                    "muestra": [],
                }
            )
            return

        if confirmar and not resultado["valido"]:
            raise ErrorValidacion(
                f"El CSV tiene {len(resultado['errores'])} error(es): "
                "no se importó ningún asiento.",
                regla=(resultado["errores"][0].get("regla")
                       if resultado["errores"] else None),
                detalles=self._detalles_importacion(resultado["errores"]),
            )

        self._enviar_json(resultado, 201 if resultado["confirmado"] else 200)

    # ------------------------------------------------------------------
    # Manejadores: exportación a CSV y Excel
    # ------------------------------------------------------------------

    def _manejar_exportar(self, argumentos: dict) -> None:
        parametros = self._parametros()
        formato = "xlsx" if parametros.get("formato") == "xlsx" else "csv"
        filtros = (
            {"desde": parametros.get("desde"), "hasta": parametros.get("hasta")}
            if argumentos["tipo"] == "asientos"
            else {"completar": parametros.get("completar") == "1"}
        )
        self._enviar_archivo(
            *exportacion.exportar(argumentos["tipo"], formato, self.ruta_base, **filtros)
        )

    def _manejar_plantilla_xlsx(self, argumentos: dict) -> None:
        """La misma plantilla CSV de ``web/plantillas``, en formato Excel."""
        tipo = argumentos["tipo"]
        texto = (RAIZ_WEB / "plantillas" / f"{tipo}.csv").read_text(encoding="utf-8")
        hoja = "Plan de cuentas" if tipo == "cuentas" else "Libro Diario"
        self._enviar_archivo(
            excel.desde_csv(texto, hoja),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"plantilla-{tipo}.xlsx",
        )

    def _manejar_excel_desde_csv(self, _: dict) -> None:
        """Convierte a Excel un CSV armado en el navegador (p. ej. el de Pira Pires)."""
        datos = self._leer_cuerpo()
        nombre = re.sub(r"[^\w.-]+", "-", str(datos.get("nombre") or "datos"))[:60]
        texto = str(datos.get("csv") or "")
        if datos.get("formato") == "libro":
            # Formato de planilla de clase, con los nombres del plan de cuentas.
            nombres = {
                c["codigo"]: c["nombre"]
                for c in plan_de_cuentas.listar_cuentas(self.ruta_base)
            }
            contenido = excel.libro_diario_de_clase(texto, nombres)
        else:
            contenido = excel.desde_csv(texto, str(datos.get("hoja") or "Hoja1"))
        self._enviar_archivo(
            contenido,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"{nombre.removesuffix('.xlsx')}.xlsx",
        )

    def _manejar_datos_demo(self, _: dict) -> None:
        from . import datos_demo

        datos = self._leer_cuerpo()
        resultado = datos_demo.cargar(self.ruta_base, recrear=bool(datos.get("recrear")))
        self._enviar_json(resultado, 201)

    def _manejar_reiniciar(self, _: dict) -> None:
        base_datos.reiniciar(self.ruta_base)
        with base_datos.conectar(self.ruta_base) as conexion:
            base_datos.asegurar_empresa(conexion)
        self._enviar_json({"mensaje": "Base de datos vaciada."})

    # ------------------------------------------------------------------

    def log_message(self, formato: str, *argumentos) -> None:
        """Log más sobrio: omite el ruido de los archivos estáticos."""
        if "/api/" in (argumentos[0] if argumentos else ""):
            super().log_message(formato, *argumentos)

    @property
    def ruta_base(self):
        """Ruta de la base de datos, fijada por el servidor al arrancar."""
        return self.server.ruta_base_datos  # type: ignore[attr-defined]


def crear_servidor(host: str = "127.0.0.1", puerto: int = 8000, ruta_base: str | None = None):
    """Crea el servidor HTTP con la base de datos ya inicializada."""
    archivo = base_datos.inicializar(ruta_base)
    with base_datos.conectar(archivo) as conexion:
        base_datos.asegurar_empresa(conexion)

    servidor = ThreadingHTTPServer((host, puerto), Manejador)
    servidor.ruta_base_datos = archivo  # type: ignore[attr-defined]
    servidor.daemon_threads = True
    return servidor, archivo