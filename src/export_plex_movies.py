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

EXPORTER_VERSION = "v1.1 Movies Export + Progress"


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


def format_duration_minutes(duration_ms: Any) -> float | None:
    if not duration_ms:
        return None

    try:
        return round(float(duration_ms) / 60000, 1)
    except Exception:
        return None


def format_file_size_mb(size_bytes: Any) -> float | None:
    if not size_bytes:
        return None

    try:
        return round(float(size_bytes) / (1024 * 1024), 2)
    except Exception:
        return None


def format_file_size_gb(size_bytes: Any) -> float | None:
    if not size_bytes:
        return None

    try:
        return round(float(size_bytes) / (1024 * 1024 * 1024), 3)
    except Exception:
        return None


def extract_volume_from_path(file_path: str | None) -> str | None:
    """
    En macOS, las rutas suelen venir como:
    /Volumes/Expansion/Movies/...
    /Volumes/Seagate Media/Movies/...

    Retorna el nombre del volumen si lo puede detectar.
    """

    if not file_path:
        return None

    path = Path(file_path)
    parts = path.parts

    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "Volumes":
        return parts[2]

    return None


def format_seconds(seconds: float | int | None) -> str:
    """
    Formatea segundos como HH:MM:SS.
    """

    if seconds is None:
        return "--:--:--"

    try:
        seconds_int = int(seconds)
    except Exception:
        return "--:--:--"

    if seconds_int < 0:
        seconds_int = 0

    hours = seconds_int // 3600
    minutes = (seconds_int % 3600) // 60
    secs = seconds_int % 60

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def calculate_eta(start_time: datetime, processed: int, total: int) -> str:
    """
    Calcula ETA simple basado en promedio real.
    """

    if processed <= 0 or total <= 0:
        return "--:--:--"

    elapsed_seconds = (datetime.now() - start_time).total_seconds()
    avg_seconds_per_item = elapsed_seconds / processed
    remaining_items = max(total - processed, 0)
    eta_seconds = remaining_items * avg_seconds_per_item

    return format_seconds(eta_seconds)


def calculate_percent(processed: int, total: int) -> float:
    if total <= 0:
        return 0.0

    return round((processed / total) * 100, 1)


# ------------------------------------------------------------
# Limpieza segura para Excel / OpenXML
# ------------------------------------------------------------

EXCEL_MAX_CELL_LENGTH = 32000


def is_valid_xml_char(char: str) -> bool:
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
    Intenta identificar IMDb, TMDb y TVDb desde formatos modernos y legacy.

    Modernos:
    - imdb://tt1234567
    - tmdb://12345
    - tvdb://12345

    Legacy:
    - com.plexapp.agents.imdb://tt1234567?lang=en
    - com.plexapp.agents.themoviedb://12345?lang=en
    - com.plexapp.agents.thetvdb://12345?lang=en
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
# Metadata técnica de películas
# ------------------------------------------------------------

def get_movie_file_info(movie: Any) -> dict[str, Any]:
    """
    Extrae metadata técnica del primer archivo asociado a la película.
    Si hay versiones múltiples, por ahora toma la primera.
    """

    result = {
        "Resolucion": None,
        "Codec Video": None,
        "Codec Audio": None,
        "Contenedor": None,
        "Bitrate": None,
        "Ancho Video": None,
        "Alto Video": None,
        "Aspect Ratio": None,
        "Canales Audio": None,
        "Archivo": None,
        "Nombre Archivo": None,
        "Volumen": None,
        "Tamaño Bytes": None,
        "Tamaño MB": None,
        "Tamaño GB": None,
        "Cantidad Media Items": 0,
        "Cantidad Partes": 0,
    }

    media_items = safe_attr(movie, "media", []) or []
    result["Cantidad Media Items"] = len(media_items)

    if not media_items:
        return result

    media = media_items[0]

    result["Resolucion"] = safe_attr(media, "videoResolution")
    result["Codec Video"] = safe_attr(media, "videoCodec")
    result["Codec Audio"] = safe_attr(media, "audioCodec")
    result["Contenedor"] = safe_attr(media, "container")
    result["Bitrate"] = safe_attr(media, "bitrate")
    result["Ancho Video"] = safe_attr(media, "width")
    result["Alto Video"] = safe_attr(media, "height")
    result["Aspect Ratio"] = safe_attr(media, "aspectRatio")
    result["Canales Audio"] = safe_attr(media, "audioChannels")

    parts = safe_attr(media, "parts", []) or []
    result["Cantidad Partes"] = len(parts)

    if parts:
        part = parts[0]
        file_path = safe_attr(part, "file")
        size_bytes = safe_attr(part, "size")

        result["Archivo"] = file_path
        result["Nombre Archivo"] = Path(file_path).name if file_path else None
        result["Volumen"] = extract_volume_from_path(file_path)
        result["Tamaño Bytes"] = size_bytes
        result["Tamaño MB"] = format_file_size_mb(size_bytes)
        result["Tamaño GB"] = format_file_size_gb(size_bytes)

    return result


