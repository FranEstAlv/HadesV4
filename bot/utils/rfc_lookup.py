"""Lectura de la BD de personal federalizado para el comando /rfc.

Fuente: evidencias/personal_federalizado_corregido.csv
(generada desde el PDF maestro con evidencias/pipeline_federalizado.py).

Solo se entregan filas con Validacion_Nombre ok u ok_homonimia.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Dict, List, Optional

_CSV_PATH = (
    Path(__file__).parent.parent.parent
    / "evidencias"
    / "personal_federalizado_corregido.csv"
)

_cache: Optional[List[Dict[str, str]]] = None


def csv_path() -> Path:
    return _CSV_PATH


def _load() -> List[Dict[str, str]]:
    global _cache
    if _cache is not None:
        return _cache
    rows: List[Dict[str, str]] = []
    try:
        with open(_CSV_PATH, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("Validacion_Nombre") in ("ok", "ok_homonimia"):
                    rows.append(row)
    except FileNotFoundError:
        return []
    _cache = rows
    return rows


def reload_cache() -> int:
    """Recarga la BD en memoria (tras regenerar el CSV). Devuelve el total."""
    global _cache
    _cache = None
    return len(_load())


def total_registros() -> int:
    return len(_load())


def get_random_record(rng: Optional[random.Random] = None) -> Optional[Dict[str, str]]:
    """Devuelve un registro aleatorio de persona, o None si no hay datos."""
    rows = _load()
    if not rows:
        return None
    r = rng or random
    return r.choice(rows)


def split_rfc_homoclave(rfc: str):
    """Separa RFC base y homoclave. RFC persona fisica = 13 posiciones."""
    rfc = (rfc or "").strip()
    if len(rfc) == 13:
        return rfc[:-3], rfc[-3:]
    return rfc, ""
