from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


# ------------------------------------------------------------
# Versión
# ------------------------------------------------------------

BUILDER_VERSION = "v1.1 Series Update Radar - 18 Months"


# ------------------------------------------------------------
# Configuración del radar
# ------------------------------------------------------------

# 18 meses aproximados.
# Se usa 548 días para evitar agregar dependencias externas y mantenerlo simple.
UPDATE_WINDOW_DAYS = 548

# Subventanas para clasificar urgencia dentro de los 18 meses.
UPDATE_NOW_DAYS = 60
UPDATE_SOON_DAYS = 180


# ------------------------------------------------------------
# Rutas base
# ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "taboplex.sqlite"


# ------------------------------------------------------------
# Utilidades SQLite
# ------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    """
    Abre conexión contra la base SQLite local de taboplex.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(f"No existe la base SQLite: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """
    Valida si existe una tabla o vista.
    """
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name = ?
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def read_table(conn: sqlite3.Connection, table_name: str) -> pd.DataFrame:
    """
    Lee una tabla completa a DataFrame.
    """
    if not table_exists(conn, table_name):
        return pd.DataFrame()

    return pd.read_sql_query(f"SELECT * FROM {table_name}", conn)


# ------------------------------------------------------------
# Utilidades de detección de columnas
# ------------------------------------------------------------

def normalize_name(value: str) -> str:
    """
    Normaliza nombres de columnas para comparar de forma flexible.
    """
    text = value.strip().lower()

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

    return text


def find_column(df: pd.DataFrame, candidates: list[str], required: bool = False) -> str | None:
    """
    Busca una columna usando una lista de nombres candidatos.
    """
    if df.empty:
        return None

    normalized_columns = {
        normalize_name(column): column
        for column in df.columns
    }

    for candidate in candidates:
        normalized_candidate = normalize_name(candidate)

        if normalized_candidate in normalized_columns:
            return normalized_columns[normalized_candidate]

    if required:
        available = "\n".join(f"- {column}" for column in df.columns)
        raise RuntimeError(
            "No pude detectar una columna requerida.\n"
            f"Candidatas esperadas: {candidates}\n\n"
            f"Columnas disponibles:\n{available}"
        )

    return None


def find_column_contains(
    df: pd.DataFrame,
    must_contain: list[str],
    must_not_contain: list[str] | None = None,
) -> str | None:
    """
    Busca una columna por palabras contenidas en su nombre.
    Sirve para tolerar pequeñas diferencias en nombres de columnas.
    """
    if df.empty:
        return None

    must_not_contain = must_not_contain or []

    for column in df.columns:
        normalized = normalize_name(column)

        if all(token in normalized for token in must_contain) and not any(
            token in normalized for token in must_not_contain
        ):
            return column

    return None


# ------------------------------------------------------------
# Utilidades de fechas
# ------------------------------------------------------------

def parse_date(value: Any) -> date | None:
    """
    Convierte valores variados a date.
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()

    if not text:
        return None

    parsed = pd.to_datetime(text, errors="coerce")

    if pd.isna(parsed):
        return None

    return parsed.date()


def days_since(value: Any) -> int | None:
    """
    Calcula días desde una fecha hasta hoy.
    """
    parsed = parse_date(value)

    if not parsed:
        return None

    return (date.today() - parsed).days


# ------------------------------------------------------------
# Construcción de radar
# ------------------------------------------------------------

def build_missing_count_by_series(missing_df: pd.DataFrame) -> dict[str, int]:
    """
    Agrupa la tabla de episodios faltantes por serie.
    """
    if missing_df.empty:
        return {}

    series_col = (
        find_column(
            missing_df,
            [
                "serie",
                "series",
                "titulo",
                "titulo_serie",
                "nombre_serie",
                "plex_serie",
                "serie_plex",
            ],
        )
        or find_column_contains(missing_df, ["serie"])
    )

    if not series_col:
        return {}

    grouped = (
        missing_df.dropna(subset=[series_col])
        .groupby(series_col)
        .size()
        .to_dict()
    )

    return {
        str(key).strip(): int(value)
        for key, value in grouped.items()
        if str(key).strip()
    }


def detect_series_check_columns(series_df: pd.DataFrame) -> dict[str, str | None]:
    """
    Detecta las columnas relevantes de series_check.
    """
    serie_col = (
        find_column(
            series_df,
            [
                "serie",
                "series",
                "titulo",
                "titulo_serie",
                "nombre_serie",
                "plex_serie",
                "serie_plex",
            ],
        )
        or find_column_contains(series_df, ["serie"])
        or find_column_contains(series_df, ["titulo"])
    )

    estado_col = (
        find_column(
            series_df,
            [
                "estado_control",
                "estado",
                "status",
                "estado_revision",
                "resultado",
            ],
        )
        or find_column_contains(series_df, ["estado"])
    )

    tvmaze_latest_date_col = (
        find_column(
            series_df,
            [
                "ultimo_episodio_tvmaze_fecha",
                "tvmaze_ultimo_episodio_fecha",
                "fecha_ultimo_episodio_tvmaze",
                "latest_episode_airdate",
                "tvmaze_latest_episode_airdate",
                "tvmaze_latest_airdate",
                "fecha_ultimo_capitulo_tvmaze",
                "ultimo_capitulo_tvmaze_fecha",
            ],
        )
        or find_column_contains(series_df, ["tvmaze", "fecha"])
        or find_column_contains(series_df, ["latest", "airdate"])
        or find_column_contains(series_df, ["ultimo", "tvmaze"])
    )

    plex_latest_date_col = (
        find_column(
            series_df,
            [
                "ultimo_episodio_plex_fecha",
                "plex_ultimo_episodio_fecha",
                "fecha_ultimo_episodio_plex",
                "plex_latest_episode_date",
                "fecha_ultimo_capitulo_plex",
                "ultimo_capitulo_plex_fecha",
            ],
        )
        or find_column_contains(series_df, ["plex", "fecha"])
        or find_column_contains(series_df, ["ultimo", "plex"])
    )

    missing_count_col = (
        find_column(
            series_df,
            [
                "episodios_faltantes",
                "missing_episodes",
                "cantidad_episodios_faltantes",
                "faltantes",
                "missing_count",
            ],
        )
        or find_column_contains(series_df, ["falt"])
        or find_column_contains(series_df, ["missing"])
    )

    tvmaze_last_episode_col = (
        find_column(
            series_df,
            [
                "ultimo_episodio_tvmaze",
                "tvmaze_ultimo_episodio",
                "tvmaze_latest_episode",
                "ultimo_capitulo_tvmaze",
            ],
        )
        or find_column_contains(series_df, ["tvmaze", "episodio"])
    )

    plex_last_episode_col = (
        find_column(
            series_df,
            [
                "ultimo_episodio_plex",
                "plex_ultimo_episodio",
                "plex_latest_episode",
                "ultimo_capitulo_plex",
            ],
        )
        or find_column_contains(series_df, ["plex", "episodio"])
    )

    return {
        "serie": serie_col,
        "estado": estado_col,
        "tvmaze_latest_date": tvmaze_latest_date_col,
        "plex_latest_date": plex_latest_date_col,
        "missing_count": missing_count_col,
        "tvmaze_last_episode": tvmaze_last_episode_col,
        "plex_last_episode": plex_last_episode_col,
    }


def to_int(value: Any, default: int = 0) -> int:
    """
    Convierte valores a entero de forma segura.
    """
    if value is None:
        return default

    try:
        if pd.isna(value):
            return default
    except Exception:
        pass

    try:
        return int(float(value))
    except Exception:
        return default


def get_suggested_action(days_tvmaze: int | None, missing_count: int) -> tuple[str, int, str]:
    """
    Define acción sugerida para una serie según:
    - días desde último capítulo TVmaze
    - cantidad de episodios faltantes

    La ventana principal es 18 meses.
    """
    if days_tvmaze is None:
        return "Sin fecha TVmaze", 99, "Sin fecha disponible para evaluar"

    if days_tvmaze > UPDATE_WINDOW_DAYS:
        return "Fuera de ventana", 9, f"Último capítulo hace más de {UPDATE_WINDOW_DAYS} días"

    if missing_count <= 0:
        return "Al día en ventana", 5, "Serie reciente, sin faltantes detectados"

    if days_tvmaze <= UPDATE_NOW_DAYS:
        return "Actualizar ahora", 1, "Capítulo reciente y hay episodios faltantes"

    if days_tvmaze <= UPDATE_SOON_DAYS:
        return "Actualizar pronto", 2, "Último capítulo dentro de 6 meses y hay faltantes"

    return "Actualizar pendiente", 3, "Último capítulo dentro de 18 meses y hay faltantes"


def build_radar_dataframe(
    series_df: pd.DataFrame,
    missing_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Construye tabla normalizada para el radar de actualización de series.
    """
    if series_df.empty:
        return pd.DataFrame()

    columns = detect_series_check_columns(series_df)

    if not columns["serie"]:
        available = "\n".join(f"- {column}" for column in series_df.columns)
        raise RuntimeError(
            "No pude detectar la columna de serie en series_check.\n\n"
            f"Columnas disponibles:\n{available}"
        )

    if not columns["tvmaze_latest_date"]:
        available = "\n".join(f"- {column}" for column in series_df.columns)
        raise RuntimeError(
            "No pude detectar la columna de fecha del último episodio TVmaze.\n\n"
            "Necesito esa columna para calcular qué series tuvieron capítulo reciente.\n\n"
            f"Columnas disponibles:\n{available}"
        )

    missing_count_by_series = build_missing_count_by_series(missing_df)

    rows: list[dict[str, Any]] = []

    for _, row in series_df.iterrows():
        serie = str(row.get(columns["serie"], "")).strip()

        if not serie:
            continue

        tvmaze_date_value = row.get(columns["tvmaze_latest_date"])
        plex_date_value = row.get(columns["plex_latest_date"]) if columns["plex_latest_date"] else None

        tvmaze_date = parse_date(tvmaze_date_value)
        plex_date = parse_date(plex_date_value)

        days_tvmaze = days_since(tvmaze_date)
        days_plex = days_since(plex_date)

        estado_control = (
            str(row.get(columns["estado"], "")).strip()
            if columns["estado"]
            else ""
        )

        missing_count = 0

        if columns["missing_count"]:
            missing_count = to_int(row.get(columns["missing_count"]), default=0)

        # Si existe tabla de episodios faltantes, esa fuente es más concreta.
        missing_count = max(missing_count, missing_count_by_series.get(serie, 0))

        tvmaze_last_episode = (
            str(row.get(columns["tvmaze_last_episode"], "")).strip()
            if columns["tvmaze_last_episode"]
            else ""
        )

        plex_last_episode = (
            str(row.get(columns["plex_last_episode"], "")).strip()
            if columns["plex_last_episode"]
            else ""
        )

        action, priority_sort, action_reason = get_suggested_action(
            days_tvmaze=days_tvmaze,
            missing_count=missing_count,
        )

        rows.append(
            {
                "serie": serie,
                "estado_control": estado_control or None,
                "ultimo_episodio_tvmaze_fecha": tvmaze_date.isoformat() if tvmaze_date else None,
                "ultimo_episodio_plex_fecha": plex_date.isoformat() if plex_date else None,
                "dias_desde_ultimo_tvmaze": days_tvmaze,
                "dias_desde_ultimo_plex": days_plex,
                "episodios_faltantes": missing_count,
                "ultimo_episodio_tvmaze": tvmaze_last_episode or None,
                "ultimo_episodio_plex": plex_last_episode or None,
                "accion_sugerida": action,
                "motivo_accion": action_reason,
                "ventana_dias": UPDATE_WINDOW_DAYS,
                "prioridad_sort": priority_sort,
                "builder_version": BUILDER_VERSION,
                "fecha_generacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    radar_df = pd.DataFrame(rows)

    if radar_df.empty:
        return radar_df

    radar_df = radar_df.sort_values(
        by=[
            "prioridad_sort",
            "dias_desde_ultimo_tvmaze",
            "episodios_faltantes",
            "serie",
        ],
        ascending=[True, True, False, True],
    )

    return radar_df


def write_radar_to_sqlite(conn: sqlite3.Connection, radar_df: pd.DataFrame) -> None:
    """
    Escribe tabla base y crea vista del radar.
    """
    print(f"Cargando series_update_radar: {len(radar_df)} registros")

    radar_df.to_sql(
        name="series_update_radar",
        con=conn,
        if_exists="replace",
        index=False,
    )

    conn.executescript(
        """
        DROP VIEW IF EXISTS v_series_update_radar;

        CREATE VIEW v_series_update_radar AS
        SELECT
            serie,
            estado_control,
            ultimo_episodio_tvmaze_fecha,
            ultimo_episodio_plex_fecha,
            dias_desde_ultimo_tvmaze,
            dias_desde_ultimo_plex,
            episodios_faltantes,
            ultimo_episodio_tvmaze,
            ultimo_episodio_plex,
            accion_sugerida,
            motivo_accion,
            ventana_dias,
            prioridad_sort,
            builder_version,
            fecha_generacion
        FROM series_update_radar;

        CREATE INDEX IF NOT EXISTS idx_series_update_radar_accion
            ON series_update_radar(accion_sugerida);

        CREATE INDEX IF NOT EXISTS idx_series_update_radar_dias_tvmaze
            ON series_update_radar(dias_desde_ultimo_tvmaze);

        CREATE INDEX IF NOT EXISTS idx_series_update_radar_faltantes
            ON series_update_radar(episodios_faltantes);

        CREATE INDEX IF NOT EXISTS idx_series_update_radar_prioridad
            ON series_update_radar(prioridad_sort);
        """
    )

    conn.commit()


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main() -> None:
    print("Inicio construcción radar actualización series")
    print(f"Versión: {BUILDER_VERSION}")
    print(f"Ventana de actualización: {UPDATE_WINDOW_DAYS} días")
    print(f"SQLite: {DB_PATH}")

    with get_connection() as conn:
        if not table_exists(conn, "series_check"):
            raise RuntimeError("No existe la tabla series_check. Ejecuta primero import_exports_to_sqlite.py")

        series_df = read_table(conn, "series_check")
        missing_df = read_table(conn, "missing_episodes")

        print(f"series_check registros: {len(series_df)}")
        print(f"missing_episodes registros: {len(missing_df)}")

        radar_df = build_radar_dataframe(
            series_df=series_df,
            missing_df=missing_df,
        )

        write_radar_to_sqlite(conn, radar_df)

    print("")
    print("=" * 70)
    print("Radar de actualización de series creado correctamente.")
    print("Tabla: series_update_radar")
    print("Vista: v_series_update_radar")
    print(f"Ventana activa: {UPDATE_WINDOW_DAYS} días")
    print("=" * 70)


if __name__ == "__main__":
    main()