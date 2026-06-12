# ==========================================================
# test_plex_recent_episodes.py
# TaboPlex - Prueba de episodios recientes desde Plex
# ==========================================================
#
# Objetivo:
# - Leer la biblioteca de series desde PLEX_LIBRARY_NAMES.
# - Buscar episodios agregados durante la semana anterior.
# - Agruparlos por serie y temporada.
# - Mostrar el resultado en consola.
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
# Leer configuración Plex existente
# ----------------------------------------------------------
plex_base_url = (
    os.getenv("PLEX_BASE_URL")
    or os.getenv("PLEX_URL")
    or os.getenv("PLEX_SERVER_URL")
)

plex_token = (
    os.getenv("PLEX_TOKEN")
    or os.getenv("PLEX_AUTH_TOKEN")
)

# ----------------------------------------------------------
# IMPORTANTE:
# Usamos tu variable existente para series.
# No usamos PLEX_TV_LIBRARY.
# ----------------------------------------------------------
plex_tv_library_names_raw = os.getenv("PLEX_LIBRARY_NAMES", "")


# ----------------------------------------------------------
# Leer zona horaria local
# ----------------------------------------------------------
local_timezone_name = (
    os.getenv("LOCAL_TIMEZONE")
    or os.getenv("TZ")
    or "America/Santiago"
)

local_timezone = ZoneInfo(local_timezone_name)


# ----------------------------------------------------------
# Separar nombres de bibliotecas
# ----------------------------------------------------------
def split_library_names(raw_value: str) -> list[str]:
    """
    Convierte una cadena de bibliotecas separadas por coma en lista.

    Ejemplo:
    PLEX_LIBRARY_NAMES=TV Shows
    PLEX_LIBRARY_NAMES=TV Shows,Anime
    """
    if not raw_value:
        return []

    normalized = raw_value.replace(";", ",")

    return [
        item.strip()
        for item in normalized.split(",")
        if item.strip()
    ]


plex_tv_library_names = split_library_names(plex_tv_library_names_raw)


