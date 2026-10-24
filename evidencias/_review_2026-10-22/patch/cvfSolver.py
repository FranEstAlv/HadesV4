"""Resolucion del reto ACIC/CVF de Amazon sin navegador.

Flujo (capturado de una sesion real):
  1. GET  https://www.amazon.com/aaut/verify/cvf?options=...  -> header amz-aamation-resp
  2. POST https://www.amazon.com/aaut/verify/cvf?options=...  -> HTML del reto
  3. Resolver el reto (PoW en Python; AWS WAF y Arkose via solver externo)
  4. POST https://www.amazon.com/aaut/verify/cvf/<sessionToken>?context=...&response=...
     -> header amz-aamation-resp con actionType=PASS y el sessionToken final
  5. POST <base>/ap/cvf/verify con cvf_aamation_response_token=<token final>
"""
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

logger = logging.getLogger(__name__)

AAC = "https://www.amazon.com"
TWOCAPTCHA_URL = "https://api.2captcha.com"
CAPSOLVER_URL = "https://api.capsolver.com"
ANTICAPTCHA_URL = "https://api.anti-captcha.com"


class CvfError(Exception):
    pass


def _setup_config(html):
    """Extrae la configuracion de acic.setupACIC del HTML de /ap/cvf/."""
    def grab(key):
        m = re.search(r'"%s"\s*:\s*"([^"]*)"' % re.escape(key), html)
        return m.group(1) if m else ""

    cfg = {
        "challenge": grab("data-challenge-type"),
        "locale": grab("data-locale"),
        "externalId": grab("data-external-id"),
        "hostConfig": grab("data-host-config"),
    }
    m = re.search(r'"data-context"\s*:\s*\'([^\']*)\'', html)
    if not m:
        m = re.search(r'"data-context"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if m:
        raw = m.group(1)
        try:
            cfg["context"] = json.loads('"%s"' % raw) if m.group(0).endswith('"') else raw
        except Exception:
            cfg["context"] = raw
    else:
        cfg["context"] = ""
    return cfg


def _options(cfg, challenge):
    data = {
        "clientData": cfg["context"],
        "challengeType": challenge,
        "locale": cfg["locale"] or "es-MX",
        "externalId": cfg["externalId"],
        "enableHeaderFooter": False,
        "enableBypassMechanism": False,
        "enableModalView": False,
        "eventTrigger": None,
        "aaExternalToken": None,
        "forceJsFlush": False,
        "aamationToken": None,
        "pageId": None,
    }
    return json.dumps(data, separators=(",", ":"))


def _headers(referer, base, kind):
    h = {
        "Accept": "*/*",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
        "Referer": base.rstrip("/") + "/",
        "Origin": base,
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if kind == "json":
        h["Content-Type"] = "application/json"
    return h


def _resp_header(resp):
    raw = resp.headers.get("amz-aamation-resp")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _drop_amazon_com_cookies(sess):
    """El XHR del navegador hacia www.amazon.com va sin cookies (cross-origin)."""
    saved = []
    try:
        for ck in list(sess.cookies.jar):
            dom = (ck.domain or "").lstrip(".")
            if dom == "amazon.com" or dom.endswith(".amazon.com"):
                saved.append(ck)
                sess.cookies.jar.clear(ck.domain, ck.path, ck.name)
    except Exception:
        return []
    return saved


def _restore_cookies(sess, saved):
    for ck in saved or []:
        try:
            sess.cookies.jar.set_cookie(ck)
        except Exception:
            pass


def _leading_zero_bits(digest):
    n = 0
    for b in digest:
        if b == 0:
            n += 8
            continue
        n += 8 - b.bit_length()
        break
    return n


def _find_nonce(prefix, bits, start=None):
    n = start if start is not None else 0
    while True:
        d = hashlib.sha256(prefix + str(n).encode()).digest()
        if _leading_zero_bits(d) >= bits:
            return n, d
        n += 1
        if n > 0x7FFFFFFF:
            n = 0


def _probe(probe_cfg):
    """Cada sonda del PoW usa un prefijo aleatorio propio."""
    bits = int(probe_cfg.get("probeDifficultyBits", 10))
    count = int(probe_cfg.get("probeCount", 5))
    timeout_ms = float(probe_cfg.get("probeTimeoutMs", 5000))
    nonces, times = [], []
    t_start = time.perf_counter()
    for i in range(count):
        if (time.perf_counter() - t_start) * 1000.0 >= timeout_ms:
            break
        prefix = ("probe_%d_%d" % (i, int(time.time() * 1000))).encode()
        t = time.perf_counter()
        n, _ = _find_nonce(prefix, bits)
        dur = (time.perf_counter() - t) * 1000.0
        nonces.append(n)
        times.append(max(dur, 0.001))
    if not nonces:
        raise CvfError("probe_failed")
    rate = sum(n + 1 for n in nonces) / max(sum(times), 0.001) * 1000.0
    return {
        "hashRate": rate,
        "probeSolveTimes": [round(t, 3) for t in times],
        "probeNonces": nonces,
    }


def _calibrate(rate, target_ms, min_bits, min_chain, probe_ms):
    """Replica de ChainedProofOfWork.calibrate()."""
    max_bits, max_chains = 48, 250
    available = max(target_ms - probe_ms, 1000.0)
    bits, chain = min_bits, min_chain
    while bits <= max_bits:
        per_hash_ms = (2.0 ** bits) / max(rate, 1.0) * 1000.0
        chain = max(min_chain, int(available / max(per_hash_ms, 1e-9)))
        if chain <= max_chains:
            break
        bits += 1
    return min(bits, max_bits), min(chain, max_chains)


def _solve_pow(html):
    m = re.search(r"encryptedStringEncoded\s*=\s*'([^']+)'", html)
    if not m:
        raise CvfError("pow_seed_not_found")
    seed = unquote(m.group(1))

    def num(name, default):
        mm = re.search(r"const\s+%s\s*=\s*([\d.]+)" % name, html)
        return float(mm.group(1)) if mm else default

    probe_cfg = {"probeDifficultyBits": 10, "probeCount": 5, "probeTimeoutMs": 5000}
    mm = re.search(r"const\s+probeConfig\s*=\s*JSON\.parse\('([^']+)'\)", html)
    if mm:
        try:
            probe_cfg.update(json.loads(mm.group(1).replace('\\"', '"')))
        except Exception:
            pass

    min_bits = int(num("minRequiredBits", 11))
    required_bits = int(num("requiredBits", min_bits))
    min_chain = int(num("minChainCount", 1))
    target_ms = num("targetDurationMs", 1000)

    t_origin = time.perf_counter()
    probe = _probe(probe_cfg)
    probe_ms = (time.perf_counter() - t_origin) * 1000.0

    bits, chain_count = _calibrate(
        float(probe["hashRate"]),
        target_ms,
        max(min_bits, required_bits),
        max(1, min_chain),
        probe_ms,
    )

    nonces, timestamps = [], []
    cur = seed.encode()
    while True:
        elapsed = (time.perf_counter() - t_origin) * 1000.0
        if len(nonces) >= min_chain and elapsed >= target_ms:
            break
        if len(nonces) >= 250:
            break
        n, d = _find_nonce(cur, bits)
        cur = d.hex().encode()
        nonces.append(n)
        timestamps.append(max(1, int((time.perf_counter() - t_origin) * 1000.0)))

    total_ms = max(1, int((time.perf_counter() - t_origin) * 1000.0))
    out = {
        "encryptedString": seed,
        "nonces": nonces,
        "timestamps": timestamps,
        "startTime": int(time.time() * 1000) - total_ms,
        "totalTime": total_ms,
        "probeResult": probe,
        "chosenBits": bits,
        "chosenChainCount": chain_count,
    }
    if len(nonces) < min_chain:
        out["timedOut"] = True
    logger.info(
        "pow solved bits=%s chain=%s nonces=%s total=%sms rate=%.0f",
        bits, chain_count, len(nonces), total_ms, float(probe["hashRate"]),
    )
    return out


def _arkose_config(html):
    """Extrae (publicKey, data) del iframe de Arkose."""
    m = re.search(r"iframe\.arkoselabs\.com/([0-9A-Fa-f-]{36})/index\.html\?data=([^\"'&#\s]+)", html)
    if not m:
        return None, None
    return m.group(1), unquote(m.group(2))


def _capsolver_create(task, timeout=30):
    """Crea una tarea en CapSolver y devuelve el taskId."""
    key = os.getenv("CAPSOLVER_API_KEY", "")
    if not key:
        return None
    import requests as _rq
    try:
        r = _rq.post(f"{CAPSOLVER_URL}/createTask", json={"clientKey": key, "task": task}, timeout=timeout)
        d = r.json() or {}
    except Exception as e:
        logger.warning("capsolver create error: %s", e)
        return None
    if d.get("errorId"):
        logger.warning("capsolver create failed: %s", str(d)[:300])
        return None
    if not d.get("taskId"):
        logger.warning("capsolver create sin taskId: %s", str(d)[:300])
        return None
    return d.get("taskId")


def _capsolver_wait(task_id, tries=50, interval=3):
    """Espera el resultado de una tarea de CapSolver."""
    key = os.getenv("CAPSOLVER_API_KEY", "")
    import requests as _rq
    for _ in range(tries):
        time.sleep(interval)
        try:
            r = _rq.post(f"{CAPSOLVER_URL}/getTaskResult", json={"clientKey": key, "taskId": task_id}, timeout=30)
            d = r.json() or {}
        except Exception as e:
            logger.warning("capsolver poll error: %s", e)
            continue
        if d.get("errorId"):
            logger.warning("capsolver task error: %s", d.get("errorCode"))
            return None
        if d.get("status") == "ready":
            sol = d.get("solution") or {}
            # CapSolver devuelve el token de AWS WAF en "cookie" (JWT) y en
            # otros servicios en "gRecaptchaResponse"/"token".
            token = (sol.get("gRecaptchaResponse") or sol.get("token")
                     or sol.get("awsWafToken") or sol.get("cookie"))
            if token:
                logger.info("capsolver solved (%s)", ",".join(sorted(sol.keys()))[:120])
            else:
                logger.warning("capsolver sin token en la solucion: %s", str(sol)[:200])
            return token
        if d.get("status") == "failed":
            logger.warning("capsolver task failed: %s", str(d)[:300])
            return None
    logger.warning("capsolver timeout task=%s", task_id)
    return None


def _awswaf_config(html):
    """Extrae los datos del reto AWS WAF (grid) de la pagina de ACIC."""
    out = {}
    m = re.search(r'"id"\s*:\s*"([A-Za-z0-9+/=_\-]+)"', html)
    if m:
        out["key"] = m.group(1)
    m = re.search(r'"problem"\s*:\s*"([^"]+)"', html)
    if m:
        out["problem"] = m.group(1)
    m = re.search(r'"visualSolutionsRequired"\s*:\s*(\d+)', html)
    if m:
        out["solutions"] = m.group(1)
    m = re.search(r'src="(https://[^"]*captcha\.awswaf\.com[^"]*\.js)"', html)
    if m:
        out["challenge_js"] = m.group(1)
    m = re.search(r'"data-context"\s*:\s*\'([^\']*)\'', html)
    if not m:
        m = re.search(r'"data-context"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if m:
        out["context"] = m.group(1)
    m = re.search(r'"data-external-id"\s*:\s*"([^"]*)"', html)
    if m:
        out["external_id"] = m.group(1)
    return out


def _awswaf_problem_url(html, domain="amazon.com.mx", locale="es-MX"):
    """Arma la URL del endpoint /problem del AWS WAF (ait.captcha.awswaf.com).

    CapSolver la necesita para descargar el rompecabezas y resolverlo.
    """
    cfg = _awswaf_config(html)
    js = cfg.get("challenge_js") or ""
    if not js or not cfg.get("key"):
        return None
    prefix = js.rsplit("/", 1)[0]
    params = [
        "kind=visual",
        f"domain={quote(domain)}",
        f"locale={quote(locale)}",
    ]
    if cfg.get("problem"):
        params.append(f"problem={quote(cfg['problem'])}")
    if cfg.get("solutions"):
        params.append(f"num_solutions_required={quote(cfg['solutions'])}")
    params.append(f"id={quote(cfg['key'], safe='')}")
    return f"{prefix}/problem?" + "&".join(params)


def _proxy_parts(proxy):
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
    return out


def _capsolver_proxy(proxy):
    """Proxy en el formato de CapSolver para AntiAwsWafTask: http:host:port:user:pass."""
    parts = _proxy_parts(proxy)
    if not parts:
        return None
    if parts.get("proxyLogin"):
        return (f"http:{parts['proxyAddress']}:{parts['proxyPort']}:"
                f"{parts['proxyLogin']}:{parts.get('proxyPassword', '')}")
    return f"http:{parts['proxyAddress']}:{parts['proxyPort']}"


def _solve_with_capsolver(challenge, html, page_url, proxy=None):
    """Token para el reto AWS WAF (grid adversarial) de Amazon via CapSolver.

    El token se pide con nuestra proxy para que la IP coincida con la de la
    sesion (Amazon valida la IP del token).
    """
    cfg = _awswaf_config(html)
    key = cfg.get("key")
    if not key:
        logger.warning("capsolver: no se encontro el awsKey en la pagina del reto")
        return None
    task = {
        "type": "AntiAwsWafTask" if proxy else "AntiAwsWafTaskProxyLess",
        "websiteURL": page_url,
        "awsKey": key,
    }
    problem_url = _awswaf_problem_url(html)
    if problem_url:
        task["awsProblemUrl"] = problem_url
    if cfg.get("context"):
        task["awsContext"] = cfg["context"]
    px = _capsolver_proxy(proxy)
    if px:
        task["proxy"] = px
    logger.info("capsolver aws-waf task key=%s problem=%s proxy=%s problem_url=%s",
                key[:24], cfg.get("problem", "?"), bool(px), bool(problem_url))
    tid = _capsolver_create(task)
    if not tid:
        return None
    return _capsolver_wait(tid)


def _capsolver_arkose_proxy(proxy):
    """Proxy en el formato de CapSolver: {"type","server","username","password"}."""
    parts = _proxy_parts(proxy)
    if not parts:
        return None
    out = {
        "type": "HTTP",
        "server": f"{parts['proxyAddress']}:{parts['proxyPort']}",
    }
    if parts.get("proxyLogin"):
        out["username"] = parts["proxyLogin"]
        out["password"] = parts.get("proxyPassword", "")
    return out


def _solve_with_capsolver_arkose(challenge, html, page_url, user_agent="", proxy=None):
    """Token Arkose/FunCaptcha via CapSolver (FunCaptchaTask).

    El paquete original traia CapSolver como unico solver de Arkose; se
    conserva como respaldo cuando 2Captcha y Anti-Captcha no resuelven.
    """
    if not os.getenv("CAPSOLVER_API_KEY", ""):
        return None
    pub, blob = _arkose_config(html)
    if not pub:
        m = re.search(r'"publicKey"\s*:\s*"([^"]+)"', html)
        pub = m.group(1) if m else None
    if not pub:
        return None
    base = {
        "websiteURL": page_url,
        "websitePublicKey": pub,
    }
    if blob:
        base["data"] = json.dumps({"blob": blob}, separators=(",", ":"))
    if user_agent:
        base["userAgent"] = user_agent
    px = _capsolver_arkose_proxy(proxy)
    variants = [dict(base, type="FunCaptchaTaskProxyless")]
    if px:
        variants.append(dict(base, type="FunCaptchaTask", proxy=px))
    for task in variants:
        tid = _capsolver_create(task)
        if not tid:
            continue
        token = _capsolver_wait(tid)
        if token:
            return token
    return None


def _run_2captcha_task(_rq, task, key):
    """Crea la tarea en 2Captcha y espera el token."""
    try:
        r = _rq.post(f"{TWOCAPTCHA_URL}/createTask", json={"clientKey": key, "task": task}, timeout=30)
        d = r.json() or {}
        tid = d.get("taskId")
        if not tid:
            logger.warning("2captcha create failed: %s", str(d)[:300])
            return None
    except Exception as e:
        logger.warning("2captcha create error: %s", e)
        return None
    for _ in range(50):
        time.sleep(3)
        try:
            r = _rq.post(f"{TWOCAPTCHA_URL}/getTaskResult", json={"clientKey": key, "taskId": tid}, timeout=30)
            d = r.json() or {}
        except Exception as e:
            logger.warning("2captcha poll error: %s", e)
            continue
        if d.get("errorCode"):
            logger.warning("2captcha task error: %s (type=%s)", d.get("errorCode"), task.get("type"))
            return None
        if d.get("status") == "ready":
            sol = d.get("solution") or {}
            return sol.get("token") or sol.get("gRecaptchaResponse")
        if d.get("status") == "failed":
            logger.warning("2captcha task failed: %s", str(d)[:300])
            return None
    logger.warning("2captcha timeout task=%s", tid)
    return None


def _solve_with_2captcha(challenge, html, page_url, user_agent="", proxy=None):
    """Token para ARKOSE_* via 2Captcha (FunCaptchaTask).

    Con proxy: el token se resuelve desde la misma IP de la sesion, que es
    lo que Amazon valida (si el token viene de otra IP se rechaza).
    """
    key = os.getenv("TWO_CAPTCHA_API_KEY", "")
    if not key:
        return None
    pub, blob = _arkose_config(html)
    if not pub:
        m = re.search(r'"publicKey"\s*:\s*"([^"]+)"', html)
        pub = m.group(1) if m else None
    if not pub:
        return None
    import requests as _rq
    proxy_fields = _proxy_parts(proxy)
    base = {
        "websiteURL": page_url,
        "websitePublicKey": pub,
    }
    if blob:
        base["data"] = json.dumps({"blob": blob}, separators=(",", ":"))
    if user_agent:
        base["userAgent"] = user_agent

    # Se prueban las dos variantes: sin proxy (pool del solver) y con nuestra
    # proxy (misma IP que la sesion). Amazon a veces solo acepta el token si
    # el IP coincide y a veces al reves.
    variants = [("proxyless", dict(base, type="FunCaptchaTaskProxyless"))]
    if proxy_fields:
        variants.append(("proxy", dict(base, type="FunCaptchaTask", **proxy_fields)))

    for label, task in variants:
        token = _run_2captcha_task(_rq, task, key)
        if token:
            return token
        logger.warning("2captcha (%s) no pudo resolver el reto; se prueba la otra variante", label)
    return None


_anti_credit = {"checked": 0.0, "value": 0.0}


def _anticaptcha_has_credit(ttl=600):
    """True si Anti-Captcha tiene saldo (cacheado 10 min).

    Evita gastar un round-trip en cada reto de Arkose cuando la cuenta esta
    en ceros (ERROR_ZERO_BALANCE).
    """
    key = os.getenv("ANTICAPTCHA_API_KEY", "")
    if not key:
        return False
    now = time.time()
    if now - _anti_credit["checked"] < ttl:
        return _anti_credit["value"] > 0
    import requests as _rq
    _anti_credit["checked"] = now
    try:
        r = _rq.post(f"{ANTICAPTCHA_URL}/getBalance", json={"clientKey": key}, timeout=15)
        d = r.json() or {}
    except Exception:
        return False
    try:
        _anti_credit["value"] = float(d.get("balance") or 0)
    except (TypeError, ValueError):
        _anti_credit["value"] = 0.0
    if _anti_credit["value"] <= 0:
        logger.warning("anticaptcha: sin saldo (%.2f); se omite como respaldo de Arkose",
                       _anti_credit["value"])
    return _anti_credit["value"] > 0


def _solve_with_anticaptcha(challenge, html, page_url, user_agent="", proxy=None):
    """Token Arkose/FunCaptcha via Anti-Captcha (task type FunCaptchaTask).

    2Captcha devuelve ERROR_CAPTCHA_UNSOLVABLE en los niveles de Amazon;
    Anti-Captcha tiene el mismo producto con otro tipo de tarea.
    """
    key = os.getenv("ANTICAPTCHA_API_KEY", "")
    if not key:
        return None
    if not _anticaptcha_has_credit():
        return None
    pub, blob = _arkose_config(html)
    if not pub:
        m = re.search(r'"publicKey"\s*:\s*"([^"]+)"', html)
        pub = m.group(1) if m else None
    if not pub:
        return None
    import requests as _rq
    task = {
        "type": "FunCaptchaTask" if proxy else "FunCaptchaTaskProxyless",
        "websiteURL": page_url,
        "websitePublicKey": pub,
    }
    if blob:
        task["data"] = json.dumps({"blob": blob}, separators=(",", ":"))
    if user_agent:
        task["userAgent"] = user_agent
    pf = _proxy_parts(proxy)
    if pf:
        task["proxyType"] = "http"
        task["proxyAddress"] = pf["proxyAddress"]
        task["proxyPort"] = pf["proxyPort"]
        if pf.get("proxyLogin"):
            task["proxyLogin"] = pf["proxyLogin"]
            task["proxyPassword"] = pf.get("proxyPassword", "")
    body = {"clientKey": key, "task": task}
    soft = os.getenv("ANTICAPTCHA_SOFT_ID", "").strip()
    if soft.isdigit():
        body["softId"] = int(soft)
    try:
        r = _rq.post(f"{ANTICAPTCHA_URL}/createTask", json=body, timeout=30)
        d = r.json() or {}
    except Exception as e:
        logger.warning("anticaptcha create error: %s", e)
        return None
    tid = d.get("taskId")
    if not tid:
        logger.warning("anticaptcha create failed: %s", str(d)[:300])
        return None
    for _ in range(50):
        time.sleep(3)
        try:
            r = _rq.post(f"{ANTICAPTCHA_URL}/getTaskResult",
                         json={"clientKey": key, "taskId": tid}, timeout=30)
            d = r.json() or {}
        except Exception as e:
            logger.warning("anticaptcha poll error: %s", e)
            continue
        if d.get("errorId"):
            logger.warning("anticaptcha task error: %s", d.get("errorCode"))
            return None
        if d.get("status") == "ready":
            sol = d.get("solution") or {}
            return sol.get("token") or sol.get("gRecaptchaResponse")
        if d.get("status") == "failed":
            logger.warning("anticaptcha task failed: %s", str(d)[:300])
            return None
    logger.warning("anticaptcha timeout task=%s", tid)
    return None


def _solve_challenge(challenge, html, page_url, proxy, user_agent=""):
    if challenge.startswith("PROOF_OF_WORK"):
        return _solve_pow(html)
    # El grid adversarial de Amazon es AWS WAF: lo resuelve CapSolver.
    # Arkose/FunCaptcha lo resuelven 2Captcha y Anti-Captcha.
    if "WAF" in challenge.upper() or "AWS" in challenge.upper():
        token = _solve_with_capsolver(challenge, html, page_url, proxy)
        if token:
            return token
        logger.warning("capsolver no pudo resolver %s; se intenta 2captcha", challenge)
    token = _solve_with_2captcha(challenge, html, page_url, user_agent, proxy)
    if token:
        return token
    token = _solve_with_anticaptcha(challenge, html, page_url, user_agent, proxy)
    if token:
        return token
    token = _solve_with_capsolver_arkose(challenge, html, page_url, user_agent, proxy)
    if token:
        return token
    raise CvfError(f"unsupported_challenge:{challenge}")


def _build_response(challenge, solved, variant=0):
    if isinstance(solved, str):
        if variant == 0:
            data = json.dumps(solved)
        elif variant == 1:
            data = json.dumps({"token": solved}, separators=(",", ":"))
        else:
            data = json.dumps({"sessionToken": solved}, separators=(",", ":"))
    else:
        data = json.dumps(solved, separators=(",", ":"))
    return json.dumps({"challengeType": challenge, "data": data}, separators=(",", ":"))


def _form_fields(html):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="cvf-aamation-challenge-form") or soup.find("form", action="verify")
    if not form:
        raise CvfError("cvf_form_not_found")
    fields = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if name:
            fields[name] = inp.get("value", "")
    action = form.get("action", "verify")
    return action, fields


def solveCvf(sess, cvf_resp, challenge, base):
    """Resuelve el reto y devuelve la respuesta de /ap/cvf/verify."""
    cfg = _setup_config(cvf_resp.text)
    if not cfg["externalId"] or not cfg["context"]:
        raise CvfError("cvf_config_not_found")
    challenge = challenge or cfg["challenge"]
    options = _options(cfg, challenge)
    referer = str(cvf_resp.url)
    _ua = ""
    try:
        _ua = (sess.headers or {}).get("User-Agent", "") or ""
    except Exception:
        pass
    if not _ua:
        try:
            _ua = cvf_resp.request.headers.get("User-Agent", "") or ""
        except Exception:
            pass
    proxy = None
    try:
        proxy = (sess.proxies or {}).get("https")
    except Exception:
        pass

    hdr = _headers(referer, base, "json")
    _saved_cookies = _drop_amazon_com_cookies(sess)
    final = None
    r3 = None
    try:
        r1 = sess.get(f"{AAC}/aaut/verify/cvf", params={"options": options}, headers=hdr, timeout=45)
        info = _resp_header(r1)
        if not info or not info.get("sessionToken"):
            raise CvfError(f"aaut_init_failed:{r1.status_code}")
        token = info["sessionToken"]
        ctx = info.get("clientSideContext", "")

        page_html = r1.text or ""
        logger.info("aaut r1 status=%s len=%s pow=%s", r1.status_code, len(page_html), "runChainedPoW" in page_html)
        if "runChainedPoW" not in page_html:
            body = {"context": None, "options": options, "response": "null", "fwcimBlob": None}
            r2 = sess.post(f"{AAC}/aaut/verify/cvf", params={"options": options},
                           data=json.dumps(body, separators=(",", ":")), headers=hdr, timeout=45)
            if r2.status_code != 200:
                raise CvfError(f"aaut_challenge_failed:{r2.status_code}")
            page_html = r2.text or ""
            logger.info("aaut r2 status=%s len=%s pow=%s", r2.status_code, len(page_html), "runChainedPoW" in page_html)
        if not challenge.startswith("PROOF_OF_WORK"):
            try:
                open(f"/tmp/cvf_r2_{challenge}.html", "w", encoding="utf-8").write(page_html)
            except Exception:
                pass
        if challenge.startswith("PROOF_OF_WORK") and "runChainedPoW" not in page_html:
            try:
                open("/tmp/cvf_r2_failed.html", "w", encoding="utf-8").write(page_html)
            except Exception:
                pass
            raise CvfError("pow_page_incomplete")

        current = challenge
        current_html = page_html
        solved = _solve_challenge(current, current_html, referer, proxy, _ua)
        variant = 0
        steps = 0
        r3 = None
        final = None
        while True:
            steps += 1
            if steps > 10:
                raise CvfError("too_many_challenges")
            response = _build_response(current, solved, variant)
            verify_url = f"{AAC}/aaut/verify/cvf/{quote(token, safe='')}"
            params = {"context": ctx, "options": options, "response": response}
            vbody = {"context": ctx, "options": options, "response": response, "fwcimBlob": None}
            _body3 = json.dumps(vbody, separators=(",", ":"))
            r3 = sess.post(verify_url, params=params, data=_body3, headers=hdr, timeout=45)
            final = _resp_header(r3)
            at = (final or {}).get("actionType")
            if at == "PASS":
                break
            if not at:
                if isinstance(solved, str) and variant < 2:
                    variant += 1
                    logger.warning("cvf retry arkose variant=%s (status=%s)", variant, r3.status_code)
                    continue
                raise CvfError(f"aaut_verify_failed:{r3.status_code}:no_action")
            if at == current:
                if isinstance(solved, str) and variant < 2:
                    variant += 1
                    logger.warning("cvf retry arkose variant=%s action=%s", variant, at)
                    continue
                raise CvfError(f"challenge_rejected:{at}")
            logger.warning("cvf next challenge=%s (was %s)", at, current)
            token = final.get("sessionToken") or token
            ctx = final.get("clientSideContext") or ctx
            current = at
            current_html = r3.text or ""
            if not current_html:
                raise CvfError(f"aaut_next_empty:{at}")
            solved = _solve_challenge(current, current_html, referer, proxy, _ua)
            variant = 0
    finally:
        _restore_cookies(sess, _saved_cookies)
    if not final or final.get("actionType") != "PASS":
        logger.warning("cvf aaut verify rejected: status=%s header=%s", r3.status_code, final)
        try:
            open("/tmp/cvf_r3_failed.html", "w", encoding="utf-8").write(r3.text or "")
        except Exception:
            pass
        raise CvfError(f"aaut_verify_failed:{r3.status_code}:{(final or {}).get('actionType')}")
    final_token = final.get("sessionToken")
    if not final_token:
        raise CvfError("aaut_no_session_token")

    action, fields = _form_fields(cvf_resp.text)
    fields["cvf_aamation_response_token"] = final_token
    fields.setdefault("cvf_aamation_error_code", "")
    action = action if action.startswith("http") else urljoin(referer, action)
    vhdr = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": referer,
        "Origin": base,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-User": "?1",
    }
    return sess.post(action, data=fields, headers=vhdr, timeout=60)
