"""Prueba de proxy para la maquina 2 (smartproxy / IPRoyal).

Uso:
    python prueba_iproyal.py                 # lee PROXY_STICKY_MX del .env de al lado
    python prueba_iproyal.py "geo.iproyal.com:12321@user:pass_country-mx_session-abc_lifetime-10m"
    python prueba_iproyal.py --env /ruta/al/.env

No modifica nada. Solo hace peticiones diminutas a ip-api.com y api.ipify.org.
"""
import argparse
import os
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    from curl_cffi import requests as curl
except ImportError:
    print("Falta curl_cffi:  pip install curl_cffi")
    sys.exit(2)


# ----------------------------------------------------------------- parsers
def normalize_fix(proxy):
    """Normalizador CORREGIDO -> siempre http://user:pass@host:port."""
    if not proxy:
        return None
    p = proxy.strip()
    if "://" in p:
        u = urlparse(p)
        if not u.username or not u.hostname:
            return p
        return f"{u.scheme}://{u.username}:{u.password or ''}@{u.hostname}:{u.port}"
    if "@" in p:                                   # host:port@user:pass
        host_port, creds = p.split("@", 1)
        return f"http://{creds}@{host_port}"
    parts = p.split(":")                           # host:port:user:pass
    if len(parts) >= 4 and parts[1].isdigit():
        return f"http://{parts[2]}:{':'.join(parts[3:])}@{parts[0]}:{parts[1]}"
    return f"http://{p}"


def gc_normalize_zip(proxy):
    """accountCreator.normalizeProxy TAL CUAL esta en el zip (para comparar)."""
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


def gc_proxy_parts_zip(proxy):
    """cvfSolver._proxy_parts TAL CUAL esta en el zip (para comparar)."""
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


def proxy_parts_fix(proxy):
    """Parser CORREGIDO para 2Captcha/CapSolver."""
    if not proxy:
        return None
    p = proxy.strip()
    if "://" not in p:
        p = "http://" + p
    u = urlparse(p)
    if not u.hostname or not u.port:
        return None
    out = {"proxyType": "http", "proxyAddress": u.hostname, "proxyPort": int(u.port)}
    if u.username:
        out["proxyLogin"] = u.username
        out["proxyPassword"] = u.password or ""
    return out


def rotate_fix(proxy):
    """Rotacion corregida: cambia SOLO el id de sesion, conserva lifetime/streaming."""
    if not proxy:
        return proxy
    sid = secrets.token_hex(8)
    return re.sub(r"(_session-)[A-Za-z0-9]+", lambda m: m.group(1) + sid, proxy)


def gc_rotate(proxy):
    """rotateProxySession TAL CUAL esta en el zip (para comparar)."""
    if not proxy:
        return proxy
    sid = secrets.token_hex(8)
    proxy = re.sub(r"_session-[A-Za-z0-9_-]+", f"_session-{sid}", proxy)
    return re.sub(r"-session-[A-Za-z0-9_-]+", f"-session-{sid}", proxy)


# ----------------------------------------------------------------- red
def geo(proxy_url):
    s = curl.Session(impersonate="chrome")
    s.proxies = {"http": proxy_url, "https": proxy_url}
    # ip-api se satura rapido; si falla se reintenta y se cae a ipify.
    for attempt in range(3):
        try:
            j = s.get(
                "http://ip-api.com/json/?fields=country,countryCode,isp,query", timeout=30
            ).json()
            if j.get("query"):
                return (f"ip={j.get('query')}  pais={j.get('countryCode')} "
                        f"({j.get('country')})  isp={j.get('isp')}")
        except Exception as e:
            last = f"ERROR {type(e).__name__}: {str(e)[:150]}"
            time.sleep(2)
            continue
    try:
        ip = s.get("https://api.ipify.org", timeout=30).text.strip()
        return f"ip={ip}  (geo no disponible: ip-api no respondio)"
    except Exception as e:
        return f"ERROR {type(e).__name__}: {str(e)[:150]}"


def read_env(path):
    env = {}
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def hr(t):
    print()
    print("=" * 78)
    print(t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("proxy", nargs="?", help="cadena de proxy completa")
    ap.add_argument("--env", help="ruta del .env (por defecto, ./ .env)")
    args = ap.parse_args()

    if args.proxy:
        raw = args.proxy.strip()
    else:
        env_path = Path(args.env or ".env")
        if not env_path.exists():
            print(f"No existe {env_path}.  Pasa la proxy como argumento o --env")
            return 2
        env = read_env(env_path)
        raw = (env.get("PROXY_STICKY_MX") or env.get("PROXY_ROTATORY") or "").strip()
        raw = raw.split(",")[0].strip()
        if not raw:
            print("El .env no tiene PROXY_STICKY_MX ni PROXY_ROTATORY")
            return 2

    hr("0) CADENA DE PRUEBA")
    print(f"   {raw}")

    good = normalize_fix(raw)
    bad = gc_normalize_zip(raw)

    hr("1) URL CORRECTA (la que debe usarse)")
    print(f"   {good}")
    print(f"   salida: {geo(good)}")

    hr("2) URL QUE ARMA EL ZIP HOY (normalizeProxy)")
    print(f"   {bad}")
    print(f"   salida: {geo(bad)}")

    hr("3) STICKY: 3 peticiones seguidas con la MISMA cadena")
    ips = []
    for i in range(3):
        r = geo(good)
        ips.append(r)
        print(f"   peticion {i + 1}: {r}")
    sticky = len(set(ips)) == 1

    hr("4) ROTACION: 3 sesiones distintas (rotador corregido)")
    rot_ips = []
    for _ in range(3):
        r = geo(rotate_fix(good))
        rot_ips.append(r)
        print(f"   {r}")
    rota = len(set(rot_ips)) > 1

    hr("5) ROTADOR DEL ZIP (comerse '_lifetime-10m')")
    rz = gc_rotate(good)
    print(f"   antes   : {good.split('@')[0]}")
    print(f"   despues : {rz.split('@')[0]}")
    lost = "_lifetime" in good and "_lifetime" not in rz
    print(f"   -> '_lifetime' perdido? {lost}")

    hr("6) QUE RECIBE 2CAPTCHA / CAPSOLVER")
    print("   con la URL REAL que arma el zip hoy (normalizeProxy roto):")
    print(f"     {gc_proxy_parts_zip(bad)}")
    print("   con la URL CORRECTA:")
    print(f"     {proxy_parts_fix(good)}")

    hr("VEREDICTO")
    print(f"   proxy usable .................. {geo(good)[:60]}")
    print(f"   IP sticky (misma sesion) ...... {'OK' if sticky else 'NO  <-- rota por peticion, mata Arkose'}")
    print(f"   rotacion por sesion nueva ..... {'OK' if rota else 'NO  <-- la sesion no se renueva'}")
    print(f"   '_lifetime' sobrevive al rotar  {'NO  <-- bug del zip' if lost else 'OK'}")
    zp = gc_proxy_parts_zip(bad)
    fp = proxy_parts_fix(good)
    print(f"   2Captcha recibe proxy valida .. {'OK' if zp == fp else 'NO  <-- el zip manda una proxy rota al solver'}")
    print(f"   URL del zip utilizable ........ {'OK' if bad == good else 'NO  <-- normalizeProxy del zip esta roto'}")

    if not sticky:
        print()
        print("   >>> ACCION: en el panel de smartproxy cambia 'Tiempo: IP diferente cada vez'")
        print("       por una SESION FIJA (10 min / 1 h). Con IP distinta por peticion,")
        print("       Arkose liga el token a la IP y ningun solver puede cerrar el reto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
