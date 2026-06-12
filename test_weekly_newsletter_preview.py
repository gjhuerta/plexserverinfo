# ==========================================================
# test_weekly_newsletter_preview.py
# TaboPlex - Prueba real del newsletter semanal
# ==========================================================
#
# Objetivo:
# - Leer películas agregadas la semana anterior desde Plex.
# - Buscar poster y descripción pública en TMDB.
# - Usar logo y fallback poster desde GitHub Pages.
# - Enviar correo HTML de prueba a MAIL_TEST_TO.
#
# Importante:
# - Este script NO envía a MAIL_BCC.
# - Solo envía a MAIL_TEST_TO.
# - Es una prueba previa al envío real a usuarios.
# - Incluye Date y Message-ID para mejorar compatibilidad con DKIM.
# ==========================================================

import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from plexapi.server import PlexServer


# ----------------------------------------------------------
# Cargar variables desde .env
# ----------------------------------------------------------
load_dotenv()


# ----------------------------------------------------------
# Leer configuración Plex
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

plex_movie_library = os.getenv("PLEX_MOVIE_LIBRARY", "Movies")


# ----------------------------------------------------------
# Leer configuración de zona horaria
# ----------------------------------------------------------
local_timezone_name = (
    os.getenv("LOCAL_TIMEZONE")
    or os.getenv("TZ")
    or "America/Santiago"
)

local_timezone = ZoneInfo(local_timezone_name)


# ----------------------------------------------------------
# Leer configuración SMTP / correo
# ----------------------------------------------------------
smtp_host = os.getenv("SMTP_HOST")
smtp_port = int(os.getenv("SMTP_PORT", "465"))
smtp_security = os.getenv("SMTP_SECURITY", "ssl").lower()

smtp_user = os.getenv("SMTP_USER")
smtp_password = os.getenv("SMTP_PASSWORD")

mail_from_name = os.getenv("MAIL_FROM_NAME", "TaboPlex")
mail_from = os.getenv("MAIL_FROM")
mail_reply_to = os.getenv("MAIL_REPLY_TO", mail_from)
mail_test_to_raw = os.getenv("MAIL_TEST_TO", "")


# ----------------------------------------------------------
# Leer configuración del newsletter
# ----------------------------------------------------------
newsletter_name = os.getenv("NEWSLETTER_NAME", "La Cartelera de la Semana 🍿")
newsletter_subject_prefix = os.getenv("NEWSLETTER_SUBJECT_PREFIX", "🎬 TaboPlex")

newsletter_logo_url = os.getenv("NEWSLETTER_LOGO_URL")
newsletter_fallback_poster_url = os.getenv("NEWSLETTER_FALLBACK_POSTER_URL")
newsletter_plex_button_url = os.getenv("NEWSLETTER_PLEX_BUTTON_URL", "https://app.plex.tv/")


# ----------------------------------------------------------
# Leer configuración TMDB
# ----------------------------------------------------------
tmdb_token = os.getenv("TMDB_READ_ACCESS_TOKEN")
tmdb_image_size = os.getenv("TMDB_IMAGE_SIZE", "w342")
tmdb_language = os.getenv("TMDB_LANGUAGE", "es-CL")


# ----------------------------------------------------------
# Utilidad: separar destinatarios
# ----------------------------------------------------------
def split_recipients(raw_value: str) -> list[str]:
    """
    Convierte una cadena de correos separados por coma en una lista.

    Aunque recomendamos coma en .env, también soporta punto y coma
    por si algún día se pega una lista copiada desde Outlook.
    """
    if not raw_value:
        return []

    normalized = raw_value.replace(";", ",")

    return [
        item.strip()
        for item in normalized.split(",")
        if item.strip()
    ]


mail_test_recipients = split_recipients(mail_test_to_raw)


