"""Gate Amazon clásico (AmazonGate directo: ajax + Prime + HardVet).

Motor del script de respaldo evidencias/amazon_api_clasico_respaldo.py
(origen: nueva_amazon/amazonapi.py). Hace el flujo completo en HTTP con
curl_cffi: init (R1+R2+dirección) → addcard → Prime pipeline → HardVet.

Expone la misma firma que usaba Leviatan para no tocar los handlers:
- amazon_classic_check(card_data, user_id=None, cookie=None)
- amazon_classic_check_multiple(cards_data, callback=None, user_id=None)

La cookie de Amazon se toma del usuario (igual que antes):
  1. Cookie guardada con /save (tabla amazon_cookies).
  2. Cuentas generadas con /gencookie (tabla amazon_accounts en BD).
  3. Archivos JSON de data/amazon_accounts/.
  4. Cookie global (AMAZON_COOKIE o data/amazon_cookie.txt).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from bot.config import gate_proxy
from bot.services.amazon_cookies import get_amazon_cookie
from bot.services.amazon_creator import get_user_amazon_accounts
from evidencias.amazon_api_clasico_respaldo import AmazonGate

logger = logging.getLogger(__name__)

# Un solo lote masivo a la vez en todo el bot (mismo patrón que antes).
_MASS_LOCK = asyncio.Lock()
MASS_TIMEOUT_S = int(os.getenv("AMAZON_CLASSIC_MASS_TIMEOUT_S", "600"))
MASS_LIMIT = 10
_CARD_DELAY_S = 2.0


def _get_global_cookie() -> Optional[str]:
    cookie = os.getenv("AMAZON_COOKIE", "").strip()
    if cookie:
        return cookie
    path = Path("data/amazon_cookie.txt")
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


def _get_user_cookie_from_files() -> Optional[str]:
    """Lee la cookie más reciente de data/amazon_accounts/ (fallback sin BD)."""
    base = Path("data/amazon_accounts")
    if not base.is_dir():
        return None
    best: Optional[Tuple[float, str]] = None
    for path in base.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cookies = str(data.get("cookies") or "").strip()
        if not cookies:
            continue
        created = str(data.get("created") or "")
        try:
            ts = time.mktime(time.strptime(created, "%Y-%m-%dT%H:%M:%S.%f"))
        except ValueError:
            try:
                ts = time.mktime(time.strptime(created, "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                ts = path.stat().st_mtime
        if best is None or ts > best[0]:
            best = (ts, cookies)
    return best[1] if best else None


async def _get_user_cookie(user_id: Optional[int]) -> Tuple[Optional[str], Optional[str]]:
    """Cookie de Amazon para el usuario, con fallback global. Devuelve (cookie, proxy)."""
    if user_id is not None:
        try:
            cookie_and_proxy = await get_amazon_cookie(user_id)
            if cookie_and_proxy and cookie_and_proxy[0]:
                return cookie_and_proxy
        except Exception as exc:
            logger.debug("Amazon clásico: no se pudo leer amazon_cookies: %s", exc)
        try:
            accounts = await get_user_amazon_accounts(user_id, limit=5)
            for acc in accounts:
                cookies = (acc or {}).get("cookies") or ""
                if cookies.strip():
                    return cookies.strip(), None
        except Exception as exc:
            logger.debug("Amazon clásico: no se pudo leer amazon_accounts: %s", exc)
        cookie = await asyncio.to_thread(_get_user_cookie_from_files)
        if cookie:
            return cookie, None
    global_cookie = await asyncio.to_thread(_get_global_cookie)
    return global_cookie, None


def _pick_proxy_url() -> Optional[str]:
    """Proxy MX sticky del pool del bot (el AmazonGate lo normaliza solo)."""
    try:
        return gate_proxy("mx")
    except Exception:
        return None


def _map_gate_result(gate_result: dict) -> Dict[str, str]:
    """Mapea buildResult/_fail del clásico al formato del bot (Live/Dead/Error)."""
    if not isinstance(gate_result, dict):
        return {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "message": "Respuesta desconocida del gate.",
            "classification": "unknown",
        }
    success = gate_result.get("success")
    api_resp = str(gate_result.get("apiResponse", ""))
    resp = str(gate_result.get("response") or gate_result.get("message") or "Sin respuesta.").strip()
    low = resp.lower()

    if success is True or "approved" in api_resp.lower() or "charged" in low:
        return {"status": "✅ Live", "message": resp, "classification": "live"}
    if success is False:
        if api_resp == "Declined" or "verification failed" in low or "declined" in low or "invalid card" in low:
            return {"status": "❌ Dead", "message": resp, "classification": "dead"}
        return {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "message": resp,
            "classification": "session_error",
        }
    if "declined" in api_resp.lower():
        return {"status": "❌ Dead", "message": resp, "classification": "dead"}
    return {
        "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
        "message": resp,
        "classification": "unknown",
    }


def _validate_card(card_data: str) -> Optional[Tuple[str, str, str, str]]:
    parts = (card_data or "").split("|")
    if len(parts) != 4:
        return None
    cc, mes, ano, cvv = (p.strip() for p in parts)
    if not cc.isdigit() or len(cc) < 13 or len(cc) > 19:
        return None
    if not mes.isdigit() or not (1 <= int(mes) <= 12):
        return None
    if not ano.isdigit():
        return None
    if len(ano) == 4:
        ano = ano[-2:]
    if not (0 <= int(ano) <= 99):
        return None
    if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
        return None
    return cc, mes.zfill(2), ano, cvv


def _sync_single(card: str, cookies: str, proxy: Optional[str]) -> Dict[str, str]:
    """Un check completo en hilo: init + process + mapeo (bloqueante)."""
    gate = AmazonGate(cookies, proxy)
    try:
        ri = gate.init()
        if not ri.get("ok"):
            msg = str(ri.get("msg", "Cookie expirada"))
            return {
                "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
                "message": msg,
                "classification": "session_error",
            }
        raw = gate.process(card)
        return _map_gate_result(raw)
    except Exception as exc:
        logger.exception("Amazon clásico: error en check")
        return {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "message": f"Error interno del gate: {exc}",
            "classification": "error",
        }
    finally:
        try:
            gate.close()
        except Exception:
            pass


async def amazon_classic_check(
    card_data: str,
    user_id: Optional[int] = None,
    cookie: Optional[str] = None,
    proxy: Optional[str] = None,
) -> Dict[str, str]:
    """Verifica una sola tarjeta con el motor clásico."""
    card_tuple = _validate_card(card_data)
    if not card_tuple:
        return {"status": "❌ Dead", "message": "Formato invalido. Usa cc|mes|año|cvv"}

    cc, mes, ano, cvv = card_tuple
    card = f"{cc}|{mes}|{ano}|{cvv}"
    cookies = (cookie or "").strip() or await _get_user_cookie(user_id)
    if not cookies:
        return {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "message": "No hay cookie de Amazon. Genera una cuenta con /gencookie o guarda una con /save.",
            "classification": "no_cookie",
        }
    # Usar proxy pasado o recuperarlo de la cookie guardada
    if not proxy:
        try:
            cookie_and_proxy = await get_amazon_cookie(user_id) if user_id else (None, None)
            if cookie_and_proxy:
                _, proxy = cookie_and_proxy
        except Exception:
            pass
    proxy_url = proxy or _pick_proxy_url()
    return await asyncio.to_thread(_sync_single, card, cookies, proxy_url)


async def amazon_classic_check_multiple(
    cards_data: List[str],
    callback: Optional[Callable[[int, Dict[str, str], str, int], None]] = None,
    user_id: Optional[int] = None,
    proxy: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Verifica un lote de tarjetas con el motor clásico (máximo 10, secuencial)."""
    cards = [c.strip() for c in cards_data if c.strip()][:MASS_LIMIT]
    total = len(cards)
    results: List[Dict[str, str]] = []

    if total == 0:
        return []

    if _MASS_LOCK.locked():
        error = {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "message": "Ya hay un lote corriendo. Espera a que termine antes de mandar otro.",
            "classification": "busy",
        }
        results = [error] * total
        if callback:
            for i, card in enumerate(cards):
                await callback(i, error, card, 0)
        return results

    await _MASS_LOCK.acquire()
    try:
        return await _classic_check_multiple_inner(cards, callback, user_id, proxy)
    finally:
        _MASS_LOCK.release()


