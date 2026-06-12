# ==========================================================
# test_plex_recent_movies.py
# Prueba de lectura de películas recientes desde Plex
# ==========================================================
#
# Objetivo:
# - Conectarse a Plex usando variables del .env
# - Leer la biblioteca de películas
# - Buscar películas agregadas durante la semana anterior
# - Mostrar resultado en consola
#
# Este script NO envía correos todavía.
# ==========================================================

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from plexapi.server import PlexServer


# ----------------------------------------------------------
# Cargar variables desde .env
# ----------------------------------------------------------
load_dotenv()


# ----------------------------------------------------------
# Leer configuración Plex desde .env
# ----------------------------------------------------------
# Se dejan nombres alternativos para no obligarte a cambiar
# variables que ya puedas tener creadas.
plex_base_url = (
    os.getenv("PLEX_BASE_URL")
    or os.getenv("PLEX_URL")
    or os.getenv("PLEX_SERVER_URL")
)

plex_token = (
    os.getenv("PLEX_TOKEN")
    or os.getenv("PLEX_AUTH_TOKEN")
)

plex_movie_library = os.getenv("PLEX_MOVIE_LIBRARY", "Movies")


# ----------------------------------------------------------
# Leer zona horaria local
# ----------------------------------------------------------
# Si ya tienes otra variable de zona horaria, puedes ajustar aquí.
local_timezone_name = (
    os.getenv("LOCAL_TIMEZONE")
    or os.getenv("TZ")
    or "America/Santiago"
)

local_timezone = ZoneInfo(local_timezone_name)


# ----------------------------------------------------------
# Validar variables mínimas
# ----------------------------------------------------------
required_vars = {
    "PLEX_BASE_URL / PLEX_URL / PLEX_SERVER_URL": plex_base_url,
    "PLEX_TOKEN / PLEX_AUTH_TOKEN": plex_token,
    "PLEX_MOVIE_LIBRARY": plex_movie_library,
}

missing_vars = [
    name for name, value in required_vars.items()
    if value is None or str(value).strip() == ""
]

if missing_vars:
    raise RuntimeError(
        "Faltan variables obligatorias en .env: "
        + ", ".join(missing_vars)
    )


# ----------------------------------------------------------
# Calcular rango de semana anterior
# ----------------------------------------------------------
# Regla:
# - Si corre cualquier día, toma la semana calendario anterior.
# - Lunes 00:00:00 hasta domingo 23:59:59.
#
# Ejemplo:
# Si corre el lunes 15, revisa lunes 8 al domingo 14.
# ----------------------------------------------------------
now = datetime.now(local_timezone)

# weekday(): lunes = 0, domingo = 6
start_of_current_week = now - timedelta(days=now.weekday())
start_of_current_week = start_of_current_week.replace(
    hour=0,
    minute=0,
    second=0,
    microsecond=0,
)

start_of_previous_week = start_of_current_week - timedelta(days=7)
end_of_previous_week = start_of_current_week - timedelta(seconds=1)


# ----------------------------------------------------------
# Función auxiliar para normalizar fechas de Plex
# ----------------------------------------------------------
def normalize_plex_datetime(value: datetime) -> datetime:
    """
    Plex normalmente entrega addedAt como datetime.
    Dependiendo del entorno puede venir con o sin timezone.

    Esta función lo normaliza a la zona horaria local para poder
    compararlo contra el rango de semana anterior.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=local_timezone)

    return value.astimezone(local_timezone)


# ----------------------------------------------------------
# Conectarse a Plex
# ----------------------------------------------------------
print("Conectando a Plex...")
print(f"Plex URL: {plex_base_url}")
print(f"Biblioteca: {plex_movie_library}")
print("")

plex = PlexServer(plex_base_url, plex_token)


# ----------------------------------------------------------
# Obtener biblioteca de películas
# ----------------------------------------------------------
library = plex.library.section(plex_movie_library)


# ----------------------------------------------------------
# Leer películas
# ----------------------------------------------------------
print("Leyendo películas desde Plex...")
movies = library.all()

print(f"Total de películas encontradas en biblioteca: {len(movies)}")
print("")


# ----------------------------------------------------------
# Filtrar películas agregadas la semana anterior
# ----------------------------------------------------------
recent_movies = []

for movie in movies:
    added_at = getattr(movie, "addedAt", None)

    if added_at is None:
        continue

    added_at_local = normalize_plex_datetime(added_at)

    if start_of_previous_week <= added_at_local <= end_of_previous_week:
        recent_movies.append(
            {
                "title": getattr(movie, "title", "Sin título"),
                "year": getattr(movie, "year", None),
                "added_at": added_at_local,
                "rating_key": getattr(movie, "ratingKey", None),
                "guid": getattr(movie, "guid", None),
            }
        )


# ----------------------------------------------------------
# Ordenar por fecha agregada descendente
# ----------------------------------------------------------
recent_movies.sort(
    key=lambda item: item["added_at"],
    reverse=True,
)


# ----------------------------------------------------------
# Mostrar resultado
# ----------------------------------------------------------
print("Rango revisado:")
print(f"- Desde: {start_of_previous_week.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"- Hasta: {end_of_previous_week.strftime('%Y-%m-%d %H:%M:%S')}")
print("")

if not recent_movies:
    print("No se encontraron películas agregadas durante la semana anterior.")
else:
    print(f"Películas agregadas la semana anterior: {len(recent_movies)}")
    print("")

    for index, movie in enumerate(recent_movies, start=1):
        year_text = f" ({movie['year']})" if movie["year"] else ""
        added_text = movie["added_at"].strftime("%Y-%m-%d %H:%M:%S")

        print(f"{index}. {movie['title']}{year_text}")
        print(f"   Agregada: {added_text}")
        print(f"   RatingKey: {movie['rating_key']}")
        print(f"   GUID: {movie['guid']}")
        print("")


print("Prueba Plex finalizada correctamente.")