"""Genera el parche de correccion del proxy (para la maquina 2).

Aplica 3 correcciones sobre la copia extraida del zip y escribe un .patch
unificado que se puede aplicar en la maquina 2 con `patch -p1` o `git apply`.

  1. accountCreator.normalizeProxy  -> arma http://user:pass@host:port
  2. accountCreator.rotateProxySession -> no se come _lifetime / _streaming
  3. cvfSolver._proxy_parts          -> entiende host:port@user:pass
"""
import difflib
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "GenCookie"
OUT = BASE / "patch"
OUT.mkdir(exist_ok=True)

# --------------------------------------------------------------- 1 y 2
ac = (SRC / "accountCreator.py").read_text(encoding="utf-8")

old_import = "from urllib .parse import urljoin ,unquote ,quote ,quote_plus"
new_import = "from urllib .parse import urljoin ,unquote ,quote ,quote_plus ,urlparse"
assert old_import in ac
ac = ac.replace(old_import, new_import, 1)

old_norm = """def normalizeProxy (proxy ):
    \"\"\"Normaliza la proxy al formato http://usuario:pass@host:puerto.\"\"\"
    if not proxy :return None
    proxy =proxy .strip ()
    if '://'in proxy :return proxy
    if '@'in proxy :return f'http://{proxy }'
    parts =proxy .split (':')
    if len (parts )>=4 and parts [1 ].isdigit ():
        return f'http://{parts [2 ]}:{":".join (parts [3 :])}@{parts [0 ]}:{parts [1 ]}'
    return f'http://{proxy }'"""

new_norm = """def normalizeProxy (proxy ):
    \"\"\"Normaliza la proxy al formato http://usuario:pass@host:puerto.

    Acepta los tres formatos que hay en circulacion:
      - host:port@usuario:pass[_params]   (formato IPRoyal del .env)
      - host:port:usuario:pass             (cuatro campos separados por ':')
      - scheme://usuario:pass@host:port    (ya normalizada)

    El formato IPRoyal trae los parametros (_country-mx, _session-xxx,
    _lifetime-3m) dentro de la CONTRASENA, y el orden host:port@user:pass
    invertido hace que curl rechace la URL con \"Unsupported proxy syntax\".
    \"\"\"
    if not proxy :return None
    proxy =proxy .strip ()
    if '://'in proxy :
        try :
            u =urlparse (proxy )
            if not u .username or not u .hostname or not u .port :return proxy
            return f'{u .scheme }://{u .username }:{u .password or ""}@{u .hostname }:{u .port }'
        except Exception :
            return proxy
    if '@'in proxy :
        host_port ,creds =proxy .split ('@',1)
        return f'http://{creds }@{host_port }'
    parts =proxy .split (':')
    if len (parts )>=4 and parts [1 ].isdigit ():
        return f'http://{parts [2 ]}:{":".join (parts [3 :])}@{parts [0 ]}:{parts [1 ]}'
    return f'http://{proxy }'"""

assert old_norm in ac, "no se encontro normalizeProxy"
ac = ac.replace(old_norm, new_norm, 1)

old_rot = """def rotateProxySession (proxy ):
    \"\"\"Genera una nueva sesion de proxy para cambiar IP dentro del mismo pais.\"\"\"
    if not proxy :return proxy
    session_id =secrets .token_hex (8 )
    proxy =re .sub (r'_session-[A-Za-z0-9_-]+',f'_session-{session_id }',proxy )
    return re .sub (r'-session-[A-Za-z0-9_-]+',f'-session-{session_id }',proxy )"""

new_rot = """def rotateProxySession (proxy ):
    \"\"\"Genera una nueva sesion de proxy para cambiar IP dentro del mismo pais.

    Solo se reemplaza el identificador de sesion. La version anterior usaba
    una clase de caracteres gulosa y se comia '_lifetime-3m' y '_streaming-1',
    dejando la proxy sin lifetime para toda la sesion.
    \"\"\"
    if not proxy :return proxy
    session_id =secrets .token_hex (8)
    proxy =re .sub (r'(_session-)[A-Za-z0-9]+',lambda m :f'{m .group (1 )}{session_id }',proxy )
    return re .sub (r'(-session-)[A-Za-z0-9]+',lambda m :f'{m .group (1 )}{session_id }',proxy )"""

assert old_rot in ac, "no se encontro rotateProxySession"
ac = ac.replace(old_rot, new_rot, 1)

