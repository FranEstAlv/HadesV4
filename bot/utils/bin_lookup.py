"""Lookup de información de BIN desde tarjetas.csv."""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Ruta relativa al directorio de trabajo del bot
_CSV_PATH = Path("data/tarjetas.csv")


def _load_bin_db() -> Dict[str, Dict[str, str]]:
    db: Dict[str, Dict[str, str]] = {}
    try:
        if not _CSV_PATH.exists():
            logger.warning("No se encontró %s; el lookup de BIN estará vacío.", _CSV_PATH)
            return db
        with _CSV_PATH.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                bin_num = row.get("bin", "").strip()
                if not bin_num:
                    continue
                db[bin_num] = {
                    "Marca": row.get("brand", "") or "Desconocido",
                    "Tipo de tarjeta": row.get("tipo", "") or "Desconocido",
                    "Nivel de tarjeta": row.get("nivel", "") or "Desconocido",
                    "Banco": row.get("Banco", "") or "Desconocido",
                    "Teléfono": row.get("teléfono", "") or "Desconocido",
                    "País": row.get("país", "") or "Desconocido",
                }
    except Exception as exc:
        logger.debug("Error cargando BIN db: %s", exc)
    return db


_BIN_DB: Optional[Dict[str, Dict[str, str]]] = None


def _get_db() -> Dict[str, Dict[str, str]]:
    global _BIN_DB
    if _BIN_DB is None:
        _BIN_DB = _load_bin_db()
    return _BIN_DB


def get_card_info(bin_number: str) -> Optional[Dict[str, str]]:
    """Busca información del BIN (6 u 8 dígitos) en la base local."""
    db = _get_db()
    if not db:
        return None
    for size in (8, 6):
        if len(bin_number) >= size:
            prefix = bin_number[:size]
            if prefix in db:
                return db[prefix]
    return None


def reload_bin_db() -> None:
    """Fuerza la recarga del CSV de BINs (útil si se actualiza en caliente)."""
    global _BIN_DB
    _BIN_DB = _load_bin_db()
