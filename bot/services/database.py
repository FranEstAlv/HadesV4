"""Capa de base de datos SQLite para Hades.

Inspirada en el manejo de alfa1: WAL, busy_timeout largo, foreign_keys ON
y cierre garantizado de conexiones.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from bot.config import DATA_DIR

DB_PATH = DATA_DIR / "bot_database.db"

# Serializa la inicialización para que no haya carreras al arrancar.
_db_init_lock = asyncio.Lock()
_db_initialized = False


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
