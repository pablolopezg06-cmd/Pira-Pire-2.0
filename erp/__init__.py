"""
Mini ERP Contable
=================

Paquete del sistema. Cada módulo corresponde a una etapa del enunciado:

    erp.dinero          Manejo de importes (centavos enteros, sin float)
    erp.base_datos      Esquema SQLite y conexión
    erp.validaciones    Reglas de negocio (controles 1 a 6)
    erp.plan_de_cuentas Etapa 1 · Plan de cuentas (sección 13)
    erp.libro_diario    Etapa 1 · Libro Diario (sección 14)
    erp.libro_mayor     Etapa 2 · Libro Mayor automático (sección 15)
    erp.balance         Etapa 3 · Balance de comprobación (sección 16)
    erp.iva             IVA de Paraguay (Ley N.º 1259/97)
    erp.asistente       Pira Pires: asistente con Groq y respaldo en Gemini
    erp.datos_demo      Empresa ficticia con 15 operaciones (sección 19)
    erp.servidor        API JSON y servidor web
"""

__version__ = "1.0.0"