# --------------------------------------------------------------- 3
cs = (SRC / "cvfSolver.py").read_text(encoding="utf-8")
cs = cs.replace(
    "from urllib.parse import quote, unquote, urljoin",
    "from urllib.parse import quote, unquote, urljoin, urlparse",
    1,
)

old_parts = '''def _proxy_parts(proxy):
    """Convierte una cadena de proxy en los campos que espera 2Captcha."""
    if not proxy:
        return None
    m = re.match(
        r"^(?:(?P<scheme>https?)://)?"
        r"(?:(?P<user>[^:@/]+):(?P<pwd>[^@/]*)@)?"
        r"(?P<host>[^:/@]+)(?::(?P<port>\\d+))?",
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
    return out'''

new_parts = '''def _proxy_parts(proxy):
    """Convierte una cadena de proxy en los campos que espera 2Captcha.

    Acepta 'scheme://user:pass@host:port' y 'host:port@user:pass' (IPRoyal).
    La version anterior asumia siempre el primer formato y, con el segundo,
    mandaba al solver un host inexistente -> ERROR_CAPTCHA_UNSOLVABLE.
    """
    if not proxy:
        return None
    p = proxy.strip()
    if "://" not in p:
        if "@" in p:
            host_port, creds = p.split("@", 1)
            p = "http://" + creds + "@" + host_port
        else:
            p = "http://" + p
    try:
        u = urlparse(p)
        if not u.hostname or not u.port:
            return None
    except Exception:
        return None
    out = {
        "proxyType": "http",
        "proxyAddress": u.hostname,
        "proxyPort": int(u.port),
    }
    if u.username:
        out["proxyLogin"] = u.username
        out["proxyPassword"] = u.password or ""
    return out'''

assert old_parts in cs, "no se encontro _proxy_parts"
cs = cs.replace(old_parts, new_parts, 1)

# --------------------------------------------------------------- salida
(OUT / "accountCreator.py").write_text(ac, encoding="utf-8")
(OUT / "cvfSolver.py").write_text(cs, encoding="utf-8")

chunks = []
for name in ("accountCreator.py", "cvfSolver.py"):
    old = (SRC / name).read_text(encoding="utf-8").splitlines(keepends=True)
    new = (OUT / name).read_text(encoding="utf-8").splitlines(keepends=True)
    chunks.extend(
        difflib.unified_diff(
            old, new,
            fromfile=f"a/GenCookie/{name}",
            tofile=f"b/GenCookie/{name}",
            n=3,
        )
    )

patch = "".join(chunks)
(OUT / "fix-proxy.patch").write_text(patch, encoding="utf-8")
print("patch escrito en", OUT / "fix-proxy.patch", f"({len(patch)} bytes)")

# --------------------------------------------------------------- validacion
import importlib.util
import sys

sys.path.insert(0, str(SRC))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"  (no se pudo importar {name}: {e})")
        return None
    return mod


print()
print("=== validacion de las funciones parcheadas ===")
raw = "geo.iproyal.com:12321@OlimpoMx:6060544328_country-mx_session-pfpVjrT4_lifetime-3m"

src_txt = (OUT / "accountCreator.py").read_text(encoding="utf-8")
ns = {}
exec(
    compile(
        "\n".join(
            l for l in src_txt.splitlines()
            if not l.startswith(("import ", "from ")) and "load_dotenv" not in l
        ).replace("def ", "def "),
        "<patched>",
        "exec",
    ),
    ns,
) if False else None

# validacion directa de la logica (sin importar el modulo completo)
from urllib.parse import urlparse  # noqa: E402


def normalize_new(proxy):
    proxy = proxy.strip()
    if "://" in proxy:
        u = urlparse(proxy)
        if not u.username or not u.hostname or not u.port:
            return proxy
        return f'{u.scheme}://{u.username}:{u.password or ""}@{u.hostname}:{u.port}'
    if "@" in proxy:
        host_port, creds = proxy.split("@", 1)
        return f"http://{creds}@{host_port}"
    parts = proxy.split(":")
    if len(parts) >= 4 and parts[1].isdigit():
        return f'http://{parts[2]}:{":".join(parts[3:])}@{parts[0]}:{parts[1]}'
    return f"http://{proxy}"


print(f"  normalizeProxy('{raw}')")
print(f"    -> {normalize_new(raw)}")
rot = re.sub(r"(_session-)[A-Za-z0-9]+", r"\g<1>abc123def456", normalize_new(raw))
print(f"  rotateProxySession -> {rot}")
print(f"    -> '_lifetime-3m' conservado? {'_lifetime-3m' in rot}")
print(f"    -> '_country-mx' conservado?  {'_country-mx' in rot}")
