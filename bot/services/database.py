"""Capa de base de datos SQLite para Hades.

Inspirada en el manejo de alfa1: WAL, busy_timeout largo, foreign_keys ON
y cierre garantizado de conexiones.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from bot.config import DATA_DIR

DB_PATH = DATA_DIR / "bot_database.db"

logger = logging.getLogger(__name__)

# Serializa la inicialización para que no haya carreras al arrancar.
_db_init_lock = asyncio.Lock()
_db_initialized = False

# Backups automáticos: se conservan los últimos N archivos.
DB_BACKUP_DIR = DATA_DIR / "backups"
DB_BACKUP_KEEP = int(__import__("os").getenv("DB_BACKUP_KEEP", "7"))
# Versión de esquema para migraciones futuras (Fase 5).
SCHEMA_VERSION = 1


async def maybe_backup_db() -> None:
    """Copia diaria de la BD a data/backups/ (Fase 5).

    Se ejecuta al arrancar: si ya existe un backup de hoy, no hace nada.
    Conserva los últimos DB_BACKUP_KEEP archivos. Nunca tumba el arranque.
    """
    import shutil
    from datetime import datetime, timezone

    try:
        if not DB_PATH.exists():
            return
        DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest = DB_BACKUP_DIR / f"bot_database.{today}.db"
        if dest.exists():
            return
        # Copia consistente: checkpoint WAL antes de copiar el archivo.
        try:
            import aiosqlite as _aios

            async with _aios.connect(DB_PATH, timeout=60) as _db:
                await _db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        shutil.copy2(DB_PATH, dest)
        # Rotación: conservar solo los últimos N.
        backups = sorted(DB_BACKUP_DIR.glob("bot_database.*.db"))
        while len(backups) > DB_BACKUP_KEEP:
            oldest = backups.pop(0)
            try:
                oldest.unlink()
            except Exception:
                break
        logger.info("Backup BD creado: %s", dest.name)
    except Exception as exc:
        logger.warning("No se pudo crear backup de BD: %s", exc)


async def _setup_connection(db: aiosqlite.Connection) -> None:
    """Aplica PRAGMAs esenciales para concurrencia e integridad referencial."""
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA busy_timeout = 60000")
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA synchronous = NORMAL")


@asynccontextmanager
async def get_connection() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Context manager que entrega una conexión SQLite lista para usar."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH, timeout=60) as db:
        await _setup_connection(db)
        yield db


async def init_db() -> None:
    """Inicializa la base de datos y crea las tablas e índices necesarios."""
    global _db_initialized

    async with _db_init_lock:
        if _db_initialized:
            return

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        # Migrar tablas existentes si faltan columnas
        async with aiosqlite.connect(DB_PATH, timeout=60) as db:
            await _setup_connection(db)
            
            # Añadir columna proxy a amazon_cookies si no existe
            try:
                await db.execute("ALTER TABLE amazon_cookies ADD COLUMN proxy TEXT")
                await db.commit()
                logger.info("Migración: columna 'proxy' añadida a amazon_cookies")
            except aiosqlite.OperationalError:
                # La columna ya existe, no hacer nada
                pass
        async with aiosqlite.connect(DB_PATH, timeout=60) as db:
            await _setup_connection(db)

            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    first_seen TEXT,
                    last_seen TEXT
                )
            ''')

            await db.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY
                )
            ''')

            await db.execute('''
                CREATE TABLE IF NOT EXISTS memberships (
                    user_id INTEGER PRIMARY KEY,
                    plan TEXT,
                    end_date TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            await db.execute('''
                CREATE TABLE IF NOT EXISTS credits (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            await db.execute('''
                CREATE TABLE IF NOT EXISTS credit_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    reason TEXT,
                    timestamp TEXT,
                    admin_id INTEGER,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            await db.execute('''
                CREATE TABLE IF NOT EXISTS amazon_cookies (
                    user_id INTEGER PRIMARY KEY,
                    cookie TEXT NOT NULL,
                    proxy TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_memberships_end_date ON memberships(end_date)"
            )
            await db.execute('''
                CREATE TABLE IF NOT EXISTS amazon_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    country TEXT,
                    domain TEXT,
                    email TEXT,
                    password TEXT,
                    name TEXT,
                    cookies TEXT,
                    created_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_amazon_cookies_user_id ON amazon_cookies(user_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_amazon_accounts_user_id ON amazon_accounts(user_id)"
            )

            await db.execute('''
                CREATE TABLE IF NOT EXISTS gencookie_access (
                    user_id INTEGER PRIMARY KEY,
                    access_type TEXT NOT NULL DEFAULT 'user',
                    granted_by INTEGER,
                    granted_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            await db.execute('''
                CREATE TABLE IF NOT EXISTS user_galletas (
                    user_id INTEGER PRIMARY KEY,
                    tipo TEXT NOT NULL DEFAULT 'olimpo',
                    total INTEGER NOT NULL DEFAULT 0,
                    usadas INTEGER NOT NULL DEFAULT 0,
                    granted_by INTEGER,
                    granted_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            await db.execute('''
                CREATE TABLE IF NOT EXISTS admin_daily_galletas (
                    user_id INTEGER PRIMARY KEY,
                    day TEXT NOT NULL,
                    allowance INTEGER NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            ''')

            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_gencookie_access_user_id ON gencookie_access(user_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_credits_user_id ON credits(user_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON credit_transactions(user_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON credit_transactions(timestamp)"
            )

            await db.commit()

        _db_initialized = True
