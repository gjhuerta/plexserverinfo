from __future__ import annotations

import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv


# ------------------------------------------------------------
# Versión
# ------------------------------------------------------------

CHECKER_VERSION = "v1.0 TVmaze Latest Episodes"


# ------------------------------------------------------------
# Rutas base del proyecto
# ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


# ------------------------------------------------------------
# Configuración base
# ------------------------------------------------------------

TVMAZE_BASE_URL = "https://api.tvmaze.com"
DEFAULT_OUTPUT_DIR = "output"

EXCEL_MAX_CELL_LENGTH = 32000


def get_env_var(name: str, required: bool = False, default: str | None = None) -> str:
    value = os.getenv(name, default)

    if required and not value:
        raise RuntimeError(f"Falta configurar la variable {name} en el archivo .env")

    return value or ""


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


def parse_float(raw_value: str | None, default: float = 0.0) -> float:
    if raw_value is None or raw_value == "":
        return default

    try:
        return float(raw_value)
    except ValueError:
        return default


def resolve_path_from_project(path_raw: str) -> Path:
    path = Path(path_raw)

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def resolve_output_dir() -> Path:
    output_dir_raw = get_env_var("OUTPUT_DIR", required=False, default=DEFAULT_OUTPUT_DIR)
    return resolve_path_from_project(output_dir_raw)


