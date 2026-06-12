# ==========================================================
# weekly_plex_newsletter.py
# TaboPlex - Newsletter semanal de Plex
# ==========================================================
#
# Objetivo:
# - Leer películas agregadas la semana anterior desde Plex.
# - Leer episodios de series agregados la semana anterior desde Plex.
# - Buscar posters públicos en TMDB.
# - Usar logo y fallback poster desde GitHub Pages.
# - Enviar un correo HTML bonito.
#
# Modos:
# - Por defecto: modo prueba, envía solo a MAIL_TEST_TO.
# - Con --prod: modo real, envía a MAIL_TO y MAIL_BCC.
#
# Ejemplos:
# python weekly_plex_newsletter.py
# python weekly_plex_newsletter.py --prod
#
# ==========================================================

import argparse
import csv
import os
import re
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from plexapi.server import PlexServer


# ==========================================================
# Carga de configuración
# ==========================================================

load_dotenv()


# ----------------------------------------------------------
# Plex
# ----------------------------------------------------------

PLEX_BASE_URL = (
    os.getenv("PLEX_BASE_URL")
    or os.getenv("PLEX_URL")
    or os.getenv("PLEX_SERVER_URL")
)

PLEX_TOKEN = (
    os.getenv("PLEX_TOKEN")
    or os.getenv("PLEX_AUTH_TOKEN")
)

# Usamos tus variables existentes.
PLEX_MOVIE_LIBRARY_NAMES_RAW = (
    os.getenv("PLEX_MOVIE_LIBRARY_NAMES")
    or os.getenv("PLEX_MOVIE_LIBRARY")
    or "Movies"
)

PLEX_TV_LIBRARY_NAMES_RAW = (
    os.getenv("PLEX_LIBRARY_NAMES")
    or os.getenv("PLEX_TV_LIBRARY")
    or ""
)


# ----------------------------------------------------------
# Zona horaria
# ----------------------------------------------------------

LOCAL_TIMEZONE_NAME = (
    os.getenv("LOCAL_TIMEZONE")
    or os.getenv("TZ")
    or "America/Santiago"
)

LOCAL_TIMEZONE = ZoneInfo(LOCAL_TIMEZONE_NAME)


# ----------------------------------------------------------
# SMTP
# ----------------------------------------------------------

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_SECURITY = os.getenv("SMTP_SECURITY", "ssl").lower()

SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "TaboPlex")
MAIL_FROM = os.getenv("MAIL_FROM")
MAIL_REPLY_TO = os.getenv("MAIL_REPLY_TO", MAIL_FROM)

MAIL_TEST_TO_RAW = os.getenv("MAIL_TEST_TO", "")
MAIL_TO_RAW = os.getenv("MAIL_TO", "")
MAIL_BCC_RAW = os.getenv("MAIL_BCC", "")


# ----------------------------------------------------------
# Newsletter
# ----------------------------------------------------------

NEWSLETTER_NAME = os.getenv("NEWSLETTER_NAME", "La Cartelera de la Semana 🍿")
NEWSLETTER_SUBJECT_PREFIX = os.getenv("NEWSLETTER_SUBJECT_PREFIX", "🎬 TaboPlex")
NEWSLETTER_SEND_EMPTY = os.getenv("NEWSLETTER_SEND_EMPTY", "no").lower()

NEWSLETTER_LOGO_URL = os.getenv("NEWSLETTER_LOGO_URL")
NEWSLETTER_FALLBACK_POSTER_URL = os.getenv("NEWSLETTER_FALLBACK_POSTER_URL")
NEWSLETTER_PLEX_BUTTON_URL = os.getenv("NEWSLETTER_PLEX_BUTTON_URL", "https://app.plex.tv/")

NEWSLETTER_MOVIE_POSTER_WIDTH = int(os.getenv("NEWSLETTER_MOVIE_POSTER_WIDTH", "148"))
NEWSLETTER_SERIES_POSTER_WIDTH = int(os.getenv("NEWSLETTER_SERIES_POSTER_WIDTH", "84"))

# Hasta este número de episodios se listan con título.
# Si una temporada tiene más episodios, se resume como rango.
NEWSLETTER_SERIES_DETAIL_THRESHOLD = int(os.getenv("NEWSLETTER_SERIES_DETAIL_THRESHOLD", "3"))

# 0 = sin límite.
NEWSLETTER_MAX_SERIES = int(os.getenv("NEWSLETTER_MAX_SERIES", "0"))


# ----------------------------------------------------------
# TMDB
# ----------------------------------------------------------

TMDB_TOKEN = os.getenv("TMDB_READ_ACCESS_TOKEN")
TMDB_IMAGE_SIZE = os.getenv("TMDB_IMAGE_SIZE", "w342")
TMDB_LANGUAGE = os.getenv("TMDB_LANGUAGE", "es-CL")


# ----------------------------------------------------------
# Logs
# ----------------------------------------------------------

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "taboplex_newsletter_log.csv"


# ==========================================================
# Utilidades generales
# ==========================================================

def split_values(raw_value: str) -> list[str]:
    """
    Convierte una cadena separada por coma o punto y coma en lista.

    Ejemplos:
    "Movies"
    "Movies,Movies 4K"
    "correo1@test.com;correo2@test.com"
    """
    if not raw_value:
        return []

    normalized = raw_value.replace(";", ",")

    return [
        item.strip()
        for item in normalized.split(",")
        if item.strip()
    ]


