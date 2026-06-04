from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, Response, redirect, render_template, request, url_for


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


@app.template_filter("clean_year")
def clean_year(value: Any) -> str:
    """
    Limpia años que vienen desde Excel/SQLite como 2013.0.
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
    """
    Abre conexión contra SQLite local.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(f"No existe la base SQLite: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    """
    Ejecuta una consulta que devuelve una sola fila.
    """
    with get_connection() as conn:
        cursor = conn.execute(query, params)
        return cursor.fetchone()


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    """
    Ejecuta una consulta que devuelve varias filas.
    """
    with get_connection() as conn:
        cursor = conn.execute(query, params)
        return cursor.fetchall()


def execute_sql(query: str, params: tuple[Any, ...] = ()) -> None:
    """
    Ejecuta una sentencia de escritura.
    """
    with get_connection() as conn:
        conn.execute(query, params)
        conn.commit()


def execute_script(sql: str) -> None:
    """
    Ejecuta un script SQL completo.
    """
    with get_connection() as conn:
        conn.executescript(sql)
        conn.commit()


def table_exists(table_name: str) -> bool:
    """
    Valida existencia de tabla o vista.
    """
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


def get_table_columns(table_name: str) -> list[str]:
    """
    Obtiene columnas de una tabla/vista.
    """
    if not table_exists(table_name):
        return []

    rows = fetch_all(f"PRAGMA table_info({table_name})")
    return [row["name"] for row in rows]


def normalize_column_name(value: str) -> str:
    """
    Normaliza nombres para comparar columnas de forma flexible.
    """
    text = str(value).strip().lower()

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

    return text


def find_column(table_name: str, candidates: list[str]) -> str | None:
    """
    Busca una columna por nombres candidatos normalizados.
    """
    columns = get_table_columns(table_name)

    normalized_map = {
        normalize_column_name(column): column
        for column in columns
    }

    for candidate in candidates:
        normalized_candidate = normalize_column_name(candidate)
        if normalized_candidate in normalized_map:
            return normalized_map[normalized_candidate]

    return None


def find_column_contains(table_name: str, must_contain: list[str]) -> str | None:
    """
    Busca una columna por tokens contenidos en el nombre.
    """
    columns = get_table_columns(table_name)
    normalized_tokens = [normalize_column_name(token) for token in must_contain]

    for column in columns:
        normalized_column = normalize_column_name(column)

        if all(token in normalized_column for token in normalized_tokens):
            return column

    return None


def safe_count(table_name: str) -> int:
    """
    Cuenta registros de una tabla/vista si existe.
    """
    if not table_exists(table_name):
        return 0

    row = fetch_one(f"SELECT COUNT(*) AS total FROM {table_name}")
    return int(row["total"]) if row else 0


def safe_sum(table_name: str, column_name: str) -> float:
    """
    Suma una columna si existe la tabla/vista.
    """
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
    """
    Obtiene valores distintos de una columna para filtros.
    """
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
    """
    Construye filtros para la página /compactacion.
    """
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
    """
    Opciones fijas para compactación.
    """
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
    """
    Construye filtros para la página /peliculas.
    """
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
    """
    Convierte un valor de request a entero positivo con límites.
    """
    try:
        parsed = int(value or default)
    except Exception:
        parsed = default

    parsed = max(parsed, minimum)

    if maximum is not None:
        parsed = min(parsed, maximum)

    return parsed


def get_movie_by_rating_key(rating_key: str) -> sqlite3.Row | None:
    """
    Obtiene una película por Plex RatingKey.
    """
    return fetch_one(
        """
        SELECT *
        FROM movies
        WHERE CAST(plex_ratingkey AS TEXT) = ?
        LIMIT 1
        """,
        (str(rating_key),),
    )


def current_return_url() -> str:
    """
    Devuelve la URL actual con query string para volver al mismo filtro/página.
    """
    full_path = request.full_path or request.path

    if full_path.endswith("?"):
        full_path = full_path[:-1]

    return full_path


# ------------------------------------------------------------
# Helpers preferencias de seguimiento de series
# ------------------------------------------------------------

def ensure_series_tracking_table() -> None:
    """
    Crea la tabla local de preferencias de seguimiento si no existe.

    Esta tabla NO debe ser reemplazada por import_exports_to_sqlite.py.
    Representa decisiones manuales del usuario:
    - Activa
    - Ignorada
    """
    sql = """
    CREATE TABLE IF NOT EXISTS series_tracking_preferences (
        serie TEXT PRIMARY KEY,
        imdb_id TEXT,
        tracking_status TEXT NOT NULL DEFAULT 'Activa',
        nota TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_series_tracking_status
        ON series_tracking_preferences(tracking_status);
    """
    execute_script(sql)


def get_series_tracking_preference(serie: str) -> dict[str, Any]:
    """
    Obtiene la preferencia de una serie.
    Si no existe registro, se considera Activa por defecto.
    """
    ensure_series_tracking_table()

    row = fetch_one(
        """
        SELECT
            serie,
            imdb_id,
            tracking_status,
            nota,
            created_at,
            updated_at
        FROM series_tracking_preferences
        WHERE LOWER(serie) = LOWER(?)
        LIMIT 1
        """,
        (serie,),
    )

    if not row:
        return {
            "serie": serie,
            "imdb_id": None,
            "tracking_status": "Activa",
            "nota": None,
            "created_at": None,
            "updated_at": None,
            "is_default": True,
        }

    return {
        "serie": row["serie"],
        "imdb_id": row["imdb_id"],
        "tracking_status": row["tracking_status"] or "Activa",
        "nota": row["nota"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "is_default": False,
    }


def set_series_tracking_preference(
    serie: str,
    tracking_status: str,
    imdb_id: str | None = None,
    nota: str | None = None,
) -> None:
    """
    Guarda la preferencia manual de seguimiento de una serie.
    """
    ensure_series_tracking_table()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = tracking_status if tracking_status in {"Activa", "Ignorada"} else "Activa"

    existing = fetch_one(
        """
        SELECT serie
        FROM series_tracking_preferences
        WHERE LOWER(serie) = LOWER(?)
        LIMIT 1
        """,
        (serie,),
    )

    if existing:
        execute_sql(
            """
            UPDATE series_tracking_preferences
            SET
                imdb_id = COALESCE(?, imdb_id),
                tracking_status = ?,
                nota = ?,
                updated_at = ?
            WHERE LOWER(serie) = LOWER(?)
            """,
            (imdb_id, status, nota, now, serie),
        )
    else:
        execute_sql(
            """
            INSERT INTO series_tracking_preferences (
                serie,
                imdb_id,
                tracking_status,
                nota,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (serie, imdb_id, status, nota, now, now),
        )


def build_tracking_join_sql() -> str:
    """
    SQL estándar para unir el radar con preferencias locales.
    """
    return """
    LEFT JOIN series_tracking_preferences p
        ON LOWER(r.serie) = LOWER(p.serie)
    """


# ------------------------------------------------------------
# Helpers actualización de series
# ------------------------------------------------------------

def build_series_update_where_clause(
    max_days: int,
    action: str,
    only_missing: str,
    search_text: str,
    show_ignored: str,
) -> tuple[str, list[Any]]:
    """
    Construye filtros para /actualizacion-series.
    """
    where_clauses = [
        "r.dias_desde_ultimo_tvmaze IS NOT NULL",
        "r.dias_desde_ultimo_tvmaze <= ?",
    ]
    params: list[Any] = [max_days]

    if action:
        where_clauses.append("r.accion_sugerida = ?")
        params.append(action)

    if only_missing == "1":
        where_clauses.append("r.episodios_faltantes > 0")

    if search_text:
        where_clauses.append("LOWER(r.serie) LIKE ?")
        params.append(f"%{search_text.lower()}%")

    if show_ignored != "1":
        where_clauses.append("COALESCE(p.tracking_status, 'Activa') <> 'Ignorada'")

    where_sql = " AND ".join(where_clauses)
    return where_sql, params


def get_series_radar_row(serie: str) -> sqlite3.Row | None:
    """
    Obtiene la fila de radar para una serie.
    """
    if not table_exists("v_series_update_radar"):
        return None

    return fetch_one(
        """
        SELECT *
        FROM v_series_update_radar
        WHERE LOWER(serie) = LOWER(?)
        LIMIT 1
        """,
        (serie,),
    )


def get_series_imdb_id(serie: str) -> str | None:
    """
    Busca IMDb ID en series_check y luego en series.
    """
    source_tables = ["series_check", "series"]

    imdb_candidates = [
        "imdb_id",
        "IMDb ID",
        "imdb",
        "codigo_imdb",
        "id_imdb",
        "identificador_preferente",
    ]

    type_candidates = [
        "tipo_identificador_preferente",
        "Tipo Identificador Preferente",
    ]

    series_candidates = [
        "serie",
        "series",
        "titulo",
        "titulo_serie",
        "nombre_serie",
        "plex_serie",
        "serie_plex",
        "show",
        "show_name",
    ]

    for table_name in source_tables:
        if not table_exists(table_name):
            continue

        series_col = find_column(table_name, series_candidates) or find_column_contains(table_name, ["serie"])
        imdb_col = find_column(table_name, imdb_candidates)
        type_col = find_column(table_name, type_candidates)

        if not series_col or not imdb_col:
            continue

        row = fetch_one(
            f"""
            SELECT *
            FROM {table_name}
            WHERE LOWER({series_col}) = LOWER(?)
            LIMIT 1
            """,
            (serie,),
        )

        if not row:
            continue

        imdb_value = row[imdb_col]

        if imdb_value:
            imdb_text = str(imdb_value).strip()

            if imdb_text.startswith("tt"):
                return imdb_text

            if type_col and str(row[type_col]).strip().lower() == "imdb":
                return imdb_text

    return None


def get_missing_episodes_for_series(serie: str) -> list[dict[str, Any]]:
    """
    Devuelve episodios faltantes de una serie desde missing_episodes.
    Usa detección flexible de columnas para tolerar cambios en el exportador.
    """
    table_name = "missing_episodes"

    if not table_exists(table_name):
        return []

    series_col = (
        find_column(
            table_name,
            [
                "serie",
                "series",
                "titulo_serie",
                "nombre_serie",
                "plex_serie",
                "serie_plex",
                "show",
                "show_name",
            ],
        )
        or find_column_contains(table_name, ["serie"])
        or find_column_contains(table_name, ["show"])
    )

    if not series_col:
        return []

    season_col = (
        find_column(table_name, ["temporada", "season", "season_number", "numero_temporada"])
        or find_column_contains(table_name, ["season"])
        or find_column_contains(table_name, ["temporada"])
    )

    episode_col = (
        find_column(table_name, ["episodio", "episode", "episode_number", "numero_episodio"])
        or find_column_contains(table_name, ["episode"])
        or find_column_contains(table_name, ["episodio"])
    )

    title_col = (
        find_column(
            table_name,
            [
                "titulo_episodio",
                "nombre_episodio",
                "episode_title",
                "episode_name",
                "titulo",
                "name",
            ],
        )
        or find_column_contains(table_name, ["titulo"])
        or find_column_contains(table_name, ["name"])
    )

    airdate_col = (
        find_column(
            table_name,
            [
                "fecha_emision",
                "airdate",
                "fecha_episodio",
                "fecha",
                "tvmaze_airdate",
                "fecha_tvmaze",
            ],
        )
        or find_column_contains(table_name, ["airdate"])
        or find_column_contains(table_name, ["fecha"])
    )

    rows = fetch_all(
        f"""
        SELECT *
        FROM {table_name}
        WHERE LOWER({series_col}) = LOWER(?)
        """,
        (serie,),
    )

    episodes: list[dict[str, Any]] = []

    for row in rows:
        season_value = row[season_col] if season_col else None
        episode_value = row[episode_col] if episode_col else None

        code = build_episode_code(season_value, episode_value)

        episodes.append(
            {
                "season": season_value,
                "episode": episode_value,
                "code": code,
                "title": row[title_col] if title_col else None,
                "airdate": row[airdate_col] if airdate_col else None,
            }
        )

    episodes.sort(
        key=lambda item: (
            to_sort_number(item["season"]),
            to_sort_number(item["episode"]),
            str(item["airdate"] or ""),
        )
    )

    return episodes


def to_sort_number(value: Any) -> int:
    """
    Convierte season/episode a número para ordenar.
    """
    try:
        return int(float(value))
    except Exception:
        return 9999


def build_episode_code(season_value: Any, episode_value: Any) -> str:
    """
    Construye código SxxEyy si existen temporada y episodio.
    """
    try:
        season_number = int(float(season_value))
        episode_number = int(float(episode_value))
        return f"S{season_number:02d}E{episode_number:02d}"
    except Exception:
        return "—"


# ------------------------------------------------------------
# Proxy seguro de imágenes Plex
# ------------------------------------------------------------

def svg_placeholder(title: str = "Sin poster") -> Response:
    """
    Placeholder SVG cuando no existe poster o no se puede consultar Plex.
    """
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
    """
    Recupera imágenes desde Plex usando token local, sin exponerlo al navegador.
    """
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
    """
    Poster de película desde Plex.
    """
    return plex_image_response(rating_key, "poster")


@app.route("/art/movie/<rating_key>")
def movie_art(rating_key: str):
    """
    Arte/fondo de película desde Plex.
    """
    return plex_image_response(rating_key, "art")


# ------------------------------------------------------------
# Rutas
# ------------------------------------------------------------

@app.route("/")
def index():
    """
    Dashboard inicial.
    """
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
    """
    Catálogo visual de películas.
    """
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
        return_to=current_return_url(),
    )


@app.route("/peliculas/<rating_key>")
def pelicula_detalle(rating_key: str):
    """
    Ficha individual de película.
    """
    movie = get_movie_by_rating_key(rating_key)
    return_to = request.args.get("return_to") or url_for("peliculas")

    if not movie:
        return render_template(
            "pelicula_detalle.html",
            movie=None,
            rating_key=rating_key,
            return_to=return_to,
        ), 404

    return render_template(
        "pelicula_detalle.html",
        movie=movie,
        rating_key=rating_key,
        return_to=return_to,
    )


@app.route("/compactacion")
def compactacion():
    """
    Página de candidatas a compactar.
    """
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


@app.route("/actualizacion-series")
def actualizacion_series():
    """
    Radar de series para actualización.
    """
    ensure_series_tracking_table()

    source_view = "v_series_update_radar"

    max_days = get_positive_int(
        request.args.get("max_days"),
        default=548,
        minimum=1,
        maximum=2000,
    )
    action = request.args.get("action", "")
    only_missing = request.args.get("only_missing", "1")
    show_ignored = request.args.get("show_ignored", "")
    search_text = request.args.get("q", "").strip()

    rows = []
    action_summary = []
    total_missing = 0
    generated_at = None
    builder_version = None

    if table_exists(source_view):
        where_sql, params = build_series_update_where_clause(
            max_days=max_days,
            action=action,
            only_missing=only_missing,
            search_text=search_text,
            show_ignored=show_ignored,
        )

        tracking_join = build_tracking_join_sql()

        rows = fetch_all(
            f"""
            SELECT
                r.serie,
                r.estado_control,
                r.ultimo_episodio_tvmaze_fecha,
                r.ultimo_episodio_plex_fecha,
                r.dias_desde_ultimo_tvmaze,
                r.dias_desde_ultimo_plex,
                r.episodios_faltantes,
                r.ultimo_episodio_tvmaze,
                r.ultimo_episodio_plex,
                r.accion_sugerida,
                r.motivo_accion,
                r.ventana_dias,
                r.prioridad_sort,
                r.builder_version,
                r.fecha_generacion,
                COALESCE(p.tracking_status, 'Activa') AS tracking_status
            FROM {source_view} r
            {tracking_join}
            WHERE {where_sql}
            ORDER BY
                r.prioridad_sort ASC,
                r.dias_desde_ultimo_tvmaze ASC,
                r.episodios_faltantes DESC,
                r.serie COLLATE NOCASE ASC
            """,
            tuple(params),
        )

        action_summary = fetch_all(
            f"""
            SELECT
                r.accion_sugerida,
                COUNT(*) AS series,
                SUM(COALESCE(r.episodios_faltantes, 0)) AS episodios_faltantes
            FROM {source_view} r
            {tracking_join}
            WHERE {where_sql}
            GROUP BY r.accion_sugerida
            ORDER BY
                CASE r.accion_sugerida
                    WHEN 'Actualizar ahora' THEN 1
                    WHEN 'Actualizar pronto' THEN 2
                    WHEN 'Actualizar pendiente' THEN 3
                    WHEN 'Al día en ventana' THEN 4
                    WHEN 'Fuera de ventana' THEN 5
                    ELSE 9
                END
            """,
            tuple(params),
        )

        meta_row = fetch_one(
            f"""
            SELECT
                MAX(fecha_generacion) AS fecha_generacion,
                MAX(builder_version) AS builder_version
            FROM {source_view}
            """
        )

        if meta_row:
            generated_at = meta_row["fecha_generacion"]
            builder_version = meta_row["builder_version"]

    total_missing = sum(int(row["episodios_faltantes"] or 0) for row in rows)

    actions = []
    if table_exists(source_view):
        actions = get_distinct_values(source_view, "accion_sugerida")

    filters = {
        "max_days": max_days,
        "action": action,
        "only_missing": only_missing,
        "show_ignored": show_ignored,
        "q": search_text,
    }

    return render_template(
        "actualizacion_series.html",
        rows=rows,
        action_summary=action_summary,
        total_missing=total_missing,
        actions=actions,
        filters=filters,
        generated_at=generated_at,
        builder_version=builder_version,
        return_to=current_return_url(),
    )


@app.route("/actualizacion-series/detalle")
def actualizacion_serie_detalle():
    """
    Detalle de actualización de una serie:
    - IMDb ID
    - estado del radar
    - episodios faltantes
    - preferencia de seguimiento
    """
    ensure_series_tracking_table()

    serie = request.args.get("serie", "").strip()
    return_to = request.args.get("return_to") or url_for("actualizacion_series")

    radar = get_series_radar_row(serie) if serie else None
    imdb_id = get_series_imdb_id(serie) if serie else None
    missing_episodes = get_missing_episodes_for_series(serie) if serie else []
    tracking = get_series_tracking_preference(serie) if serie else None

    return render_template(
        "actualizacion_serie_detalle.html",
        serie=serie,
        radar=radar,
        imdb_id=imdb_id,
        missing_episodes=missing_episodes,
        tracking=tracking,
        return_to=return_to,
    )


@app.route("/actualizacion-series/seguimiento", methods=["POST"])
def actualizar_seguimiento_serie():
    """
    Cambia manualmente el seguimiento de una serie:
    - Activa
    - Ignorada
    """
    serie = request.form.get("serie", "").strip()
    imdb_id = request.form.get("imdb_id", "").strip() or None
    tracking_status = request.form.get("tracking_status", "Activa").strip()
    return_to = request.form.get("return_to") or url_for("actualizacion_series")

    if serie:
        set_series_tracking_preference(
            serie=serie,
            imdb_id=imdb_id,
            tracking_status=tracking_status,
            nota=None,
        )

    return redirect(return_to)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )