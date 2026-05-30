from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from plexapi.exceptions import NotFound
from plexapi.server import PlexServer


# ------------------------------------------------------------
# Versión del exportador
# ------------------------------------------------------------

EXPORTER_VERSION = "v2.2 GUID Legacy + Fecha Desempate"


# ------------------------------------------------------------
# Rutas base del proyecto
# ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


# ------------------------------------------------------------
# Configuración / utilitarios
# ------------------------------------------------------------

def get_env_var(name: str, required: bool = True, default: str | None = None) -> str:
    value = os.getenv(name, default)

    if required and not value:
        raise RuntimeError(f"Falta configurar la variable {name} en el archivo .env")

    return value or ""


def parse_library_names(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def parse_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None or raw_value == "":
        return default

    return raw_value.strip().lower() in {"1", "true", "yes", "y", "si", "sí"}


def parse_int(raw_value: str | None, default: int = 0) -> int:
    if raw_value is None or raw_value == "":
        return default

    try:
        return int(raw_value)
    except ValueError:
        return default


def resolve_output_dir(output_dir_raw: str) -> Path:
    """
    Resuelve la carpeta de salida siempre desde la raíz del proyecto.
    Esto evita que PyCharm cree accidentalmente /src/output.
    """

    output_path = Path(output_dir_raw)

    if output_path.is_absolute():
        return output_path

    return PROJECT_ROOT / output_path


def safe_attr(obj: Any, attr: str, default: Any = None) -> Any:
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def safe_date(value: Any) -> str | None:
    if not value:
        return None

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    try:
        return value.strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def safe_datetime(value: Any) -> str | None:
    if not value:
        return None

    try:
        return value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def coerce_int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except Exception:
        return None


def make_episode_label(season_number: Any, episode_number: Any) -> str | None:
    season_int = coerce_int(season_number)
    episode_int = coerce_int(episode_number)

    if season_int is None or episode_int is None:
        return None

    return f"S{season_int:02d}E{episode_int:02d}"


def extract_date_for_sort(value: Any) -> date | None:
    """
    Devuelve un objeto date para poder ordenar episodios por fecha.
    Plex normalmente entrega originallyAvailableAt como date/datetime.
    """

    if not value:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    return None


def sort_number(value: int | None) -> int:
    """
    Normaliza valores None para ordenar sin romper.
    """

    if value is None:
        return -1

    return value


# ------------------------------------------------------------
# Limpieza segura para Excel / OpenXML
# ------------------------------------------------------------

EXCEL_MAX_CELL_LENGTH = 32000


def is_valid_xml_char(char: str) -> bool:
    """
    XML 1.0 permite:
    - tab, LF, CR
    - caracteres desde 0x20 a 0xD7FF
    - caracteres desde 0xE000 a 0xFFFD
    - caracteres desde 0x10000 a 0x10FFFF

    Excel puede fallar al abrir si recibe caracteres de control,
    nulos o surrogates inválidos en strings.
    """

    codepoint = ord(char)

    return (
        codepoint == 0x09
        or codepoint == 0x0A
        or codepoint == 0x0D
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def clean_excel_value(value: Any) -> Any:
    """
    Limpia valores antes de escribirlos a Excel para evitar archivos corruptos.
    Solo transforma strings; mantiene números y fechas como están.
    """

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if not isinstance(value, str):
        return value

    cleaned = "".join(char for char in value if is_valid_xml_char(char))

    if len(cleaned) > EXCEL_MAX_CELL_LENGTH:
        cleaned = cleaned[:EXCEL_MAX_CELL_LENGTH] + " [TRUNCADO]"

    return cleaned


def sanitize_dataframe_for_excel(df: pd.DataFrame, df_name: str) -> pd.DataFrame:
    """
    Sanitiza todo el DataFrame antes de exportarlo.
    """

    if df.empty:
        return df

    print(f"Sanitizando datos para Excel: {df_name}")

    sanitized_df = df.copy()

    for column in sanitized_df.columns:
        sanitized_df[column] = sanitized_df[column].map(clean_excel_value)

    return sanitized_df


# ------------------------------------------------------------
# GUIDs externos
# ------------------------------------------------------------

def parse_external_guid_value(guid_value: str, result: dict[str, Any]) -> None:
    """
    Intenta identificar IMDb, TMDb y TVDb desde distintos formatos posibles.

    Formatos habituales modernos:
    - imdb://tt0805669
    - tmdb://12345
    - tvdb://67890

    Formatos legacy posibles:
    - com.plexapp.agents.themoviedb://93736?lang=en
    - com.plexapp.agents.thetvdb://12345?lang=en
    - com.plexapp.agents.imdb://tt1234567?lang=en
    """

    if not guid_value:
        return

    guid_lower = guid_value.lower()

    imdb_match = re.search(r"imdb://(tt\d+)", guid_lower)
    if imdb_match and not result["IMDb ID"]:
        result["IMDb ID"] = imdb_match.group(1)

    tmdb_match = re.search(r"(?:tmdb|themoviedb)://(\d+)", guid_lower)
    if tmdb_match and not result["TMDb ID"]:
        result["TMDb ID"] = tmdb_match.group(1)

    tvdb_match = re.search(r"(?:tvdb|thetvdb)://(\d+)", guid_lower)
    if tvdb_match and not result["TVDb ID"]:
        result["TVDb ID"] = tvdb_match.group(1)


def get_external_guids(item: Any) -> dict[str, Any]:
    result = {
        "IMDb ID": None,
        "TMDb ID": None,
        "TVDb ID": None,
        "Plex GUID Principal": None,
        "Guids Externos": None,
        "Tipo Identificador Preferente": None,
        "Identificador Preferente": None,
    }

    main_guid = safe_attr(item, "guid")
    result["Plex GUID Principal"] = main_guid

    guid_values: list[str] = []

    if main_guid:
        main_guid_text = str(main_guid)
        guid_values.append(main_guid_text)
        parse_external_guid_value(main_guid_text, result)

    guids = safe_attr(item, "guids", []) or []

    for guid_item in guids:
        guid_value = safe_attr(guid_item, "id")

        if not guid_value:
            continue

        guid_text = str(guid_value)
        guid_values.append(guid_text)
        parse_external_guid_value(guid_text, result)

    unique_guid_values = list(dict.fromkeys(guid_values))
    result["Guids Externos"] = "; ".join(unique_guid_values) if unique_guid_values else None

    if result["IMDb ID"]:
        result["Tipo Identificador Preferente"] = "IMDb"
        result["Identificador Preferente"] = result["IMDb ID"]
    elif result["TMDb ID"]:
        result["Tipo Identificador Preferente"] = "TMDb"
        result["Identificador Preferente"] = result["TMDb ID"]
    elif result["TVDb ID"]:
        result["Tipo Identificador Preferente"] = "TVDb"
        result["Identificador Preferente"] = result["TVDb ID"]

    return result


# ------------------------------------------------------------
# Metadata técnica de episodios
# ------------------------------------------------------------

def get_episode_file_info(episode: Any) -> dict[str, Any]:
    """
    Extrae información técnica básica del archivo del episodio.
    Si no encuentra media/parts, retorna valores vacíos sin romper el script.
    """

    result = {
        "Resolucion": None,
        "Codec Video": None,
        "Codec Audio": None,
        "Contenedor": None,
        "Bitrate": None,
        "Archivo": None,
        "Nombre Archivo": None,
    }

    media_items = safe_attr(episode, "media", []) or []
    if not media_items:
        return result

    media = media_items[0]

    result["Resolucion"] = safe_attr(media, "videoResolution")
    result["Codec Video"] = safe_attr(media, "videoCodec")
    result["Codec Audio"] = safe_attr(media, "audioCodec")
    result["Contenedor"] = safe_attr(media, "container")
    result["Bitrate"] = safe_attr(media, "bitrate")

    parts = safe_attr(media, "parts", []) or []
    if parts:
        file_path = safe_attr(parts[0], "file")
        result["Archivo"] = file_path
        result["Nombre Archivo"] = Path(file_path).name if file_path else None

    return result


# ------------------------------------------------------------
# Cálculos por serie
# ------------------------------------------------------------

def calculate_show_episode_summary(episode_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Calcula varios criterios de último episodio.

    Criterios:
    - Último por orden general: máximo Temporada/Episodio, incluyendo S00.
    - Último por orden regular: máximo Temporada/Episodio, excluyendo S00.
    - Último por fecha emisión: fecha más reciente disponible, incluyendo especiales.
    - Último por fecha emisión regular: fecha más reciente, excluyendo S00.

    En caso de empate por fecha, usa Temporada/Episodio como desempate.
    Esto ayuda con temporadas publicadas completas el mismo día, como ocurre
    frecuentemente en Netflix y otros servicios de streaming.
    """

    result = {
        "Temporadas Todas en Plex": 0,
        "Temporadas Regulares en Plex": 0,
        "Episodios en Plex": len(episode_summaries),
        "Episodios Especiales S00": 0,
        "Episodios sin Fecha Emision": 0,
        "Tiene Especiales S00": "No",
        "Tiene Episodios sin Fecha": "No",
        "Ultimo Episodio por Orden": None,
        "Fecha Ultimo Episodio por Orden": None,
        "Ultimo Episodio Regular por Orden": None,
        "Fecha Ultimo Episodio Regular por Orden": None,
        "Ultimo Episodio por Fecha Emision": None,
        "Fecha Maxima Emision Plex": None,
        "Ultimo Episodio Regular por Fecha Emision": None,
        "Fecha Maxima Emision Regular Plex": None,
        "Diferencia Orden vs Fecha": "No",
        "Posible Revision Metadata": "No",
    }

    if not episode_summaries:
        return result

    seasons_all = {
        item["season_number"]
        for item in episode_summaries
        if item["season_number"] is not None
    }

    seasons_regular = {
        item["season_number"]
        for item in episode_summaries
        if item["season_number"] is not None and item["season_number"] > 0
    }

    result["Temporadas Todas en Plex"] = len(seasons_all)
    result["Temporadas Regulares en Plex"] = len(seasons_regular)

    specials_count = sum(1 for item in episode_summaries if item["season_number"] == 0)
    no_date_count = sum(1 for item in episode_summaries if item["air_date_sort"] is None)

    result["Episodios Especiales S00"] = specials_count
    result["Episodios sin Fecha Emision"] = no_date_count
    result["Tiene Especiales S00"] = "Sí" if specials_count > 0 else "No"
    result["Tiene Episodios sin Fecha"] = "Sí" if no_date_count > 0 else "No"

    valid_order_all = [
        item
        for item in episode_summaries
        if item["season_number"] is not None and item["episode_number"] is not None
    ]

    valid_order_regular = [
        item
        for item in valid_order_all
        if item["season_number"] > 0
    ]

    valid_date_all = [
        item
        for item in episode_summaries
        if item["air_date_sort"] is not None
    ]

    valid_date_regular = [
        item
        for item in valid_date_all
        if item["season_number"] is not None and item["season_number"] > 0
    ]

    if valid_order_all:
        last_by_order = max(
            valid_order_all,
            key=lambda item: (
                sort_number(item["season_number"]),
                sort_number(item["episode_number"]),
            ),
        )
        result["Ultimo Episodio por Orden"] = last_by_order["episode_label"]
        result["Fecha Ultimo Episodio por Orden"] = safe_date(last_by_order["air_date_sort"])

    if valid_order_regular:
        last_regular_by_order = max(
            valid_order_regular,
            key=lambda item: (
                sort_number(item["season_number"]),
                sort_number(item["episode_number"]),
            ),
        )
        result["Ultimo Episodio Regular por Orden"] = last_regular_by_order["episode_label"]
        result["Fecha Ultimo Episodio Regular por Orden"] = safe_date(last_regular_by_order["air_date_sort"])

    if valid_date_all:
        last_by_date = max(
            valid_date_all,
            key=lambda item: (
                item["air_date_sort"],
                sort_number(item["season_number"]),
                sort_number(item["episode_number"]),
            ),
        )
        result["Ultimo Episodio por Fecha Emision"] = last_by_date["episode_label"]
        result["Fecha Maxima Emision Plex"] = safe_date(last_by_date["air_date_sort"])

    if valid_date_regular:
        last_regular_by_date = max(
            valid_date_regular,
            key=lambda item: (
                item["air_date_sort"],
                sort_number(item["season_number"]),
                sort_number(item["episode_number"]),
            ),
        )
        result["Ultimo Episodio Regular por Fecha Emision"] = last_regular_by_date["episode_label"]
        result["Fecha Maxima Emision Regular Plex"] = safe_date(last_regular_by_date["air_date_sort"])

    if (
        result["Ultimo Episodio Regular por Orden"]
        and result["Ultimo Episodio Regular por Fecha Emision"]
        and result["Ultimo Episodio Regular por Orden"] != result["Ultimo Episodio Regular por Fecha Emision"]
    ):
        result["Diferencia Orden vs Fecha"] = "Sí"

    if result["Diferencia Orden vs Fecha"] == "Sí" or no_date_count > 0:
        result["Posible Revision Metadata"] = "Sí"

    return result


# ------------------------------------------------------------
# Conexión Plex
# ------------------------------------------------------------

def connect_to_plex() -> PlexServer:
    plex_base_url = get_env_var("PLEX_BASE_URL")
    plex_token = get_env_var("PLEX_TOKEN")

    print("Conectando a Plex...")
    plex = PlexServer(plex_base_url, plex_token)

    print(f"Conectado a: {plex.friendlyName}")
    return plex


# ------------------------------------------------------------
# Export principal
# ------------------------------------------------------------

def export_series_from_libraries(
    plex: PlexServer,
    library_names: list[str],
    process_only_show_libraries: bool,
    max_series: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    episode_rows: list[dict[str, Any]] = []
    series_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    available_sections = plex.library.sections()

    print("")
    print("Bibliotecas disponibles en Plex:")
    for section in available_sections:
        section_title = safe_attr(section, "title")
        section_type = safe_attr(section, "TYPE", safe_attr(section, "type", "desconocido"))
        print(f" - {section_title} ({section_type})")

    for library_name in library_names:
        print("")
        print("=" * 70)
        print(f"Procesando biblioteca configurada: {library_name}")
        print("=" * 70)

        try:
            section = plex.library.section(library_name)
        except NotFound:
            print(f"ADVERTENCIA: No se encontró la biblioteca '{library_name}'. Se omite.")
            summary_rows.append(
                {
                    "Biblioteca": library_name,
                    "Estado": "No encontrada",
                    "Tipo Plex": None,
                    "Series Exportadas": 0,
                    "Episodios Exportados": 0,
                    "Observacion": "La biblioteca configurada no existe en Plex.",
                }
            )
            continue

        section_type = safe_attr(section, "TYPE", safe_attr(section, "type", None))

        if process_only_show_libraries and section_type != "show":
            print(
                f"ADVERTENCIA: La biblioteca '{library_name}' es tipo '{section_type}', "
                "no tipo 'show'. Se omite para evitar procesar películas, música u otras bibliotecas."
            )
            summary_rows.append(
                {
                    "Biblioteca": library_name,
                    "Estado": "Omitida",
                    "Tipo Plex": section_type,
                    "Series Exportadas": 0,
                    "Episodios Exportados": 0,
                    "Observacion": "Omitida porque no es biblioteca de series.",
                }
            )
            continue

        try:
            shows = section.all()
        except Exception as exc:
            print(f"ERROR: No se pudo leer la biblioteca '{library_name}': {exc}")
            summary_rows.append(
                {
                    "Biblioteca": library_name,
                    "Estado": "Error",
                    "Tipo Plex": section_type,
                    "Series Exportadas": 0,
                    "Episodios Exportados": 0,
                    "Observacion": str(exc),
                }
            )
            continue

        if max_series > 0:
            shows = shows[:max_series]
            print(f"Modo prueba activo: se procesarán solo las primeras {max_series} series.")

        total_shows = len(shows)
        library_series_start = len(series_rows)
        library_episodes_start = len(episode_rows)

        print(f"Series encontradas/procesables en '{library_name}': {total_shows}")

        for show_index, show in enumerate(shows, start=1):
            show_title = safe_attr(show, "title")
            show_year = safe_attr(show, "year")
            show_rating_key = safe_attr(show, "ratingKey")
            show_summary = safe_attr(show, "summary")
            show_added_at = safe_datetime(safe_attr(show, "addedAt"))
            show_updated_at = safe_datetime(safe_attr(show, "updatedAt"))
            show_content_rating = safe_attr(show, "contentRating")
            show_studio = safe_attr(show, "studio")
            show_rating = safe_attr(show, "rating")
            show_audience_rating = safe_attr(show, "audienceRating")

            external_guids = get_external_guids(show)

            print(f"[{show_index}/{total_shows}] Procesando serie: {show_title}")

            episode_summaries_for_show: list[dict[str, Any]] = []

            try:
                seasons = show.seasons()
            except Exception as exc:
                print(f"  ADVERTENCIA: No se pudieron leer temporadas de '{show_title}': {exc}")
                seasons = []

            for season in seasons:
                season_number_raw = safe_attr(season, "seasonNumber", safe_attr(season, "index"))
                season_number = coerce_int(season_number_raw)

                try:
                    episodes = season.episodes()
                except Exception as exc:
                    print(
                        f"  ADVERTENCIA: No se pudieron leer episodios de "
                        f"'{show_title}' temporada {season_number}: {exc}"
                    )
                    episodes = []

                for episode in episodes:
                    episode_number_raw = safe_attr(episode, "episodeNumber", safe_attr(episode, "index"))
                    episode_number = coerce_int(episode_number_raw)

                    episode_title = safe_attr(episode, "title")
                    originally_available_at_raw = safe_attr(episode, "originallyAvailableAt")
                    originally_available_at = safe_date(originally_available_at_raw)
                    air_date_sort = extract_date_for_sort(originally_available_at_raw)

                    episode_added_at = safe_datetime(safe_attr(episode, "addedAt"))
                    episode_updated_at = safe_datetime(safe_attr(episode, "updatedAt"))
                    view_count = safe_attr(episode, "viewCount", 0) or 0
                    last_viewed_at = safe_datetime(safe_attr(episode, "lastViewedAt"))

                    duration_ms = safe_attr(episode, "duration")
                    duration_min = round(duration_ms / 60000, 1) if duration_ms else None

                    episode_label = make_episode_label(season_number, episode_number)

                    file_info = get_episode_file_info(episode)

                    episode_summaries_for_show.append(
                        {
                            "season_number": season_number,
                            "episode_number": episode_number,
                            "episode_label": episode_label,
                            "air_date_sort": air_date_sort,
                        }
                    )

                    episode_rows.append(
                        {
                            "Biblioteca": library_name,
                            "Serie": show_title,
                            "Año Serie": show_year,
                            "Tipo Identificador Preferente Serie": external_guids["Tipo Identificador Preferente"],
                            "Identificador Preferente Serie": external_guids["Identificador Preferente"],
                            "IMDb ID Serie": external_guids["IMDb ID"],
                            "TMDb ID Serie": external_guids["TMDb ID"],
                            "TVDb ID Serie": external_guids["TVDb ID"],
                            "Plex RatingKey Serie": show_rating_key,
                            "Plex GUID Principal Serie": external_guids["Plex GUID Principal"],
                            "Guids Externos Serie": external_guids["Guids Externos"],
                            "Temporada": season_number,
                            "Episodio": episode_number,
                            "Codigo Episodio": episode_label,
                            "Titulo Episodio": episode_title,
                            "Fecha Emision Original": originally_available_at,
                            "Fecha Agregado Plex": episode_added_at,
                            "Fecha Actualizado Plex": episode_updated_at,
                            "Visto": "Sí" if view_count > 0 else "No",
                            "Cantidad Vistas": view_count,
                            "Ultima Vista": last_viewed_at,
                            "Duracion Min": duration_min,
                            "Resolucion": file_info["Resolucion"],
                            "Codec Video": file_info["Codec Video"],
                            "Codec Audio": file_info["Codec Audio"],
                            "Contenedor": file_info["Contenedor"],
                            "Bitrate": file_info["Bitrate"],
                            "Nombre Archivo": file_info["Nombre Archivo"],
                            "Archivo": file_info["Archivo"],
                        }
                    )

            show_episode_summary = calculate_show_episode_summary(episode_summaries_for_show)

            series_rows.append(
                {
                    "Biblioteca": library_name,
                    "Serie": show_title,
                    "Año Serie": show_year,
                    "Tipo Identificador Preferente": external_guids["Tipo Identificador Preferente"],
                    "Identificador Preferente": external_guids["Identificador Preferente"],
                    "IMDb ID": external_guids["IMDb ID"],
                    "TMDb ID": external_guids["TMDb ID"],
                    "TVDb ID": external_guids["TVDb ID"],
                    "Plex RatingKey Serie": show_rating_key,
                    "Plex GUID Principal": external_guids["Plex GUID Principal"],
                    "Guids Externos": external_guids["Guids Externos"],
                    "Temporadas Todas en Plex": show_episode_summary["Temporadas Todas en Plex"],
                    "Temporadas Regulares en Plex": show_episode_summary["Temporadas Regulares en Plex"],
                    "Episodios en Plex": show_episode_summary["Episodios en Plex"],
                    "Episodios Especiales S00": show_episode_summary["Episodios Especiales S00"],
                    "Episodios sin Fecha Emision": show_episode_summary["Episodios sin Fecha Emision"],
                    "Tiene Especiales S00": show_episode_summary["Tiene Especiales S00"],
                    "Tiene Episodios sin Fecha": show_episode_summary["Tiene Episodios sin Fecha"],
                    "Ultimo Episodio por Orden": show_episode_summary["Ultimo Episodio por Orden"],
                    "Fecha Ultimo Episodio por Orden": show_episode_summary["Fecha Ultimo Episodio por Orden"],
                    "Ultimo Episodio Regular por Orden": show_episode_summary["Ultimo Episodio Regular por Orden"],
                    "Fecha Ultimo Episodio Regular por Orden": show_episode_summary[
                        "Fecha Ultimo Episodio Regular por Orden"
                    ],
                    "Ultimo Episodio por Fecha Emision": show_episode_summary["Ultimo Episodio por Fecha Emision"],
                    "Fecha Maxima Emision Plex": show_episode_summary["Fecha Maxima Emision Plex"],
                    "Ultimo Episodio Regular por Fecha Emision": show_episode_summary[
                        "Ultimo Episodio Regular por Fecha Emision"
                    ],
                    "Fecha Maxima Emision Regular Plex": show_episode_summary[
                        "Fecha Maxima Emision Regular Plex"
                    ],
                    "Diferencia Orden vs Fecha": show_episode_summary["Diferencia Orden vs Fecha"],
                    "Posible Revision Metadata": show_episode_summary["Posible Revision Metadata"],
                    "Fecha Agregado Plex": show_added_at,
                    "Fecha Actualizado Plex": show_updated_at,
                    "Clasificacion Contenido": show_content_rating,
                    "Studio": show_studio,
                    "Rating Plex": show_rating,
                    "Audience Rating Plex": show_audience_rating,
                    "Resumen Plex": show_summary,
                }
            )

            if show_index % 25 == 0 or show_index == total_shows:
                print(
                    f"Avance '{library_name}': "
                    f"{show_index}/{total_shows} series procesadas | "
                    f"{len(series_rows) - library_series_start} series de esta biblioteca | "
                    f"{len(episode_rows) - library_episodes_start} episodios de esta biblioteca | "
                    f"{len(episode_rows)} episodios acumulados"
                )

        exported_series = len(series_rows) - library_series_start
        exported_episodes = len(episode_rows) - library_episodes_start

        print("")
        print(f"Biblioteca '{library_name}' finalizada.")
        print(f"Series exportadas en biblioteca: {exported_series}")
        print(f"Episodios exportados en biblioteca: {exported_episodes}")

        summary_rows.append(
            {
                "Biblioteca": library_name,
                "Estado": "Exportada",
                "Tipo Plex": section_type,
                "Series Exportadas": exported_series,
                "Episodios Exportados": exported_episodes,
                "Observacion": None,
            }
        )

    return (
        pd.DataFrame(series_rows),
        pd.DataFrame(episode_rows),
        pd.DataFrame(summary_rows),
    )


# ------------------------------------------------------------
# Escritura Excel
# ------------------------------------------------------------

def autosize_excel_columns(workbook: Any, sheet_names: list[str]) -> None:
    for sheet_name in sheet_names:
        worksheet = workbook[sheet_name]
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        for column_cells in worksheet.columns:
            max_length = 0
            column_letter = column_cells[0].column_letter

            for cell in column_cells:
                value = "" if cell.value is None else str(cell.value)
                max_length = max(max_length, len(value))

            worksheet.column_dimensions[column_letter].width = min(max_length + 2, 70)


def write_excel(
    series_df: pd.DataFrame,
    episodes_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"plex_series_export_{timestamp}.xlsx"

    print("")
    print("Preparando datos para Excel...")

    series_df = sanitize_dataframe_for_excel(series_df, "Series")
    episodes_df = sanitize_dataframe_for_excel(episodes_df, "Episodios")
    summary_df = sanitize_dataframe_for_excel(summary_df, "Bibliotecas")

    run_summary_df = pd.DataFrame(
        [
            {
                "Campo": "Fecha ejecución",
                "Valor": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            {
                "Campo": "Raíz proyecto",
                "Valor": str(PROJECT_ROOT),
            },
            {
                "Campo": "Series exportadas",
                "Valor": len(series_df),
            },
            {
                "Campo": "Episodios exportados",
                "Valor": len(episodes_df),
            },
            {
                "Campo": "Versión exportador",
                "Valor": EXPORTER_VERSION,
            },
        ]
    )

    run_summary_df = sanitize_dataframe_for_excel(run_summary_df, "Resumen")

    print("")
    print("Generando archivo Excel...")
    print(f"Carpeta de salida: {output_dir}")
    print(f"Archivo destino: {output_file}")

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        run_summary_df.to_excel(writer, index=False, sheet_name="Resumen")
        summary_df.to_excel(writer, index=False, sheet_name="Bibliotecas")
        series_df.to_excel(writer, index=False, sheet_name="Series")
        episodes_df.to_excel(writer, index=False, sheet_name="Episodios")

        workbook = writer.book
        autosize_excel_columns(
            workbook=workbook,
            sheet_names=["Resumen", "Bibliotecas", "Series", "Episodios"],
        )

    return output_file


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    print("Inicio de exportación Plex")
    print(f"Versión exportador: {EXPORTER_VERSION}")
    print(f"Raíz del proyecto detectada: {PROJECT_ROOT}")

    library_names_raw = get_env_var("PLEX_LIBRARY_NAMES")
    output_dir_raw = get_env_var("OUTPUT_DIR", required=False, default="output")

    process_only_show_libraries = parse_bool(
        os.getenv("PLEX_PROCESS_ONLY_SHOW_LIBRARIES"),
        default=True,
    )

    max_series = parse_int(
        os.getenv("MAX_SERIES"),
        default=0,
    )

    library_names = parse_library_names(library_names_raw)
    if not library_names:
        raise RuntimeError("No hay bibliotecas configuradas en PLEX_LIBRARY_NAMES")

    output_dir = resolve_output_dir(output_dir_raw)

    print(f"Bibliotecas configuradas: {', '.join(library_names)}")
    print(f"Procesar solo bibliotecas tipo show: {'Sí' if process_only_show_libraries else 'No'}")
    print(f"MAX_SERIES: {max_series if max_series > 0 else 'Sin límite'}")
    print(f"OUTPUT_DIR configurado: {output_dir_raw}")
    print(f"OUTPUT_DIR resuelto: {output_dir}")

    plex = connect_to_plex()

    series_df, episodes_df, summary_df = export_series_from_libraries(
        plex=plex,
        library_names=library_names,
        process_only_show_libraries=process_only_show_libraries,
        max_series=max_series,
    )

    output_file = write_excel(
        series_df=series_df,
        episodes_df=episodes_df,
        summary_df=summary_df,
        output_dir=output_dir,
    )

    print("")
    print("=" * 70)
    print("Exportación finalizada correctamente.")
    print(f"Versión exportador: {EXPORTER_VERSION}")
    print(f"Series exportadas: {len(series_df)}")
    print(f"Episodios exportados: {len(episodes_df)}")
    print(f"Archivo generado: {output_file}")
    print("=" * 70)


if __name__ == "__main__":
    main()