def find_latest_plex_export(output_dir: Path) -> Path:
    files = sorted(
        output_dir.glob("plex_series_export_*.xlsx"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not files:
        raise FileNotFoundError(
            f"No encontré archivos plex_series_export_*.xlsx en {output_dir}"
        )

    return files[0]


# ------------------------------------------------------------
# Limpieza Excel
# ------------------------------------------------------------

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
# Utilitarios de parsing
# ------------------------------------------------------------

def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9áéíóúüñ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def extract_year(value: Any) -> int | None:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    text = str(value)

    match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", text)
    if not match:
        return None

    return int(match.group(1))


def safe_str(value: Any) -> str | None:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    text = str(value).strip()
    return text if text else None


def safe_int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    try:
        return int(value)
    except Exception:
        return None


def parse_iso_date(value: Any) -> date | None:
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

    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def format_date(value: date | None) -> str | None:
    if value is None:
        return None

    return value.strftime("%Y-%m-%d")


def parse_episode_code(code: Any) -> tuple[int | None, int | None]:
    if code is None:
        return None, None

    try:
        if pd.isna(code):
            return None, None
    except Exception:
        pass

    text = str(code).strip().upper()

    match = re.search(r"S(\d+)E(\d+)", text)
    if not match:
        return None, None

    return int(match.group(1)), int(match.group(2))


def make_episode_code(season: Any, number: Any) -> str | None:
    season_int = safe_int(season)
    number_int = safe_int(number)

    if season_int is None or number_int is None:
        return None

    return f"S{season_int:02d}E{number_int:02d}"


def episode_tuple_is_after(
    candidate_season: int | None,
    candidate_number: int | None,
    base_season: int | None,
    base_number: int | None,
) -> bool:
    if candidate_season is None or candidate_number is None:
        return False

    if base_season is None or base_number is None:
        return True

    return (candidate_season, candidate_number) > (base_season, base_number)


def episode_tuple_compare(
    left_season: int | None,
    left_number: int | None,
    right_season: int | None,
    right_number: int | None,
) -> int | None:
    if left_season is None or left_number is None:
        return None

    if right_season is None or right_number is None:
        return None

    left = (left_season, left_number)
    right = (right_season, right_number)

    if left == right:
        return 0

    if left > right:
        return 1

    return -1


# ------------------------------------------------------------
# Cliente TVmaze
# ------------------------------------------------------------

class TVMazeClient:
    def __init__(self, sleep_seconds: float = 0.6, timeout_seconds: int = 20) -> None:
        self.sleep_seconds = sleep_seconds
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "plexserverinfo/1.0 local-personal-audit",
                "Accept": "application/json",
            }
        )

    def _sleep(self) -> None:
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)

    def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        allow_404: bool = True,
    ) -> tuple[int, Any | None, str | None]:
        url = f"{TVMAZE_BASE_URL}{path}"

        self._sleep()

        try:
            response = self.session.get(
                url,
                params=params or {},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            return 0, None, str(exc)

        if response.status_code == 404 and allow_404:
            return 404, None, None

        if response.status_code == 429:
            return 429, None, "Rate limit alcanzado en TVmaze. Reintenta más tarde o aumenta TVMAZE_SLEEP_SECONDS."

        if response.status_code < 200 or response.status_code >= 300:
            return response.status_code, None, response.text[:500]

        try:
            return response.status_code, response.json(), None
        except Exception as exc:
            return response.status_code, None, f"No se pudo parsear JSON: {exc}"

    def lookup_show_by_imdb(self, imdb_id: str) -> tuple[dict[str, Any] | None, str | None]:
        status, payload, error = self.get_json(
            "/lookup/shows",
            params={"imdb": imdb_id},
            allow_404=True,
        )

        if error:
            return None, error

        if status == 404:
            return None, None

        return payload, None

    def lookup_show_by_thetvdb(self, tvdb_id: str) -> tuple[dict[str, Any] | None, str | None]:
        status, payload, error = self.get_json(
            "/lookup/shows",
            params={"thetvdb": tvdb_id},
            allow_404=True,
        )

        if error:
            return None, error

        if status == 404:
            return None, None

        return payload, None

    def search_show(self, title: str) -> tuple[list[dict[str, Any]], str | None]:
        status, payload, error = self.get_json(
            "/search/shows",
            params={"q": title},
            allow_404=True,
        )

        if error:
            return [], error

        if status == 404 or not payload:
            return [], None

        if isinstance(payload, list):
            return payload, None

        return [], "Respuesta inesperada desde TVmaze search."

    def get_show_episodes(self, tvmaze_id: int) -> tuple[list[dict[str, Any]], str | None]:
        status, payload, error = self.get_json(
            f"/shows/{tvmaze_id}/episodes",
            params={},
            allow_404=True,
        )

        if error:
            return [], error

        if status == 404 or not payload:
            return [], None

        if isinstance(payload, list):
            return payload, None

        return [], "Respuesta inesperada desde TVmaze episodes."


# ------------------------------------------------------------
# Matching de series
# ------------------------------------------------------------

def score_search_candidate(
    plex_title: str,
    plex_year: int | None,
    imdb_id: str | None,
    tvdb_id: str | None,
    candidate: dict[str, Any],
) -> int:
    show = candidate.get("show", {}) or {}

    score = 0

    plex_title_norm = normalize_text(plex_title)
    tvmaze_title_norm = normalize_text(show.get("name"))

    if plex_title_norm and tvmaze_title_norm:
        if plex_title_norm == tvmaze_title_norm:
            score += 70
        elif plex_title_norm in tvmaze_title_norm or tvmaze_title_norm in plex_title_norm:
            score += 40

    externals = show.get("externals", {}) or {}
    candidate_imdb = externals.get("imdb")
    candidate_tvdb = externals.get("thetvdb")

    if imdb_id and candidate_imdb and str(imdb_id).lower() == str(candidate_imdb).lower():
        score += 100

    if tvdb_id and candidate_tvdb and str(tvdb_id) == str(candidate_tvdb):
        score += 100

    premiered_year = extract_year(show.get("premiered"))

    if plex_year and premiered_year:
        if plex_year == premiered_year:
            score += 25
        elif abs(plex_year - premiered_year) <= 1:
            score += 10

    tvmaze_score = candidate.get("score")
    try:
        score += int(float(tvmaze_score) * 10)
    except Exception:
        pass

    return score


def find_tvmaze_show(
    client: TVMazeClient,
    plex_title: str,
    plex_year: int | None,
    imdb_id: str | None,
    tvdb_id: str | None,
) -> tuple[dict[str, Any] | None, str, int, str | None]:
    """
    Retorna:
    - show TVmaze
    - método de match
    - confianza numérica
    - error/observación
    """

    if imdb_id:
        show, error = client.lookup_show_by_imdb(imdb_id)
        if error:
            return None, "IMDb", 0, error
        if show:
            return show, "IMDb", 100, None

    if tvdb_id:
        show, error = client.lookup_show_by_thetvdb(tvdb_id)
        if error:
            return None, "TheTVDB", 0, error
        if show:
            return show, "TheTVDB", 100, None

    candidates, error = client.search_show(plex_title)
    if error:
        return None, "Búsqueda por nombre", 0, error

    if not candidates:
        return None, "Búsqueda por nombre", 0, "Sin resultados en TVmaze."

    scored_candidates = []

    for candidate in candidates:
        score = score_search_candidate(
            plex_title=plex_title,
            plex_year=plex_year,
            imdb_id=imdb_id,
            tvdb_id=tvdb_id,
            candidate=candidate,
        )
        scored_candidates.append((score, candidate))

    scored_candidates.sort(key=lambda item: item[0], reverse=True)

    best_score, best_candidate = scored_candidates[0]
    best_show = best_candidate.get("show", {}) or {}

    if best_score < 60:
        return (
            best_show,
            "Búsqueda por nombre",
            best_score,
            "Match débil por nombre/año. Requiere revisión manual.",
        )

    return best_show, "Búsqueda por nombre", best_score, None


# ------------------------------------------------------------
# Cálculo de episodios TVmaze
# ------------------------------------------------------------

def summarize_tvmaze_episodes(
    episodes: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    aired_episodes: list[dict[str, Any]] = []
    future_episodes: list[dict[str, Any]] = []

    for episode in episodes:
        airdate = parse_iso_date(episode.get("airdate"))
        season = safe_int(episode.get("season"))
        number = safe_int(episode.get("number"))

        if season is None or number is None:
            continue

        if airdate and airdate <= today:
            aired_episodes.append(episode)
        elif airdate and airdate > today:
            future_episodes.append(episode)

    result = {
        "TVMaze Episodios Totales": len(episodes),
        "TVMaze Episodios Emitidos": len(aired_episodes),
        "TVMaze Episodios Futuros": len(future_episodes),
        "TVMaze Ultimo Emitido Codigo": None,
        "TVMaze Ultimo Emitido Temporada": None,
        "TVMaze Ultimo Emitido Episodio": None,
        "TVMaze Ultimo Emitido Titulo": None,
        "TVMaze Ultimo Emitido Fecha": None,
        "TVMaze Proximo Codigo": None,
        "TVMaze Proximo Temporada": None,
        "TVMaze Proximo Episodio": None,
        "TVMaze Proximo Titulo": None,
        "TVMaze Proximo Fecha": None,
    }

    if aired_episodes:
        last_aired = max(
            aired_episodes,
            key=lambda item: (
                parse_iso_date(item.get("airdate")) or date.min,
                safe_int(item.get("season")) or -1,
                safe_int(item.get("number")) or -1,
            ),
        )

        last_season = safe_int(last_aired.get("season"))
        last_number = safe_int(last_aired.get("number"))

        result["TVMaze Ultimo Emitido Codigo"] = make_episode_code(last_season, last_number)
        result["TVMaze Ultimo Emitido Temporada"] = last_season
        result["TVMaze Ultimo Emitido Episodio"] = last_number
        result["TVMaze Ultimo Emitido Titulo"] = safe_str(last_aired.get("name"))
        result["TVMaze Ultimo Emitido Fecha"] = format_date(parse_iso_date(last_aired.get("airdate")))

    if future_episodes:
        next_episode = min(
            future_episodes,
            key=lambda item: (
                parse_iso_date(item.get("airdate")) or date.max,
                safe_int(item.get("season")) or 9999,
                safe_int(item.get("number")) or 9999,
            ),
        )

        next_season = safe_int(next_episode.get("season"))
        next_number = safe_int(next_episode.get("number"))

        result["TVMaze Proximo Codigo"] = make_episode_code(next_season, next_number)
        result["TVMaze Proximo Temporada"] = next_season
        result["TVMaze Proximo Episodio"] = next_number
        result["TVMaze Proximo Titulo"] = safe_str(next_episode.get("name"))
        result["TVMaze Proximo Fecha"] = format_date(parse_iso_date(next_episode.get("airdate")))

    return result


def build_missing_episode_rows(
    plex_row: pd.Series,
    tvmaze_show: dict[str, Any],
    episodes: list[dict[str, Any]],
    today: date,
    plex_last_season: int | None,
    plex_last_number: int | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for episode in episodes:
        season = safe_int(episode.get("season"))
        number = safe_int(episode.get("number"))
        airdate = parse_iso_date(episode.get("airdate"))

        if season is None or number is None:
            continue

        if airdate is None or airdate > today:
            continue

        if not episode_tuple_is_after(
            candidate_season=season,
            candidate_number=number,
            base_season=plex_last_season,
            base_number=plex_last_number,
        ):
            continue

        rows.append(
            {
                "Serie Plex": plex_row.get("Serie"),
                "Año Serie Plex": plex_row.get("Año Serie"),
                "IMDb ID Plex": plex_row.get("IMDb ID"),
                "TMDb ID Plex": plex_row.get("TMDb ID"),
                "TVDb ID Plex": plex_row.get("TVDb ID"),
                "TVMaze ID": tvmaze_show.get("id"),
                "TVMaze Serie": tvmaze_show.get("name"),
                "TVMaze URL": tvmaze_show.get("url"),
                "Codigo Episodio Faltante": make_episode_code(season, number),
                "Temporada": season,
                "Episodio": number,
                "Titulo Episodio": safe_str(episode.get("name")),
                "Fecha Emision": format_date(airdate),
            }
        )

    rows.sort(
        key=lambda item: (
            item["Serie Plex"] or "",
            item["Temporada"] or -1,
            item["Episodio"] or -1,
        )
    )

    return rows


# ------------------------------------------------------------
# Clasificación
# ------------------------------------------------------------

def classify_status(
    plex_last_season: int | None,
    plex_last_number: int | None,
    tvmaze_last_season: int | None,
    tvmaze_last_number: int | None,
    match_error: str | None,
    tvmaze_show_found: bool,
    tvmaze_episode_count: int,
) -> str:
    if not tvmaze_show_found:
        return "Sin match TVmaze"

    if match_error:
        return "Match con revisión"

    if tvmaze_episode_count == 0:
        return "Sin episodios TVmaze"

    comparison = episode_tuple_compare(
        left_season=plex_last_season,
        left_number=plex_last_number,
        right_season=tvmaze_last_season,
        right_number=tvmaze_last_number,
    )

    if comparison is None:
        return "Sin dato suficiente"

    if comparison == 0:
        return "Al día"

    if comparison < 0:
        return "Faltan episodios"

    return "Plex supera TVmaze"


# ------------------------------------------------------------
# Proceso principal de comparación
# ------------------------------------------------------------

def run_latest_check(
    plex_export_file: Path,
    output_dir: Path,
    max_series: int,
    sleep_seconds: float,
) -> Path:
    print("")
    print(f"Leyendo inventario Plex: {plex_export_file}")

    series_df = pd.read_excel(plex_export_file, sheet_name="Series")

    if max_series > 0:
        print(f"Modo prueba activo: se procesarán solo las primeras {max_series} series.")
        series_df = series_df.head(max_series).copy()

    total_series = len(series_df)

    print(f"Series a revisar contra TVmaze: {total_series}")

    client = TVMazeClient(
        sleep_seconds=sleep_seconds,
        timeout_seconds=20,
    )

    today = date.today()

    check_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []

    for index, plex_row in series_df.iterrows():
        row_number = len(check_rows) + 1

        plex_title = safe_str(plex_row.get("Serie")) or ""
        plex_year = safe_int(plex_row.get("Año Serie"))
        imdb_id = safe_str(plex_row.get("IMDb ID"))
        tvdb_id = safe_str(plex_row.get("TVDb ID"))
        tmdb_id = safe_str(plex_row.get("TMDb ID"))

        plex_last_code = safe_str(plex_row.get("Ultimo Episodio Regular por Orden"))
        plex_last_season, plex_last_number = parse_episode_code(plex_last_code)

        print(f"[{row_number}/{total_series}] Revisando: {plex_title}")

        tvmaze_show, match_method, match_confidence, match_error = find_tvmaze_show(
            client=client,
            plex_title=plex_title,
            plex_year=plex_year,
            imdb_id=imdb_id,
            tvdb_id=tvdb_id,
        )

        tvmaze_episodes: list[dict[str, Any]] = []
        episodes_error: str | None = None

        if tvmaze_show:
            tvmaze_id = safe_int(tvmaze_show.get("id"))

            if tvmaze_id is not None:
                tvmaze_episodes, episodes_error = client.get_show_episodes(tvmaze_id)
            else:
                episodes_error = "TVmaze no retornó ID de show."

        tvmaze_summary = summarize_tvmaze_episodes(
            episodes=tvmaze_episodes,
            today=today,
        )

        tvmaze_last_season = safe_int(tvmaze_summary["TVMaze Ultimo Emitido Temporada"])
        tvmaze_last_number = safe_int(tvmaze_summary["TVMaze Ultimo Emitido Episodio"])

        status = classify_status(
            plex_last_season=plex_last_season,
            plex_last_number=plex_last_number,
            tvmaze_last_season=tvmaze_last_season,
            tvmaze_last_number=tvmaze_last_number,
            match_error=match_error,
            tvmaze_show_found=tvmaze_show is not None,
            tvmaze_episode_count=safe_int(tvmaze_summary["TVMaze Episodios Emitidos"]) or 0,
        )

        missing_for_show: list[dict[str, Any]] = []

        if status == "Faltan episodios" and tvmaze_show:
            missing_for_show = build_missing_episode_rows(
                plex_row=plex_row,
                tvmaze_show=tvmaze_show,
                episodes=tvmaze_episodes,
                today=today,
                plex_last_season=plex_last_season,
                plex_last_number=plex_last_number,
            )

            missing_rows.extend(missing_for_show)

        check_rows.append(
            {
                "Estado Control": status,
                "Serie Plex": plex_title,
                "Año Serie Plex": plex_year,
                "Biblioteca Plex": plex_row.get("Biblioteca"),
                "IMDb ID Plex": imdb_id,
                "TMDb ID Plex": tmdb_id,
                "TVDb ID Plex": tvdb_id,
                "Tipo Identificador Preferente Plex": plex_row.get("Tipo Identificador Preferente"),
                "Identificador Preferente Plex": plex_row.get("Identificador Preferente"),
                "Episodios en Plex": plex_row.get("Episodios en Plex"),
                "Plex Ultimo Regular Codigo": plex_last_code,
                "Plex Ultimo Regular Temporada": plex_last_season,
                "Plex Ultimo Regular Episodio": plex_last_number,
                "Plex Fecha Ultimo Regular": plex_row.get("Fecha Ultimo Episodio Regular por Orden"),
                "TVMaze ID": tvmaze_show.get("id") if tvmaze_show else None,
                "TVMaze Serie": tvmaze_show.get("name") if tvmaze_show else None,
                "TVMaze URL": tvmaze_show.get("url") if tvmaze_show else None,
                "TVMaze Premiered": tvmaze_show.get("premiered") if tvmaze_show else None,
                "TVMaze Ended": tvmaze_show.get("ended") if tvmaze_show else None,
                "TVMaze Status": tvmaze_show.get("status") if tvmaze_show else None,
                "TVMaze Network": (
                    ((tvmaze_show.get("network") or {}).get("name"))
                    if tvmaze_show
                    else None
                ),
                "TVMaze WebChannel": (
                    ((tvmaze_show.get("webChannel") or {}).get("name"))
                    if tvmaze_show
                    else None
                ),
                "Match Metodo": match_method,
                "Match Confianza": match_confidence,
                "Match Observacion": match_error,
                "Error Episodios": episodes_error,
                "TVMaze Episodios Totales": tvmaze_summary["TVMaze Episodios Totales"],
                "TVMaze Episodios Emitidos": tvmaze_summary["TVMaze Episodios Emitidos"],
                "TVMaze Episodios Futuros": tvmaze_summary["TVMaze Episodios Futuros"],
                "TVMaze Ultimo Emitido Codigo": tvmaze_summary["TVMaze Ultimo Emitido Codigo"],
                "TVMaze Ultimo Emitido Temporada": tvmaze_summary["TVMaze Ultimo Emitido Temporada"],
                "TVMaze Ultimo Emitido Episodio": tvmaze_summary["TVMaze Ultimo Emitido Episodio"],
                "TVMaze Ultimo Emitido Titulo": tvmaze_summary["TVMaze Ultimo Emitido Titulo"],
                "TVMaze Ultimo Emitido Fecha": tvmaze_summary["TVMaze Ultimo Emitido Fecha"],
                "TVMaze Proximo Codigo": tvmaze_summary["TVMaze Proximo Codigo"],
                "TVMaze Proximo Temporada": tvmaze_summary["TVMaze Proximo Temporada"],
                "TVMaze Proximo Episodio": tvmaze_summary["TVMaze Proximo Episodio"],
                "TVMaze Proximo Titulo": tvmaze_summary["TVMaze Proximo Titulo"],
                "TVMaze Proximo Fecha": tvmaze_summary["TVMaze Proximo Fecha"],
                "Cantidad Episodios Faltantes": len(missing_for_show),
            }
        )

        if row_number % 25 == 0 or row_number == total_series:
            print(
                f"Avance TVmaze: {row_number}/{total_series} series | "
                f"faltantes acumulados: {len(missing_rows)}"
            )

    check_df = pd.DataFrame(check_rows)
    missing_df = pd.DataFrame(missing_rows)

    no_match_df = check_df[
        check_df["Estado Control"].isin(
            [
                "Sin match TVmaze",
                "Match con revisión",
                "Sin dato suficiente",
                "Plex supera TVmaze",
            ]
        )
    ].copy()

    status_summary_df = (
        check_df.groupby("Estado Control", dropna=False)
        .size()
        .reset_index(name="Cantidad")
        .sort_values("Cantidad", ascending=False)
    )

    run_summary_df = pd.DataFrame(
        [
            {
                "Campo": "Fecha ejecución",
                "Valor": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            {
                "Campo": "Versión checker",
                "Valor": CHECKER_VERSION,
            },
            {
                "Campo": "Archivo Plex origen",
                "Valor": str(plex_export_file),
            },
            {
                "Campo": "Series revisadas",
                "Valor": len(check_df),
            },
            {
                "Campo": "Episodios faltantes detectados",
                "Valor": len(missing_df),
            },
            {
                "Campo": "Sleep segundos TVmaze",
                "Valor": sleep_seconds,
            },
        ]
    )

    output_file = output_dir / f"plex_latest_check_tvmaze_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    write_excel_report(
        output_file=output_file,
        run_summary_df=run_summary_df,
        status_summary_df=status_summary_df,
        check_df=check_df,
        missing_df=missing_df,
        no_match_df=no_match_df,
    )

    return output_file


# ------------------------------------------------------------
# Escritura del reporte
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


def write_excel_report(
    output_file: Path,
    run_summary_df: pd.DataFrame,
    status_summary_df: pd.DataFrame,
    check_df: pd.DataFrame,
    missing_df: pd.DataFrame,
    no_match_df: pd.DataFrame,
) -> None:
    print("")
    print("Preparando reporte Excel...")

    run_summary_df = sanitize_dataframe_for_excel(run_summary_df, "Resumen")
    status_summary_df = sanitize_dataframe_for_excel(status_summary_df, "Resumen_Estados")
    check_df = sanitize_dataframe_for_excel(check_df, "Series_Check")
    missing_df = sanitize_dataframe_for_excel(missing_df, "Episodios_Faltantes")
    no_match_df = sanitize_dataframe_for_excel(no_match_df, "Revision_Manual")

    print(f"Generando archivo: {output_file}")

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        run_summary_df.to_excel(writer, index=False, sheet_name="Resumen")
        status_summary_df.to_excel(writer, index=False, sheet_name="Resumen_Estados")
        check_df.to_excel(writer, index=False, sheet_name="Series_Check")
        missing_df.to_excel(writer, index=False, sheet_name="Episodios_Faltantes")
        no_match_df.to_excel(writer, index=False, sheet_name="Revision_Manual")

        workbook = writer.book
        autosize_excel_columns(
            workbook=workbook,
            sheet_names=[
                "Resumen",
                "Resumen_Estados",
                "Series_Check",
                "Episodios_Faltantes",
                "Revision_Manual",
            ],
        )


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    print("Inicio revisión de últimos episodios")
    print(f"Versión checker: {CHECKER_VERSION}")
    print(f"Raíz proyecto detectada: {PROJECT_ROOT}")

    output_dir = resolve_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    plex_export_file_raw = get_env_var("PLEX_EXPORT_FILE", required=False, default="")
    tvmaze_max_series = parse_int(os.getenv("TVMAZE_MAX_SERIES"), default=0)
    tvmaze_sleep_seconds = parse_float(os.getenv("TVMAZE_SLEEP_SECONDS"), default=0.6)

    if plex_export_file_raw:
        plex_export_file = resolve_path_from_project(plex_export_file_raw)
    else:
        plex_export_file = find_latest_plex_export(output_dir)

    print(f"OUTPUT_DIR: {output_dir}")
    print(f"Archivo Plex origen: {plex_export_file}")
    print(f"TVMAZE_MAX_SERIES: {tvmaze_max_series if tvmaze_max_series > 0 else 'Sin límite'}")
    print(f"TVMAZE_SLEEP_SECONDS: {tvmaze_sleep_seconds}")

    output_file = run_latest_check(
        plex_export_file=plex_export_file,
        output_dir=output_dir,
        max_series=tvmaze_max_series,
        sleep_seconds=tvmaze_sleep_seconds,
    )

    print("")
    print("=" * 70)
    print("Revisión TVmaze finalizada correctamente.")
    print(f"Archivo generado: {output_file}")
    print("=" * 70)


if __name__ == "__main__":
    main()