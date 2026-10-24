"""Diagnostico 2: la rotacion de IP de IPRoyal realmente cambia la IP de salida?

Usa el pool del .env del proyecto. Peticiones diminutas a un eco de IP.
"""
import re
import secrets
import sys
from pathlib import Path

ROOT = Path("/home/olimpo/GenCookie/GenCookie")

env = {}
for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip().strip('"').strip("'")

RAW = env["PROXY_STICKY_MX"].strip().rstrip(",")
HOST_PORT, CREDS = RAW.split("@", 1)          # geo.iproyal.com:12321  /  OlimpoMx:6060..._country-mx_...
USER, PWD = CREDS.split(":", 1)


def gc_rotate(proxy):
    """rotateProxySession tal cual esta en el zip."""
    if not proxy:
        return proxy
    sid = secrets.token_hex(8)
    proxy = re.sub(r"_session-[A-Za-z0-9_-]+", f"_session-{sid}", proxy)
    return re.sub(r"-session-[A-Za-z0-9_-]+", f"-session-{sid}", proxy)


def build(user, pwd):
    return f"http://{user}:{pwd}@{HOST_PORT}"


def exit_ip(proxy_url):
    from curl_cffi import requests as curl
    s = curl.Session(impersonate="chrome")
    s.proxies = {"http": proxy_url, "https": proxy_url}
    try:
        r = s.get("https://api.ipify.org", timeout=25)
        return f"HTTP {r.status_code} ip={r.text.strip()}"
    except Exception as e:
        return f"ERROR {type(e).__name__}: {str(e)[:150]}"


def mask(p):
    return re.sub(r"(?<=://)([^:@/]+):([^@/]+)@", r"\1:***@", p)


print("=" * 78)
print("A) Proxy tal cual esta en .env (params IPRoyal en el PASSWORD):")
print("   ", mask(build(USER, PWD)))
p1 = build(USER, PWD)
print("    salida ->", exit_ip(p1))

print()
print("B) Mismo proxy, SESION DISTINTA (session-ccc) -> should change IP:")
p2 = build(USER, re.sub(r"_session-[A-Za-z0-9_-]+", "_session-" + secrets.token_hex(8), PWD))
print("   ", mask(p2))
print("    salida ->", exit_ip(p2))

print()
print("C) MISMA sesion otra vez -> should be SAME IP (sticky):")
print("    salida ->", exit_ip(p1))

print()
print("D) rotateProxySession() del ZIP aplicado a la cadena correcta:")
rot = gc_rotate(build(USER, PWD))
print("   ", mask(rot))
print("    -> password resultante:", rot.split(":", 1)[1].split("@")[0].replace(PWD.split(":", 1)[1], "<PASS_BASE>"))
print("    salida ->", exit_ip(rot))

print()
print("E) country observado en la IP de salida (geo check):")
from curl_cffi import requests as curl
s = curl.Session(impersonate="chrome")
s.proxies = {"http": p1, "https": p1}
try:
    j = s.get("http://ip-api.com/json/?fields=country,countryCode,isp,query", timeout=25).json()
    print("   ", j)
except Exception as e:
    print("   ERROR", e)