def get_genres(movie: Any) -> str | None:
    genres = safe_attr(movie, "genres", []) or []
    values = []

    for genre in genres:
        tag = safe_attr(genre, "tag")
        if tag:
            values.append(str(tag))

    return "; ".join(values) if values else None


def get_directors(movie: Any) -> str | None:
    directors = safe_attr(movie, "directors", []) or []
    values = []

    for director in directors:
        tag = safe_attr(director, "tag")
        if tag:
            values.append(str(tag))

    return "; ".join(values) if values else None


def get_writers(movie: Any) -> str | None:
    writers = safe_attr(movie, "writers", []) or []
    values = []

    for writer in writers:
        tag = safe_attr(writer, "tag")
        if tag:
            values.append(str(tag))

    return "; ".join(values) if values else None


def get_collections(movie: Any) -> str | None:
    collections = safe_attr(movie, "collections", []) or []
    values = []

    for collection in collections:
        tag = safe_attr(collection, "tag")
        if tag:
            values.append(str(tag))

    return "; ".join(values) if values else None


def get_countries(movie: Any) -> str | None:
    countries = safe_attr(movie, "countries", []) or []
    values = []

    for country in countries:
        tag = safe_attr(country, "tag")
        if tag:
            values.append(str(tag))

    return "; ".join(values) if values else None


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