def bool_from_yes_no(value: str) -> bool:
    """
    Convierte yes/no, true/false, 1/0 en booleano.
    """
    return str(value).strip().lower() in ("yes", "true", "1", "y", "si", "sí")


def truncate_text(text: str, max_length: int = 650) -> str:
    """
    Acorta textos largos para que el correo no se vuelva pesado.
    """
    clean_text = (text or "").strip()

    if len(clean_text) <= max_length:
        return clean_text

    return clean_text[:max_length].rsplit(" ", 1)[0] + "..."


def clean_show_title_for_search(title: str) -> str:
    """
    Limpia nombres tipo 'Invasion (2021)' para mejorar búsqueda en TMDB.
    """
    return re.sub(r"\s+\(\d{4}\)$", "", title or "").strip()


def get_previous_week_range() -> tuple[datetime, datetime]:
    """
    Retorna el rango de la semana calendario anterior:
    lunes 00:00:00 a domingo 23:59:59.
    """
    now = datetime.now(LOCAL_TIMEZONE)

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


def normalize_plex_datetime(value: datetime) -> datetime:
    """
    Normaliza fechas de Plex a la zona horaria local.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=LOCAL_TIMEZONE)

    return value.astimezone(LOCAL_TIMEZONE)


def format_period(start_date: datetime, end_date: datetime) -> str:
    """
    Formatea rango de fechas para mostrar en correo.
    """
    return f"{start_date.strftime('%d-%m-%Y')} al {end_date.strftime('%d-%m-%Y')}"


def format_period_short(start_date: datetime, end_date: datetime) -> str:
    """
    Formatea rango corto para asunto.
    """
    return f"{start_date.strftime('%d %b')} al {end_date.strftime('%d %b')}"


# ==========================================================
# Validaciones
# ==========================================================

def validate_required_config() -> None:
    """
    Valida configuración mínima antes de ejecutar.
    """
    required_vars = {
        "PLEX_BASE_URL / PLEX_URL / PLEX_SERVER_URL": PLEX_BASE_URL,
        "PLEX_TOKEN / PLEX_AUTH_TOKEN": PLEX_TOKEN,
        "PLEX_MOVIE_LIBRARY_NAMES": PLEX_MOVIE_LIBRARY_NAMES_RAW,
        "PLEX_LIBRARY_NAMES": PLEX_TV_LIBRARY_NAMES_RAW,
        "SMTP_HOST": SMTP_HOST,
        "SMTP_PORT": SMTP_PORT,
        "SMTP_SECURITY": SMTP_SECURITY,
        "SMTP_USER": SMTP_USER,
        "SMTP_PASSWORD": SMTP_PASSWORD,
        "MAIL_FROM": MAIL_FROM,
        "NEWSLETTER_LOGO_URL": NEWSLETTER_LOGO_URL,
        "NEWSLETTER_FALLBACK_POSTER_URL": NEWSLETTER_FALLBACK_POSTER_URL,
        "TMDB_READ_ACCESS_TOKEN": TMDB_TOKEN,
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


# ==========================================================
# TMDB
# ==========================================================

def tmdb_get(path: str, params: dict | None = None) -> dict:
    """
    Ejecuta GET contra TMDB usando Bearer Token.
    """
    url = f"https://api.themoviedb.org/3{path}"

    headers = {
        "Authorization": f"Bearer {TMDB_TOKEN}",
        "accept": "application/json",
    }

    request_params = params or {}
    request_params.setdefault("language", TMDB_LANGUAGE)

    response = requests.get(
        url,
        headers=headers,
        params=request_params,
        timeout=30,
    )

    response.raise_for_status()
    return response.json()


def build_tmdb_poster_url(poster_path: str | None) -> str:
    """
    Construye URL pública de poster TMDB.
    Si no hay poster, usa fallback.
    """
    if not poster_path:
        return NEWSLETTER_FALLBACK_POSTER_URL

    return f"https://image.tmdb.org/t/p/{TMDB_IMAGE_SIZE}{poster_path}"


def extract_external_ids(plex_item) -> dict:
    """
    Extrae IDs externos desde un objeto Plex.

    Puede retornar:
    - imdb
    - tmdb
    - tvdb
    """
    ids = {
        "imdb": None,
        "tmdb": None,
        "tvdb": None,
    }

    if plex_item is None:
        return ids

    try:
        plex_item = plex_item.reload()
    except Exception:
        pass

    guids = getattr(plex_item, "guids", []) or []

    for guid_item in guids:
        guid_value = getattr(guid_item, "id", "") or ""

        if guid_value.startswith("imdb://"):
            ids["imdb"] = guid_value.replace("imdb://", "").strip()

        elif guid_value.startswith("tmdb://"):
            ids["tmdb"] = guid_value.replace("tmdb://", "").strip()

        elif guid_value.startswith("tvdb://"):
            ids["tvdb"] = guid_value.replace("tvdb://", "").strip()

    return ids


def resolve_movie_from_tmdb(movie) -> dict:
    """
    Resuelve poster, descripción y link TMDB de una película.
    """
    title = getattr(movie, "title", "Sin título")
    year = getattr(movie, "year", None)
    plex_summary = getattr(movie, "summary", None)

    external_ids = extract_external_ids(movie)
    tmdb_item = None

    # 1. TMDB ID directo.
    if external_ids.get("tmdb"):
        try:
            tmdb_item = tmdb_get(f"/movie/{external_ids['tmdb']}")
        except Exception as exc:
            print(f"TMDB movie: no se pudo buscar por TMDB ID para {title}: {exc}")

    # 2. IMDb ID.
    if tmdb_item is None and external_ids.get("imdb"):
        try:
            find_data = tmdb_get(
                f"/find/{external_ids['imdb']}",
                params={
                    "external_source": "imdb_id",
                    "language": TMDB_LANGUAGE,
                },
            )

            results = find_data.get("movie_results", [])

            if results:
                tmdb_item = results[0]

        except Exception as exc:
            print(f"TMDB movie: no se pudo buscar por IMDb ID para {title}: {exc}")

    # 3. Título + año.
    if tmdb_item is None:
        try:
            params = {
                "query": title,
                "include_adult": "false",
                "language": TMDB_LANGUAGE,
            }

            if year:
                params["year"] = str(year)

            search_data = tmdb_get("/search/movie", params=params)
            results = search_data.get("results", [])

            if results:
                tmdb_item = results[0]

        except Exception as exc:
            print(f"TMDB movie: no se pudo buscar por título para {title}: {exc}")

    if tmdb_item:
        tmdb_id = tmdb_item.get("id")
        tmdb_url = (
            f"https://www.themoviedb.org/movie/{tmdb_id}"
            if tmdb_id
            else "https://www.themoviedb.org/"
        )

        return {
            "poster_url": build_tmdb_poster_url(tmdb_item.get("poster_path")),
            "overview": tmdb_item.get("overview") or plex_summary or "Sin descripción disponible.",
            "tmdb_url": tmdb_url,
            "source": "TMDB",
        }

    return {
        "poster_url": NEWSLETTER_FALLBACK_POSTER_URL,
        "overview": plex_summary or "Sin descripción disponible.",
        "tmdb_url": "https://www.themoviedb.org/",
        "source": "Fallback",
    }


def resolve_show_from_tmdb(show_title: str, show_object=None) -> dict:
    """
    Resuelve poster y link TMDB de una serie.

    Orden:
    1. TMDB ID si Plex lo tiene.
    2. IMDb ID si Plex lo tiene.
    3. TVDB ID si Plex lo tiene.
    4. Búsqueda por título.
    """
    external_ids = extract_external_ids(show_object) if show_object else {}
    tmdb_item = None

    # 1. TMDB ID directo.
    if external_ids.get("tmdb"):
        try:
            tmdb_item = tmdb_get(f"/tv/{external_ids['tmdb']}")
        except Exception as exc:
            print(f"TMDB show: no se pudo buscar por TMDB ID para {show_title}: {exc}")

    # 2. IMDb ID.
    if tmdb_item is None and external_ids.get("imdb"):
        try:
            find_data = tmdb_get(
                f"/find/{external_ids['imdb']}",
                params={
                    "external_source": "imdb_id",
                    "language": TMDB_LANGUAGE,
                },
            )

            results = find_data.get("tv_results", [])

            if results:
                tmdb_item = results[0]

        except Exception as exc:
            print(f"TMDB show: no se pudo buscar por IMDb ID para {show_title}: {exc}")

    # 3. TVDB ID.
    if tmdb_item is None and external_ids.get("tvdb"):
        try:
            find_data = tmdb_get(
                f"/find/{external_ids['tvdb']}",
                params={
                    "external_source": "tvdb_id",
                    "language": TMDB_LANGUAGE,
                },
            )

            results = find_data.get("tv_results", [])

            if results:
                tmdb_item = results[0]

        except Exception as exc:
            print(f"TMDB show: no se pudo buscar por TVDB ID para {show_title}: {exc}")

    # 4. Título.
    if tmdb_item is None:
        try:
            search_title = clean_show_title_for_search(show_title)

            search_data = tmdb_get(
                "/search/tv",
                params={
                    "query": search_title,
                    "include_adult": "false",
                    "language": TMDB_LANGUAGE,
                },
            )

            results = search_data.get("results", [])

            if results:
                tmdb_item = results[0]

        except Exception as exc:
            print(f"TMDB show: no se pudo buscar por título para {show_title}: {exc}")

    if tmdb_item:
        tmdb_id = tmdb_item.get("id")
        tmdb_url = (
            f"https://www.themoviedb.org/tv/{tmdb_id}"
            if tmdb_id
            else "https://www.themoviedb.org/"
        )

        return {
            "poster_url": build_tmdb_poster_url(tmdb_item.get("poster_path")),
            "tmdb_url": tmdb_url,
            "source": "TMDB",
        }

    return {
        "poster_url": NEWSLETTER_FALLBACK_POSTER_URL,
        "tmdb_url": "https://www.themoviedb.org/",
        "source": "Fallback",
    }


# ==========================================================
# Plex - Películas
# ==========================================================

def get_recent_movies_from_plex(plex: PlexServer) -> tuple[list[dict], datetime, datetime]:
    """
    Lee películas agregadas durante la semana anterior.
    """
    start_date, end_date = get_previous_week_range()
    library_names = split_values(PLEX_MOVIE_LIBRARY_NAMES_RAW)

    all_recent_movies = []

    for library_name in library_names:
        print(f"Leyendo biblioteca de películas: {library_name}")

        library = plex.library.section(library_name)
        movies = library.all()

        print(f"- Películas encontradas: {len(movies)}")

        for movie in movies:
            added_at = getattr(movie, "addedAt", None)

            if added_at is None:
                continue

            added_at_local = normalize_plex_datetime(added_at)

            if start_date <= added_at_local <= end_date:
                all_recent_movies.append(
                    {
                        "plex_object": movie,
                        "library": library_name,
                        "title": getattr(movie, "title", "Sin título"),
                        "year": getattr(movie, "year", None),
                        "added_at": added_at_local,
                    }
                )

    all_recent_movies.sort(
        key=lambda item: item["added_at"],
        reverse=True,
    )

    return all_recent_movies, start_date, end_date


# ==========================================================
# Plex - Series / Episodios
# ==========================================================

def get_recent_episodes_from_plex(plex: PlexServer) -> list[dict]:
    """
    Lee episodios agregados durante la semana anterior.
    """
    start_date, end_date = get_previous_week_range()
    library_names = split_values(PLEX_TV_LIBRARY_NAMES_RAW)

    all_recent_episodes = []

    for library_name in library_names:
        print(f"Leyendo biblioteca de series: {library_name}")

        library = plex.library.section(library_name)
        episodes = library.search(libtype="episode")

        print(f"- Episodios encontrados: {len(episodes)}")

        for episode in episodes:
            added_at = getattr(episode, "addedAt", None)

            if added_at is None:
                continue

            added_at_local = normalize_plex_datetime(added_at)

            if start_date <= added_at_local <= end_date:
                all_recent_episodes.append(
                    {
                        "library": library_name,
                        "show_title": getattr(episode, "grandparentTitle", "Serie sin título"),
                        "season_number": getattr(episode, "parentIndex", None),
                        "episode_number": getattr(episode, "index", None),
                        "episode_title": getattr(episode, "title", "Episodio sin título"),
                        "added_at": added_at_local,
                        "show_rating_key": getattr(episode, "grandparentRatingKey", None),
                        "episode_object": episode,
                    }
                )

    return all_recent_episodes


def group_episodes_by_show_and_season(episodes: list[dict]) -> list[dict]:
    """
    Agrupa episodios por serie y temporada.
    """
    grouped = {}

    for episode in episodes:
        show_title = episode["show_title"]
        show_rating_key = episode.get("show_rating_key")
        season_number = episode["season_number"] or 0

        if show_title not in grouped:
            grouped[show_title] = {
                "show_title": show_title,
                "show_rating_key": show_rating_key,
                "total_episodes": 0,
                "seasons": {},
            }

        grouped[show_title]["total_episodes"] += 1

        if not grouped[show_title].get("show_rating_key") and show_rating_key:
            grouped[show_title]["show_rating_key"] = show_rating_key

        grouped[show_title]["seasons"].setdefault(season_number, [])
        grouped[show_title]["seasons"][season_number].append(episode)

    result = []

    for show_data in grouped.values():
        seasons_list = []

        for season_number, season_episodes in show_data["seasons"].items():
            season_episodes.sort(
                key=lambda item: (
                    item["episode_number"] or 0,
                    item["added_at"],
                )
            )

            seasons_list.append(
                {
                    "season_number": season_number,
                    "episodes": season_episodes,
                }
            )

        seasons_list.sort(key=lambda item: item["season_number"] or 0)

        result.append(
            {
                "show_title": show_data["show_title"],
                "show_rating_key": show_data.get("show_rating_key"),
                "total_episodes": show_data["total_episodes"],
                "seasons": seasons_list,
            }
        )

    result.sort(
        key=lambda item: (
            -item["total_episodes"],
            item["show_title"].lower(),
        )
    )

    if NEWSLETTER_MAX_SERIES > 0:
        result = result[:NEWSLETTER_MAX_SERIES]

    return result


def enrich_series_with_tmdb(plex: PlexServer, series_groups: list[dict]) -> list[dict]:
    """
    Agrega poster y URL TMDB a cada serie agrupada.
    """
    enriched = []

    for index, show_data in enumerate(series_groups, start=1):
        show_title = show_data["show_title"]

        print(f"Resolviendo TMDB serie {index}/{len(series_groups)}: {show_title}")

        show_object = None

        if show_data.get("show_rating_key"):
            try:
                show_object = plex.fetchItem(show_data["show_rating_key"])
            except Exception:
                show_object = None

        tmdb_data = resolve_show_from_tmdb(
            show_title=show_title,
            show_object=show_object,
        )

        enriched.append(
            {
                **show_data,
                "poster_url": tmdb_data["poster_url"],
                "tmdb_url": tmdb_data["tmdb_url"],
                "source": tmdb_data["source"],
            }
        )

    return enriched


# ==========================================================
# HTML - Películas
# ==========================================================

def build_movie_cards_html(movies: list[dict]) -> str:
    """
    Construye tarjetas grandes para películas.
    """
    if not movies:
        return ""

    cards = [
        """
        <tr>
          <td style="padding:26px 28px 14px 28px;">
            <h2 style="margin:0; font-size:24px; line-height:30px; color:#1f1f1f;">
              🎬 Películas agregadas
            </h2>
          </td>
        </tr>
        """
    ]

    for movie in movies:
        title = escape(movie["title"])
        year = escape(str(movie["year"] or "s/f"))
        overview = escape(truncate_text(movie["overview"]))
        poster_url = escape(movie["poster_url"], quote=True)
        tmdb_url = escape(movie["tmdb_url"], quote=True)
        source = escape(movie["source"])
        added_at = escape(movie["added_at"].strftime("%d-%m-%Y %H:%M"))

        card = f"""
        <tr>
          <td style="padding:0 28px 22px 28px;">

            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e8dfd2; border-radius:16px; overflow:hidden; background:#fbfaf7;">
              <tr>

                <td width="{NEWSLETTER_MOVIE_POSTER_WIDTH + 36}" valign="top" style="width:{NEWSLETTER_MOVIE_POSTER_WIDTH + 36}px; padding:18px;">
                  <img
                    src="{poster_url}"
                    alt="{title}"
                    width="{NEWSLETTER_MOVIE_POSTER_WIDTH}"
                    style="display:block; width:{NEWSLETTER_MOVIE_POSTER_WIDTH}px; height:auto; border-radius:12px; border:1px solid #d8d3ca;"
                  >
                </td>

                <td valign="top" style="padding:18px 18px 18px 0;">

                  <div style="display:inline-block; padding:4px 10px; background:#101827; color:#ffffff; border-radius:999px; font-size:12px; line-height:16px; margin-bottom:10px;">
                    Película · {source}
                  </div>

                  <h3 style="margin:0 0 6px 0; font-size:23px; line-height:29px; color:#1f1f1f;">
                    {title}
                  </h3>

                  <div style="font-size:14px; line-height:20px; color:#6b6258; margin-bottom:12px;">
                    Año: {year} · Agregada: {added_at}
                  </div>

                  <p style="margin:0 0 16px 0; font-size:14px; line-height:22px; color:#3c3a36;">
                    {overview}
                  </p>

                  <a href="{tmdb_url}" style="display:inline-block; background:#ec8f6c; color:#1f1f1f; text-decoration:none; font-weight:bold; padding:10px 14px; border-radius:10px; font-size:14px;">
                    Ver ficha
                  </a>

                </td>

              </tr>
            </table>

          </td>
        </tr>
        """

        cards.append(card)

    return "\n".join(cards)


# ==========================================================
# HTML - Series
# ==========================================================

def summarize_episode_range(episodes: list[dict]) -> str:
    """
    Resume episodios como:
    E01–E08
    o si no hay números:
    8 episodios
    """
    episode_numbers = [
        episode["episode_number"]
        for episode in episodes
        if episode.get("episode_number") is not None
    ]

    if not episode_numbers:
        count = len(episodes)
        episode_word = "episodio" if count == 1 else "episodios"
        return f"{count} {episode_word}"

    first_episode = min(episode_numbers)
    last_episode = max(episode_numbers)

    if first_episode == last_episode:
        return f"E{first_episode:02d}"

    return f"E{first_episode:02d}–E{last_episode:02d}"


def build_season_summary_html(season: dict) -> str:
    """
    Construye resumen HTML de una temporada.

    Si tiene pocos episodios, lista títulos.
    Si tiene muchos, usa rango.
    """
    season_number = season["season_number"]
    episodes = season["episodes"]

    season_label = (
        f"Temporada {season_number}"
        if season_number
        else "Temporada desconocida"
    )

    safe_season_label = escape(season_label)

    if len(episodes) <= NEWSLETTER_SERIES_DETAIL_THRESHOLD:
        episode_lines = []

        for episode in episodes:
            episode_number = episode.get("episode_number")
            episode_title = escape(episode.get("episode_title") or "Episodio sin título")

            if episode_number:
                episode_code = f"E{episode_number:02d}"
            else:
                episode_code = "E??"

            episode_lines.append(
                f"""
                <div style="font-size:13px; line-height:19px; color:#3c3a36;">
                  • {escape(episode_code)} - {episode_title}
                </div>
                """
            )

        detail_html = "\n".join(episode_lines)

    else:
        range_text = escape(summarize_episode_range(episodes))
        count = len(episodes)
        episode_word = "episodio" if count == 1 else "episodios"
        count_text = escape(f"{count} {episode_word}")

        detail_html = f"""
        <div style="font-size:13px; line-height:19px; color:#3c3a36;">
          • {range_text} · {count_text}
        </div>
        """

    return f"""
    <div style="margin:0 0 8px 0;">
      <div style="font-size:13px; line-height:19px; color:#6b6258; font-weight:bold;">
        {safe_season_label}
      </div>
      {detail_html}
    </div>
    """


def build_series_cards_html(series_groups: list[dict]) -> str:
    """
    Construye tarjetas compactas para series.
    """
    if not series_groups:
        return ""

    total_episodes = sum(item["total_episodes"] for item in series_groups)

    cards = [
        f"""
        <tr>
          <td style="padding:8px 28px 14px 28px;">
            <h2 style="margin:0; font-size:24px; line-height:30px; color:#1f1f1f;">
              📺 Series agregadas
            </h2>
            <p style="margin:6px 0 0 0; font-size:14px; line-height:21px; color:#6b6258;">
              {len(series_groups)} series · {total_episodes} episodios agregados
            </p>
          </td>
        </tr>
        """
    ]

    for show_data in series_groups:
        show_title = escape(show_data["show_title"])
        poster_url = escape(show_data["poster_url"], quote=True)
        tmdb_url = escape(show_data["tmdb_url"], quote=True)
        source = escape(show_data["source"])
        total_episodes = show_data["total_episodes"]

        if total_episodes == 1:
            total_text = "1 episodio agregado"
        else:
            total_text = f"{total_episodes} episodios agregados"

        seasons_html = "\n".join(
            build_season_summary_html(season)
            for season in show_data["seasons"]
        )

        card = f"""
        <tr>
          <td style="padding:0 28px 16px 28px;">

            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e8dfd2; border-radius:14px; overflow:hidden; background:#fbfaf7;">
              <tr>

                <td width="{NEWSLETTER_SERIES_POSTER_WIDTH + 28}" valign="top" style="width:{NEWSLETTER_SERIES_POSTER_WIDTH + 28}px; padding:14px;">
                  <img
                    src="{poster_url}"
                    alt="{show_title}"
                    width="{NEWSLETTER_SERIES_POSTER_WIDTH}"
                    style="display:block; width:{NEWSLETTER_SERIES_POSTER_WIDTH}px; height:auto; border-radius:10px; border:1px solid #d8d3ca;"
                  >
                </td>

                <td valign="top" style="padding:14px 16px 14px 0;">

                  <div style="display:inline-block; padding:3px 9px; background:#101827; color:#ffffff; border-radius:999px; font-size:11px; line-height:15px; margin-bottom:7px;">
                    Serie · {source}
                  </div>

                  <h3 style="margin:0 0 4px 0; font-size:19px; line-height:24px; color:#1f1f1f;">
                    {show_title}
                  </h3>

                  <div style="font-size:13px; line-height:19px; color:#6b6258; margin-bottom:10px;">
                    {escape(total_text)}
                  </div>

                  {seasons_html}

                  <a href="{tmdb_url}" style="display:inline-block; margin-top:4px; color:#1f1f1f; text-decoration:underline; font-size:13px; line-height:18px;">
                    Ver ficha
                  </a>

                </td>

              </tr>
            </table>

          </td>
        </tr>
        """

        cards.append(card)

    return "\n".join(cards)


# ==========================================================
# HTML completo
# ==========================================================

def build_email_html(
    movies: list[dict],
    series_groups: list[dict],
    start_date: datetime,
    end_date: datetime,
) -> str:
    """
    Construye HTML completo.
    """
    period_text = escape(format_period(start_date, end_date))
    safe_newsletter_name = escape(NEWSLETTER_NAME)
    safe_logo_url = escape(NEWSLETTER_LOGO_URL, quote=True)
    safe_plex_url = escape(NEWSLETTER_PLEX_BUTTON_URL, quote=True)

    total_movies = len(movies)
    total_series = len(series_groups)
    total_episodes = sum(item["total_episodes"] for item in series_groups)

    summary_parts = []

    if total_movies == 1:
        summary_parts.append("1 película")
    elif total_movies > 1:
        summary_parts.append(f"{total_movies} películas")

    if total_series == 1:
        episode_word = "episodio" if total_episodes == 1 else "episodios"
        summary_parts.append(f"1 serie / {total_episodes} {episode_word}")
    elif total_series > 1:
        summary_parts.append(f"{total_series} series / {total_episodes} episodios")

    if summary_parts:
        summary_text = " · ".join(summary_parts)
    else:
        summary_text = "Sin novedades esta semana"

    movie_cards_html = build_movie_cards_html(movies)
    series_cards_html = build_series_cards_html(series_groups)

    empty_html = ""

    if not movies and not series_groups:
        empty_html = """
        <tr>
          <td style="padding:28px;">
            <p style="margin:0; font-size:16px; line-height:24px;">
              Esta semana no se agregaron nuevas películas ni episodios a TaboPlex 🎬
            </p>
          </td>
        </tr>
        """

    return f"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>{safe_newsletter_name}</title>
</head>

<body style="margin:0; padding:0; background:#f4f2ee; font-family:Arial, Helvetica, sans-serif; color:#1f1f1f;">

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f2ee; padding:24px 0;">
    <tr>
      <td align="center">

        <table role="presentation" width="660" cellpadding="0" cellspacing="0" style="width:660px; max-width:94%; background:#ffffff; border-radius:18px; overflow:hidden; border:1px solid #e5ded3;">

          <tr>
            <td align="center" style="padding:30px 24px 22px 24px; background:#101827;">
              <img
                src="{safe_logo_url}"
                alt="TaboPlex"
                width="190"
                style="display:block; max-width:190px; width:190px; height:auto; margin:0 auto 16px auto;"
              >

              <div style="font-size:14px; line-height:20px; color:#f4d58d; letter-spacing:0.5px;">
                {period_text}
              </div>

              <h1 style="margin:8px 0 0 0; font-size:30px; line-height:36px; color:#ffffff;">
                {safe_newsletter_name}
              </h1>

              <p style="margin:10px 0 0 0; font-size:16px; line-height:24px; color:#d8d3ca;">
                {escape(summary_text)} durante la semana anterior 🍿
              </p>
            </td>
          </tr>

          <tr>
            <td style="padding:24px 28px 8px 28px;">
              <p style="margin:0; font-size:16px; line-height:24px;">
                Hola! Estas son las novedades agregadas recientemente a la biblioteca de TaboPlex 🎬📺
              </p>
            </td>
          </tr>

          {movie_cards_html}

          {series_cards_html}

          {empty_html}

          <tr>
            <td align="center" style="padding:10px 28px 32px 28px;">
              <a href="{safe_plex_url}" style="display:inline-block; background:#101827; color:#ffffff; text-decoration:none; font-weight:bold; padding:13px 22px; border-radius:12px; font-size:15px;">
                Abrir Plex
              </a>
            </td>
          </tr>

          <tr>
            <td style="padding:18px 28px; background:#fbfaf7; border-top:1px solid #e8dfd2;">
              <p style="margin:0; font-size:12px; line-height:18px; color:#7c746c;">
                Movie/TV data and images provided by TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.
              </p>
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>

</body>
</html>
""".strip()


