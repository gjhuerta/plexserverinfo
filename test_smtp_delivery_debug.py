# ==========================================================
# test_smtp_delivery_debug.py
# Prueba de entrega SMTP con trazas básicas
# ==========================================================
#
# Objetivo:
# - Validar que el SMTP acepta el correo.
# - Mostrar exactamente a qué destinatarios se está enviando.
# - Forzar el envío usando una lista explícita de destinatarios.
#
# Este test no usa Plex ni TMDB.
# ==========================================================

import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from dotenv import load_dotenv


# ----------------------------------------------------------
# Cargar variables desde .env
# ----------------------------------------------------------
load_dotenv()


# ----------------------------------------------------------
# Leer variables SMTP
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
# Convertir MAIL_TEST_TO en lista real de destinatarios
# ----------------------------------------------------------
mail_test_recipients = [
    item.strip()
    for item in mail_test_to_raw.split(",")
    if item.strip()
]


# ----------------------------------------------------------
# Validar variables mínimas
# ----------------------------------------------------------
required_vars = {
    "SMTP_HOST": smtp_host,
    "SMTP_PORT": smtp_port,
    "SMTP_SECURITY": smtp_security,
    "SMTP_USER": smtp_user,
    "SMTP_PASSWORD": smtp_password,
    "MAIL_FROM": mail_from,
    "MAIL_TEST_TO": mail_test_to_raw,
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
# Mostrar configuración segura en consola
# ----------------------------------------------------------
print("Configuración de envío:")
print(f"- SMTP_HOST: {smtp_host}")
print(f"- SMTP_PORT: {smtp_port}")
print(f"- SMTP_SECURITY: {smtp_security}")
print(f"- SMTP_USER: {smtp_user}")
print(f"- MAIL_FROM: {mail_from}")
print(f"- MAIL_FROM_NAME: {mail_from_name}")
print("- MAIL_TEST_TO:")
for recipient in mail_test_recipients:
    print(f"  - {recipient}")
print("")


# ----------------------------------------------------------
# Crear correo simple
# ----------------------------------------------------------
message = EmailMessage()

message["Subject"] = "TaboPlex - prueba de entrega SMTP"
message["From"] = formataddr((mail_from_name, mail_from))
message["To"] = ", ".join(mail_test_recipients)
message["Reply-To"] = mail_reply_to

message.set_content(
    f"""
Hola!

Este es un correo de prueba simple para validar entrega SMTP de TaboPlex.

Si llegó este correo, el envío está funcionando correctamente.

Remitente:
{mail_from_name} <{mail_from}>

Destinatarios:
{", ".join(mail_test_recipients)}

Saludos,
TaboPlex
""".strip()
)


# ----------------------------------------------------------
# Enviar correo
# ----------------------------------------------------------
print("Conectando al servidor SMTP...")

if smtp_security == "ssl":
    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        # Activa salida técnica SMTP en consola.
        server.set_debuglevel(1)

        server.login(smtp_user, smtp_password)

        # Envío explícito: from_addr y to_addrs.
        refused = server.send_message(
            message,
            from_addr=mail_from,
            to_addrs=mail_test_recipients,
        )

elif smtp_security == "starttls":
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.set_debuglevel(1)

        server.ehlo()
        server.starttls()
        server.ehlo()

        server.login(smtp_user, smtp_password)

        refused = server.send_message(
            message,
            from_addr=mail_from,
            to_addrs=mail_test_recipients,
        )

else:
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.set_debuglevel(1)

        server.login(smtp_user, smtp_password)

        refused = server.send_message(
            message,
            from_addr=mail_from,
            to_addrs=mail_test_recipients,
        )


# ----------------------------------------------------------
# Resultado
# ----------------------------------------------------------
if refused:
    print("")
    print("El servidor rechazó algunos destinatarios:")
    print(refused)
else:
    print("")
    print("El servidor SMTP aceptó todos los destinatarios.")
    print("Correo de prueba enviado correctamente.")