# ----------------------------------------------------------
# Validar variables mínimas
# ----------------------------------------------------------
required_vars = {
    "PLEX_BASE_URL / PLEX_URL / PLEX_SERVER_URL": plex_base_url,
    "PLEX_TOKEN / PLEX_AUTH_TOKEN": plex_token,
    "PLEX_MOVIE_LIBRARY": plex_movie_library,
    "SMTP_HOST": smtp_host,
    "SMTP_PORT": smtp_port,
    "SMTP_SECURITY": smtp_security,
    "SMTP_USER": smtp_user,
    "SMTP_PASSWORD": smtp_password,
    "MAIL_FROM": mail_from,
    "MAIL_TEST_TO": mail_test_to_raw,
    "NEWSLETTER_LOGO_URL": newsletter_logo_url,
    "NEWSLETTER_FALLBACK_POSTER_URL": newsletter_fallback_poster_url,
    "TMDB_READ_ACCESS_TOKEN": tmdb_token,
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

if not mail_test_recipients:
    raise RuntimeError("MAIL_TEST_TO no tiene destinatarios válidos.")


# ----------------------------------------------------------
# Calcular semana anterior
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
# Cliente TMDB genérico
# ----------------------------------------------------------
def tmdb_get(path: str, params: dict | None = None) -> dict:
    """
    Ejecuta una llamada GET simple contra TMDB usando Bearer Token.
    """
    url = f"https://api.themoviedb.org/3{path}"

    headers = {
        "Authorization": f"Bearer {tmdb_token}",
        "accept": "application/json",
    }

    request_params = params or {}
    request_params.setdefault("language", tmdb_language)

    response = requests.get(
        url,
        headers=headers,
        params=request_params,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


# ----------------------------------------------------------
# Construir URL pública de poster TMDB
# ----------------------------------------------------------
def build_tmdb_poster_url(poster_path: str | None) -> str:
    """
    Construye la URL pública del poster TMDB.
    Si no hay poster, retorna el fallback de GitHub Pages.
    """
    if not poster_path:
        return newsletter_fallback_poster_url

    return f"https://image.tmdb.org/t/p/{tmdb_image_size}{poster_path}"


# ----------------------------------------------------------
# Extraer IDs externos desde Plex
# ----------------------------------------------------------
def extract_external_ids(movie) -> dict:
    """
    Intenta extraer IDs externos disponibles en Plex.

    Plex puede devolver guids como:
    - imdb://tt1234567
    - tmdb://12345

    Para películas nos interesan principalmente IMDb y TMDB.
    """
    ids = {
        "imdb": None,
        "tmdb": None,
    }

    try:
        movie = movie.reload()
    except Exception:
        # Si no se puede recargar, seguimos con el objeto original.
        pass

    guids = getattr(movie, "guids", []) or []

    for guid_item in guids:
        guid_value = getattr(guid_item, "id", "") or ""

        if guid_value.startswith("imdb://"):
            ids["imdb"] = guid_value.replace("imdb://", "").strip()

        elif guid_value.startswith("tmdb://"):
            ids["tmdb"] = guid_value.replace("tmdb://", "").strip()

    return ids


# ----------------------------------------------------------
# Resolver metadata pública de TMDB
# ----------------------------------------------------------
def resolve_movie_from_tmdb(movie) -> dict:
    """
    Busca una película en TMDB usando este orden:
    1. TMDB ID si Plex lo tiene.
    2. IMDb ID si Plex lo tiene.
    3. Búsqueda por título + año.

    Retorna poster, descripción y URL pública de TMDB.
    """
    title = getattr(movie, "title", "Sin título")
    year = getattr(movie, "year", None)
    plex_summary = getattr(movie, "summary", None)

    external_ids = extract_external_ids(movie)

    tmdb_item = None

    # 1. Buscar directo por TMDB ID.
    if external_ids.get("tmdb"):
        try:
            tmdb_item = tmdb_get(f"/movie/{external_ids['tmdb']}")
        except Exception as exc:
            print(f"TMDB: no se pudo buscar por TMDB ID para {title}: {exc}")

    # 2. Buscar por IMDb ID.
    if tmdb_item is None and external_ids.get("imdb"):
        try:
            find_data = tmdb_get(
                f"/find/{external_ids['imdb']}",
                params={
                    "external_source": "imdb_id",
                    "language": tmdb_language,
                },
            )

            movie_results = find_data.get("movie_results", [])

            if movie_results:
                tmdb_item = movie_results[0]

        except Exception as exc:
            print(f"TMDB: no se pudo buscar por IMDb ID para {title}: {exc}")

    # 3. Buscar por título + año.
    if tmdb_item is None:
        try:
            search_params = {
                "query": title,
                "include_adult": "false",
                "language": tmdb_language,
            }

            if year:
                search_params["year"] = str(year)

            search_data = tmdb_get("/search/movie", params=search_params)
            results = search_data.get("results", [])

            if results:
                tmdb_item = results[0]

        except Exception as exc:
            print(f"TMDB: no se pudo buscar por título para {title}: {exc}")

    # 4. Preparar resultado final si TMDB encontró algo.
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
            "tmdb_title": tmdb_item.get("title") or title,
            "source": "TMDB",
        }

    # 5. Fallback si TMDB no encontró nada.
    return {
        "poster_url": newsletter_fallback_poster_url,
        "overview": plex_summary or "Sin descripción disponible.",
        "tmdb_url": "https://www.themoviedb.org/",
        "tmdb_title": title,
        "source": "Fallback",
    }


# ----------------------------------------------------------
# Leer películas recientes desde Plex
# ----------------------------------------------------------
def get_recent_movies_from_plex() -> tuple[list[dict], datetime, datetime]:
    """
    Conecta a Plex, lee la biblioteca y retorna las películas agregadas
    durante la semana anterior.
    """
    start_date, end_date = get_previous_week_range()

    print("Conectando a Plex...")
    print(f"- Plex URL: {plex_base_url}")
    print(f"- Biblioteca: {plex_movie_library}")

    plex = PlexServer(plex_base_url, plex_token)
    library = plex.library.section(plex_movie_library)

    print("Leyendo películas desde Plex...")
    movies = library.all()

    print(f"- Total películas en biblioteca: {len(movies)}")

    recent_movies = []

    for movie in movies:
        added_at = getattr(movie, "addedAt", None)

        if added_at is None:
            continue

        added_at_local = normalize_plex_datetime(added_at)

        if start_date <= added_at_local <= end_date:
            recent_movies.append(
                {
                    "plex_object": movie,
                    "title": getattr(movie, "title", "Sin título"),
                    "year": getattr(movie, "year", None),
                    "added_at": added_at_local,
                    "rating_key": getattr(movie, "ratingKey", None),
                    "guid": getattr(movie, "guid", None),
                }
            )

    recent_movies.sort(
        key=lambda item: item["added_at"],
        reverse=True,
    )

    return recent_movies, start_date, end_date


# ----------------------------------------------------------
# Formatear rango para correo
# ----------------------------------------------------------
def format_period(start_date: datetime, end_date: datetime) -> str:
    """
    Formatea el rango de fechas en estilo simple para el correo.
    """
    return (
        f"{start_date.strftime('%d-%m-%Y')} "
        f"al {end_date.strftime('%d-%m-%Y')}"
    )


# ----------------------------------------------------------
# Acortar sinopsis para mantener limpio el correo
# ----------------------------------------------------------
def truncate_text(text: str, max_length: int = 650) -> str:
    """
    Acorta textos largos para que el newsletter mantenga buen ritmo visual.
    """
    clean_text = (text or "").strip()

    if len(clean_text) <= max_length:
        return clean_text

    return clean_text[:max_length].rsplit(" ", 1)[0] + "..."


# ----------------------------------------------------------
# Construir tarjetas HTML de películas
# ----------------------------------------------------------
def build_movie_cards_html(newsletter_movies: list[dict]) -> str:
    """
    Construye las tarjetas HTML para cada película.
    """
    if not newsletter_movies:
        return """
        <tr>
          <td style="padding:28px;">
            <p style="margin:0; font-size:16px; line-height:24px;">
              Esta semana no se agregaron películas nuevas a la biblioteca 🎬
            </p>
          </td>
        </tr>
        """

    cards = []

    for movie in newsletter_movies:
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

                <td width="184" valign="top" style="width:184px; padding:18px;">
                  <img
                    src="{poster_url}"
                    alt="{title}"
                    width="148"
                    style="display:block; width:148px; height:auto; border-radius:12px; border:1px solid #d8d3ca;"
                  >
                </td>

                <td valign="top" style="padding:18px 18px 18px 0;">

                  <div style="display:inline-block; padding:4px 10px; background:#101827; color:#ffffff; border-radius:999px; font-size:12px; line-height:16px; margin-bottom:10px;">
                    Película · {source}
                  </div>

                  <h2 style="margin:0 0 6px 0; font-size:23px; line-height:29px; color:#1f1f1f;">
                    {title}
                  </h2>

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


# ----------------------------------------------------------
# Construir HTML completo del correo
# ----------------------------------------------------------
def build_email_html(
    newsletter_movies: list[dict],
    start_date: datetime,
    end_date: datetime,
) -> str:
    """
    Construye el HTML completo del newsletter.
    """
    period_text = escape(format_period(start_date, end_date))
    movie_count = len(newsletter_movies)

    if movie_count == 1:
        movie_count_text = "1 película agregada"
    else:
        movie_count_text = f"{movie_count} películas agregadas"

    movie_cards_html = build_movie_cards_html(newsletter_movies)

    safe_newsletter_name = escape(newsletter_name)
    safe_logo_url = escape(newsletter_logo_url, quote=True)
    safe_plex_url = escape(newsletter_plex_button_url, quote=True)

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
                {movie_count_text} durante la semana anterior 🎬
              </p>
            </td>
          </tr>

          <tr>
            <td style="padding:24px 28px 18px 28px;">
              <p style="margin:0; font-size:16px; line-height:24px;">
                Hola! Estas son las novedades agregadas recientemente a la biblioteca de TaboPlex 🍿
              </p>
            </td>
          </tr>

          {movie_cards_html}

          <tr>
            <td align="center" style="padding:4px 28px 32px 28px;">
              <a href="{safe_plex_url}" style="display:inline-block; background:#101827; color:#ffffff; text-decoration:none; font-weight:bold; padding:13px 22px; border-radius:12px; font-size:15px;">
                Abrir Plex
              </a>
            </td>
          </tr>

          <tr>
            <td style="padding:18px 28px; background:#fbfaf7; border-top:1px solid #e8dfd2;">
              <p style="margin:0; font-size:12px; line-height:18px; color:#7c746c;">
                Movie data and images provided by TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.
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


# ----------------------------------------------------------
# Construir texto plano
# ----------------------------------------------------------
def build_plain_text(
    newsletter_movies: list[dict],
    start_date: datetime,
    end_date: datetime,
) -> str:
    """
    Construye versión texto plano del correo.
    """
    lines = [
        f"{newsletter_name}",
        "",
        f"Periodo: {format_period(start_date, end_date)}",
        "",
    ]

    if not newsletter_movies:
        lines.append("Esta semana no se agregaron películas nuevas.")
    else:
        lines.append("Películas agregadas:")
        lines.append("")

        for index, movie in enumerate(newsletter_movies, start=1):
            year_text = f" ({movie['year']})" if movie["year"] else ""
            added_text = movie["added_at"].strftime("%d-%m-%Y %H:%M")

            lines.append(f"{index}. {movie['title']}{year_text}")
            lines.append(f"   Agregada: {added_text}")
            lines.append(f"   Ficha: {movie['tmdb_url']}")
            lines.append("")

    lines.extend(
        [
            "",
            "Saludos,",
            "TaboPlex",
        ]
    )

    return "\n".join(lines)


# ----------------------------------------------------------
# Enviar correo
# ----------------------------------------------------------
def send_email(subject: str, plain_body: str, html_body: str) -> None:
    """
    Envía el correo usando SMTP.
    Para esta prueba solo utiliza MAIL_TEST_TO.
    """
    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = formataddr((mail_from_name, mail_from))
    message["To"] = ", ".join(mail_test_recipients)
    message["Reply-To"] = mail_reply_to

    # ------------------------------------------------------
    # Headers estándar para mejorar compatibilidad y DKIM
    # ------------------------------------------------------
    # Algunos servidores firman Date y Message-ID.
    # Si el correo sale sin estos headers, otro servidor puede
    # agregarlos después y eso puede romper la validación DKIM.
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="gustavohuerta.com")

    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")

    print("Enviando correo de prueba...")
    print("- Destinatarios:")
    for recipient in mail_test_recipients:
        print(f"  - {recipient}")

    if smtp_security == "ssl":
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(
                message,
                from_addr=mail_from,
                to_addrs=mail_test_recipients,
            )

    elif smtp_security == "starttls":
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(
                message,
                from_addr=mail_from,
                to_addrs=mail_test_recipients,
            )

    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(
                message,
                from_addr=mail_from,
                to_addrs=mail_test_recipients,
            )

    print("Correo de prueba enviado correctamente.")