def export_movies_from_libraries(
    plex: PlexServer,
    library_names: list[str],
    process_only_movie_libraries: bool,
    max_movies: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    movie_rows: list[dict[str, Any]] = []
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
                    "Peliculas Exportadas": 0,
                    "Tamaño Total GB": 0,
                    "Observacion": "La biblioteca configurada no existe en Plex.",
                }
            )
            continue

        section_type = safe_attr(section, "TYPE", safe_attr(section, "type", None))

        if process_only_movie_libraries and section_type != "movie":
            print(
                f"ADVERTENCIA: La biblioteca '{library_name}' es tipo '{section_type}', "
                "no tipo 'movie'. Se omite para evitar procesar series, música u otras bibliotecas."
            )
            summary_rows.append(
                {
                    "Biblioteca": library_name,
                    "Estado": "Omitida",
                    "Tipo Plex": section_type,
                    "Peliculas Exportadas": 0,
                    "Tamaño Total GB": 0,
                    "Observacion": "Omitida porque no es biblioteca de películas.",
                }
            )
            continue

        try:
            movies = section.all()
        except Exception as exc:
            print(f"ERROR: No se pudo leer la biblioteca '{library_name}': {exc}")
            summary_rows.append(
                {
                    "Biblioteca": library_name,
                    "Estado": "Error",
                    "Tipo Plex": section_type,
                    "Peliculas Exportadas": 0,
                    "Tamaño Total GB": 0,
                    "Observacion": str(exc),
                }
            )
            continue

        if max_movies > 0:
            movies = movies[:max_movies]
            print(f"Modo prueba activo: se procesarán solo las primeras {max_movies} películas.")

        total_movies = len(movies)
        library_movies_start = len(movie_rows)
        library_start_time = datetime.now()

        print(f"Películas encontradas/procesables en '{library_name}': {total_movies}")

        for movie_index, movie in enumerate(movies, start=1):
            title = safe_attr(movie, "title")
            original_title = safe_attr(movie, "originalTitle")
            year = safe_attr(movie, "year")
            rating_key = safe_attr(movie, "ratingKey")
            summary = safe_attr(movie, "summary")
            tagline = safe_attr(movie, "tagline")
            content_rating = safe_attr(movie, "contentRating")
            studio = safe_attr(movie, "studio")
            rating = safe_attr(movie, "rating")
            audience_rating = safe_attr(movie, "audienceRating")
            user_rating = safe_attr(movie, "userRating")
            duration_min = format_duration_minutes(safe_attr(movie, "duration"))
            originally_available_at = safe_date(safe_attr(movie, "originallyAvailableAt"))
            added_at = safe_datetime(safe_attr(movie, "addedAt"))
            updated_at = safe_datetime(safe_attr(movie, "updatedAt"))
            view_count = safe_attr(movie, "viewCount", 0) or 0
            last_viewed_at = safe_datetime(safe_attr(movie, "lastViewedAt"))

            external_guids = get_external_guids(movie)
            file_info = get_movie_file_info(movie)

            current_percent = calculate_percent(movie_index, total_movies)
            elapsed = format_seconds((datetime.now() - library_start_time).total_seconds())
            eta = calculate_eta(library_start_time, movie_index, total_movies)

            print(
                f"[{movie_index}/{total_movies}] "
                f"{current_percent}% | "
                f"Transcurrido {elapsed} | "
                f"ETA {eta} | "
                f"Procesando película: {title}"
            )

            movie_rows.append(
                {
                    "Biblioteca": library_name,
                    "Titulo": title,
                    "Titulo Original": original_title,
                    "Año": year,
                    "Tipo Identificador Preferente": external_guids["Tipo Identificador Preferente"],
                    "Identificador Preferente": external_guids["Identificador Preferente"],
                    "IMDb ID": external_guids["IMDb ID"],
                    "TMDb ID": external_guids["TMDb ID"],
                    "TVDb ID": external_guids["TVDb ID"],
                    "Plex RatingKey": rating_key,
                    "Plex GUID Principal": external_guids["Plex GUID Principal"],
                    "Guids Externos": external_guids["Guids Externos"],
                    "Fecha Estreno Original": originally_available_at,
                    "Duracion Min": duration_min,
                    "Visto": "Sí" if view_count > 0 else "No",
                    "Cantidad Vistas": view_count,
                    "Ultima Vista": last_viewed_at,
                    "Fecha Agregado Plex": added_at,
                    "Fecha Actualizado Plex": updated_at,
                    "Clasificacion Contenido": content_rating,
                    "Studio": studio,
                    "Rating Plex": rating,
                    "Audience Rating Plex": audience_rating,
                    "User Rating Plex": user_rating,
                    "Generos": get_genres(movie),
                    "Directores": get_directors(movie),
                    "Escritores": get_writers(movie),
                    "Colecciones": get_collections(movie),
                    "Paises": get_countries(movie),
                    "Tagline": tagline,
                    "Resumen Plex": summary,
                    "Resolucion": file_info["Resolucion"],
                    "Codec Video": file_info["Codec Video"],
                    "Codec Audio": file_info["Codec Audio"],
                    "Contenedor": file_info["Contenedor"],
                    "Bitrate": file_info["Bitrate"],
                    "Ancho Video": file_info["Ancho Video"],
                    "Alto Video": file_info["Alto Video"],
                    "Aspect Ratio": file_info["Aspect Ratio"],
                    "Canales Audio": file_info["Canales Audio"],
                    "Cantidad Media Items": file_info["Cantidad Media Items"],
                    "Cantidad Partes": file_info["Cantidad Partes"],
                    "Tamaño Bytes": file_info["Tamaño Bytes"],
                    "Tamaño MB": file_info["Tamaño MB"],
                    "Tamaño GB": file_info["Tamaño GB"],
                    "Volumen": file_info["Volumen"],
                    "Nombre Archivo": file_info["Nombre Archivo"],
                    "Archivo": file_info["Archivo"],
                }
            )

            if movie_index % 100 == 0 or movie_index == total_movies:
                exported_so_far = len(movie_rows) - library_movies_start
                total_gb_so_far = sum(
                    row.get("Tamaño GB") or 0
                    for row in movie_rows[library_movies_start:]
                )
                progress_percent = calculate_percent(movie_index, total_movies)
                elapsed_summary = format_seconds((datetime.now() - library_start_time).total_seconds())
                eta_summary = calculate_eta(library_start_time, movie_index, total_movies)

                print(
                    ""
                    f"Avance '{library_name}': "
                    f"{movie_index}/{total_movies} películas | "
                    f"{progress_percent}% | "
                    f"{exported_so_far} exportadas | "
                    f"{round(total_gb_so_far, 2)} GB acumulados | "
                    f"Transcurrido {elapsed_summary} | "
                    f"ETA {eta_summary}"
                    ""
                )

        exported_movies = len(movie_rows) - library_movies_start
        total_gb = sum(
            row.get("Tamaño GB") or 0
            for row in movie_rows[library_movies_start:]
        )

        elapsed_library = format_seconds((datetime.now() - library_start_time).total_seconds())

        print("")
        print(f"Biblioteca '{library_name}' finalizada.")
        print(f"Películas exportadas en biblioteca: {exported_movies}")
        print(f"Tamaño total biblioteca: {round(total_gb, 2)} GB")
        print(f"Tiempo total biblioteca: {elapsed_library}")

        summary_rows.append(
            {
                "Biblioteca": library_name,
                "Estado": "Exportada",
                "Tipo Plex": section_type,
                "Peliculas Exportadas": exported_movies,
                "Tamaño Total GB": round(total_gb, 2),
                "Tiempo Ejecucion": elapsed_library,
                "Observacion": None,
            }
        )

    return (
        pd.DataFrame(movie_rows),
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
    movies_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"plex_movies_export_{timestamp}.xlsx"

    print("")
    print("Preparando datos para Excel...")

    movies_df = sanitize_dataframe_for_excel(movies_df, "Peliculas")
    summary_df = sanitize_dataframe_for_excel(summary_df, "Bibliotecas")

    total_movies = len(movies_df)
    total_gb = 0

    if not movies_df.empty and "Tamaño GB" in movies_df.columns:
        total_gb = movies_df["Tamaño GB"].fillna(0).sum()

    watched_count = 0
    unwatched_count = 0

    if not movies_df.empty and "Visto" in movies_df.columns:
        watched_count = int((movies_df["Visto"] == "Sí").sum())
        unwatched_count = int((movies_df["Visto"] == "No").sum())

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
                "Campo": "Películas exportadas",
                "Valor": total_movies,
            },
            {
                "Campo": "Películas vistas",
                "Valor": watched_count,
            },
            {
                "Campo": "Películas no vistas",
                "Valor": unwatched_count,
            },
            {
                "Campo": "Tamaño total GB",
                "Valor": round(float(total_gb), 2),
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
        movies_df.to_excel(writer, index=False, sheet_name="Peliculas")

        workbook = writer.book
        autosize_excel_columns(
            workbook=workbook,
            sheet_names=["Resumen", "Bibliotecas", "Peliculas"],
        )

    return output_file


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    start_time = datetime.now()

    print("Inicio de exportación de películas Plex")
    print(f"Versión exportador: {EXPORTER_VERSION}")
    print(f"Raíz del proyecto detectada: {PROJECT_ROOT}")

    library_names_raw = get_env_var("PLEX_MOVIE_LIBRARY_NAMES")
    output_dir_raw = get_env_var("OUTPUT_DIR", required=False, default="output")

    process_only_movie_libraries = parse_bool(
        os.getenv("PLEX_PROCESS_ONLY_MOVIE_LIBRARIES"),
        default=True,
    )

    max_movies = parse_int(
        os.getenv("MAX_MOVIES"),
        default=0,
    )

    library_names = parse_library_names(library_names_raw)
    if not library_names:
        raise RuntimeError("No hay bibliotecas configuradas en PLEX_MOVIE_LIBRARY_NAMES")

    output_dir = resolve_output_dir(output_dir_raw)

    print(f"Bibliotecas de películas configuradas: {', '.join(library_names)}")
    print(f"Procesar solo bibliotecas tipo movie: {'Sí' if process_only_movie_libraries else 'No'}")
    print(f"MAX_MOVIES: {max_movies if max_movies > 0 else 'Sin límite'}")
    print(f"OUTPUT_DIR configurado: {output_dir_raw}")
    print(f"OUTPUT_DIR resuelto: {output_dir}")

    plex = connect_to_plex()

    movies_df, summary_df = export_movies_from_libraries(
        plex=plex,
        library_names=library_names,
        process_only_movie_libraries=process_only_movie_libraries,
        max_movies=max_movies,
    )

    output_file = write_excel(
        movies_df=movies_df,
        summary_df=summary_df,
        output_dir=output_dir,
    )

    elapsed_total = format_seconds((datetime.now() - start_time).total_seconds())

    print("")
    print("=" * 70)
    print("Exportación de películas finalizada correctamente.")
    print(f"Versión exportador: {EXPORTER_VERSION}")
    print(f"Películas exportadas: {len(movies_df)}")

    if not movies_df.empty and "Tamaño GB" in movies_df.columns:
        total_gb = movies_df["Tamaño GB"].fillna(0).sum()
        print(f"Tamaño total exportado: {round(float(total_gb), 2)} GB")

    print(f"Tiempo total ejecución: {elapsed_total}")
    print(f"Archivo generado: {output_file}")
    print("=" * 70)


if __name__ == "__main__":
    main()