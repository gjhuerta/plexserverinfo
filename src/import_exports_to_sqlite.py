from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


# ------------------------------------------------------------
# Versión
# ------------------------------------------------------------

IMPORTER_VERSION = "v1.0 Excel to SQLite"


# ------------------------------------------------------------
# Rutas base
# ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "taboplex.sqlite"


# ------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------

def find_latest_file(pattern: str, required: bool = True) -> Path | None:
    files = sorted(
        OUTPUT_DIR.glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not files:
        if required:
            raise FileNotFoundError(f"No encontré archivos con patrón {pattern} en {OUTPUT_DIR}")
        return None

    return files[0]


def normalize_column_name(name: Any) -> str:
    text = str(name).strip().lower()

    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ü": "u",
        "ñ": "n",
    }

    for source, target in replacements.items():
        text = text.replace(source, target)

    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")

    if not text:
        text = "campo"

    if text[0].isdigit():
        text = f"c_{text}"

    return text


def normalize_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    used_names: dict[str, int] = {}
    new_columns: list[str] = []

    for column in normalized.columns:
        base_name = normalize_column_name(column)
        final_name = base_name

        if final_name in used_names:
            used_names[base_name] += 1
            final_name = f"{base_name}_{used_names[base_name]}"
        else:
            used_names[base_name] = 1

        new_columns.append(final_name)

    normalized.columns = new_columns
    return normalized


def clean_value_for_sql(value: Any) -> Any:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    return value


