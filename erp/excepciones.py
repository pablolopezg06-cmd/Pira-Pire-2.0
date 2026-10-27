"""
Módulo: excepciones
===================

Jerarquía de errores del Mini ERP Contable.

La idea es que **toda** violación de una regla de negocio travelle con la
identificación del control infringido (C1..C6 de la sección 18 del
enunciado). Así la interfaz web puede mostrarle al usuario no solo qué
está mal, sino **qué regla del ERP se violó**.
"""

from __future__ import annotations


class ErrorERP(Exception):
    """Error genérico del ERP."""


class ErrorValidacion(ErrorERP):
    """
    Error de validación de datos.

    Attributes
    ----------
    mensaje : str
        Texto legible para el usuario.
    regla : str | None
        Código del control infringido ('C1'..'C6') o None si es una
        validación de formato que no pertenece a un control puntual.
    detalles : list[str]
        Lista de problemas concretos (útil cuando fallan varias reglas a la vez).
    """

    def __init__(self, mensaje: str, regla: str | None = None, detalles: list[str] | None = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.regla = regla
        self.detalles = detalles or []

    def como_diccionario(self) -> dict:
        return {
            "error": self.mensaje,
            "regla": self.regla,
            "titulo_regla": TITULOS_REGLAS.get(self.regla, "Validación de datos"),
            "detalles": self.detalles,
        }


class ErrorNoEncontrado(ErrorERP):
    """El registro solicitado no existe."""


class ErrorOperacionInvalida(ErrorERP):
    """La operación pedida no tiene sentido en el estado actual del sistema."""


class ErrorBaseDeDatos(ErrorERP):
    """Falla de integridad o de la base de datos."""


#: Títulos de las reglas, para poder mostrarlos en la interfaz.
#: Deben coincidir con `validaciones.REGLAS`.
TITULOS_REGLAS = {
    "C1": "Control 1 - El asiento debe cuadrar (Debe = Haber)",
    "C2": "Control 2 - Solo se pueden usar cuentas existentes y activas",
    "C3": "Control 3 - Las cuentas deben estar clasificadas correctamente",
    "C4": "Control 4 - El Libro Mayor se obtiene del Libro Diario",
    "C5": "Control 5 - Los balances se generan de la información contabilizada",
    "C6": "Control 6 - Se diferencia la naturaleza deudora y acreedora",
}