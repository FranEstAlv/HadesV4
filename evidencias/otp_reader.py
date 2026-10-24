import random
import string
import time
import re
import sys
import os
import threading
import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime, timezone, timedelta

_file_lock = threading.Lock()


def random_string(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def format_proxy(s):
    if not s:
        return None
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    parts = s.split(":")
    if len(parts) == 4:
        if "." in parts[2]:
            user, pwd, host, port = parts
        else:
            host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{s}"
    return None


def cargar_proxies(ruta=None):
    if ruta is None:
        ruta = "proxies.txt"
    if not os.path.exists(ruta):
        return []
    with open(ruta) as f:
        raw = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    proxies = []
    for p in raw:
        url = format_proxy(p)
        if url:
            proxies.append(url)
    return proxies


def extraer_otp(texto):
    patterns = [
        r'(?:c[oó]digo|OTP|verificaci[oó]n|token|clave)\s*[:]?\s*(\d{4,6})',
        r'(\d{4,6})\s*(?:es tu|c[oó]digo|OTP|verificaci[oó]n|token|clave)',
    ]
    for pattern in patterns:
        match = re.search(pattern, texto, re.IGNORECASE)
        if match:
            return match.group(1)
    numbers = re.findall(r'\b(\d{4,6})\b', texto)
    for num in numbers:
        if not (num.startswith("20") or num.startswith("19") or num == "5555" or num == "1234"):
            return num
    return None


def _parsear_fecha(msg) -> datetime:
    """Devuelve la fecha del mensaje como datetime UTC. Mínimo si falla."""
    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _extraer_texto(msg) -> str:
    """Extrae subject + body plaintext de un mensaje."""
    subject_str = ""
    for part, encoding in decode_header(msg.get("Subject", "")):
        if isinstance(part, bytes):
            part = part.decode(encoding or "utf-8", errors="ignore")
        subject_str += part

    body_text = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body_text += payload.decode("utf-8", errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body_text += payload.decode("utf-8", errors="ignore")

    return f"{subject_str} {body_text}"


def _buscar_en_ids(mail_conn, ids, email_esperado, min_time=None) -> str | None:
    """
    Recorre una lista de IDs IMAP, filtra por alias y fecha,
    devuelve el OTP del mensaje más reciente que coincida.

    min_time: datetime UTC — solo considera mensajes posteriores a este instante.
              Si es None, acepta cualquier fecha.
    """
    candidatos = []

    for msg_id in ids:
        try:
            status, msg_data = mail_conn.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue

            msg = email.message_from_bytes(msg_data[0][1])

            # Filtrar por alias exacto en To:
            to_addr = parseaddr(msg.get("To", ""))[1].lower()
            if to_addr != email_esperado.lower():
                continue

            # Filtrar por fecha mínima
            msg_time = _parsear_fecha(msg)
            if min_time and msg_time < min_time:
                continue

            otp = extraer_otp(_extraer_texto(msg))
            if otp:
                candidatos.append((msg_time, otp, msg_id))

        except Exception as e:
            print(f"  [!] Error procesando msg {msg_id}: {e}")
            continue

    if not candidatos:
        return None

    # El más reciente gana — evita leer tokens viejos acumulados en threads
    candidatos.sort(key=lambda x: x[0], reverse=True)
    _, otp, msg_id = candidatos[0]
    mail_conn.store(msg_id, '+FLAGS', '\\Seen')
    return otp


def esperar_otp_gmail(
    email_user: str,
    email_pass: str,
    email_esperado: str,
    timeout: int = 60,
    min_time: datetime | None = None,
) -> str | None:
    """
    Espera y devuelve el OTP para `email_esperado`.

    Parámetros:
      email_user    — cuenta Gmail completa
      email_pass    — contraseña de aplicación
      email_esperado — alias completo, ej: user+alias123@gmail.com
      timeout       — segundos de polling activo (default 60)
      min_time      — datetime UTC de cuando se envió la verificación.
                      Solo se consideran correos posteriores a este instante.
                      Si es None, se usa now() - 2 minutos como margen.

    Flujo:
      1. Espera 15 segundos (el correo de verificación viaja)
      2. Polling cada 2s buscando en mensajes DESDE min_time
      3. Si agota timeout → búsqueda exhaustiva en TODOS los mensajes del alias
         ordenados por fecha, tomando siempre el más reciente
    """
    if min_time is None:
        min_time = datetime.now(timezone.utc) - timedelta(minutes=2)

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_user, email_pass)
        mail.select("inbox")
    except Exception as e:
        print(f"[ERROR] Conexión Gmail: {e}")
        return None

    print(f"[·] Esperando 15s para que el correo viaje...")
    time.sleep(15)

    # IMAP SINCE solo acepta fecha (no hora), usamos la fecha de min_time
    since_str = min_time.strftime("%d-%b-%Y")

    # ── Fase 1: polling activo ──────────────────────────────────
    print(f"[·] Iniciando búsqueda activa (alias: {email_esperado})")
    start = time.time()

    while time.time() - start < timeout:
        try:
            mail.check()
            status, messages = mail.search(None, f'SINCE "{since_str}"')
            if status == "OK" and messages[0]:
                ids = messages[0].split()
                otp = _buscar_en_ids(mail, ids, email_esperado, min_time)
                if otp:
                    mail.close()
                    mail.logout()
                    return otp
        except Exception as e:
            print(f"  [!] Error en búsqueda: {e}")

        elapsed = int(time.time() - start)
        print(f"  Esperando OTP... {elapsed}s / {timeout}s")
        time.sleep(2)

    # ── Fase 2: búsqueda exhaustiva ────────────────────────────
    # Timeout alcanzado. Busca en TODOS los mensajes del inbox sin filtro de
    # fecha, ordenando por fecha descendente y tomando el más reciente del alias.
    print("[·] Timeout. Búsqueda exhaustiva en todos los mensajes acumulados...")
    try:
        mail.check()
        status, messages = mail.search(None, 'ALL')
        if status == "OK" and messages[0]:
            ids = messages[0].split()
            # Sin min_time para atrapar el correo aunque haya llegado tarde
            otp = _buscar_en_ids(mail, ids, email_esperado, min_time=None)
            if otp:
                mail.close()
                mail.logout()
                return otp
    except Exception as e:
        print(f"[ERROR] Búsqueda exhaustiva: {e}")

    mail.close()
    mail.logout()
    return None


def guardar_resultado(datos):
    with _file_lock:
        with open("resultados.txt", "a", encoding="utf-8") as f:
            f.write(f"{datos}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--gmail",      type=str, required=True, help="Correo Gmail")
    parser.add_argument("--gmail-pass", type=str, required=True, help="Contraseña de aplicación")
    parser.add_argument("--alias",      type=str, required=True, help="Alias a usar")
    args = parser.parse_args()

    local, domain = args.gmail.split("@")
    email_esperado = f"{local}+{args.alias}@{domain}"

    print(f"\n{'='*50}")
    print(f"  ESPERANDO OTP")
    print(f"  Correo:  {args.gmail}")
    print(f"  Alias:   {args.alias}")
    print(f"  Destino: {email_esperado}")
    print(f"{'='*50}\n")

    # Registrar el instante ANTES del delay para anclar la búsqueda
    min_time = datetime.now(timezone.utc)

    # ── Aquí iría el trigger de la verificación (registro de cuenta) ──

    otp = esperar_otp_gmail(
        args.gmail,
        args.gmail_pass,
        email_esperado,
        timeout=60,
        min_time=min_time,
    )

    if otp:
        print(f"\n✅ OTP: {otp}")
        guardar_resultado({"email": email_esperado, "otp": otp, "timestamp": min_time.isoformat()})
    else:
        print("\n❌ No se recibió OTP en el tiempo esperado")


if __name__ == "__main__":
    main()
