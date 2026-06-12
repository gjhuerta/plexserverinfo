#!/bin/bash

# ============================================================
# taboplex - Actualización completa
# ============================================================
# Este script actualiza todos los datos de taboplex:
# 1. Exporta series desde Plex
# 2. Consulta TVmaze para detectar episodios faltantes
# 3. Exporta películas desde Plex
# 4. Importa los Excel a SQLite
# 5. Reconstruye el radar de actualización de series
# 6. Levanta el servidor web local
#
# Proyecto:
# /Users/gjhuerta/PycharmProjects/plexserverinfo
# ============================================================

# Detiene el script si algún comando falla.
set -e

# Ruta raíz del proyecto.
PROJECT_DIR="/Users/gjhuerta/PycharmProjects/plexserverinfo"

# Ruta del entorno virtual.
VENV_DIR="$PROJECT_DIR/.venv"

# Separador visual para la consola.
print_step() {
    echo ""
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

print_step "Entrando al proyecto taboplex"
cd "$PROJECT_DIR"

print_step "Activando entorno virtual"
source "$VENV_DIR/bin/activate"

print_step "Exportando series desde Plex"
python src/export_plex_series.py

print_step "Consultando TVmaze y detectando episodios faltantes"
python src/check_latest_episodes_tvmaze.py

print_step "Exportando películas desde Plex"
python src/export_plex_movies.py

print_step "Importando datos a SQLite"
python src/import_exports_to_sqlite.py

print_step "Reconstruyendo radar de actualización de series"
python src/build_series_update_radar.py

print_step "Actualización completa finalizada"
echo "Base actualizada:"
echo "$PROJECT_DIR/data/taboplex.sqlite"

print_step "Levantando servidor web taboplex"
echo "Abre en tu navegador:"
echo "http://127.0.0.1:5000"
echo ""
echo "Para detener el servidor usa CTRL + C"
echo ""

python web/app.py