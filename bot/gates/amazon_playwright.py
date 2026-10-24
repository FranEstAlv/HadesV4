"""Proveedor de sesión de Amazon MX con Playwright local (Chromium real).

POR QUÉ PLAYWRIGHT LOCAL Y NO BROWSERLESS
-----------------------------------------
Browserless funciona (ver `bot/gates/amazon_browserless.py`) pero tiene tres
límites para este gate:

1. **La sesión de Browserless está topada a 120 s** por el plan: el login +
   el entry de Prime apenas cabe y hay que recortar la query (no se puede
   traer el HTML completo de la página de Prime, que pesa ~1 MB).
2. **Cada corrida es un navegador nuevo**: no se puede reutilizar ni
   encadenar pasos (p. ej. login → CVF → Prime → tarjeta).
3. **Gasta unidades** por cada corrida, y el gate es de alto volumen.

Con Playwright local se resuelve todo:

- Un **único Chromium reutilizado** para todo el proceso (una sesión de
  login = un `context` nuevo, no un navegador nuevo).
- Se puede hacer **todo el flujo en el navegador**: login → CVF → entry de
  Prime, y de ahí sale el `ppw-widgetState` + `customerID`.
- La IP es la **residencial MX del pool IPRoyal** (`PROXY_STICKY_MX`), que es
  lo que Amazon acepta: desde la IP del VPS (datacenter) responde 503.
- Cero unidades de Browserless.

COSTO EN LA VPS (medido el 2026-10-21, Chromium headless + driver):
- Chromium: 6 procesos, ~257 MB RSS.
- Driver node de Playwright: ~120 MB.
- Árbol del bot con el navegador abierto: ~407 MB (el bot solo ~197 MB).
- Con `--disable-dev-shm-usage` no se toca /dev/shm (tmpfs de 3.9 GB).

Por eso el navegador:
- se lanza **perezoso** (solo cuando hace falta),
- se **reutiliza** entre verificaciones,
- se **cierra solo** tras `IDLE_CLOSE_S` sin uso (para devolver la RAM),
- y las sesiones simultáneas están limitadas por un **semáforo**.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
# Segundos sin uso tras los cuales se cierra el Chromium (devuelve la RAM).
IDLE_CLOSE_S = int(os.getenv("AMAZON_PW_IDLE_CLOSE_S", "120"))
# Segundos máximos de vida del navegador (se recicla para no acumular memoria).
MAX_BROWSER_AGE_S = int(os.getenv("AMAZON_PW_MAX_AGE_S", "900"))
# Sesiones simultáneas (contexts) permitidas.
MAX_CONCURRENCY = int(os.getenv("AMAZON_PW_MAX_CONCURRENCY", "2"))
# Reintentos de la sesión completa (el pool de proxies IPRoyal es intermitente:
# se midió ERR_TUNNEL_CONNECTION_FAILED con uno de cada ~3 intentos).
SESSION_RETRIES = int(os.getenv("AMAZON_PW_RETRIES", "3"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=Translate,BackForwardCache,AcceptCHFrame",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--mute-audio",
]

PRIME_ENTRY_URL = (
    "https://www.amazon.com.mx/hp/wlp/pipeline/membersignup?campaignId=SlashPrime"
)

# Muros (texto visible) para clasificar, sin adivinar.
_WALL_SUSPENDED = (
    "tu cuenta de amazon está suspendida",
    "your account has been suspended",
    "revoke-account-suspension",
    "account-status.amazon",
)
_WALL_CVF = (
    "confirma tu identidad",
    "verifica el número de celular",
    "verifica el numero de celular",
    "código de seguridad que te enviamos",
    "demasiadas otp",
    "too many otp",
)
_WALL_OTP = (
    "código de verificación",
    "ingresa los caracteres",
    "escribe los caracteres",
)
_WALL_BAD_CREDENTIALS = (
    "contraseña incorrecta",
    "incorrect password",
    "clave incorrecta",
)
_WALL_WAF = (
    "api-services-support@amazon.com",
    "to discuss automated access to amazon data",
)


class PlaywrightError(RuntimeError):
    """Fallo de Playwright (no de negocio)."""


# ---------------------------------------------------------------------------
# Proxies
# ---------------------------------------------------------------------------
@dataclass
class ProxyEntry:
    """Un proxy del pool, en los dos formatos que hace falta."""

    play: Dict[str, str]          # para Playwright
    curl: Optional[Dict[str, str]]  # para curl_cffi (misma IP)


_POOL_CACHE: Optional[List[ProxyEntry]] = None
_POOL_CACHE_TS: float = 0.0
_POOL_TTL_S = 60.0  # menor que el lifetime sticky (2m) del .env


def _proxy_pool() -> List[ProxyEntry]:
    """Genera un lote fresco de proxies sticky MX con lifetime.

    Se regenera en cada flujo (ver `_pool`): las sesiones IPRoyal caducan y
    reutilizar una entrada vieja produce timeouts silenciosos.
    """
    entries: List[str] = []
    try:
        from bot.config import gate_proxies

        entries = gate_proxies("mx", 6)
    except Exception:
        pass
    if not entries:
        raw = os.getenv("PROXY_STICKY_MX", "")
        entries = [
            p.strip()
            for p in raw.replace('"', "").replace("'", "").replace("\n", ",").split(",")
            if p.strip()
        ]
    if not entries:
        return []

    pool: List[ProxyEntry] = []
    seen = set()
    for entry in entries:
        if not entry:
            continue
        if not entry.startswith("http://") and not entry.startswith("https://"):
            entry = "http://" + entry
        parsed = urlparse(entry)
        if not parsed.hostname or not parsed.port:
            continue
        key = f"{parsed.hostname}:{parsed.port}:{parsed.username}:{parsed.password}"
        if key in seen:
            continue
        seen.add(key)

        play = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            play["username"] = parsed.username
        if parsed.password:
            play["password"] = parsed.password

        curl: Optional[Dict[str, str]] = None
        if parsed.username and parsed.password:
            url = f"{parsed.scheme}://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}"
        else:
            url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        curl = {"http": url, "https": url}

        pool.append(ProxyEntry(play=play, curl=curl))

    if pool:
        random.shuffle(pool)
    return pool


def _pool() -> List[ProxyEntry]:
    """Pool de proxies sticky MX, refrescado antes de que caduque la sesión."""
    global _POOL_CACHE, _POOL_CACHE_TS
    if _POOL_CACHE is None or time.time() - _POOL_CACHE_TS > _POOL_TTL_S:
        _POOL_CACHE = _proxy_pool()
        _POOL_CACHE_TS = time.time()
    return _POOL_CACHE


def _probe_proxy(entry: ProxyEntry, timeout: int = 12) -> bool:
    """Comprueba si un proxy sirve para Amazon (HEAD barato).

    IMPORTANTE: la sesión se cierra siempre. Las sesiones sticky de IPRoyal
    admiten una sola conexión a la vez: si la sonda deja el socket abierto,
    el Chromium que viene después recibe `ERR_TUNNEL_CONNECTION_FAILED`.
    """
    session = None
    try:
        from curl_cffi import requests as curl_requests

        session = curl_requests.Session(impersonate="chrome150")
        response = session.get(
            "https://www.amazon.com.mx/ap/signin",
            headers={"User-Agent": USER_AGENT},
            proxies=entry.curl,
            timeout=timeout,
        )
        # 503 = Amazon (o el proxy) no está sirviendo: NO cuenta como operativo.
        # Con 503 el Chromium igual fallaría en el goto, así que es mejor no
        # gastarlo.
        return response.status_code in (200, 202, 404)
    except Exception:
        return False
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


# Cuando el pool se cae, no se vuelve a sondar en cada intento: se espera
# este tiempo antes de volver a comprobarlo (así un lote no gasta 30 s
# sondeando proxies muertos).
POOL_DEAD_COOLDOWN_S = int(os.getenv("AMAZON_PW_POOL_DEAD_COOLDOWN_S", "90"))
_pool_dead_until: float = 0.0


def pool_is_dead() -> bool:
    return time.time() < _pool_dead_until


def find_working_proxy(max_tries: int = 3) -> Optional[ProxyEntry]:
    """Devuelve el primer proxy del pool que responde.

    Se usa ANTES de lanzar Chromium: el pool de IPRoyal se cae por periods
    (se midió `CONNECT tunnel failed, response 500` en las 50 entradas el
    2026-10-21) y abrir un navegador para que falle en el `goto` tira ~400 MB
    de RAM y ~15 s para nada.
    """
    global _pool_dead_until

    if pool_is_dead():
        return None

    pool = _pool()
    if not pool:
        return None
    for entry in pool[:max_tries]:
        if _probe_proxy(entry):
            logger.info("Playwright: proxy operativo encontrado en el pool MX")
            _pool_dead_until = 0.0
            return entry
    logger.warning(
        "Playwright: ningún proxy del pool responde (%d probados)",
        min(max_tries, len(pool)),
    )
    _pool_dead_until = time.time() + POOL_DEAD_COOLDOWN_S
    return None


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------
@dataclass
class BrowserSession:
    """Sesión de Prime abierta en el Chromium local."""

    status: str = "unknown"      # ok | cvf | otp | suspended | bad_credentials | waf | error
    detail: str = ""
    cookie: str = ""
    cookies: Dict[str, str] = field(default_factory=dict)
    widget_state: str = ""
    customer_id: str = ""
    final_url: str = ""
    account: str = ""
    proxy: Optional[Dict[str, str]] = None   # mismo proxy (misma IP) para los POST

    @property
    def usable(self) -> bool:
        return self.status == "ok" and bool(self.widget_state) and bool(self.customer_id)


# ---------------------------------------------------------------------------
# Gestor del navegador (singleton, reutilizado, cierre por inactividad)
# ---------------------------------------------------------------------------
class BrowserManager:
    """Un solo Chromium para todo el proceso.

    - `session()`: limita concurrencia y garantiza el cierre del context.
    - `close()`: cierra el navegador (libera la RAM).
    - watchdog: cierra el navegador si pasa `IDLE_CLOSE_S` sin uso.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._proxy: Optional[ProxyEntry] = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        self._last_use = 0.0
        self._created_at = 0.0
        self._sessions = 0
        self._failures = 0
        self._watchdog: Optional[asyncio.Task] = None

    # -- estado ----------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._browser is not None

    @property
    def current_proxy(self) -> Optional[ProxyEntry]:
        return self._proxy

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "sessions": self._sessions,
            "failures": self._failures,
            "max_concurrency": MAX_CONCURRENCY,
        }

    # -- ciclo de vida ----------------------------------------------------
    async def _ensure_browser(self) -> Tuple[object, Optional[ProxyEntry]]:
        async with self._lock:
            if self._browser is not None:
                age = time.time() - self._created_at
                if age < MAX_BROWSER_AGE_S:
                    self._last_use = time.time()
                    return self._browser, self._proxy
                logger.info("Playwright: navegador viejo (%.0f s), se recicla", age)
                await self._close_locked()

            if self._playwright is None:
                try:
                    from playwright.async_api import async_playwright
                except ImportError as exc:  # pragma: no cover
                    raise PlaywrightError("Playwright no está instalado") from exc
                self._playwright = await async_playwright().start()

            pool = _pool()
            # Se busca un proxy que responder de verdad ANTES de abrir el
            # navegador: si el pool está caído, no se gasta RAM en un
            # Chromium que va a fallar en el primer goto.
            working = find_working_proxy()
            if pool and working is None:
                raise PlaywrightError(
                    "Ningún proxy del pool MX responde; el Chromium no se lanza."
                )
            self._proxy = working or (pool[0] if pool else None)
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    proxy=self._proxy.play if self._proxy else None,
                    args=CHROMIUM_ARGS,
                    timeout=45_000,
                )
            except Exception as exc:
                logger.error("Playwright: no se pudo lanzar Chromium: %s", exc)
                raise PlaywrightError(f"No se pudo lanzar Chromium: {exc}") from exc

            self._created_at = time.time()
            self._last_use = time.time()
            logger.info(
                "Playwright: Chromium lanzado (proxy=%s, concurrencia máx=%d)",
                "pool MX" if self._proxy else "directo",
                MAX_CONCURRENCY,
            )
            self._start_watchdog()
            return self._browser, self._proxy

    async def _close_locked(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._proxy = None

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

    def _start_watchdog(self) -> None:
        if self._watchdog is not None:
            return

        async def _watch() -> None:
            try:
                while True:
                    await asyncio.sleep(20)
                    if self._browser is None:
                        return
                    if time.time() - self._last_use > IDLE_CLOSE_S:
                        logger.info(
                            "Playwright: navegador sin uso por %d s, se cierra (libera RAM)",
                            IDLE_CLOSE_S,
                        )
                        async with self._lock:
                            await self._close_locked()
                        return
            except asyncio.CancelledError:
                return

        self._watchdog = asyncio.create_task(_watch())

    # -- uso -------------------------------------------------------------
    def session(self):
        """Context manager: limita concurrencia y garantiza el cierre."""

        manager = self

        class _Ctx:
            def __init__(self):
                self.ctx = None
                self.proxy = None

            async def __aenter__(self):
                await manager._semaphore.acquire()
                try:
                    browser, proxy = await manager._ensure_browser()
                    manager._sessions += 1
                    manager._last_use = time.time()
                    self.proxy = proxy
                    self.ctx = await browser.new_context(
                        locale="es-MX",
                        timezone_id="America/Mexico_City",
                        viewport={"width": 1366, "height": 900},
                        user_agent=USER_AGENT,
                        extra_http_headers={"Accept-Language": "es-MX,es;q=0.9,en;q=0.8"},
                    )
                    return self.ctx
                except Exception:
                    manager._semaphore.release()
                    raise

            async def __aexit__(self, exc_type, exc, tb):
                if self.ctx is not None:
                    try:
                        await self.ctx.close()
                    except Exception:
                        pass
                    self.ctx = None
                manager._last_use = time.time()
                manager._semaphore.release()

        return _Ctx()


MANAGER = BrowserManager()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
async def _body_text(page) -> str:
    try:
        return await page.evaluate(
            "() => (document.body && document.body.innerText || '').slice(0, 6000)"
        )
    except Exception:
        return ""


def _classify(url: str, text: str) -> Tuple[str, str]:
    url_low = (url or "").lower()
    low = (text or "").lower()
    if any(m in url_low for m in ("account-status.amazon", "revoke-account-suspension")) or any(
        m in low for m in _WALL_SUSPENDED
    ):
        return "suspended", "Amazon indica que la cuenta está suspendida."
    if "/ap/cvf" in url_low or any(m in low for m in _WALL_CVF):
        return (
            "cvf",
            "Amazon pide verificar el número de celular (CVF) para iniciar sesión.",
        )
    if any(m in low for m in _WALL_WAF):
        return "waf", "Amazon mostró el muro de WAF automatizado."
    if any(m in low for m in _WALL_BAD_CREDENTIALS):
        return "bad_credentials", "Amazon rechazó el correo o la contraseña."
    if any(m in low for m in _WALL_OTP):
        return "otp", "Amazon pidió un código de verificación."
    if "/ap/signin" in url_low:
        return "signin", "Amazon volvió a pedir iniciar sesión."
    return "unknown", ""


def _extract_widget_state(html: str) -> str:
    for pattern in (
        r'name="ppw-widgetState" value="(.*?)"',
        r'value="([^"]*)"[^>]*name="ppw-widgetState"',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return ""


def _extract_customer_id(html: str) -> str:
    match = re.search(r'"customerID"\s*:\s*"([^"]+)"', html)
    if match:
        return match.group(1)
    match = re.search(r'customerId\s*:\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
    return match.group(1) if match else ""


async def _fill_and_submit(page, email: str, password: str) -> None:
    """Escribe el correo, sigue al paso de contraseña y envía.

    Si la página no trae el formulario se falla DE INMEDIATO (no se espera
    20 s a que expire el selector): Amazon devuelve 503 o una página de
    robot cuando el proxy no sirve, y en ese caso no hay nada que esperar.
    """
    await page.wait_for_load_state("domcontentloaded")
    html = await page.content()
    if "ap_email_login" not in html:
        raise PlaywrightError(
            "Amazon no devolvió el formulario de login (¿proxy roto o muro de robot?)."
        )
    await page.wait_for_selector("#ap_email_login", timeout=15_000)
    await page.fill("#ap_email_login", email)
    await page.click("#continue")

    try:
        await page.wait_for_selector("#ap_password", timeout=12_000)
    except Exception:
        # Muro CVF/OTP: no hay campo de contraseña, se sigue así.
        return

    await page.fill("#ap_password", password)
    await page.click("#signInSubmit")


async def _read_prime_state(page) -> Tuple[str, str]:
    """Lee ppw-widgetState y customerID de la página actual."""
    html = await page.content()
    return _extract_widget_state(html), _extract_customer_id(html)


async def open_prime_session(
    email: str,
    password: str,
    entry_url: str = PRIME_ENTRY_URL,
    otp_code: Optional[str] = None,
    retries: Optional[int] = None,
) -> BrowserSession:
    """Abre sesión en el Chromium local y llega al entry de Prime.

    Devuelve una `BrowserSession` con la cookie, el `widget_state` y el
    `customer_id`. El context de Playwright se cierra siempre antes de
    devolver (no hay fugas): el flujo de tarjeta sigue por HTTP con la
    cookie y **el mismo proxy** (misma IP) que usó el navegador.
    """
    retries = retries if retries is not None else SESSION_RETRIES
    result = BrowserSession(account=email)

    for attempt in range(1, retries + 1):
        try:
            async with MANAGER.session() as context:
                page = await context.new_page()
                page.set_default_timeout(30_000)
                result.proxy = MANAGER.current_proxy.curl if MANAGER.current_proxy else None

                await page.goto(entry_url, wait_until="domcontentloaded", timeout=45_000)
                await _fill_and_submit(page, email, password)
                await page.wait_for_timeout(4_000)

                # CVF con código disponible: se completa en el navegador.
                if otp_code and "/ap/cvf" in page.url:
                    try:
                        await page.fill("#verification-code-form input[name=code]", otp_code)
                        await page.click(
                            "#verification-code-form button[type=submit], "
                            "#verification-code-form input[type=submit]"
                        )
                        await page.wait_for_timeout(4_000)
                    except Exception as exc:
                        logger.warning("Playwright: no se pudo enviar el código CVF: %s", exc)

                text = await _body_text(page)
                status, detail = _classify(page.url, text)

                cookies = await context.cookies()
                result.cookie = "; ".join(
                    f"{c['name']}={c['value']}" for c in cookies if c.get("value")
                )
                result.cookies = {c["name"]: c["value"] for c in cookies}
                result.final_url = page.url

                if status == "unknown":
                    widget_state, customer_id = await _read_prime_state(page)
                    if not widget_state and "/ap/signin" not in page.url:
                        # El login funcionó: se entra al entry en esta sesión.
                        await page.goto(
                            entry_url, wait_until="domcontentloaded", timeout=45_000
                        )
                        await page.wait_for_timeout(2_000)
                        widget_state, customer_id = await _read_prime_state(page)

                    if widget_state and customer_id:
                        result.status = "ok"
                        result.detail = "Sesión de Prime lista (widgetState + customerID)."
                        result.widget_state = widget_state
                        result.customer_id = customer_id
                        return result

                result.status = status
                result.detail = detail or "No se pudo obtener la página de Prime."
                return result

        except Exception as exc:
            MANAGER._failures += 1
            logger.warning(
                "Playwright: fallo la sesión de Prime (intento %d/%d): %s",
                attempt, retries, exc,
            )
            result.status = "error"
            result.detail = f"Error de Playwright: {exc}"
            if attempt < retries:
                # El pool IPRoyal es intermitente: se rota el proxy
                # (el navegador se recicla para tomar otro).
                await _rotate_proxy()
                await asyncio.sleep(2)

    return result


async def _rotate_proxy() -> None:
    """Cierra el navegador para que se lance con otro proxy del pool."""
    async with MANAGER._lock:
        await MANAGER._close_locked()


async def close_browser() -> None:
    """Cierra el Chromium (lo usa el apagado del bot y los scripts)."""
    await MANAGER.close()