def build_plain_text(
    movies: list[dict],
    series_groups: list[dict],
    start_date: datetime,
    end_date: datetime,
) -> str:
    """
    Construye versión texto plano.
    """
    lines = [
        NEWSLETTER_NAME,
        "",
        f"Periodo: {format_period(start_date, end_date)}",
        "",
    ]

    if movies:
        lines.append("Películas agregadas:")
        lines.append("")

        for index, movie in enumerate(movies, start=1):
            year_text = f" ({movie['year']})" if movie["year"] else ""
            lines.append(f"{index}. {movie['title']}{year_text}")
            lines.append(f"   Agregada: {movie['added_at'].strftime('%d-%m-%Y %H:%M')}")
            lines.append(f"   Ficha: {movie['tmdb_url']}")
            lines.append("")

    if series_groups:
        lines.append("Series agregadas:")
        lines.append("")

        for show_data in series_groups:
            episode_word = "episodio" if show_data["total_episodes"] == 1 else "episodios"
            lines.append(f"- {show_data['show_title']} ({show_data['total_episodes']} {episode_word})")

            for season in show_data["seasons"]:
                season_number = season["season_number"]
                season_label = f"Temporada {season_number}" if season_number else "Temporada desconocida"

                if len(season["episodes"]) <= NEWSLETTER_SERIES_DETAIL_THRESHOLD:
                    lines.append(f"  {season_label}")

                    for episode in season["episodes"]:
                        episode_number = episode.get("episode_number")
                        episode_code = f"E{episode_number:02d}" if episode_number else "E??"
                        lines.append(f"  - {episode_code}: {episode['episode_title']}")

                else:
                    lines.append(f"  {season_label}: {summarize_episode_range(season['episodes'])}")

            lines.append(f"  Ficha: {show_data['tmdb_url']}")
            lines.append("")

    if not movies and not series_groups:
        lines.append("Esta semana no se agregaron nuevas películas ni episodios.")

    lines.extend(
        [
            "",
            "Saludos,",
            "TaboPlex",
        ]
    )

    return "\n".join(lines)


