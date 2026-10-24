"""Servicio TempMail API para GenCookie.

Expone:
  - Servidor SMTP (por defecto 0.0.0.0:1025) para recibir correos.
  - API HTTP (por defecto 127.0.0.1:3000) para generar emails temporales
    y leer sus mensajes.

Para recibir correos desde Internet se requiere:
  - Un registro MX apuntando al servidor.
  - Redirección del puerto 25 al puerto SMTP configurado (ej. iptables).
"""
import os
import random
import sqlite3
import string
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from email import message_from_bytes
from email.policy import default as email_policy
from pathlib import Path
from typing import Dict, List

import uvicorn
from aiosmtpd.controller import Controller
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from collections import defaultdict
from time import time

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("EMAILAPI_DB_PATH", BASE_DIR / "emails.db"))
SMTP_HOST = os.getenv("EMAILAPI_SMTP_HOST", "0.0.0.0")
SMTP_PORT = int(os.getenv("EMAILAPI_SMTP_PORT", "1025"))
API_HOST = os.getenv("EMAILAPI_API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("EMAILAPI_API_PORT", "3000"))
EXPIRY_MINUTES = int(os.getenv("EMAILAPI_EXPIRY_MINUTES", "30"))
CLEANUP_INTERVAL_S = int(os.getenv("EMAILAPI_CLEANUP_INTERVAL_S", "300"))
RATE_LIMIT_REQUESTS = int(os.getenv("EMAILAPI_RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW_S = int(os.getenv("EMAILAPI_RATE_LIMIT_WINDOW_S", "60"))

# Dominios para correos temporales. Se pueden pasar por variable de entorno
# separados por comas, ej: EMAILAPI_DOMAINS=dom1.cyou,dom2.cyou
_DEFAULT_DOMAINS = os.getenv("EMAILAPI_DOMAINS", "olimpo.cyou")
NAMESPACES: Dict[str, List[str]] = {
    "odisea": [d.strip() for d in _DEFAULT_DOMAINS.split(",") if d.strip()],
}

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_db_lock = threading.Lock()

# Rate limiting por IP (API)
_rate_limit_tracker: Dict[str, List[float]] = defaultdict(list)
_rate_limit_tracker_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db() -> None:
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
                expires_at  TEXT NOT NULL
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_acc_expires ON accounts(expires_at)")
        c.commit()


def db_insert(recipient: str, sender: str, subject: str, body: str) -> None:
    with _db_lock, _conn() as c:
        c.execute(
            "INSERT INTO messages (recipient, sender, subject, body, received_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (recipient.lower(), sender, subject, body, datetime.now().isoformat()),
        )
        c.commit()


def db_messages(email: str) -> list:
    cutoff = (datetime.now() - timedelta(minutes=EXPIRY_MINUTES)).isoformat()
    with _db_lock, _conn() as c:
        rows = c.execute(
            "SELECT sender, subject, body, received_at "
            "FROM messages WHERE recipient = ? AND received_at > ? "
            "ORDER BY received_at DESC",
            (email.lower(), cutoff),
        ).fetchall()
    return [dict(r) for r in rows]


def db_cleanup() -> None:
    cutoff = (datetime.now() - timedelta(minutes=EXPIRY_MINUTES)).isoformat()
    with _db_lock, _conn() as c:
        n = c.execute("DELETE FROM messages WHERE received_at <= ?", (cutoff,)).rowcount
        c.commit()
    if n:
        print(f"[Cleanup] {n} mensajes expirados eliminados.")


# Pesos opcionales por dominio para reducir la frecuencia de dominios
# descansando o en prueba. Formato: dom1:peso,dom2:peso
# Dominios sin peso explícito usan peso 1.
_DOMAIN_WEIGHTS: Dict[str, int] = {}
_weight_str = os.getenv("EMAILAPI_DOMAIN_WEIGHTS", "")
for part in _weight_str.split(","):
    if ":" in part:
        d, w = part.split(":", 1)
        d = d.strip()
        try:
            _DOMAIN_WEIGHTS[d] = int(w.strip())
        except ValueError:
            pass


def _next_domain(domains: List[str]) -> str:
    weights = [_DOMAIN_WEIGHTS.get(d, 1) for d in domains]
    return random.choices(domains, weights=weights, k=1)[0]


def db_insert_account(token: str, email: str, expires_at: datetime) -> None:
    with _db_lock, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO accounts (token, email, expires_at) VALUES (?, ?, ?)",
            (token, email.lower(), expires_at.isoformat()),
        )
        c.commit()


def db_get_account(token: str) -> dict | None:
    with _db_lock, _conn() as c:
        row = c.execute(
            "SELECT token, email, expires_at FROM accounts WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        return None
    return {"token": row["token"], "email": row["email"], "expires_at": row["expires_at"]}


def db_cleanup_accounts() -> None:
    cutoff = datetime.now().isoformat()
    with _db_lock, _conn() as c:
        n = c.execute("DELETE FROM accounts WHERE expires_at <= ?", (cutoff,)).rowcount
        c.commit()
    if n:
        print(f"[Cleanup] {n} cuentas expiradas eliminadas.")


def _cleanup_loop() -> None:
    db_cleanup()
    db_cleanup_accounts()
    timer = threading.Timer(CLEANUP_INTERVAL_S, _cleanup_loop)
    timer.daemon = True
    timer.start()


class SMTPHandler:
    async def handle_DATA(self, server, session, envelope):
        msg = message_from_bytes(envelope.content, policy=email_policy)
        sender = envelope.mail_from
        subject = msg.get("Subject", "")
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    try:
                        body += (part.get_content() or "").strip() + "\n"
                    except Exception:
                        pass
        else:
            try:
                body = (msg.get_content() or "").strip()
            except Exception:
                pass

        body = body.strip()

        # Validar que todos los destinatarios tengan un dominio permitido
        domains = NAMESPACES.get("odisea", [])
        for recipient in envelope.rcpt_tos:
            recipient_domain = recipient.split("@")[-1].lower() if "@" in recipient else ""
            if recipient_domain not in [d.lower() for d in domains]:
                print(f"[SMTP] RECHAZADO: dominio no permitido {recipient}")
                return "554 5.7.1 Recipient address rejected: invalid domain"
        
        for recipient in envelope.rcpt_tos:
            db_insert(recipient, sender, subject, body)
            print(f"[SMTP] {datetime.now():%H:%M:%S} {sender} -> {recipient} | {subject!r}")

        return "250 Message accepted for delivery"


def make_router(prefix: str) -> APIRouter:
    domains = NAMESPACES[prefix]
    router = APIRouter(prefix=f"/{prefix}")

    @router.get("/generate")
    def generate(domain: str | None = None):
        chosen_domain = domain if domain in domains else _next_domain(domains)
        email = (
            f"{''.join(random.choices(string.ascii_lowercase + string.digits, k=10))}"
            f"@{chosen_domain}"
        )
        token = str(uuid.uuid4())
        expires = datetime.now() + timedelta(minutes=EXPIRY_MINUTES)
        db_insert_account(token, email, expires)
        return {
            "email": email,
            "token": token,
            "expires_in": f"{EXPIRY_MINUTES} minutos",
            "expires_at": expires.isoformat(),
        }

    @router.get("/messages/{token}")
    def messages(token: str):
        acc = db_get_account(token)
        if not acc:
            raise HTTPException(404, "Token invalido o expirado.")
        expires = datetime.fromisoformat(acc["expires_at"])
        if datetime.now() > expires:
            db_cleanup_accounts()
            raise HTTPException(410, "Correo expirado.")
        remaining = int((expires - datetime.now()).total_seconds())
        return {
            "email": acc["email"],
            "expires_in_seconds": max(remaining, 0),
            "messages": db_messages(acc["email"]),
        }

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


app = FastAPI(title="GenCookie TempMail API", lifespan=lifespan)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting por IP en la API."""
    client_ip = request.client.host if request.client else "unknown"
    # La API es local y el bot consulta desde localhost. No aplicar
    # rate-limit a loopback para evitar 429 por polling interno;
    # solo se limita a IPs externas.
    if client_ip in ("127.0.0.1", "::1"):
        return await call_next(request)
    current_time = time()

    with _rate_limit_tracker_lock:
        # Limpiar entradas antiguas (fuera de la ventana)
        _rate_limit_tracker[client_ip] = [
            t for t in _rate_limit_tracker[client_ip]
            if current_time - t < RATE_LIMIT_WINDOW_S
        ]

        if len(_rate_limit_tracker[client_ip]) >= RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Inténtalo de nuevo en unos segundos."
                }
            )

        _rate_limit_tracker[client_ip].append(current_time)

    try:
        response = await call_next(request)
        return response
    except Exception:
        raise


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
