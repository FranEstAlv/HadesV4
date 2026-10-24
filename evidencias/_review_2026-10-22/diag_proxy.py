"""Diagnostico: como normaliza y parsea el proxy el codigo del zip.

Solo lee .env del proyecto y hace 2 peticiones diminutas a un eco de IP.
No modifica nada.
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path("/home/olimpo/GenCookie/GenCookie")


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def mask(s):
    return re.sub(r"(?<=://)([^:@/]+):([^@/]+)@", r"\1:***@", s)


# --- normalizador del bot (referencia que SI funciona) --------------------
def bot_parse(raw):
    out = []
    for entry in (p.strip() for p in raw.replace('"', "").replace("'", "").split(",") if p.strip()):
        if entry.startswith(("http://", "https://")):
            out.append(entry)
            continue
        if "@" in entry:
            host_port, creds = entry.split("@", 1)
            out.append(f"http://{creds}@{host_port}")
        else:
            out.append(f"http://{entry}")
    return out


# --- normalizador del zip (GenCookie) --------------------------------------
def gc_normalize(proxy):
    if not proxy:
        return None
    proxy = proxy.strip()
    if "://" in proxy:
        return proxy
    if "@" in proxy:
        return f"http://{proxy}"
    parts = proxy.split(":")
    if len(parts) >= 4 and parts[1].isdigit():
        return f"http://{parts[2]}:{':'.join(parts[3:])}@{parts[0]}:{parts[1]}"
    return f"http://{proxy}"


# --- parser del zip para 2Captcha/CapSolver --------------------------------
def gc_proxy_parts(proxy):
    if not proxy:
        return None
    m = re.match(
        r"^(?:(?P<scheme>https?)://)?"
        r"(?:(?P<user>[^:@/]+):(?P<pwd>[^@/]*)@)?"
        r"(?P<host>[^:/@]+)(?::(?P<port>\d+))?",
        proxy.strip(),
    )
    if not m:
        return None
    out = {
        "proxyType": "http",
        "proxyAddress": m.group("host"),
        "proxyPort": int(m.group("port") or 80),
    }
    if m.group("user"):
        out["proxyLogin"] = m.group("user")
        out["proxyPassword"] = m.group("pwd") or ""
    return out


def gc_rotate(proxy):
    if not proxy:
        return proxy
    import secrets
    sid = secrets.token_hex(8)
    proxy = re.sub(r"_session-[A-Za-z0-9_-]+", f"_session-{sid}", proxy)
    return re.sub(r"-session-[A-Za-z0-9_-]+", f"-session-{sid}", proxy)


def main():
    env = load_env()
    raw = env.get("PROXY_STICKY_MX", "")
    print("=" * 78)
    print("1) .env crudo (PROXY_STICKY_MX):")
    print("   ", mask(raw.strip()))
    print()
    print("2) Normalizador del BOT (bot/config.py:_parse_proxies) ->")
    bot = bot_parse(raw)[0]
    print("   ", mask(bot))
    p = urlparse(bot)
    print(f"    -> host={p.hostname} port={p.port} user={p.username} pass={mask(p.password or '')}")
    print()
    print("3) Normalizador del ZIP (accountCreator.normalizeProxy) ->")
    gc = gc_normalize(raw.strip())
    print("   ", mask(gc))
    p2 = urlparse(gc)
    try:
        port2 = p2.port
    except ValueError as e:
        port2 = f"<<URL INVALIDA: {e}>>"
    print(f"    -> host={p2.hostname} port={port2} user={p2.username} pass={mask(p2.password or '')}")
    print()
    print("4) rotateProxySession(ZIP) sobre la cadena del bot:")
    print("   ", mask(gc_rotate(bot)))
    print()
    print("5) _proxy_parts(ZIP) sobre la cadena que ve 2Captcha:")
    print("   ", gc_proxy_parts(gc))
    print("   _proxy_parts(ZIP) sobre la cadena CORRECTA (si el proxy quedara bien armado):")
    print("   ", gc_proxy_parts(bot))
    print()

    # --- prueba real de salida por cada variante ---------------------------
    try:
        from curl_cffi import requests as curl
    except Exception as e:
        print("curl_cffi no disponible:", e)
        return 0

    echo = "https://api.ipify.org"
    for label, url in (("URL DEL BOT (correcta)", bot), ("URL DEL ZIP (invertida)", gc)):
        try:
            s = curl.Session(impersonate="chrome")
            s.proxies = {"http": url, "https": url}
            r = s.get(echo, timeout=25)
            print(f"[{label}] -> HTTP {r.status_code} ip={r.text.strip()}")
        except Exception as e:
            print(f"[{label}] -> ERROR {type(e).__name__}: {str(e)[:220]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