# ==========================================================
# Envío de correo
# ==========================================================

def get_recipients(prod: bool) -> tuple[list[str], list[str], list[str]]:
    """
    Retorna destinatarios:
    - to_recipients
    - bcc_recipients
    - envelope_recipients
    """
    if prod:
        to_recipients = split_values(MAIL_TO_RAW)
        bcc_recipients = split_values(MAIL_BCC_RAW)
    else:
        to_recipients = split_values(MAIL_TEST_TO_RAW)
        bcc_recipients = []

    envelope_recipients = to_recipients + bcc_recipients

    if not envelope_recipients:
        raise RuntimeError("No hay destinatarios configurados para el envío.")

    return to_recipients, bcc_recipients, envelope_recipients


def send_email(
    subject: str,
    plain_body: str,
    html_body: str,
    prod: bool,
) -> tuple[list[str], list[str]]:
    """
    Envía el correo.
    """
    to_recipients, bcc_recipients, envelope_recipients = get_recipients(prod)

    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = formataddr((MAIL_FROM_NAME, MAIL_FROM))
    message["To"] = ", ".join(to_recipients)
    message["Reply-To"] = MAIL_REPLY_TO

    # ------------------------------------------------------
    # Muy importante para DKIM:
    # El servidor firma estos headers. Los dejamos creados
    # antes de enviar para que no sean agregados después.
    # ------------------------------------------------------
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="gustavohuerta.com")

    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")

    print("Enviando correo...")
    print(f"- Modo: {'PROD' if prod else 'TEST'}")
    print("- To:")
    for recipient in to_recipients:
        print(f"  - {recipient}")

    if bcc_recipients:
        print("- BCC:")
        for recipient in bcc_recipients:
            print(f"  - {recipient}")

    if SMTP_SECURITY == "ssl":
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(
                message,
                from_addr=MAIL_FROM,
                to_addrs=envelope_recipients,
            )

    elif SMTP_SECURITY == "starttls":
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(
                message,
                from_addr=MAIL_FROM,
                to_addrs=envelope_recipients,
            )

    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(
                message,
                from_addr=MAIL_FROM,
                to_addrs=envelope_recipients,
            )

    print("Correo enviado correctamente.")

    return to_recipients, bcc_recipients


