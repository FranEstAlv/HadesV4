"""Servicio TempMail API.

Expone:
  - Servidor SMTP (por defecto 0.0.0.0:1025) para recibir correos de dominios
    propios configurados en EMAILAPI_DOMAINS.
  - API HTTP (por defecto 127.0.0.1:3000) para generar buzones temporales,
    registrar buzones externos por IMAP y leer sus mensajes/OTP.

Buzones soportados:
  1. Locales: se genera una dirección aleatoria en los dominios configurados
     y los correos se reciben por SMTP.
  2. IMAP: cualquier correo existente (Gmail con contraseña de aplicación,
     dominio privado, Zoho, Outlook, etc.). La API se conecta por IMAP y lee
     los mensajes directamente del buzón.

Para recibir correos desde Internet en los dominios locales se requiere:
  - Un registro MX apuntando al servidor.
  - Redirección del puerto 25 al puerto SMTP configurado (ej. iptables).
"""
from __future__ import annotations

import base64
import html
import imaplib
import os
import random
import re
import socket
import sqlite3
import string
import threading
import time
import uuid
from contextlib import asynccontextmanager
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.policy import default as email_policy
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import uvicorn
from aiosmtpd.controller import Controller
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent

# Carga de .env: primero el de EmailApi, luego el de la raiz del proyecto.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    load_dotenv(BASE_DIR.parent / ".env")
except Exception:
    pass