# ----------------------------------------------------------
# Flujo principal
# ----------------------------------------------------------
def main() -> None:
    """
    Ejecuta la prueba completa:
    Plex → TMDB → HTML → SMTP.
    """
    recent_movies, start_date, end_date = get_recent_movies_from_plex()

    print("")
    print("Rango revisado:")
    print(f"- Desde: {start_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"- Hasta: {end_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"- Películas encontradas: {len(recent_movies)}")
    print("")

    newsletter_movies = []

    for index, movie in enumerate(recent_movies, start=1):
        title = movie["title"]
        year = movie["year"]

        print(f"Resolviendo TMDB {index}/{len(recent_movies)}: {title} ({year})")

        tmdb_data = resolve_movie_from_tmdb(movie["plex_object"])

        newsletter_movies.append(
            {
                "title": title,
                "year": year,
                "added_at": movie["added_at"],
                "poster_url": tmdb_data["poster_url"],
                "overview": tmdb_data["overview"],
                "tmdb_url": tmdb_data["tmdb_url"],
                "source": tmdb_data["source"],
            }
        )

    # ------------------------------------------------------
    # Asunto de prueba.
    # En la versión final quitaremos "Prueba Plex".
    # ------------------------------------------------------
    subject = f"{newsletter_subject_prefix} — {newsletter_name} — Prueba Plex"

    plain_body = build_plain_text(newsletter_movies, start_date, end_date)
    html_body = build_email_html(newsletter_movies, start_date, end_date)

    print("")
    send_email(subject, plain_body, html_body)


if __name__ == "__main__":
    main()