# ==========================================================
# Log
# ==========================================================

def write_log(
    prod: bool,
    subject: str,
    start_date: datetime,
    end_date: datetime,
    movie_count: int,
    series_count: int,
    episode_count: int,
    to_recipients: list[str],
    bcc_recipients: list[str],
    status: str,
    detail: str,
) -> None:
    """
    Escribe log CSV simple.
    """
    LOG_DIR.mkdir(exist_ok=True)

    file_exists = LOG_FILE.exists()

    with LOG_FILE.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "timestamp",
                "mode",
                "subject",
                "period_start",
                "period_end",
                "movie_count",
                "series_count",
                "episode_count",
                "to",
                "bcc",
                "status",
                "detail",
            ],
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(
            {
                "timestamp": datetime.now(LOCAL_TIMEZONE).isoformat(),
                "mode": "prod" if prod else "test",
                "subject": subject,
                "period_start": start_date.isoformat(),
                "period_end": end_date.isoformat(),
                "movie_count": movie_count,
                "series_count": series_count,
                "episode_count": episode_count,
                "to": ",".join(to_recipients),
                "bcc": ",".join(bcc_recipients),
                "status": status,
                "detail": detail,
            }
        )


# ==========================================================
# Flujo principal
# ==========================================================

def build_subject(start_date: datetime, end_date: datetime, prod: bool) -> str:
    """
    Construye asunto del correo.
    """
    period_short = format_period_short(start_date, end_date)

    subject = f"{NEWSLETTER_SUBJECT_PREFIX} — {NEWSLETTER_NAME} — {period_short}"

    if not prod:
        subject = f"{subject} — TEST"

    return subject


