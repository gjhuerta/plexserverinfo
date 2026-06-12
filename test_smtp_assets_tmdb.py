# ==========================================================
# test_smtp_assets_tmdb.py
# Prueba integral TaboPlex:
# - Envía correo SMTP
# - Muestra logo desde GitHub Pages
# - Busca una película o serie al azar en TMDB
# - Muestra poster público desde TMDB
# ==========================================================
#
# Objetivo:
# Validar que el correo pueda cargar imágenes públicas externas
# antes de conectar la lógica real de Plex.
#
# Importante:
# - No escribir claves ni passwords en este archivo.
# - Todo debe venir desde .env.
# ==========================================================

import os
import random
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

import requests
from dotenv import load_dotenv


# ----------------------------------------------------------
# Cargar variables desde .env
# ----------------------------------------------------------
load_dotenv()


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
mail_test_to = os.getenv("MAIL_TEST_TO", mail_from)


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
# Validar variables mínimas requeridas
# ----------------------------------------------------------
required_vars = {
    "SMTP_HOST": smtp_host,
    "SMTP_PORT": smtp_port,
    "SMTP_SECURITY": smtp_security,
    "SMTP_USER": smtp_user,
    "SMTP_PASSWORD": smtp_password,
    "MAIL_FROM": mail_from,
    "MAIL_TEST_TO": mail_test_to,
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


# ----------------------------------------------------------
# Función auxiliar: validar que una imagen pública responda
# ----------------------------------------------------------
def check_public_url(url: str, label: str) -> None:
    """
    Valida de manera simple que una URL pública responda.
    No garantiza que el cliente de correo la muestre, pero ayuda
    a detectar errores de URL, GitHub Pages pendiente, nombres malos, etc.
    """
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        print(f"{label}: OK ({response.status_code}) -> {url}")
    except Exception as exc:
        print(f"{label}: ERROR -> {url}")
        print(f"Detalle: {exc}")


# ----------------------------------------------------------
# Buscar un título aleatorio en TMDB
# ----------------------------------------------------------
def get_random_tmdb_title() -> dict:
    """
    Busca tendencias semanales de películas y series en TMDB,
    selecciona un resultado al azar y devuelve los datos necesarios
    para armar una tarjeta visual en el correo.
    """

    url = "https://api.themoviedb.org/3/trending/all/week"

    headers = {
        "Authorization": f"Bearer {tmdb_token}",
        "accept": "application/json",
    }

    params = {
        "language": tmdb_language,
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()
    results = data.get("results", [])

    # Filtrar solo elementos que tengan poster y sean movie o tv.
    valid_results = [
        item for item in results
        if item.get("media_type") in ("movie", "tv")
    ]

    if not valid_results:
        raise RuntimeError("TMDB no devolvió películas o series válidas.")

    item = random.choice(valid_results)

    media_type = item.get("media_type", "movie")

    if media_type == "movie":
        title = item.get("title") or item.get("original_title") or "Sin título"
        release_date = item.get("release_date", "")
        type_label = "Película"
    else:
        title = item.get("name") or item.get("original_name") or "Sin título"
        release_date = item.get("first_air_date", "")
        type_label = "Serie"

    year = release_date[:4] if release_date else "s/f"

    overview = item.get("overview") or "Sin descripción disponible."

    poster_path = item.get("poster_path")

    if poster_path:
        poster_url = f"https://image.tmdb.org/t/p/{tmdb_image_size}{poster_path}"
    else:
        poster_url = newsletter_fallback_poster_url

    tmdb_id = item.get("id")
    tmdb_url = f"https://www.themoviedb.org/{media_type}/{tmdb_id}" if tmdb_id else "https://www.themoviedb.org/"

    return {
        "title": title,
        "year": year,
        "overview": overview,
        "poster_url": poster_url,
        "type_label": type_label,
        "tmdb_url": tmdb_url,
    }


# ----------------------------------------------------------
# Preparar datos de prueba
# ----------------------------------------------------------
print("Validando URL pública del logo...")
check_public_url(newsletter_logo_url, "Logo GitHub Pages")

print("Validando URL pública del fallback poster...")
check_public_url(newsletter_fallback_poster_url, "Fallback poster GitHub Pages")

print("Buscando título aleatorio en TMDB...")
tmdb_item = get_random_tmdb_title()

print("Título seleccionado:")
print(f"- {tmdb_item['type_label']}: {tmdb_item['title']} ({tmdb_item['year']})")
print(f"- Poster: {tmdb_item['poster_url']}")


# ----------------------------------------------------------
# Armar asunto
# ----------------------------------------------------------
subject = f"{newsletter_subject_prefix} — Prueba visual"


# ----------------------------------------------------------
# Armar versión texto plano
# ----------------------------------------------------------
plain_body = f"""
Hola!

Este es un correo de prueba visual de {newsletter_name}.

Validaciones:
- Logo desde GitHub Pages: {newsletter_logo_url}
- Fallback poster desde GitHub Pages: {newsletter_fallback_poster_url}
- Poster aleatorio desde TMDB: {tmdb_item['poster_url']}

Título de prueba:
{tmdb_item['type_label']}: {tmdb_item['title']} ({tmdb_item['year']})

Descripción:
{tmdb_item['overview']}

Saludos,
TaboPlex
""".strip()


# ----------------------------------------------------------
# Armar versión HTML
# ----------------------------------------------------------
html_body = f"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>{subject}</title>
</head>

<body style="margin:0; padding:0; background:#f4f2ee; font-family:Arial, Helvetica, sans-serif; color:#1f1f1f;">

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f2ee; padding:24px 0;">
    <tr>
      <td align="center">

        <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:640px; max-width:94%; background:#ffffff; border-radius:18px; overflow:hidden; border:1px solid #e5ded3;">

          <!-- Header -->
          <tr>
            <td align="center" style="padding:28px 24px 18px 24px; background:#101827;">
              <img
                src="{newsletter_logo_url}"
                alt="TaboPlex"
                width="180"
                style="display:block; max-width:180px; width:180px; height:auto; margin:0 auto 16px auto;"
              >

              <div style="font-size:14px; line-height:20px; color:#f4d58d; letter-spacing:0.5px;">
                Prueba visual del newsletter
              </div>

              <h1 style="margin:8px 0 0 0; font-size:28px; line-height:34px; color:#ffffff;">
                {newsletter_name}
              </h1>
            </td>
          </tr>

          <!-- Intro -->
          <tr>
            <td style="padding:24px 28px 10px 28px;">
              <p style="margin:0; font-size:16px; line-height:24px;">
                Hola! Este es un correo de prueba para validar que el logo desde GitHub Pages
                y los posters desde TMDB se vean correctamente en el correo 🎬🍿
              </p>
            </td>
          </tr>

          <!-- Card TMDB -->
          <tr>
            <td style="padding:18px 28px 28px 28px;">

              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e8dfd2; border-radius:16px; overflow:hidden; background:#fbfaf7;">
                <tr>

                  <!-- Poster -->
                  <td width="190" valign="top" style="width:190px; padding:18px;">
                    <img
                      src="{tmdb_item['poster_url']}"
                      alt="{tmdb_item['title']}"
                      width="154"
                      style="display:block; width:154px; height:auto; border-radius:12px; border:1px solid #d8d3ca;"
                    >
                  </td>

                  <!-- Content -->
                  <td valign="top" style="padding:18px 18px 18px 0;">
                    <div style="display:inline-block; padding:4px 10px; background:#101827; color:#ffffff; border-radius:999px; font-size:12px; line-height:16px; margin-bottom:10px;">
                      {tmdb_item['type_label']}
                    </div>

                    <h2 style="margin:0 0 6px 0; font-size:24px; line-height:30px; color:#1f1f1f;">
                      {tmdb_item['title']}
                    </h2>

                    <div style="font-size:14px; line-height:20px; color:#6b6258; margin-bottom:12px;">
                      Año: {tmdb_item['year']}
                    </div>

                    <p style="margin:0 0 16px 0; font-size:14px; line-height:22px; color:#3c3a36;">
                      {tmdb_item['overview']}
                    </p>

                    <a href="{tmdb_item['tmdb_url']}" style="display:inline-block; background:#ec8f6c; color:#1f1f1f; text-decoration:none; font-weight:bold; padding:10px 14px; border-radius:10px; font-size:14px;">
                      Ver ficha en TMDB
                    </a>
                  </td>

                </tr>
              </table>

            </td>
          </tr>

          <!-- Fallback poster check -->
          <tr>
            <td style="padding:0 28px 24px 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f2ee; border-radius:14px;">
                <tr>
                  <td style="padding:16px;">
                    <strong>Fallback poster:</strong>
                    <span style="color:#6b6258;">
                      también estamos validando que tu imagen no-poster.png esté publicada correctamente.
                    </span>
                  </td>
                  <td width="74" align="right" style="padding:12px 16px 12px 0;">
                    <img
                      src="{newsletter_fallback_poster_url}"
                      alt="No poster"
                      width="54"
                      style="display:block; width:54px; height:auto; border-radius:6px;"
                    >
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- CTA -->
          <tr>
            <td align="center" style="padding:0 28px 30px 28px;">
              <a href="{newsletter_plex_button_url}" style="display:inline-block; background:#101827; color:#ffffff; text-decoration:none; font-weight:bold; padding:13px 22px; border-radius:12px; font-size:15px;">
                Abrir Plex
              </a>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:18px 28px; background:#fbfaf7; border-top:1px solid #e8dfd2;">
              <p style="margin:0; font-size:12px; line-height:18px; color:#7c746c;">
                Esta es una prueba automática de TaboPlex. Movie/TV data and images provided by TMDB.
                This product uses the TMDB API but is not endorsed or certified by TMDB.
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
# Crear mensaje de correo
# ----------------------------------------------------------
message = EmailMessage()

message["Subject"] = subject
message["From"] = formataddr((mail_from_name, mail_from))
message["To"] = mail_test_to
message["Reply-To"] = mail_reply_to

message.set_content(plain_body)
message.add_alternative(html_body, subtype="html")


# ----------------------------------------------------------
# Enviar correo según seguridad SMTP
# ----------------------------------------------------------
print("Conectando al servidor SMTP...")

if smtp_security == "ssl":
    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(message)

elif smtp_security == "starttls":
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_password)
        server.send_message(message)

else:
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(message)


print("Correo de prueba visual enviado correctamente.")