def clean_dataframe_for_sql(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    cleaned = df.copy()

    for column in cleaned.columns:
        cleaned[column] = cleaned[column].map(clean_value_for_sql)

    return cleaned


def load_excel_sheet(file_path: Path, sheet_name: str) -> pd.DataFrame:
    print(f"Leyendo {file_path.name} / hoja {sheet_name}")

    df = pd.read_excel(file_path, sheet_name=sheet_name)
    df = normalize_dataframe_columns(df)
    df = clean_dataframe_for_sql(df)

    return df


def write_table(conn: sqlite3.Connection, table_name: str, df: pd.DataFrame) -> None:
    print(f"Cargando tabla {table_name}: {len(df)} registros")

    df.to_sql(
        name=table_name,
        con=conn,
        if_exists="replace",
        index=False,
    )


def execute_sql(conn: sqlite3.Connection, sql: str) -> None:
    conn.executescript(sql)
    conn.commit()


# ------------------------------------------------------------
# Tablas derivadas
# ------------------------------------------------------------

def create_movie_compression_candidates(conn: sqlite3.Connection) -> None:
    """
    Crea tabla derivada para la futura vista web:
    /compactacion

    Reglas iniciales:
    - tamaño mayor o igual a 2.5 GB
    - 4K / 2K quedan como revisión especial
    - >6 GB prioridad alta
    - 4-6 GB prioridad media
    - 2.5-4 GB prioridad baja
    """

    sql = """
    DROP TABLE IF EXISTS movie_compression_candidates;

    CREATE TABLE movie_compression_candidates AS
    SELECT
        titulo,
        ano,
        tamano_gb,
        volumen,
        resolucion,
        codec_video,
        codec_audio,
        contenedor,
        bitrate,
        duracion_min,
        visto,
        cantidad_vistas,
        fecha_agregado_plex,
        nombre_archivo,
        archivo,
        imdb_id,
        tmdb_id,
        CASE
            WHEN resolucion IN ('4K', '2K') THEN 'Revision especial'
            WHEN tamano_gb >= 6 THEN 'Alta'
            WHEN tamano_gb >= 4 THEN 'Media'
            WHEN tamano_gb >= 2.5 THEN 'Baja'
            ELSE 'No aplica'
        END AS prioridad_compactacion,
        CASE
            WHEN resolucion IN ('4K', '2K') THEN 'Resolucion alta: revisar antes de compactar'
            WHEN tamano_gb >= 6 THEN 'Archivo muy pesado'
            WHEN tamano_gb >= 4 THEN 'Archivo pesado'
            WHEN tamano_gb >= 2.5 THEN 'Sobre umbral recomendado'
            ELSE 'No aplica'
        END AS motivo_compactacion
    FROM movies
    WHERE tamano_gb IS NOT NULL
      AND tamano_gb >= 2.5
    ORDER BY
        CASE
            WHEN resolucion IN ('4K', '2K') THEN 1
            WHEN tamano_gb >= 6 THEN 2
            WHEN tamano_gb >= 4 THEN 3
            WHEN tamano_gb >= 2.5 THEN 4
            ELSE 9
        END,
        tamano_gb DESC;
    """

    execute_sql(conn, sql)


def create_summary_tables(conn: sqlite3.Connection) -> None:
    sql = """
    DROP TABLE IF EXISTS movie_summary_by_volume;

    CREATE TABLE movie_summary_by_volume AS
    SELECT
        volumen,
        COUNT(*) AS peliculas,
        ROUND(SUM(COALESCE(tamano_gb, 0)), 2) AS tamano_total_gb,
        ROUND(AVG(COALESCE(tamano_gb, 0)), 3) AS tamano_promedio_gb
    FROM movies
    GROUP BY volumen
    ORDER BY tamano_total_gb DESC;

    DROP TABLE IF EXISTS movie_summary_by_resolution;

    CREATE TABLE movie_summary_by_resolution AS
    SELECT
        resolucion,
        COUNT(*) AS peliculas,
        ROUND(SUM(COALESCE(tamano_gb, 0)), 2) AS tamano_total_gb
    FROM movies
    GROUP BY resolucion
    ORDER BY peliculas DESC;

    DROP TABLE IF EXISTS series_status_summary;

    CREATE TABLE series_status_summary AS
    SELECT
        estado_control,
        COUNT(*) AS series
    FROM series_check
    GROUP BY estado_control
    ORDER BY series DESC;
    """

    execute_sql(conn, sql)


def create_import_run_table(conn: sqlite3.Connection, source_files: dict[str, str | None]) -> None:
    rows = [
        {
            "campo": "fecha_ejecucion",
            "valor": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        {
            "campo": "version_importador",
            "valor": IMPORTER_VERSION,
        },
        {
            "campo": "base_sqlite",
            "valor": str(DB_PATH),
        },
    ]

    for key, value in source_files.items():
        rows.append(
            {
                "campo": key,
                "valor": value,
            }
        )

    df = pd.DataFrame(rows)
    write_table(conn, "import_run", df)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main() -> None:
    print("Inicio importación taboplex a SQLite")
    print(f"Versión importador: {IMPORTER_VERSION}")
    print(f"Raíz proyecto: {PROJECT_ROOT}")
    print(f"Output dir: {OUTPUT_DIR}")
    print(f"Data dir: {DATA_DIR}")
    print(f"SQLite destino: {DB_PATH}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    series_export_file = find_latest_file("plex_series_export_*.xlsx", required=True)
    movies_export_file = find_latest_file("plex_movies_export_*.xlsx", required=True)
    tvmaze_check_file = find_latest_file("plex_latest_check_tvmaze_*.xlsx", required=False)

    print("")
    print("Archivos origen detectados:")
    print(f"Series: {series_export_file}")
    print(f"Movies: {movies_export_file}")
    print(f"TVmaze: {tvmaze_check_file if tvmaze_check_file else 'No encontrado / omitido'}")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")

        # Series export
        series_df = load_excel_sheet(series_export_file, "Series")
        episodes_df = load_excel_sheet(series_export_file, "Episodios")
        series_libraries_df = load_excel_sheet(series_export_file, "Bibliotecas")

        write_table(conn, "series", series_df)
        write_table(conn, "episodes", episodes_df)
        write_table(conn, "series_libraries", series_libraries_df)

        # Movies export
        movies_df = load_excel_sheet(movies_export_file, "Peliculas")
        movie_libraries_df = load_excel_sheet(movies_export_file, "Bibliotecas")

        write_table(conn, "movies", movies_df)
        write_table(conn, "movie_libraries", movie_libraries_df)

        # TVmaze check, optional
        if tvmaze_check_file:
            series_check_df = load_excel_sheet(tvmaze_check_file, "Series_Check")
            missing_episodes_df = load_excel_sheet(tvmaze_check_file, "Episodios_Faltantes")
            manual_review_df = load_excel_sheet(tvmaze_check_file, "Revision_Manual")

            write_table(conn, "series_check", series_check_df)
            write_table(conn, "missing_episodes", missing_episodes_df)
            write_table(conn, "manual_review", manual_review_df)
        else:
            write_table(conn, "series_check", pd.DataFrame())
            write_table(conn, "missing_episodes", pd.DataFrame())
            write_table(conn, "manual_review", pd.DataFrame())

        create_movie_compression_candidates(conn)
        create_summary_tables(conn)

        create_import_run_table(
            conn,
            {
                "archivo_series": str(series_export_file) if series_export_file else None,
                "archivo_movies": str(movies_export_file) if movies_export_file else None,
                "archivo_tvmaze": str(tvmaze_check_file) if tvmaze_check_file else None,
            },
        )

        conn.commit()

    print("")
    print("=" * 70)
    print("Importación finalizada correctamente.")
    print(f"SQLite generado: {DB_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()