def main() -> None:
    """
    Ejecuta el newsletter completo.
    """
    parser = argparse.ArgumentParser(
        description="Envía newsletter semanal de Plex para TaboPlex."
    )

    parser.add_argument(
        "--prod",
        action="store_true",
        help="Envía a MAIL_TO y MAIL_BCC. Sin este flag, envía solo a MAIL_TEST_TO.",
    )

    args = parser.parse_args()
    prod = bool(args.prod)

    validate_required_config()

    print("==============================================")
    print("TaboPlex - Newsletter semanal")
    print("==============================================")
    print(f"Modo: {'PROD' if prod else 'TEST'}")
    print(f"Plex URL: {PLEX_BASE_URL}")
    print(f"Películas: {PLEX_MOVIE_LIBRARY_NAMES_RAW}")
    print(f"Series: {PLEX_TV_LIBRARY_NAMES_RAW}")
    print("")

    start_date, end_date = get_previous_week_range()

    plex = PlexServer(PLEX_BASE_URL, PLEX_TOKEN)

    # ------------------------------------------------------
    # Películas
    # ------------------------------------------------------
    recent_movies, start_date, end_date = get_recent_movies_from_plex(plex)

    print("")
    print(f"Películas agregadas encontradas: {len(recent_movies)}")

    newsletter_movies = []

    for index, movie in enumerate(recent_movies, start=1):
        print(f"Resolviendo TMDB película {index}/{len(recent_movies)}: {movie['title']}")

        tmdb_data = resolve_movie_from_tmdb(movie["plex_object"])

        newsletter_movies.append(
            {
                "title": movie["title"],
                "year": movie["year"],
                "added_at": movie["added_at"],
                "poster_url": tmdb_data["poster_url"],
                "overview": tmdb_data["overview"],
                "tmdb_url": tmdb_data["tmdb_url"],
                "source": tmdb_data["source"],
            }
        )

    # ------------------------------------------------------
    # Series
    # ------------------------------------------------------
    print("")
    recent_episodes = get_recent_episodes_from_plex(plex)
    series_groups = group_episodes_by_show_and_season(recent_episodes)

    print("")
    print(f"Series con episodios agregados: {len(series_groups)}")
    print(f"Episodios agregados encontrados: {len(recent_episodes)}")

    newsletter_series = enrich_series_with_tmdb(plex, series_groups)

    # ------------------------------------------------------
    # Decidir envío vacío
    # ------------------------------------------------------
    total_items = len(newsletter_movies) + len(newsletter_series)

    if total_items == 0 and not bool_from_yes_no(NEWSLETTER_SEND_EMPTY):
        subject = build_subject(start_date, end_date, prod)

        print("")
        print("No hay novedades y NEWSLETTER_SEND_EMPTY=no. No se envía correo.")

        write_log(
            prod=prod,
            subject=subject,
            start_date=start_date,
            end_date=end_date,
            movie_count=0,
            series_count=0,
            episode_count=0,
            to_recipients=[],
            bcc_recipients=[],
            status="skipped",
            detail="Sin novedades y NEWSLETTER_SEND_EMPTY=no",
        )

        return

    # ------------------------------------------------------
    # Construir correo
    # ------------------------------------------------------
    subject = build_subject(start_date, end_date, prod)

    plain_body = build_plain_text(
        movies=newsletter_movies,
        series_groups=newsletter_series,
        start_date=start_date,
        end_date=end_date,
    )

    html_body = build_email_html(
        movies=newsletter_movies,
        series_groups=newsletter_series,
        start_date=start_date,
        end_date=end_date,
    )

    # ------------------------------------------------------
    # Enviar
    # ------------------------------------------------------
    try:
        to_recipients, bcc_recipients = send_email(
            subject=subject,
            plain_body=plain_body,
            html_body=html_body,
            prod=prod,
        )

        write_log(
            prod=prod,
            subject=subject,
            start_date=start_date,
            end_date=end_date,
            movie_count=len(newsletter_movies),
            series_count=len(newsletter_series),
            episode_count=len(recent_episodes),
            to_recipients=to_recipients,
            bcc_recipients=bcc_recipients,
            status="sent",
            detail="Correo enviado correctamente",
        )

    except Exception as exc:
        write_log(
            prod=prod,
            subject=subject,
            start_date=start_date,
            end_date=end_date,
            movie_count=len(newsletter_movies),
            series_count=len(newsletter_series),
            episode_count=len(recent_episodes),
            to_recipients=[],
            bcc_recipients=[],
            status="error",
            detail=str(exc),
        )

        raise

    print("")
    print("Proceso finalizado correctamente.")


if __name__ == "__main__":
    main()
