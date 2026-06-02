from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, Response, render_template, request


# ------------------------------------------------------------
# Rutas base
# ------------------------------------------------------------

WEB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "taboplex.sqlite"

load_dotenv(PROJECT_ROOT / ".env")


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
    if value is None:
        return "0"

    try:
        number = int(value)
    except Exception:
        return str(value)

    return f"{number:,}".replace(",", ".")


@app.template_filter("cl_float")
def cl_float(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "0"

    try:
        number = float(value)
    except Exception:
        return str(value)

    formatted = f"{number:,.{decimals}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("clean_year")
def clean_year(value: Any) -> str:
    """
    Limpia años que vienen desde Excel/SQLite como 2013.0
    y los muestra como 2013.
    """
    if value is None:
        return "s/a"

    try:
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return str(value)
    except Exception:
        text = str(value).strip()
        if text.endswith(".0"):
            return text[:-2]
        return text or "s/a"


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
# Helpers películas
# ------------------------------------------------------------

def build_movies_where_clause(
    search_text: str,
    volume: str,
    resolution: str,
    watched: str,
) -> tuple[str, list[Any]]:
    where_clauses = ["1 = 1"]
    params: list[Any] = []

    if search_text:
        where_clauses.append("LOWER(titulo) LIKE ?")
        params.append(f"%{search_text.lower()}%")

    if volume:
        where_clauses.append("volumen = ?")
        params.append(volume)

    if resolution:
        where_clauses.append("resolucion = ?")
        params.append(resolution)

    if watched:
        where_clauses.append("visto = ?")
        params.append(watched)

    where_sql = " AND ".join(where_clauses)
    return where_sql, params


def get_positive_int(value: str | None, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        parsed = int(value or default)
    except Exception:
        parsed = default

    parsed = max(parsed, minimum)

    if maximum is not None:
        parsed = min(parsed, maximum)

    return parsed


def get_movie_by_rating_key(rating_key: str) -> sqlite3.Row | None:
    return fetch_one(
        """
        SELECT *
        FROM movies
        WHERE CAST(plex_ratingkey AS TEXT) = ?
        LIMIT 1
        """,
        (str(rating_key),),
    )


# ------------------------------------------------------------
# Proxy seguro de imágenes Plex
# ------------------------------------------------------------

def svg_placeholder(title: str = "Sin poster") -> Response:
    safe_title = (title or "Sin poster").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
        <rect width="300" height="450" rx="18" fill="#f1eee8"/>
        <rect x="24" y="24" width="252" height="402" rx="14" fill="#fbfaf7" stroke="#e6e1d8"/>
        <circle cx="150" cy="150" r="42" fill="#d6a23f" opacity="0.25"/>
        <polygon points="138,128 138,172 174,150" fill="#d6a23f"/>
        <text x="150" y="245" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" font-size="18" font-weight="700" fill="#252525">taboplex</text>
        <text x="150" y="276" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" font-size="13" fill="#747474">{safe_title[:28]}</text>
    </svg>
    """

    return Response(svg.strip(), mimetype="image/svg+xml")


def plex_image_response(rating_key: str, image_type: str) -> Response:
    movie = get_movie_by_rating_key(rating_key)

    if not movie:
        return svg_placeholder("Sin imagen")

    if image_type == "art":
        plex_path = movie["plex_art"] or movie["plex_art_fallback"]
    else:
        plex_path = movie["plex_thumb"] or movie["plex_thumb_fallback"]

    if not plex_path:
        return svg_placeholder(movie["titulo"])

    plex_base_url = os.getenv("PLEX_BASE_URL", "").rstrip("/")
    plex_token = os.getenv("PLEX_TOKEN", "")

    if not plex_base_url or not plex_token:
        return svg_placeholder(movie["titulo"])

    image_url = f"{plex_base_url}{plex_path}"

    try:
        response = requests.get(
            image_url,
            params={"X-Plex-Token": plex_token},
            timeout=15,
        )
    except requests.RequestException:
        return svg_placeholder(movie["titulo"])

    if response.status_code < 200 or response.status_code >= 300:
        return svg_placeholder(movie["titulo"])

    content_type = response.headers.get("Content-Type", "image/jpeg")

    return Response(
        response.content,
        mimetype=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
        },
    )


@app.route("/poster/movie/<rating_key>")
def movie_poster(rating_key: str):
    return plex_image_response(rating_key, "poster")


@app.route("/art/movie/<rating_key>")
def movie_art(rating_key: str):
    return plex_image_response(rating_key, "art")


# ------------------------------------------------------------
# Rutas
# ------------------------------------------------------------

@app.route("/")
def index():
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


@app.route("/peliculas")
def peliculas():
    search_text = request.args.get("q", "").strip()
    volume = request.args.get("volume", "")
    resolution = request.args.get("resolution", "")
    watched = request.args.get("watched", "")
    page = get_positive_int(request.args.get("page"), default=1, minimum=1)
    page_size = get_positive_int(request.args.get("page_size"), default=60, minimum=24, maximum=120)

    where_sql, params = build_movies_where_clause(
        search_text=search_text,
        volume=volume,
        resolution=resolution,
        watched=watched,
    )

    total_row = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM movies
        WHERE {where_sql}
        """,
        tuple(params),
    )

    total_movies = int(total_row["total"] or 0) if total_row else 0
    total_pages = max((total_movies + page_size - 1) // page_size, 1)

    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size

    rows = fetch_all(
        f"""
        SELECT
            titulo,
            ano,
            plex_ratingkey,
            visto,
            resolucion,
            codec_video,
            contenedor,
            tamano_gb,
            volumen,
            fecha_agregado_plex
        FROM movies
        WHERE {where_sql}
        ORDER BY
            titulo COLLATE NOCASE ASC,
            ano ASC
        LIMIT ?
        OFFSET ?
        """,
        tuple(params + [page_size, offset]),
    )

    volumes = get_distinct_values("movies", "volumen")
    resolutions = get_distinct_values("movies", "resolucion")

    filters = {
        "q": search_text,
        "volume": volume,
        "resolution": resolution,
        "watched": watched,
        "page": page,
        "page_size": page_size,
    }

    pagination = {
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "total_movies": total_movies,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": max(page - 1, 1),
        "next_page": min(page + 1, total_pages),
    }

    return render_template(
        "peliculas.html",
        rows=rows,
        filters=filters,
        volumes=volumes,
        resolutions=resolutions,
        pagination=pagination,
    )


@app.route("/peliculas/<rating_key>")
def pelicula_detalle(rating_key: str):
    movie = get_movie_by_rating_key(rating_key)

    if not movie:
        return render_template(
            "pelicula_detalle.html",
            movie=None,
            rating_key=rating_key,
        ), 404

    return render_template(
        "pelicula_detalle.html",
        movie=movie,
        rating_key=rating_key,
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