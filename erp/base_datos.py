"""
Módulo: base_datos
==================

Conexión a SQLite y definición del esquema del Mini ERP Contable.

DISEÑO CLAVE
------------
El sistema tiene **cuatro** tablas de datos contables: `empresa`, `cuenta`,
`asiento` y `partida`.

No existe ninguna tabla para el Libro Mayor ni para el Balance. Esa
ausencia es deliberada y es justamente lo que garantiza los controles 4 y
5 del enunciado: si no hay dónde cargar esos datos, no pueden cargarse a
mano; se calculan siempre con consultas `SELECT` sobre el Libro Diario.

Los importes se guardan como **enteros en centavos** (ver `erp/dinero.py`).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import validaciones
from .excepciones import ErrorBaseDeDatos

#: Ruta por defecto de la base de datos.
RUTA_POR_DEFECTO = Path(__file__).resolve().parent.parent / "datos" / "erp.db"


ESQUEMA = """
-- ---------------------------------------------------------------------------
-- Empresa: datos cabecera del estudio. Una sola fila (id = 1).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS empresa (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    nombre          TEXT    NOT NULL,
    cuit            TEXT,
    actividad       TEXT,
    ejercicio_inicio TEXT,
    ejercicio_fin    TEXT,
    moneda          TEXT    NOT NULL DEFAULT 'G.'
);

-- ---------------------------------------------------------------------------
-- Plan de cuentas (sección 13).
--   codigo            -> clave primaria, código jerárquico ("1.1.01")
--   clasificacion     -> 1..6 (Activo, Pasivo, Patrimonio Neto, Ingresos,
--                         Costos, Gastos)
--   naturaleza        -> 'D' deudora / 'A' acreedora  (control 6)
--   recibe_movimiento -> 0 = cuenta agrupadora (colectora), 1 = cuenta de detalle
--   estado            -> ACTIVA / INACTIVA
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cuenta (
    codigo            TEXT    PRIMARY KEY,
    nombre            TEXT    NOT NULL,
    clasificacion     INTEGER NOT NULL CHECK (clasificacion BETWEEN 1 AND 6),
    nivel             INTEGER NOT NULL CHECK (nivel BETWEEN 1 AND 3),
    recibe_movimiento INTEGER NOT NULL DEFAULT 1 CHECK (recibe_movimiento IN (0, 1)),
    naturaleza        TEXT    NOT NULL CHECK (naturaleza IN ('D', 'A')),
    estado            TEXT    NOT NULL DEFAULT 'ACTIVA'
                              CHECK (estado IN ('ACTIVA', 'INACTIVA')),
    orden             INTEGER NOT NULL DEFAULT 0,
    creado_en         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cuenta_clasificacion ON cuenta (clasificacion);
CREATE INDEX IF NOT EXISTS idx_cuenta_padre ON cuenta (codigo);

-- ---------------------------------------------------------------------------
-- Libro Diario: cabecera del asiento (sección 14).
--   estado -> BORRADOR (editable) / CONFIRMADO (contabilizado) / ANULADO
--   Los asientos ANULADOS y BORRADOR no entran al Libro Mayor ni al Balance.
--   total_debe / total_haber están en centavos y se recalculan al guardar.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asiento (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    numero           INTEGER NOT NULL UNIQUE,
    fecha            TEXT    NOT NULL CHECK (fecha GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    concepto         TEXT    NOT NULL,
    estado           TEXT    NOT NULL DEFAULT 'BORRADOR'
                              CHECK (estado IN ('BORRADOR', 'CONFIRMADO', 'ANULADO')),
    total_debe       INTEGER NOT NULL DEFAULT 0,
    total_haber      INTEGER NOT NULL DEFAULT 0,
    confirmado_en    TEXT,
    observaciones    TEXT
);

CREATE INDEX IF NOT EXISTS idx_asiento_fecha ON asiento (fecha);
CREATE INDEX IF NOT EXISTS idx_asiento_estado ON asiento (estado);

-- ---------------------------------------------------------------------------
-- Libro Diario: partidas (líneas) del asiento.
--   cuenta -> clave foránea al plan de cuentas (control 2 reforzado por la BD).
--   debe / haber en centavos; CHECK impide ambos en cero o ambos positivos.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partida (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    asiento_id INTEGER NOT NULL REFERENCES asiento (id) ON DELETE CASCADE,
    linea     INTEGER NOT NULL,
    cuenta    TEXT    NOT NULL REFERENCES cuenta (codigo),
    detalle   TEXT,
    debe      INTEGER NOT NULL DEFAULT 0 CHECK (debe >= 0),
    haber     INTEGER NOT NULL DEFAULT 0 CHECK (haber >= 0),
    CHECK (NOT (debe > 0 AND haber > 0)),
    CHECK (debe > 0 OR haber > 0),
    UNIQUE (asiento_id, linea)
);

CREATE INDEX IF NOT EXISTS idx_partida_cuenta ON partida (cuenta);
CREATE INDEX IF NOT EXISTS idx_partida_asiento ON partida (asiento_id);
"""


def ruta_base_datos(ruta: str | Path | None = None) -> Path:
    """Devuelve la ruta del archivo SQLite, creyendo la carpeta si hace falta."""
    destino = Path(ruta) if ruta else RUTA_POR_DEFECTO
    destino.parent.mkdir(parents=True, exist_ok=True)
    return destino


def _abrir(destino: Path) -> sqlite3.Connection:
    """
    Abre una conexión cruda a SQLite (sin verificar el esquema).

    Activa las claves foráneas —SQLite las tiene apagadas por defecto y el
    control 2 se apoya en ellas— y el acceso por nombre de columna.
    """
    conexion = sqlite3.connect(str(destino))
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    return conexion


def inicializar(ruta: str | Path | None = None) -> Path:
    """
    Crea el archivo de base de datos y el esquema si no existen.
    Devuelve la ruta del archivo creado.
    """
    destino = ruta_base_datos(ruta)
    conexion = _abrir(destino)
    try:
        conexion.executescript(ESQUEMA)
        conexion.commit()
    finally:
        conexion.close()
    return destino


@contextmanager
def conectar(ruta: str | Path | None = None):
    """
    Abre una conexión a SQLite.

    Si el archivo de base de datos todavía no existe, se crea con el esquema
    completo: así cualquier punto de entrada (servidor, consola, prueba) puede
    conectar sin pasos previos.
    """
    destino = ruta_base_datos(ruta)
    if not destino.exists():
        inicializar(destino)

    conexion = _abrir(destino)
    try:
        yield conexion
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()


def reiniciar(ruta: str | Path | None = None) -> Path:
    """Borra el archivo de base de datos y lo vuelve a crear vacío."""
    destino = ruta_base_datos(ruta)
    if destino.exists():
        destino.unlink()
    return inicializar(destino)


def empresa_por_defecto() -> dict:
    """
    Datos de la empresa cuando todavía no se cargó ninguna.

    Arranca **vacía a propósito**: el usuario que entra le pone su nombre desde
    el inicio de sesión, y el resto de los datos (CUIT, actividad, ejercicio)
    los completa en el formulario de la empresa. Antes traía los datos de una
    empresa ficticia, que se veían como si fueran reales.
    """
    return {
        "nombre": "",
        "cuit": "",
        "actividad": "",
        "ejercicio_inicio": "",
        "ejercicio_fin": "",
        "moneda": "G.",
    }


def asegurar_empresa(conexion) -> None:
    """Inserta la empresa por defecto en una base recién creada."""
    if conexion.execute("SELECT COUNT(*) AS n FROM empresa").fetchone()["n"] == 0:
        datos = empresa_por_defecto()
        conexion.execute(
            """INSERT INTO empresa (id, nombre, cuit, actividad,
                                     ejercicio_inicio, ejercicio_fin, moneda)
               VALUES (1, ?, ?, ?, ?, ?, ?)""",
            (
                datos["nombre"],
                datos["cuit"],
                datos["actividad"],
                datos["ejercicio_inicio"],
                datos["ejercicio_fin"],
                datos["moneda"],
            ),
        )


def nombre_clasificacion(numero) -> str:
    """'1' -> 'ACTIVO'. Devuelve '' si la clasificación no existe."""
    return validaciones.CLASIFICACIONES.get(int(numero), {}).get("nombre", "")


def mapear_fila(fila: sqlite3.Row | None) -> dict | None:
    """Convierte una Row de sqlite3 en diccionario, o None si la fila es None."""
    return dict(fila) if fila is not None else None


def error_sql_a_mensaje(excepcion: sqlite3.Error) -> ErrorBaseDeDatos:
    """Traduce errores de SQLite a mensajes entendibles en español."""
    texto = str(excepcion)
    if "UNIQUE constraint failed" in texto:
        return ErrorBaseDeDatos("Ya existe un registro con ese código o número.")
    if "FOREIGN KEY constraint failed" in texto:
        return ErrorBaseDeDatos(
            "No se puede guardar: la cuenta o el asiento de referencia no existe."
        )
    if "CHECK constraint failed" in texto:
        return ErrorBaseDeDatos(
            "No se puede guardar: un valor infringe una restricción de integridad "
            "(por ejemplo, una partida con Debe y Haber a la vez)."
        )
    return ErrorBaseDeDatos(f"Error de base de datos: {texto}")