DB_PATH = Path(os.getenv("EMAILAPI_DB_PATH", BASE_DIR / "emails.db"))
SMTP_HOST = os.getenv("EMAILAPI_SMTP_HOST", "0.0.0.0")
SMTP_PORT = int(os.getenv("EMAILAPI_SMTP_PORT", "1025"))
API_HOST = os.getenv("EMAILAPI_API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("EMAILAPI_API_PORT", "3000"))
EXPIRY_MINUTES = int(os.getenv("EMAILAPI_EXPIRY_MINUTES", "30"))
IMAP_TOKEN_MINUTES = 120
IMAP_TIMEOUT_S = 15
IMAP_CACHE_S = 2.0
IMAP_MAX_FETCH = 80
CLEANUP_INTERVAL_S = int(os.getenv("EMAILAPI_CLEANUP_INTERVAL_S", "300"))
IMAP_IDLE_CLOSE_S = 600
RATE_LIMIT_REQUESTS = int(os.getenv("EMAILAPI_RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW_S = int(os.getenv("EMAILAPI_RATE_LIMIT_WINDOW_S", "60"))

# Rate limiting por IP (API). Solo aplica a IPs externas: el bot consulta
# desde localhost y limitarlo provocaria 429 durante el polling.
_rate_limit_tracker: Dict[str, List[float]] = defaultdict(list)
_rate_limit_tracker_lock = threading.Lock()

# Dominios para correos temporales locales. Se pueden pasar por variable de
# entorno separados por comas, ej: EMAILAPI_DOMAINS=dom1.cyou,dom2.cyou
_DEFAULT_DOMAINS = os.getenv("EMAILAPI_DOMAINS", "olimpo.cyou")
NAMESPACES: Dict[str, List[str]] = {
    "odisea": [d.strip() for d in _DEFAULT_DOMAINS.split(",") if d.strip()],
}

# Servidores IMAP conocidos: dominio -> (host, puerto, ssl)
_IMAP_PRESETS: Dict[str, Tuple[str, int, bool]] = {
    "gmail.com": ("imap.gmail.com", 993, True),
    "googlemail.com": ("imap.gmail.com", 993, True),
    "outlook.com": ("outlook.office365.com", 993, True),
    "hotmail.com": ("outlook.office365.com", 993, True),
    "live.com": ("outlook.office365.com", 993, True),
    "yahoo.com": ("imap.mail.yahoo.com", 993, True),
    "icloud.com": ("imap.mail.me.com", 993, True),
    "zoho.com": ("imap.zoho.com", 993, True),
    "gmx.com": ("imap.gmx.com", 993, True),
}

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_db_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Cifrado en reposo para las contraseñas IMAP
# ---------------------------------------------------------------------------
_KEY_PATH = DB_PATH.parent / ".emailapi.key"


def _load_key() -> bytes:
    try:
        raw = _KEY_PATH.read_bytes()
        if len(raw) == 32:
            return raw
    except FileNotFoundError:
        pass
    raw = os.urandom(32)
    _KEY_PATH.write_bytes(raw)
    try:
        _KEY_PATH.chmod(0o600)
    except OSError:
        pass
    return raw


_AES = AESGCM(_load_key())


def _encrypt(secret: str) -> str:
    nonce = os.urandom(12)
    ct = _AES.encrypt(nonce, secret.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def _decrypt(token: str) -> str:
    raw = base64.b64decode(token.encode("ascii"))
    return _AES.decrypt(raw[:12], raw[12:], None).decode("utf-8")


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db() -> None:
    """Crea las tablas de mensajes y de cuentas si no existen."""
    with _db_lock, _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient   TEXT NOT NULL,
                sender      TEXT NOT NULL,
                subject     TEXT NOT NULL DEFAULT '',
                body        TEXT NOT NULL DEFAULT '',
                received_at TEXT NOT NULL
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_rcpt ON messages(recipient)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_date ON messages(received_at)")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                token       TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                kind        TEXT NOT NULL DEFAULT 'local',
                imap_host   TEXT,
                imap_port   INTEGER,
                imap_ssl    INTEGER NOT NULL DEFAULT 1,
                imap_user   TEXT,
                imap_secret TEXT,
                alias       TEXT,
                mark_seen   INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT
            )
            """
        )
        actuales = {row[1] for row in c.execute("PRAGMA table_info(accounts)")}
        columnas = {
            "kind": "TEXT NOT NULL DEFAULT 'local'",
            "imap_host": "TEXT",
            "imap_port": "INTEGER",
            "imap_ssl": "INTEGER NOT NULL DEFAULT 1",
            "imap_user": "TEXT",
            "imap_secret": "TEXT",
            "alias": "TEXT",
            "mark_seen": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "TEXT",
        }
        for nombre, ddl in columnas.items():
            if nombre not in actuales:
                c.execute(f"ALTER TABLE accounts ADD COLUMN {nombre} {ddl}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_acc_expires ON accounts(expires_at)")
        c.commit()


# Pesos opcionales por dominio para reducir la frecuencia de dominios
# descansando o en prueba. Formato: dom1:peso,dom2:peso
_DOMAIN_WEIGHTS: Dict[str, int] = {}
for part in os.getenv("EMAILAPI_DOMAIN_WEIGHTS", "").split(","):
    if ":" in part:
        d, w = part.split(":", 1)
        try:
            _DOMAIN_WEIGHTS[d.strip()] = int(w.strip())
        except ValueError:
            pass


def _next_domain(domains: List[str]) -> str:
    """Elige dominio al azar, pesando segun EMAILAPI_DOMAIN_WEIGHTS."""
    weights = [_DOMAIN_WEIGHTS.get(d, 1) for d in domains]
    return random.choices(domains, weights=weights, k=1)[0]


def db_insert(recipient: str, sender: str, subject: str, body: str) -> None:
    """Guarda un mensaje recibido."""
    with _db_lock, _conn() as c:
        c.execute(
            "INSERT INTO messages (recipient, sender, subject, body, received_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (recipient.lower(), sender, subject, body, datetime.now().isoformat()),
        )
        c.commit()


def db_messages(email: str) -> list:
    """Mensajes del buzon que siguen vigentes."""
    cutoff = (datetime.now() - timedelta(minutes=EXPIRY_MINUTES)).isoformat()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, sender, subject, body, received_at "
            "FROM messages WHERE recipient = ? AND received_at > ? "
            "ORDER BY received_at DESC",
            (email.lower(), cutoff),
        ).fetchall()
    return [
        {
            "id": f"local:{r['id']}",
            "sender": r["sender"],
            "from": r["sender"],
            "subject": r["subject"],
            "body": r["body"],
            "received_at": r["received_at"],
        }
        for r in rows
    ]


def db_cleanup() -> None:
    """Borra los mensajes que ya vencieron."""
    cutoff = (datetime.now() - timedelta(minutes=EXPIRY_MINUTES)).isoformat()
    with _db_lock, _conn() as c:
        n = c.execute("DELETE FROM messages WHERE received_at <= ?", (cutoff,)).rowcount
        c.commit()
    if n:
        print(f"[Cleanup] {n} mensajes expirados eliminados.")


def db_insert_account(acc: dict) -> None:
    """Guarda una cuenta (temporal o IMAP) con su token."""
    with _db_lock, _conn() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO accounts
                (token, email, expires_at, kind, imap_host, imap_port, imap_ssl,
                 imap_user, imap_secret, alias, mark_seen, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                acc["token"],
                acc["email"].lower(),
                acc["expires_at"],
                acc.get("kind", "local"),
                acc.get("imap_host"),
                acc.get("imap_port"),
                1 if acc.get("imap_ssl", True) else 0,
                acc.get("imap_user"),
                acc.get("imap_secret"),
                acc.get("alias"),
                1 if acc.get("mark_seen") else 0,
                acc.get("created_at", datetime.now().isoformat()),
            ),
        )
        c.commit()


def db_get_account(token: str) -> Optional[dict]:
    """Devuelve la cuenta de un token, o None si no existe."""
    with _db_lock, _conn() as c:
        row = c.execute("SELECT * FROM accounts WHERE token = ?", (token,)).fetchone()
    if not row:
        return None
    acc = dict(row)
    acc["imap_ssl"] = bool(acc["imap_ssl"])
    acc["mark_seen"] = bool(acc["mark_seen"])
    return acc


def db_delete_account(token: str) -> None:
    """Borra la cuenta de un token."""
    with _db_lock, _conn() as c:
        c.execute("DELETE FROM accounts WHERE token = ?", (token,))
        c.commit()


def db_cleanup_accounts() -> List[str]:
    """Borra las cuentas vencidas y devuelve sus tokens."""
    cutoff = datetime.now().isoformat()
    with _db_lock, _conn() as c:
        rows = c.execute(
            "SELECT token FROM accounts WHERE expires_at <= ?", (cutoff,)
        ).fetchall()
        tokens = [r["token"] for r in rows]
        n = c.execute("DELETE FROM accounts WHERE expires_at <= ?", (cutoff,)).rowcount
        c.commit()
    if n:
        print(f"[Cleanup] {n} cuentas expiradas eliminadas.")
    return tokens


# ---------------------------------------------------------------------------
# Lectura de mensajes
# ---------------------------------------------------------------------------
_HTML_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_HTML_TAG = re.compile(r"<[^>]+>")

_OTP_PATTERNS = [
    r"(?:password\s+monouso|one[-\s]?time\s+password|OTP\)?)[^\d]{0,80}(\d{6})",
    r"(?:codice|code|c[oó]digo|verification\s+code)[^\d]{0,40}(\d{6})",
    r"[：:]\s*(\d{6})\b",
    r"(?:確認コード|認証コード|ワンタイムパスワード|コード)[^\d]{0,20}(\d{6})",
    r"(\d{6})[^\d]{0,40}(?:codice|code|c[oó]digo|verifica)",
    r"(?:c[oó]digo|OTP|verificaci[oó]n|token|clave)\s*[:]?\s*(\d{4,6})",
    r"(\d{4,6})\s*(?:es\s+tu|c[oó]digo|OTP|verificaci[oó]n|token|clave)",
]


def extraer_otp(texto: str) -> Optional[str]:
    """Extrae un OTP del asunto o cuerpo. Prioriza códigos de 6 dígitos."""
    if not texto:
        return None
    for pattern in _OTP_PATTERNS:
        match = re.search(pattern, texto, re.IGNORECASE)
        if match:
            return match.group(1)
    numbers = re.findall(r"\b(\d{4,6})\b", texto)
    for num in numbers:
        if num.startswith("20") or num.startswith("19") or num in ("5555", "1234"):
            continue
        return num
    return None


def _decode_header(value: str) -> str:
    """Decodifica los encabezados con acentos."""
    if not value:
        return ""
    parts = []
    try:
        for part, encoding in decode_header(value):
            if isinstance(part, bytes):
                part = part.decode(encoding or "utf-8", errors="ignore")
            parts.append(part)
    except Exception:
        return str(value)
    return " ".join(parts)


def _plain_body(msg) -> str:
    """Texto plano del mensaje (descarta el HTML)."""
    chunks: List[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                text = payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
            except Exception:
                continue
            if ctype == "text/html":
                text = _HTML_SCRIPT.sub(" ", text)
                text = _HTML_TAG.sub(" ", text)
                text = html.unescape(text)
            chunks.append(text)
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                text = payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")
                if msg.get_content_type() == "text/html":
                    text = _HTML_SCRIPT.sub(" ", text)
                    text = _HTML_TAG.sub(" ", text)
                    text = html.unescape(text)
                chunks.append(text)
        except Exception:
            pass
    return "\n".join(chunks)[:20000]


def _message_date(msg) -> datetime:
    """Fecha del mensaje (o la minima si no la trae)."""
    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _recipients(msg) -> List[str]:
    """Destinatarios del mensaje."""
    candidatos: List[str] = []
    for header in ("To", "Cc", "Delivered-To", "X-Original-To", "X-Forwarded-To"):
        for _, addr in getaddresses(msg.get_all(header, [])):
            if addr:
                candidatos.append(addr.lower())
    return candidatos


_GMAIL_DOMAINS = ("gmail.com", "googlemail.com")


def _sin_puntos(addr: str) -> str:
    """Gmail ignora los puntos en la parte local: se normalizan para comparar."""
    local, _, dominio = addr.lower().strip().partition("@")
    if dominio in _GMAIL_DOMAINS:
        local = local.replace(".", "")
    return f"{local}@{dominio}"


def _coincide_destinatario(msg, esperado: str) -> bool:
    """True si el mensaje va a la direccion del buzon."""
    esperado = esperado.lower().strip()
    if not esperado:
        return True
    destinatarios = _recipients(msg)
    if esperado in destinatarios:
        return True
    normalizado = _sin_puntos(esperado)
    return any(_sin_puntos(d) == normalizado for d in destinatarios)


def _resolver_imap(
    email: str,
    host: Optional[str],
    port: Optional[int],
    use_ssl: Optional[bool],
) -> Tuple[str, int, bool]:
    """Resuelve host, puerto y TLS del servidor IMAP para un correo."""
    """Resuelve host, puerto y TLS del servidor IMAP para un correo."""
    dominio = email.split("@")[-1].lower()

    if host:
        ssl = True if use_ssl is None else use_ssl
        return host.strip(), int(port or (993 if ssl else 143)), ssl

    if dominio in _IMAP_PRESETS:
        preset_host, preset_port, preset_ssl = _IMAP_PRESETS[dominio]
        ssl = preset_ssl if use_ssl is None else use_ssl
        return preset_host, int(port or preset_port), ssl

    ssl = True if use_ssl is None else use_ssl
    return f"imap.{dominio}", int(port or (993 if ssl else 143)), ssl


def _normalizar_password(provider_domain: str, password: str) -> str:
    """Quita los espacios que Gmail pone en las contrasenas de aplicacion."""
    # Las contraseñas de aplicación de Gmail se muestran con espacios.
    if provider_domain.lower() in ("gmail.com", "googlemail.com"):
        return password.replace(" ", "")
    return password


class ImapBox:
    """Buzón IMAP con conexión cacheada y un candado por token."""

    def __init__(self, acc: dict):
        self.acc = acc
        self.lock = threading.RLock()
        self.conn: Optional[imaplib.IMAP4] = None
        self.last_used = time.time()
        self.cache: List[dict] = []
        self.cache_at = 0.0

    def _connect(self) -> imaplib.IMAP4:
        host = self.acc["imap_host"]
        port = int(self.acc["imap_port"])
        ssl = bool(self.acc["imap_ssl"])
        user = self.acc["imap_user"]
        secret = _decrypt(self.acc["imap_secret"])
        secret = _normalizar_password(user.split("@")[-1], secret)

        if ssl:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT_S)
        else:
            conn = imaplib.IMAP4(host, port, timeout=IMAP_TIMEOUT_S)
            conn.starttls()
        conn.login(user, secret)
        conn.select("INBOX", readonly=not bool(self.acc["mark_seen"]))
        return conn

    def _close(self) -> None:
        if not self.conn:
            return
        try:
            self.conn.logout()
        except Exception:
            pass
        self.conn = None

    def close(self) -> None:
        with self.lock:
            self._close()

    def _buscar_uids(self, since: str, esperado: str) -> List[bytes]:
        """Busca los UID candidatos en el servidor (rapido) y cae a SINCE."""
        candidatos = {esperado.lower()}
        local, _, dominio = esperado.lower().partition("@")
        if dominio in _GMAIL_DOMAINS and "." in local:
            # Gmail tambien recibe en la variante sin puntos.
            candidatos.add(f"{local.replace('.', '')}@{dominio}")

        uids: List[bytes] = []
        for addr in candidatos:
            try:
                status, data = self.conn.uid("search", None, f'(SINCE {since} TO "{addr}")')
            except Exception:
                continue
            if status == "OK" and data and data[0]:
                uids += data[0].split()

        if not uids:
            # Respaldo: si el servidor no soporta TO, revisa los ultimos correos.
            status, data = self.conn.uid("search", None, f"SINCE {since}")
            if status != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[-IMAP_MAX_FETCH:]

        vistos: List[bytes] = []
        for uid in uids:
            if uid not in vistos:
                vistos.append(uid)
        return vistos[-IMAP_MAX_FETCH:]

    def _fetch(self) -> List[dict]:
        """Trae los mensajes del buzon IMAP."""
        created = datetime.fromisoformat(
            self.acc.get("created_at") or (datetime.now() - timedelta(minutes=5)).isoformat()
        )
        if created.tzinfo is None:
            created = created.astimezone()
        min_time = created - timedelta(seconds=120)
        since = (min_time - timedelta(days=1)).strftime("%d-%b-%Y")
        esperado = self.acc["email"]

        uids = self._buscar_uids(since, esperado)
        if not uids:
            return []

        encontrados: List[dict] = []
        for uid in reversed(uids):
            try:
                status, msg_data = self.conn.uid("fetch", uid, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue
                raw = None
                for item in msg_data:
                    if isinstance(item, tuple) and len(item) > 1:
                        raw = item[1]
                        break
                if not raw:
                    continue
                msg = message_from_bytes(raw, policy=email_policy)
                if not _coincide_destinatario(msg, esperado):
                    continue
                fecha = _message_date(msg)
                if fecha != datetime.min.replace(tzinfo=timezone.utc) and fecha < min_time:
                    continue
                asunto = _decode_header(msg.get("Subject", ""))
                cuerpo = _plain_body(msg)
                remitente = parseaddr(msg.get("From", ""))[1] or str(msg.get("From", ""))
                mid = str(msg.get("Message-ID") or f"uid:{uid.decode() if isinstance(uid, bytes) else uid}")
                encontrados.append(
                    {
                        "id": mid,
                        "sender": remitente,
                        "from": remitente,
                        "subject": asunto,
                        "body": cuerpo,
                        "received_at": fecha.isoformat()
                        if fecha != datetime.min.replace(tzinfo=timezone.utc)
                        else datetime.now().isoformat(),
                        "otp": extraer_otp(f"{asunto}\n{cuerpo}"),
                    }
                )
                if self.acc["mark_seen"]:
                    try:
                        self.conn.uid("store", uid, "+FLAGS", r"(\Seen)")
                    except Exception:
                        pass
            except Exception as exc:
                print(f"[IMAP] Error leyendo uid {uid!r}: {type(exc).__name__}: {exc}")
                if os.getenv("EMAILAPI_DEBUG"):
                    import traceback
                    traceback.print_exc()
                continue

        encontrados.sort(key=lambda m: m["received_at"], reverse=True)
        return encontrados

    def messages(self) -> List[dict]:
        """Lista los mensajes del buzon con su codigo."""
        with self.lock:
            self.last_used = time.time()
            if self.cache and (time.time() - self.cache_at) < IMAP_CACHE_S:
                return self.cache
            for intento in (1, 2):
                try:
                    if self.conn is None:
                        self.conn = self._connect()
                    self.cache = self._fetch()
                    self.cache_at = time.time()
                    return self.cache
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, socket.error) as exc:
                    self._close()
                    if intento == 2:
                        print(f"[IMAP] Falla al leer {self.acc['email']}: {type(exc).__name__}")
                        return self.cache or []
                    time.sleep(0.4)
            return self.cache or []


_boxes: Dict[str, ImapBox] = {}
_boxes_lock = threading.Lock()


def _get_box(acc: dict) -> ImapBox:
    with _boxes_lock:
        box = _boxes.get(acc["token"])
        if box is None:
            box = ImapBox(acc)
            _boxes[acc["token"]] = box
        else:
            box.acc = acc
        return box


def _drop_box(token: str) -> None:
    with _boxes_lock:
        box = _boxes.pop(token, None)
    if box:
        box.close()


# ---------------------------------------------------------------------------
# Limpieza periódica
# ---------------------------------------------------------------------------
def _cleanup_loop() -> None:
    """Limpieza periodica de mensajes y cuentas vencidas."""
    try:
        db_cleanup()
        for token in db_cleanup_accounts():
            _drop_box(token)
        ahora = time.time()
        with _boxes_lock:
            idle = [t for t, b in _boxes.items() if ahora - b.last_used > IMAP_IDLE_CLOSE_S]
        for token in idle:
            _drop_box(token)
    except Exception as exc:
        print(f"[Cleanup] error: {type(exc).__name__}: {exc}")
    finally:
        timer = threading.Timer(CLEANUP_INTERVAL_S, _cleanup_loop)
        timer.daemon = True
        timer.start()


# ---------------------------------------------------------------------------
# SMTP (buzones locales)
# ---------------------------------------------------------------------------
class SMTPHandler:
    async def handle_DATA(self, server, session, envelope):
        msg = message_from_bytes(envelope.content, policy=email_policy)
        sender = envelope.mail_from
        subject = _decode_header(msg.get("Subject", ""))
        body = _plain_body(msg)

        # Solo se aceptan buzones de los dominios configurados.
        permitidos = [d.lower() for d in NAMESPACES.get("odisea", [])]
        for recipient in envelope.rcpt_tos:
            dominio = recipient.split("@")[-1].lower() if "@" in recipient else ""
            if dominio not in permitidos:
                print(f"[SMTP] RECHAZADO: dominio no permitido {recipient}")
                return "554 5.7.1 Recipient address rejected: invalid domain"

        for recipient in envelope.rcpt_tos:
            db_insert(recipient, sender, subject, body)
            print(f"[SMTP] {datetime.now():%H:%M:%S} {sender} -> {recipient} | {subject!r}")

        return "250 Message accepted for delivery"


# ---------------------------------------------------------------------------
# API HTTP
# ---------------------------------------------------------------------------
class MailboxIn(BaseModel):
    email: str = Field(..., min_length=5, max_length=320)
    password: str = Field(..., min_length=1, max_length=512)
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    use_ssl: Optional[bool] = None
    alias: Optional[str] = None
    mark_seen: bool = False
    expires_minutes: Optional[int] = None


def _validar_correo(email: str) -> bool:
    """True si la direccion de correo tiene forma valida."""
    return bool(re.fullmatch(r"[\w.+-]+@[\w.-]+\.\w+", email))


def _crear_cuenta_imap(body: MailboxIn) -> dict:
    """Valida el buzon IMAP y da de alta la cuenta."""
    email = body.email.strip().lower()
    if not _validar_correo(email):
        raise HTTPException(400, "La direccion de correo no es valida.")

    local, _, dominio = email.partition("@")
    base_local = local.split("+", 1)[0]
    alias = (body.alias or "").strip() or None
    if "+" in local and not alias:
        alias = local.split("+", 1)[1]

    if dominio in _GMAIL_DOMAINS:
        # Para iniciar sesion, Gmail ignora puntos y etiquetas +alias.
        imap_user = f"{base_local.replace('.', '')}@{dominio}"
        direccion = f"{base_local}+{alias}@{dominio}" if alias else email
    else:
        imap_user = f"{base_local}@{dominio}" if alias else email
        direccion = f"{base_local}+{alias}@{dominio}" if alias else email

    host, port, ssl = _resolver_imap(imap_user, body.imap_host, body.imap_port, body.use_ssl)

    password = _normalizar_password(dominio, body.password)
    try:
        if ssl:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT_S)
        else:
            conn = imaplib.IMAP4(host, port, timeout=IMAP_TIMEOUT_S)
            conn.starttls()
        try:
            conn.login(imap_user, password)
            conn.select("INBOX", readonly=True)
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as exc:
        print(f"[IMAP] Fallo de validacion para {direccion}: {type(exc).__name__}")
        raise HTTPException(
            400,
            "No se pudo conectar al correo. Revisa la direccion, el servidor IMAP "
            "y la contrasena de aplicacion.",
        )

    minutos = body.expires_minutes or IMAP_TOKEN_MINUTES
    token = str(uuid.uuid4())
    expires = datetime.now() + timedelta(minutes=minutos)
    acc = {
        "token": token,
        "email": direccion,
        "expires_at": expires.isoformat(),
        "kind": "imap",
        "imap_host": host,
        "imap_port": port,
        "imap_ssl": ssl,
        "imap_user": imap_user,
        "imap_secret": _encrypt(password),
        "alias": alias,
        "mark_seen": bool(body.mark_seen),
        "created_at": datetime.now().isoformat(),
    }
    db_insert_account(acc)
    return {
        "email": direccion,
        "token": token,
        "kind": "imap",
        "provider": "gmail" if dominio in ("gmail.com", "googlemail.com") else dominio,
        "imap_host": host,
        "imap_port": port,
        "use_ssl": ssl,
        "expires_in": f"{minutos} minutos",
        "expires_at": expires.isoformat(),
    }


def make_router(prefix: str) -> APIRouter:
    """Arma las rutas del namespace (generate, mailbox, messages, otp)."""
    domains = NAMESPACES[prefix]
    router = APIRouter(prefix=f"/{prefix}")

    @router.get("/generate")
    def generate(domain: Optional[str] = None):
        """Crea un buzon temporal nuevo."""
        elegido = domain if domain in domains else _next_domain(domains)
        email = (
            f"{''.join(random.choices(string.ascii_lowercase + string.digits, k=10))}"
            f"@{elegido}"
        )
        token = str(uuid.uuid4())
        expires = datetime.now() + timedelta(minutes=EXPIRY_MINUTES)
        db_insert_account(
            {
                "token": token,
                "email": email,
                "expires_at": expires.isoformat(),
                "kind": "local",
                "created_at": datetime.now().isoformat(),
            }
        )
        return {
            "email": email,
            "token": token,
            "kind": "local",
            "expires_in": f"{EXPIRY_MINUTES} minutos",
            "expires_at": expires.isoformat(),
        }

    @router.post("/mailbox")
    def mailbox(body: MailboxIn):
        """Da de alta un buzon IMAP con su token."""
        return _crear_cuenta_imap(body)

    @router.delete("/mailbox/{token}")
    def mailbox_delete(token: str):
        """Borra un buzon IMAP y cierra su conexion."""
        acc = db_get_account(token)
        if not acc:
            raise HTTPException(404, "Token invalido o expirado.")
        db_delete_account(token)
        _drop_box(token)
        return {"ok": True, "email": acc["email"]}

    def _leer(token: str) -> dict:
        """Lee el buzon (local o IMAP) y arma la respuesta con sus mensajes."""
        acc = db_get_account(token)
        if not acc:
            raise HTTPException(404, "Token invalido o expirado.")
        expires = datetime.fromisoformat(acc["expires_at"])
        if datetime.now() > expires:
            db_delete_account(token)
            _drop_box(token)
            raise HTTPException(410, "Correo expirado.")
        remaining = int((expires - datetime.now()).total_seconds())

        if acc["kind"] == "imap":
            mensajes = _get_box(acc).messages()
        else:
            mensajes = db_messages(acc["email"])

        for m in mensajes:
            if not m.get("otp"):
                m["otp"] = extraer_otp(f"{m.get('subject', '')}\n{m.get('body', '')}")

        otp = next((m.get("otp") for m in mensajes if m.get("otp")), None)
        return {
            "email": acc["email"],
            "kind": acc["kind"],
            "expires_in_seconds": max(remaining, 0),
            "messages": mensajes,
            "otp": otp,
        }

    @router.get("/messages/{token}")
    def messages(token: str):
        """Lista los mensajes del buzon con su codigo."""
        return _leer(token)

    @router.get("/otp/{token}")
    def otp(token: str):
        """Devuelve solo el codigo de verificacion del buzon."""
        data = _leer(token)
        return {"email": data["email"], "otp": data["otp"], "messages": len(data["messages"])}

    return router


_smtp_ctrl = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _smtp_ctrl
    init_db()
    _smtp_ctrl = Controller(SMTPHandler(), hostname=SMTP_HOST, port=SMTP_PORT)
    _smtp_ctrl.start()
    t = threading.Timer(0, _cleanup_loop)
    t.daemon = True
    t.start()
    yield
    if _smtp_ctrl:
        _smtp_ctrl.stop()
    with _boxes_lock:
        tokens = list(_boxes)
    for token in tokens:
        _drop_box(token)


app = FastAPI(title="GenCookie TempMail API", lifespan=lifespan)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Limita peticiones por IP solo para clientes externos."""
    client_ip = request.client.host if request.client else "unknown"
    if client_ip in ("127.0.0.1", "::1"):
        return await call_next(request)
    ahora = time.time()
    with _rate_limit_tracker_lock:
        _rate_limit_tracker[client_ip] = [
            t for t in _rate_limit_tracker[client_ip]
            if ahora - t < RATE_LIMIT_WINDOW_S
        ]
        if len(_rate_limit_tracker[client_ip]) >= RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "Demasiadas peticiones. Intentalo de nuevo en unos segundos."},
            )
        _rate_limit_tracker[client_ip].append(ahora)
    return await call_next(request)


for ns, ns_domains in NAMESPACES.items():
    if ns_domains:
        app.include_router(make_router(ns))


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="warning",
    )
