from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from flask import Flask, render_template, request


# ------------------------------------------------------------
# Rutas base
# ------------------------------------------------------------

WEB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "taboplex.sqlite"


# ------------------------------------------------------------
# App Flask
# ------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(WEB_DIR / "templates"),
    static_folder=str(WEB_DIR / "static"),
)


# ------------------------------------------------------------
# Filtros de formato
# ------------------------------------------------------------

@app.template_filter("cl_int")
def cl_int(value: Any) -> str:
    """
    Formato entero estilo español/chileno:
    20415 -> 20.415
    """
    if value is None:
        return "0"

    try:
        number = int(value)
    except Exception:
        return str(value)

    return f"{number:,}".replace(",", ".")


@app.template_filter("cl_float")
def cl_float(value: Any, decimals: int = 2) -> str:
    """
    Formato decimal estilo español/chileno:
    10097.89 -> 10.097,89
    """
    if value is None:
        return "0"

    try:
        number = float(value)
    except Exception:
        return str(value)

    formatted = f"{number:,.{decimals}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------
# Utilidades DB
# ------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"No existe la base SQLite: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with get_connection() as conn:
        cursor = conn.execute(query, params)
        return cursor.fetchone()


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cursor = conn.execute(query, params)
        return cursor.fetchall()


def table_exists(table_name: str) -> bool:
    row = fetch_one(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name = ?
        """,
        (table_name,),
    )
    return row is not None


def safe_count(table_name: str) -> int:
    if not table_exists(table_name):
        return 0

    row = fetch_one(f"SELECT COUNT(*) AS total FROM {table_name}")
    return int(row["total"]) if row else 0


def safe_sum(table_name: str, column_name: str) -> float:
    if not table_exists(table_name):
        return 0.0

    row = fetch_one(
        f"""
        SELECT ROUND(SUM(COALESCE({column_name}, 0)), 2) AS total
        FROM {table_name}
        """
    )
    return float(row["total"] or 0) if row else 0.0


def get_distinct_values(table_name: str, column_name: str) -> list[sqlite3.Row]:
    if not table_exists(table_name):
        return []

    return fetch_all(
        f"""
        SELECT DISTINCT {column_name} AS value
        FROM {table_name}
        WHERE {column_name} IS NOT NULL
          AND TRIM({column_name}) <> ''
        ORDER BY value
        """
    )


# ------------------------------------------------------------
# Helpers compactación
# ------------------------------------------------------------

def build_compression_where_clause(
    min_size: float,
    priority: str,
    volume: str,
    resolution: str,
    only_unwatched: str,
) -> tuple[str, list[Any]]:
    where_clauses = ["tamano_gb >= ?"]
    params: list[Any] = [min_size]

    if priority:
        where_clauses.append("prioridad_compactacion = ?")
        params.append(priority)

    if volume:
        where_clauses.append("volumen = ?")
        params.append(volume)

    if resolution:
        where_clauses.append("resolucion = ?")
        params.append(resolution)

    if only_unwatched == "1":
        where_clauses.append("visto = 'No'")

    where_sql = " AND ".join(where_clauses)
    return where_sql, params


def get_priority_options() -> list[dict[str, str]]:
    return [
        {"value": "Revision especial"},
        {"value": "Alta"},
        {"value": "Media"},
        {"value": "Baja"},
        {"value": "Bajo umbral"},
    ]


# ------------------------------------------------------------
# Rutas
# ------------------------------------------------------------

@app.route("/")
def index():
    compression_source = (
        "v_movie_compression_analysis"
        if table_exists("v_movie_compression_analysis")
        else "movies"
    )

    summary = {
        "movies": safe_count("movies"),
        "series": safe_count("series"),
        "episodes": safe_count("episodes"),
        "missing_episodes": safe_count("missing_episodes"),
        "compression_candidates": 0,
        "movies_total_gb": safe_sum("movies", "tamano_gb"),
        "compression_total_gb": 0.0,
    }

    if table_exists("v_movie_compression_analysis"):
        row = fetch_one(
            """
            SELECT
                COUNT(*) AS peliculas,
                ROUND(SUM(COALESCE(tamano_gb, 0)), 2) AS tamano_total_gb
            FROM v_movie_compression_analysis
            WHERE tamano_gb >= 2.5
            """
        )

        if row:
            summary["compression_candidates"] = int(row["peliculas"] or 0)
            summary["compression_total_gb"] = float(row["tamano_total_gb"] or 0.0)
    elif table_exists(compression_source):
        row = fetch_one(
            f"""
            SELECT
                COUNT(*) AS peliculas,
                ROUND(SUM(COALESCE(tamano_gb, 0)), 2) AS tamano_total_gb
            FROM {compression_source}
            WHERE tamano_gb >= 2.5
            """
        )

        if row:
            summary["compression_candidates"] = int(row["peliculas"] or 0)
            summary["compression_total_gb"] = float(row["tamano_total_gb"] or 0.0)

    movie_by_volume = []
    if table_exists("movie_summary_by_volume"):
        movie_by_volume = fetch_all(
            """
            SELECT
                volumen,
                peliculas,
                tamano_total_gb,
                tamano_promedio_gb
            FROM movie_summary_by_volume
            ORDER BY tamano_total_gb DESC
            """
        )

    series_status = []
    if table_exists("series_status_summary"):
        series_status = fetch_all(
            """
            SELECT
                estado_control,
                series
            FROM series_status_summary
            ORDER BY series DESC
            """
        )

    return render_template(
        "index.html",
        summary=summary,
        movie_by_volume=movie_by_volume,
        series_status=series_status,
        db_path=DB_PATH,
    )


@app.route("/compactacion")
def compactacion():
    min_size_raw = request.args.get("min_size", "2.5")
    priority = request.args.get("priority", "")
    volume = request.args.get("volume", "")
    resolution = request.args.get("resolution", "")
    only_unwatched = request.args.get("only_unwatched", "")

    try:
        min_size = float(min_size_raw)
    except ValueError:
        min_size = 2.5

    source_view = "v_movie_compression_analysis"

    rows = []
    priority_summary = []
    volume_summary = []
    resolution_summary = []

    if table_exists(source_view):
        where_sql, params = build_compression_where_clause(
            min_size=min_size,
            priority=priority,
            volume=volume,
            resolution=resolution,
            only_unwatched=only_unwatched,
        )

        rows = fetch_all(
            f"""
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
                prioridad_compactacion,
                motivo_compactacion,
                riesgo_appletv_wifi,
                nombre_archivo,
                archivo
            FROM {source_view}
            WHERE {where_sql}
            ORDER BY
                CASE prioridad_compactacion
                    WHEN 'Revision especial' THEN 1
                    WHEN 'Alta' THEN 2
                    WHEN 'Media' THEN 3
                    WHEN 'Baja' THEN 4
                    WHEN 'Bajo umbral' THEN 5
                    ELSE 9
                END,
                tamano_gb DESC
            """,
            tuple(params),
        )

        priority_summary = fetch_all(
            f"""
            SELECT
                prioridad_compactacion,
                COUNT(*) AS peliculas,
                ROUND(SUM(COALESCE(tamano_gb, 0)), 2) AS tamano_total_gb
            FROM {source_view}
            WHERE {where_sql}
            GROUP BY prioridad_compactacion
            ORDER BY
                CASE prioridad_compactacion
                    WHEN 'Revision especial' THEN 1
                    WHEN 'Alta' THEN 2
                    WHEN 'Media' THEN 3
                    WHEN 'Baja' THEN 4
                    WHEN 'Bajo umbral' THEN 5
                    ELSE 9
                END
            """,
            tuple(params),
        )

        volume_summary = fetch_all(
            f"""
            SELECT
                volumen,
                COUNT(*) AS peliculas,
                ROUND(SUM(COALESCE(tamano_gb, 0)), 2) AS tamano_total_gb
            FROM {source_view}
            WHERE {where_sql}
            GROUP BY volumen
            ORDER BY tamano_total_gb DESC
            """,
            tuple(params),
        )

        resolution_summary = fetch_all(
            f"""
            SELECT
                resolucion,
                COUNT(*) AS peliculas,
                ROUND(SUM(COALESCE(tamano_gb, 0)), 2) AS tamano_total_gb
            FROM {source_view}
            WHERE {where_sql}
            GROUP BY resolucion
            ORDER BY peliculas DESC
            """,
            tuple(params),
        )

    total_gb = round(sum(float(row["tamano_gb"] or 0) for row in rows), 2)

    priorities = get_priority_options()
    volumes = get_distinct_values(source_view, "volumen")
    resolutions = get_distinct_values(source_view, "resolucion")

    filters = {
        "min_size": min_size,
        "priority": priority,
        "volume": volume,
        "resolution": resolution,
        "only_unwatched": only_unwatched,
    }

    return render_template(
        "compactacion.html",
        rows=rows,
        total_gb=total_gb,
        filters=filters,
        priorities=priorities,
        volumes=volumes,
        resolutions=resolutions,
        priority_summary=priority_summary,
        volume_summary=volume_summary,
        resolution_summary=resolution_summary,
    )


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )