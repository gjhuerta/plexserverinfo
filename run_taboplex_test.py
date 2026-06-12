# ==========================================================
# run_taboplex_test.py
# Lanza TaboPlex en modo TEST
# ==========================================================
#
# Este archivo ejecuta weekly_plex_newsletter.py sin parámetros.
# Resultado:
# - Envía solo a MAIL_TEST_TO
# - El asunto queda marcado con TEST
# ==========================================================

import sys

import weekly_plex_newsletter

# ----------------------------------------------------------
# Simular ejecución sin parámetros
# ----------------------------------------------------------
sys.argv = ["weekly_plex_newsletter.py"]

# ----------------------------------------------------------
# Ejecutar newsletter en modo prueba
# ----------------------------------------------------------
weekly_plex_newsletter.main()