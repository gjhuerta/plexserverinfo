# ==========================================================
# run_taboplex_prod.py
# Lanza TaboPlex en modo PROD
# ==========================================================
#
# Este archivo ejecuta weekly_plex_newsletter.py con --prod.
# Resultado:
# - Envía a MAIL_TO
# - Envía a MAIL_BCC
# - NO marca el asunto como TEST
#
# Usar solo cuando el correo de prueba ya fue validado.
# ==========================================================

import sys

import weekly_plex_newsletter

# ----------------------------------------------------------
# Simular ejecución con parámetro --prod
# ----------------------------------------------------------
sys.argv = ["weekly_plex_newsletter.py", "--prod"]

# ----------------------------------------------------------
# Ejecutar newsletter en modo real
# ----------------------------------------------------------
weekly_plex_newsletter.main()