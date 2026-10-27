#!/usr/bin/env bash
# =============================================================================
# Mini ERP Contable — arranque rápido
# =============================================================================
# Carga los datos de prueba (una sola vez) y levanta el servidor web.
#
#   ./INICIAR.sh              puerto 8000, con datos de prueba
#   ./INICIAR.sh 8100         otro puerto
#   ./INICIAR.sh 8000 limpio  vacía la base antes de cargar
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"

PUERTO="${1:-8000}"
MODO="${2:-}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "No se encontró python3. El ERP solo necesita Python 3, sin instalar nada más."
  exit 1
fi

if [ "$MODO" = "limpio" ]; then
  echo "▸ Vaciando la base de datos…"
  python3 main.py --reiniciar >/dev/null
fi

# La carga solo tiene efecto si no hay asientos: no pisa datos existentes.
if [ ! -f datos/erp.db ]; then
  echo "▸ Primera ejecución: cargando los datos de prueba…"
  python3 main.py --cargar-demo
fi

echo
echo "▸ Iniciando el servidor en http://127.0.0.1:${PUERTO}"
echo "  Para detenerlo: Ctrl+C"
echo

exec python3 main.py --puerto "$PUERTO" --abrir