async def _classic_check_multiple_inner(
    cards: List[str],
    callback: Optional[Callable[[int, Dict[str, str], str, int], None]] = None,
    user_id: Optional[int] = None,
    proxy: Optional[str] = None,
) -> List[Dict[str, str]]:
    total = len(cards)
    results: List[Dict[str, str]] = []

    cookies = await _get_user_cookie(user_id)
    if not cookies:
        error = {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "message": "No hay cookie de Amazon. Genera una cuenta con /gencookie o guarda una con /save.",
            "classification": "no_cookie",
        }
        results = [error] * total
        if callback:
            for i, card in enumerate(cards):
                await callback(i, error, card, 0)
        return results

    # Usar proxy pasado o recuperarlo de la cookie guardada
    if not proxy:
        try:
            cookie_and_proxy = await get_amazon_cookie(user_id) if user_id else (None, None)
            if cookie_and_proxy:
                _, proxy = cookie_and_proxy
        except Exception:
            pass
    proxy_url = proxy or _pick_proxy_url()
    deadline = time.monotonic() + MASS_TIMEOUT_S

    for i, card_data in enumerate(cards):
        if time.monotonic() > deadline:
            stopped = {
                "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
                "message": f"El lote se detuvo por tiempo (más de {MASS_TIMEOUT_S // 60} min).",
                "classification": "timeout",
            }
            for j in range(i, total):
                results.append(stopped)
                if callback:
                    try:
                        await callback(j, stopped, cards[j], 0)
                    except Exception:
                        logger.exception("Error en callback de Amazon clásico mass")
            break

        card_tuple = _validate_card(card_data)
        if not card_tuple:
            result = {"status": "❌ Dead", "message": "Formato invalido"}
        else:
            cc, mes, ano, cvv = card_tuple
            card = f"{cc}|{mes}|{ano}|{cvv}"
            result = await asyncio.to_thread(_sync_single, card, cookies, proxy_url)

        results.append(result)
        if callback:
            try:
                await callback(i, result, card_data, 0)
            except Exception:
                logger.exception("Error en callback de Amazon clásico mass")

        if i < total - 1:
            await asyncio.sleep(_CARD_DELAY_S)

    return results