# ----------------------------------------------------------
# Validar variables mínimas
# ----------------------------------------------------------
required_vars = {
    "PLEX_BASE_URL / PLEX_URL / PLEX_SERVER_URL": plex_base_url,
    "PLEX_TOKEN / PLEX_AUTH_TOKEN": plex_token,
    "PLEX_LIBRARY_NAMES": plex_tv_library_names_raw,
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

if not plex_tv_library_names:
    raise RuntimeError("PLEX_LIBRARY_NAMES no tiene bibliotecas válidas.")


# ----------------------------------------------------------
# Calcular rango de semana anterior
# ----------------------------------------------------------
def get_previous_week_range() -> tuple[datetime, datetime]:
    """
    Retorna el rango de la semana calendario anterior:
    lunes 00:00:00 a domingo 23:59:59.
    """
    now = datetime.now(local_timezone)

    start_of_current_week = now - timedelta(days=now.weekday())
    start_of_current_week = start_of_current_week.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    start_of_previous_week = start_of_current_week - timedelta(days=7)
    end_of_previous_week = start_of_current_week - timedelta(seconds=1)

    return start_of_previous_week, end_of_previous_week


# ----------------------------------------------------------
# Normalizar fecha de Plex
# ----------------------------------------------------------
def normalize_plex_datetime(value: datetime) -> datetime:
    """
    Normaliza fechas de Plex a la zona horaria local.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=local_timezone)

    return value.astimezone(local_timezone)


# ----------------------------------------------------------
# Leer episodios recientes desde una biblioteca
# ----------------------------------------------------------
def get_recent_episodes_from_library(plex, library_name: str) -> list[dict]:
    """
    Lee todos los episodios de una biblioteca de series y devuelve
    los agregados durante la semana anterior.
    """
    start_date, end_date = get_previous_week_range()

    print(f"Leyendo biblioteca de series: {library_name}")

    library = plex.library.section(library_name)

    # ------------------------------------------------------
    # En una biblioteca de series, search(libtype="episode")
    # permite recuperar episodios directamente.
    # ------------------------------------------------------
    episodes = library.search(libtype="episode")

    print(f"- Episodios encontrados en biblioteca: {len(episodes)}")

    recent_episodes = []

    for episode in episodes:
        added_at = getattr(episode, "addedAt", None)

        if added_at is None:
            continue

        added_at_local = normalize_plex_datetime(added_at)

        if start_date <= added_at_local <= end_date:
            show_title = getattr(episode, "grandparentTitle", "Serie sin título")
            season_number = getattr(episode, "parentIndex", None)
            episode_number = getattr(episode, "index", None)
            episode_title = getattr(episode, "title", "Episodio sin título")

            recent_episodes.append(
                {
                    "library": library_name,
                    "show_title": show_title,
                    "season_number": season_number,
                    "episode_number": episode_number,
                    "episode_title": episode_title,
                    "added_at": added_at_local,
                    "rating_key": getattr(episode, "ratingKey", None),
                    "guid": getattr(episode, "guid", None),
                }
            )

    return recent_episodes


# ----------------------------------------------------------
# Agrupar episodios por serie y temporada
# ----------------------------------------------------------
def group_episodes(episodes: list[dict]) -> dict:
    """
    Agrupa episodios en esta estructura:
    {
      "Serie": {
        1: [episodios],
        2: [episodios]
      }
    }
    """
    grouped = {}

    for episode in episodes:
        show_title = episode["show_title"]
        season_number = episode["season_number"] or 0

        grouped.setdefault(show_title, {})
        grouped[show_title].setdefault(season_number, [])
        grouped[show_title][season_number].append(episode)

    # Ordenar episodios dentro de cada temporada.
    for show_title in grouped:
        for season_number in grouped[show_title]:
            grouped[show_title][season_number].sort(
                key=lambda item: (
                    item["episode_number"] or 0,
                    item["added_at"],
                )
            )

    return grouped


# ----------------------------------------------------------
# Mostrar resultado en consola
# ----------------------------------------------------------
def print_grouped_episodes(grouped: dict, start_date: datetime, end_date: datetime) -> None:
    """
    Imprime el resultado agrupado en consola.
    """
    print("")
    print("Rango revisado:")
    print(f"- Desde: {start_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"- Hasta: {end_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print("")

    if not grouped:
        print("No se encontraron episodios agregados durante la semana anterior.")
        return

    total_shows = len(grouped)
    total_episodes = sum(
        len(episodes)
        for seasons in grouped.values()
        for episodes in seasons.values()
    )

    print(f"Series con episodios agregados: {total_shows}")
    print(f"Episodios agregados: {total_episodes}")
    print("")

    for show_title in sorted(grouped.keys()):
        print(f"📺 {show_title}")

        seasons = grouped[show_title]

        for season_number in sorted(seasons.keys()):
            season_label = (
                f"Temporada {season_number}"
                if season_number
                else "Temporada desconocida"
            )

            print(f"   {season_label}")

            for episode in seasons[season_number]:
                episode_number = episode["episode_number"]

                if episode_number:
                    episode_code = f"E{episode_number:02d}"
                else:
                    episode_code = "E??"

                added_text = episode["added_at"].strftime("%Y-%m-%d %H:%M:%S")

                print(f"   - {episode_code}: {episode['episode_title']}")
                print(f"     Agregado: {added_text}")
                print(f"     RatingKey: {episode['rating_key']}")

        print("")


# ----------------------------------------------------------
# Flujo principal
# ----------------------------------------------------------
def main() -> None:
    """
    Ejecuta la prueba:
    Plex → bibliotecas de series → episodios recientes → consola.
    """
    start_date, end_date = get_previous_week_range()

    print("Conectando a Plex...")
    print(f"- Plex URL: {plex_base_url}")
    print(f"- Bibliotecas de series: {', '.join(plex_tv_library_names)}")
    print("")

    plex = PlexServer(plex_base_url, plex_token)

    all_recent_episodes = []

    for library_name in plex_tv_library_names:
        library_episodes = get_recent_episodes_from_library(
            plex=plex,
            library_name=library_name,
        )

        all_recent_episodes.extend(library_episodes)

    grouped = group_episodes(all_recent_episodes)

    print_grouped_episodes(
        grouped=grouped,
        start_date=start_date,
        end_date=end_date,
    )

    print("Prueba de series finalizada correctamente.")


if __name__ == "__main__":
    main()