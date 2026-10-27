#!/usr/bin/env python3
"""
Mini ERP Contable — punto de entrada.

Uso:

    python3 main.py                      arranca en http://127.0.0.1:8000
    python3 main.py --puerto 9000        usa otro puerto
    python3 main.py --datos otra.db      usa otro archivo de base de datos
    python3 main.py --cargar-demo        carga la empresa y las 15 operaciones
    python3 main.py --auto-sembrar       carga la demo solo si no hay asientos
    python3 main.py --reiniciar          vacía la base de datos y sale
    python3 main.py --consola            ejecuta una consulta SQL de lectura

No requiere instalar nada: sólo la biblioteca estándar de Python 3.

El asistente virtual (Groq con respaldo en Gemini) lee sus claves de las
variables de entorno, o en su defecto del archivo local `claves.env`
(ignorado por git; plantilla en `claves.env.example`).

En un hosting que inyecta las variables de entorno `HOST` y `PORT` —como
Render— los valores por defecto pasan a ser esos, así que alcanza con
`python3 main.py`. El servidor arranca vacío: los datos de prueba se cargan
a mano desde el Panel (o con `--auto-sembrar` si se quiere la demo al
arrancar).

Render solo inyecta `PORT` (no `HOST`), y exige que el servidor escuche en
`0.0.0.0` para que su proxy pueda alcanzarlo. Por eso, cuando la variable
`RENDER` está presente, el host por defecto pasa a ser `0.0.0.0`; en la
máquina propia se sigue escuchando solo en `127.0.0.1`.
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from erp import base_datos, datos_demo
from erp.dinero import formato_monto


#: Archivo local con las claves de API del asistente. Se ignora en git.
ARCHIVO_CLAVES = Path(__file__).resolve().parent / "claves.env"

#: Claves que pueden declararse en ese archivo.
CLAVES_ASISTENTE = ("GROQ_API_KEY", "GEMINI_API_KEY")


def cargar_claves(ruta: Path | None = None) -> list[str]:
    """
    Carga las claves del asistente desde `claves.env` (formato
    ``CLAVE=valor``, una por línea, ``#`` para comentarios).

    Las variables ya presentes en el entorno **no** se pisan: así en un
    hosting mandan las variables del sistema y el archivo local sirve para
    desarrollar en la máquina propia. Devuelve las claves que quedaron
    definidas.
    """
    archivo = ruta or ARCHIVO_CLAVES
    if not archivo.is_file():
        return []

    definidas: list[str] = []
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        texto = linea.strip()
        if not texto or texto.startswith("#") or "=" not in texto:
            continue
        clave, _, valor = texto.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if clave in CLAVES_ASISTENTE and valor and clave not in os.environ:
            os.environ[clave] = valor
            definidas.append(clave)
    return definidas


def _crear_parser() -> argparse.ArgumentParser:
    # Render solo inyecta PORT: sin esto el servidor escucharía en
    # 127.0.0.1 y el proxy de Render no llegaría a él (error 502).
    host_por_defecto = os.environ.get("HOST") or (
        "0.0.0.0" if os.environ.get("RENDER") else "127.0.0.1"
    )

    parser = argparse.ArgumentParser(
        description="Mini ERP Contable · Etapas 1 a 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default=host_por_defecto,
        help="Dirección de escucha (por defecto, la variable HOST o 127.0.0.1)",
    )
    parser.add_argument(
        "--puerto",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Puerto HTTP (por defecto, la variable PORT o 8000)",
    )
    parser.add_argument("--datos", default=None, help="Ruta del archivo de base de datos")
    parser.add_argument(
        "--cargar-demo", action="store_true",
        help="Carga la empresa ficticia con las 15 operaciones de prueba y sale",
    )
    parser.add_argument(
        "--auto-sembrar", action="store_true",
        help=(
            "Al arrancar el servidor, carga los datos de prueba únicamente si "
            "la base todavía no tiene asientos. Es opcional: por defecto el "
            "servidor arranca vacío y la demo se carga desde el Panel."
        ),
    )
    parser.add_argument(
        "--reiniciar", action="store_true",
        help="Borra la base de datos (asientos y cuentas) y sale",
    )
    parser.add_argument(
        "--consola", default=None, metavar="SQL",
        help="Ejecuta una consulta SQL de lectura y muestra el resultado",
    )
    parser.add_argument(
        "--abrir", action="store_true", help="Abre el navegador al arrancar"
    )
    return parser


def main() -> int:
    # La consola de Windows usa cp1252 y no puede imprimir «→» ni otros
    # caracteres de los mensajes: sin esto el programa se cae al arrancar.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")

    cargar_claves()
    argumentos = _crear_parser().parse_args()

    # -- Modo consola: una consulta SQL de lectura ------------------------
    if argumentos.consola:
        from erp import balance, libro_mayor

        base_datos.inicializar(argumentos.datos)
        consulta = argumentos.consola.strip().lower()
        if consulta.startswith("select") and "libro mayor" in consulta:
            resultado = libro_mayor.libro_mayor_completo(argumentos.datos)
        elif consulta.startswith("select") and "balance" in consulta:
            resultado = balance.balance_comprobacion(argumentos.datos)
        else:
            with base_datos.conectar(argumentos.datos) as conexion:
                resultado = [dict(f) for f in conexion.execute(argumentos.consola).fetchall()]
        for fila in resultado:
            print(fila)
        return 0

    # -- Modo reiniciar ---------------------------------------------------
    if argumentos.reiniciar:
        ruta = base_datos.reiniciar(argumentos.datos)
        with base_datos.conectar(ruta) as conexion:
            base_datos.asegurar_empresa(conexion)
        print(f"Base de datos vaciada: {ruta}")
        return 0

    # -- Modo carga de datos de prueba ------------------------------------
    if argumentos.cargar_demo:
        resultado = datos_demo.cargar(argumentos.datos, recrear=True)
        cuadre = resultado["cuadre_libro_diario"]
        print(f"Empresa:       {resultado['empresa']['nombre']}")
        print(f"Cuentas:       {resultado['cuentas_creadas']}")
        print(f"Asientos:      {resultado['asientos_creados']}")
        print(f"Total Debe:    {formato_monto(cuadre['total_debe'])}")
        print(f"Total Haber:   {formato_monto(cuadre['total_haber'])}")
        print(f"Cuadra:        {'sí' if cuadre['cuadra'] else 'NO'}")
        if resultado["errores"]:
            print(f"Errores:       {len(resultado['errores'])}")
            for error in resultado["errores"]:
                print(f"  - {error['fecha']} {error['concepto']}: {error['error']}")
        return 0

    # -- Modo servidor web -------------------------------------------------
    from erp import servidor as modulo_servidor

    servidor, archivo = modulo_servidor.crear_servidor(
        argumentos.host, argumentos.puerto, argumentos.datos
    )

    # Siembra automática: solo cuando la base está vacía, para que el
    # sistema sea usable desde la primera visita. En un plan gratuito el
    # disco se borra en cada reinicio, así que esto se repite.
    sembrado = False
    if argumentos.auto_sembrar:
        with base_datos.conectar(archivo) as conexion:
            cuantos = conexion.execute("SELECT COUNT(*) FROM asiento").fetchone()[0]
        if cuantos == 0:
            datos_demo.cargar(archivo)
            sembrado = True

    direccion = f"http://{argumentos.host}:{argumentos.puerto}"
    print("=" * 62)
    print("  MINI ERP CONTABLE · Etapas 1 a 3")
    print("=" * 62)
    print(f"  Interfaz web : {direccion}")
    print(f"  Base de datos: {archivo}")
    if sembrado:
        print("  Datos de prueba: cargados (la base estaba vacía)")
    print("  Flujo: Plan de cuentas → Libro Diario → Libro Mayor → Balance")
    print("  Para demostrar, usá el botón «Cargar datos de prueba» en el Panel.")
    hay_claves = any(os.environ.get(c, "").strip() for c in CLAVES_ASISTENTE)
    print(
        "  Asistente IA  : "
        + (
            "activo — Groq con respaldo en Gemini"
            if hay_claves
            else "no configurado (completá claves.env o exportá GROQ_API_KEY)"
        )
    )
    print("  Detener el servidor: Ctrl+C")
    if os.environ.get("RENDER"):
        # Aviso real: en el plan gratuito de Render el sistema de archivos
        # es efímero y se pierde también cuando el servicio se apaga a los
        # 15 minutos de inactividad.
        print("  ────────────────────────────────────────────────────")
        print("  hosting: plan gratuito con disco efímero.")
        print("  Los asientos que cargues se pierden si el servicio se")
        print("  apaga por inactividad o se vuelve a desplegar.")
    print("=" * 62)

    if argumentos.abrir:
        try:
            webbrowser.open(direccion)
        except Exception:  # pragma: no cover
            pass

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        servidor.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())