import email
import html
import imaplib
import logging
import os
import re
import shlex
import ssl
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from enum import Enum
from urllib.parse import urlparse

from appium import webdriver
from appium.options.android import UiAutomator2Options
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


LOGGER = logging.getLogger("amazon_registration")

DEFAULT_APP_PACKAGE = "com.amazon.mShop.android.shopping"
DEFAULT_APP_ACTIVITY = "com.amazon.mShop.navigation.MainActivity"

REGISTRATION_MARKERS = (
    "input[name='customerName']",
    "input[name='email']",
    "#createAccountSubmit",
    "a[href*='/ap/register']",
    "a[href*='/auth/register']",
)

CAPTCHA_SELECTORS = (
    "img[src*='captcha' i]",
    "#captchacharacters",
    "input[name='cvf_captcha_input']",
    "input[name='captcha']",
    ".cvf-widget-captcha",
    "[id*='captcha' i]",
    "[class*='captcha' i]",
)

OTP_SELECTORS = (
    "input[name='code']",
    "#cvf-input-code",
    "input[autocomplete='one-time-code']",
)

SUCCESS_SELECTORS = (
    "#ap_email_confirmed_notification",
    "[data-registration-complete='true']",
)

AUTHENTICATED_SELECTORS = (
    "#nav-link-accountList",
    "[data-csa-c-slot-id='nav-link-accountList']",
)

OTP_PATTERN = re.compile(
    r"(?:code|código|verification|verificación|otp)"
    r"[^0-9]{0,40}(\d{6})",
    re.IGNORECASE,
)

GENERIC_OTP_PATTERN = re.compile(r"\b(\d{6})\b")


class ConfigurationError(ValueError):
    pass


class AdbError(RuntimeError):
    pass


class RegistrationError(RuntimeError):
    pass


class ChallengeDetected(RegistrationError):
    pass


class RegistrationStatus(str, Enum):
    SUCCESS = "success"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class Settings:
    device_host: str
    device_port: int
    device_serial: str
    adb_timeout: float
    adb_connect_timeout: float
    clear_app_data: bool
    clear_external_cache: bool
    trim_device_caches: bool
    reset_advertising_id: bool
    clear_clipboard: bool
    enable_proxy: bool
    proxy_host: str
    proxy_port: int
    appium_url: str
    device_name: str
    app_package: str
    app_activity: str
    name: str
    email_address: str
    password: str
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_subject: str
    imap_sender: str
    ui_timeout: float
    webview_timeout: float
    otp_timeout: float
    otp_poll_interval: float

    @classmethod
    def from_environment(cls):
        device_host = required_environment("DEVICE_HOST")
        device_port = environment_int("DEVICE_PORT", 5555)
        device_serial = os.getenv(
            "DEVICE_SERIAL",
            f"{device_host}:{device_port}",
        ).strip()

        settings = cls(
            device_host=device_host,
            device_port=device_port,
            device_serial=device_serial,
            adb_timeout=environment_float("ADB_TIMEOUT", 20),
            adb_connect_timeout=environment_float(
                "ADB_CONNECT_TIMEOUT",
                30,
            ),
            clear_app_data=environment_bool("CLEAR_APP_DATA", True),
            clear_external_cache=environment_bool(
                "CLEAR_EXTERNAL_CACHE",
                True,
            ),
            trim_device_caches=environment_bool(
                "TRIM_DEVICE_CACHES",
                True,
            ),
            reset_advertising_id=environment_bool(
                "RESET_ADVERTISING_ID",
                True,
            ),
            clear_clipboard=environment_bool(
                "CLEAR_CLIPBOARD",
                True,
            ),
            enable_proxy=environment_bool("ENABLE_PROXY", True),
            proxy_host=required_environment("PROXY_HOST"),
            proxy_port=environment_int("PROXY_PORT", 8080),
            appium_url=os.getenv(
                "APPIUM_URL",
                "http://127.0.0.1:4723",
            ).rstrip("/"),
            device_name=os.getenv(
                "DEVICE_NAME",
                device_serial,
            ).strip(),
            app_package=os.getenv(
                "APP_PACKAGE",
                DEFAULT_APP_PACKAGE,
            ).strip(),
            app_activity=os.getenv(
                "APP_ACTIVITY",
                DEFAULT_APP_ACTIVITY,
            ).strip(),
            name=required_environment("REGISTRATION_NAME"),
            email_address=required_environment("REGISTRATION_EMAIL"),
            password=required_environment("REGISTRATION_PASSWORD"),
            imap_host=required_environment("IMAP_HOST"),
            imap_port=environment_int("IMAP_PORT", 993),
            imap_user=required_environment("IMAP_USER"),
            imap_password=required_environment("IMAP_PASSWORD"),
            imap_subject=os.getenv("IMAP_SUBJECT", "Amazon").strip(),
            imap_sender=os.getenv("IMAP_SENDER", "").strip(),
            ui_timeout=environment_float("UI_TIMEOUT", 30),
            webview_timeout=environment_float("WEBVIEW_TIMEOUT", 30),
            otp_timeout=environment_float("OTP_TIMEOUT", 120),
            otp_poll_interval=environment_float(
                "OTP_POLL_INTERVAL",
                5,
            ),
        )

        settings.validate()
        return settings

    def validate(self):
        if not self.device_serial:
            raise ConfigurationError("DEVICE_SERIAL está vacío")

        if not 1 <= self.device_port <= 65535:
            raise ConfigurationError("DEVICE_PORT está fuera de rango")

        if not 1 <= self.proxy_port <= 65535:
            raise ConfigurationError("PROXY_PORT está fuera de rango")

        if not 1 <= self.imap_port <= 65535:
            raise ConfigurationError("IMAP_PORT está fuera de rango")

        parsed_url = urlparse(self.appium_url)
        if parsed_url.scheme not in {"http", "https"}:
            raise ConfigurationError(
                "APPIUM_URL debe comenzar con http:// o https://"
            )

        if not parsed_url.hostname:
            raise ConfigurationError("APPIUM_URL no contiene un host")

        if "@" not in self.email_address:
            raise ConfigurationError(
                "REGISTRATION_EMAIL no parece válido"
            )

        if len(self.password) < 8:
            raise ConfigurationError(
                "REGISTRATION_PASSWORD debe tener al menos 8 caracteres"
            )

        numeric_values = (
            self.adb_timeout,
            self.adb_connect_timeout,
            self.ui_timeout,
            self.webview_timeout,
            self.otp_timeout,
            self.otp_poll_interval,
        )

        if any(value <= 0 for value in numeric_values):
            raise ConfigurationError(
                "Los valores de timeout deben ser positivos"
            )


@dataclass(frozen=True)
class RegistrationResult:
    status: RegistrationStatus
    final_url: str
    elapsed_seconds: float


def required_environment(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"La variable de entorno {name} es obligatoria"
        )
    return value


def environment_bool(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().casefold()

    if normalized in {"1", "true", "yes", "on", "si", "sí"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ConfigurationError(
        f"{name} debe contener true o false"
    )


def environment_int(name, default):
    raw = os.getenv(name)

    try:
        return default if raw is None else int(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} debe contener un número entero"
        ) from exc


def environment_float(name, default):
    raw = os.getenv(name)

    try:
        return default if raw is None else float(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} debe contener un número"
        ) from exc


class AdbDeviceController:
    def __init__(self, settings):
        self.settings = settings
        self.previous_proxy = None
        self.connected = False
        self.state_captured = False

    def run(self, arguments, timeout=None, check=True):
        command = ["adb", *arguments]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self.settings.adb_timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AdbError("No se encontró el ejecutable adb") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(
                f"Timeout ejecutando: {shlex.join(command)}"
            ) from exc

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if check and result.returncode != 0:
            detail = stderr or stdout or "sin información adicional"
            raise AdbError(
                f"ADB falló con código {result.returncode}: {detail}"
            )

        return result

    def shell(self, arguments, timeout=None, check=True):
        result = self.run(
            ["-s", self.settings.device_serial, "shell", *arguments],
            timeout=timeout,
            check=check,
        )
        return result.stdout.strip()

    def connect(self):
        target = (
            f"{self.settings.device_host}:"
            f"{self.settings.device_port}"
        )

        result = self.run(
            ["connect", target],
            timeout=self.settings.adb_connect_timeout,
        )

        output = f"{result.stdout}\n{result.stderr}".casefold()

        if not any(
            marker in output
            for marker in (
                "connected to",
                "already connected to",
            )
        ):
            raise AdbError(
                f"No se pudo confirmar la conexión con {target}"
            )

        deadline = time.monotonic() + self.settings.adb_connect_timeout

        while time.monotonic() < deadline:
            state = self.run(
                ["-s", self.settings.device_serial, "get-state"],
                check=False,
            )

            if (
                state.returncode == 0
                and state.stdout.strip() == "device"
            ):
                self.connected = True
                return

            time.sleep(1)

        raise AdbError(
            f"El dispositivo {self.settings.device_serial} "
            "no alcanzó el estado device"
        )

    def capture_original_state(self):
        self.previous_proxy = self.shell(
            ["settings", "get", "global", "http_proxy"],
            check=False,
        ).strip()
        self.state_captured = True

    def clear_app_data(self):
        if not self.settings.clear_app_data:
            return

        output = self.shell(
            ["pm", "clear", self.settings.app_package]
        )

        if "success" not in output.casefold():
            raise AdbError(
                f"No se pudieron borrar los datos de "
                f"{self.settings.app_package}: {output}"
            )

    def clear_external_cache(self):
        if not self.settings.clear_external_cache:
            return

        path = (
            f"/sdcard/Android/data/"
            f"{self.settings.app_package}/cache"
        )

        self.shell(["rm", "-rf", path])

    def trim_caches(self):
        if not self.settings.trim_device_caches:
            return

        self.shell(
            ["cmd", "package", "trim-caches", "1000000000"],
            check=False,
        )

    def reset_advertising_id(self):
        if not self.settings.reset_advertising_id:
            return

        result = self.shell(
            [
                "am",
                "broadcast",
                "-a",
                "com.google.android.gms.ads.RESET_ADVERTISING_ID",
            ],
            check=False,
        )

        if result:
            LOGGER.info("Reinicio de advertising ID solicitado")

    def clear_device_clipboard(self):
        if not self.settings.clear_clipboard:
            return

        commands = (
            [
                "cmd",
                "clipboard",
                "set",
                "text",
                "",
            ],
            [
                "am",
                "broadcast",
                "-a",
                "clipper.set",
                "--es",
                "text",
                "",
            ],
        )

        for command in commands:
            result = self.run(
                [
                    "-s",
                    self.settings.device_serial,
                    "shell",
                    *command,
                ],
                check=False,
            )

            if result.returncode == 0:
                return

        LOGGER.warning(
            "El dispositivo no confirmó la limpieza del portapapeles"
        )

    def configure_proxy(self):
        if not self.settings.enable_proxy:
            return

        proxy = (
            f"{self.settings.proxy_host}:"
            f"{self.settings.proxy_port}"
        )

        self.shell(
            [
                "settings",
                "put",
                "global",
                "http_proxy",
                proxy,
            ]
        )

        configured = self.shell(
            ["settings", "get", "global", "http_proxy"]
        ).strip()

        if configured != proxy:
            raise AdbError(
                f"El proxy configurado no coincide: {configured}"
            )

    def restore_proxy(self):
        if not self.state_captured:
            return

        previous = (self.previous_proxy or "").strip()

        if previous.casefold() in {"", "null", ":0"}:
            self.shell(
                ["settings", "delete", "global", "http_proxy"],
                check=False,
            )
            self.shell(
                ["settings", "put", "global", "http_proxy", ":0"],
                check=False,
            )
            return

        self.shell(
            [
                "settings",
                "put",
                "global",
                "http_proxy",
                previous,
            ],
            check=False,
        )

    def prepare(self):
        self.connect()
        self.capture_original_state()
        self.clear_app_data()
        self.clear_external_cache()
        self.trim_caches()
        self.reset_advertising_id()
        self.clear_device_clipboard()
        self.configure_proxy()

    def restore(self):
        errors = []

        for operation in (
            self.restore_proxy,
            self.clear_device_clipboard,
        ):
            try:
                operation()
            except Exception as exc:
                errors.append(exc)
                LOGGER.error(
                    "Falló la restauración del dispositivo: %s",
                    exc,
                )

        return errors


def create_driver(settings):
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.device_name = settings.device_name
    options.udid = settings.device_serial
    options.app_package = settings.app_package
    options.app_activity = settings.app_activity
    options.no_reset = True
    options.full_reset = False
    options.auto_grant_permissions = True
    options.new_command_timeout = 120

    return webdriver.Remote(
        command_executor=settings.appium_url,
        options=options,
    )


def has_visible_element(driver, selector):
    for element in driver.find_elements(By.CSS_SELECTOR, selector):
        try:
            if element.is_displayed():
                return True
        except StaleElementReferenceException:
            continue

    return False


def find_visible(driver, selector, timeout):
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located(
            (By.CSS_SELECTOR, selector)
        )
    )


def find_clickable(driver, selector, timeout):
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, selector)
        )
    )


def replace_text(element, value):
    element.click()
    element.clear()
    element.send_keys(value)


def scroll_and_click(driver, element):
    driver.execute_script(
        "arguments[0].scrollIntoView({"
        "behavior:'auto',block:'center',inline:'nearest'"
        "});",
        element,
    )

    WebDriverWait(driver, 10).until(
        lambda current_driver: (
            element.is_displayed() and element.is_enabled()
        )
    )

    element.click()


def registration_marker_is_present(driver):
    return any(
        driver.find_elements(By.CSS_SELECTOR, selector)
        for selector in REGISTRATION_MARKERS
    )


def wait_for_registration_webview(driver, timeout):
    deadline = time.monotonic() + timeout
    observed = set()

    while time.monotonic() < deadline:
        try:
            contexts = list(driver.contexts)
        except WebDriverException:
            time.sleep(1)
            continue

        for context in contexts:
            if "WEBVIEW" not in context.upper():
                continue

            observed.add(context)

            try:
                driver.switch_to.context(context)

                if registration_marker_is_present(driver):
                    return context
            except WebDriverException:
                pass

            try:
                driver.switch_to.context("NATIVE_APP")
            except WebDriverException:
                pass

        time.sleep(1)

    context_list = ", ".join(sorted(observed)) or "ninguno"

    raise RegistrationError(
        "No se encontró el WebView de registro. "
        f"Contextos observados: {context_list}"
    )


def challenge_is_visible(driver):
    return any(
        has_visible_element(driver, selector)
        for selector in CAPTCHA_SELECTORS
    )


def require_no_challenge(driver):
    if challenge_is_visible(driver):
        raise ChallengeDetected(
            "Se detectó un CAPTCHA o desafío que requiere "
            "intervención manual autorizada"
        )


def decode_email_header(value):
    if not value:
        return ""

    return str(make_header(decode_header(value)))


def decode_message_part(part):
    payload = part.get_payload(decode=True)

    if not isinstance(payload, bytes):
        return ""

    charset = part.get_content_charset() or "utf-8"

    try:
        return payload.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def html_to_text(value):
    without_scripts = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        " ",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_scripts)
    return html.unescape(without_tags)


def message_text(message):
    plain_parts = []
    html_parts = []

    parts = message.walk() if message.is_multipart() else (message,)

    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue

        content_type = part.get_content_type()

        if content_type == "text/plain":
            plain_parts.append(decode_message_part(part))
        elif content_type == "text/html":
            html_parts.append(
                html_to_text(decode_message_part(part))
            )

    selected = plain_parts if plain_parts else html_parts
    return "\n".join(selected)


def extract_otp(value):
    contextual_match = OTP_PATTERN.search(value)

    if contextual_match:
        return contextual_match.group(1)

    matches = GENERIC_OTP_PATTERN.findall(value)
    unique_matches = list(dict.fromkeys(matches))

    if len(unique_matches) == 1:
        return unique_matches[0]

    return None


class ImapOtpProvider:
    def __init__(self, settings):
        self.settings = settings

    def wait_for_code(self, not_before):
        deadline = time.monotonic() + self.settings.otp_timeout
        last_error = None

        while time.monotonic() < deadline:
            try:
                code = self.poll_once(not_before)

                if code:
                    return code
            except imaplib.IMAP4.error as exc:
                raise RegistrationError(
                    f"Error de autenticación o protocolo IMAP: {exc}"
                ) from exc
            except (OSError, ssl.SSLError) as exc:
                last_error = exc
                LOGGER.warning(
                    "Error temporal consultando IMAP: %s",
                    type(exc).__name__,
                )

            time.sleep(self.settings.otp_poll_interval)

        suffix = ""

        if last_error:
            suffix = (
                f" Último error: {type(last_error).__name__}."
            )

        raise RegistrationError(
            "No se recibió un OTP válido dentro del tiempo "
            f"configurado.{suffix}"
        )

    def poll_once(self, not_before):
        with imaplib.IMAP4_SSL(
            host=self.settings.imap_host,
            port=self.settings.imap_port,
            timeout=15,
        ) as mailbox:
            mailbox.login(
                self.settings.imap_user,
                self.settings.imap_password,
            )

            status, _ = mailbox.select("INBOX", readonly=True)

            if status != "OK":
                raise RegistrationError(
                    "No se pudo seleccionar el buzón INBOX"
                )

            criteria = ["UNSEEN"]

            if self.settings.imap_subject:
                criteria.extend(
                    ["SUBJECT", f'"{self.settings.imap_subject}"']
                )

            if self.settings.imap_sender:
                criteria.extend(
                    ["FROM", f'"{self.settings.imap_sender}"']
                )

            status, payload = mailbox.search(None, *criteria)

            if status != "OK" or not payload:
                return None

            message_ids = payload[0].split()

            for message_id in reversed(message_ids[-20:]):
                status, data = mailbox.fetch(
                    message_id,
                    "(INTERNALDATE RFC822)",
                )

                if status != "OK" or not data:
                    continue

                raw_message = next(
                    (
                        item[1]
                        for item in data
                        if isinstance(item, tuple)
                        and isinstance(item[1], bytes)
                    ),
                    None,
                )

                if raw_message is None:
                    continue

                message = BytesParser(
                    policy=policy.default
                ).parsebytes(raw_message)

                if not self.matches_message(
                    message,
                    not_before,
                ):
                    continue

                code = extract_otp(message_text(message))

                if code:
                    return code

        return None

    def matches_message(self, message, not_before):
        subject = decode_email_header(message.get("Subject"))
        sender = decode_email_header(message.get("From"))

        if (
            self.settings.imap_subject
            and self.settings.imap_subject.casefold()
            not in subject.casefold()
        ):
            return False

        if (
            self.settings.imap_sender
            and self.settings.imap_sender.casefold()
            not in sender.casefold()
        ):
            return False

        raw_date = message.get("Date")

        if raw_date:
            try:
                message_date = parsedate_to_datetime(raw_date)

                if message_date.tzinfo is None:
                    message_date = message_date.replace(
                        tzinfo=timezone.utc
                    )

                if (
                    message_date.timestamp()
                    < not_before.timestamp() - 30
                ):
                    return False
            except (
                TypeError,
                ValueError,
                OverflowError,
            ):
                return False

        return True


class RegistrationWorkflow:
    def __init__(self, driver, settings, otp_provider):
        self.driver = driver
        self.settings = settings
        self.otp_provider = otp_provider

    def run(self):
        started = time.monotonic()

        self.open_registration()
        self.fill_registration_form()

        otp_not_before = datetime.now(timezone.utc)

        self.submit_registration_form()
        require_no_challenge(self.driver)
        self.verify_email(otp_not_before)

        status = (
            RegistrationStatus.SUCCESS
            if self.registration_was_confirmed()
            else RegistrationStatus.UNCONFIRMED
        )

        return RegistrationResult(
            status=status,
            final_url=self.safe_current_url(),
            elapsed_seconds=time.monotonic() - started,
        )

    def open_registration(self):
        sign_in = WebDriverWait(
            self.driver,
            self.settings.ui_timeout,
        ).until(
            EC.element_to_be_clickable(
                (
                    By.ID,
                    f"{self.settings.app_package}:id/sign_in_button",
                )
            )
        )

        sign_in.click()

        wait_for_registration_webview(
            self.driver,
            self.settings.webview_timeout,
        )

        create_account = find_clickable(
            self.driver,
            (
                "#createAccountSubmit, "
                "a[href*='/ap/register'], "
                "a[href*='/auth/register'], "
                "[data-nav-target*='register']"
            ),
            self.settings.ui_timeout,
        )

        scroll_and_click(self.driver, create_account)

        find_visible(
            self.driver,
            "input[name='customerName']",
            self.settings.ui_timeout,
        )

    def fill_registration_form(self):
        name_field = find_visible(
            self.driver,
            "input[name='customerName']",
            self.settings.ui_timeout,
        )
        replace_text(name_field, self.settings.name)

        email_field = find_visible(
            self.driver,
            "input[name='email']",
            self.settings.ui_timeout,
        )
        replace_text(
            email_field,
            self.settings.email_address,
        )

        password_field = find_visible(
            self.driver,
            "input[name='password']",
            self.settings.ui_timeout,
        )
        replace_text(
            password_field,
            self.settings.password,
        )

        confirmation_fields = self.driver.find_elements(
            By.CSS_SELECTOR,
            "input[name='passwordCheck']",
        )

        if confirmation_fields:
            replace_text(
                confirmation_fields[0],
                self.settings.password,
            )

    def submit_registration_form(self):
        name_field = find_visible(
            self.driver,
            "input[name='customerName']",
            self.settings.ui_timeout,
        )

        form = name_field.find_element(
            By.XPATH,
            "./ancestor::form[1]",
        )

        submit = form.find_element(
            By.CSS_SELECTOR,
            (
                "input#continue, "
                "input[type='submit'], "
                "button[type='submit']"
            ),
        )

        scroll_and_click(self.driver, submit)

        WebDriverWait(
            self.driver,
            self.settings.ui_timeout,
        ).until(
            lambda driver: (
                challenge_is_visible(driver)
                or any(
                    driver.find_elements(
                        By.CSS_SELECTOR,
                        selector,
                    )
                    for selector in OTP_SELECTORS
                )
            )
        )

    def verify_email(self, not_before):
        otp_selector = ", ".join(OTP_SELECTORS)

        otp_field = find_visible(
            self.driver,
            otp_selector,
            self.settings.ui_timeout,
        )

        otp = self.otp_provider.wait_for_code(not_before)
        replace_text(otp_field, otp)

        form = otp_field.find_element(
            By.XPATH,
            "./ancestor::form[1]",
        )

        submit = form.find_element(
            By.CSS_SELECTOR,
            (
                "input#continue, "
                "input[type='submit'], "
                "button[type='submit']"
            ),
        )

        scroll_and_click(self.driver, submit)

        WebDriverWait(
            self.driver,
            self.settings.ui_timeout,
        ).until(
            lambda driver: (
                challenge_is_visible(driver)
                or self.registration_was_confirmed()
                or "error" in self.safe_current_url().casefold()
            )
        )

        require_no_challenge(self.driver)

    def registration_was_confirmed(self):
        if any(
            has_visible_element(self.driver, selector)
            for selector in SUCCESS_SELECTORS
        ):
            return True

        otp_is_present = any(
            self.driver.find_elements(
                By.CSS_SELECTOR,
                selector,
            )
            for selector in OTP_SELECTORS
        )

        if otp_is_present:
            return False

        return any(
            has_visible_element(self.driver, selector)
            for selector in AUTHENTICATED_SELECTORS
        )

    def safe_current_url(self):
        try:
            return self.driver.current_url
        except WebDriverException:
            return ""


def configure_logging():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s: %(message)s"
        ),
    )


def main():
    configure_logging()

    try:
        settings = Settings.from_environment()
    except ConfigurationError as exc:
        LOGGER.error("Configuración inválida: %s", exc)
        return 2

    device = AdbDeviceController(settings)
    driver = None
    result = None
    exit_code = 1

    try:
        device.prepare()

        driver = create_driver(settings)

        workflow = RegistrationWorkflow(
            driver=driver,
            settings=settings,
            otp_provider=ImapOtpProvider(settings),
        )

        result = workflow.run()

        exit_code = (
            0
            if result.status is RegistrationStatus.SUCCESS
            else 7
        )
    except ChallengeDetected as exc:
        LOGGER.warning("%s", exc)
        exit_code = 3
    except TimeoutException as exc:
        LOGGER.error("Timeout esperando la interfaz: %s", exc)
        exit_code = 4
    except AdbError as exc:
        LOGGER.error("Error preparando el dispositivo: %s", exc)
        exit_code = 5
    except RegistrationError as exc:
        LOGGER.error("Registro no completado: %s", exc)
        exit_code = 6
    except WebDriverException as exc:
        LOGGER.error(
            "Error de Appium/WebDriver: %s",
            type(exc).__name__,
        )
        exit_code = 8
    finally:
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException as exc:
                LOGGER.warning(
                    "No se pudo cerrar Appium: %s",
                    type(exc).__name__,
                )

        restore_errors = device.restore()

        if restore_errors and exit_code == 0:
            exit_code = 9

    if result is not None:
        LOGGER.info(
            "Flujo terminado: status=%s elapsed=%.2fs",
            result.status.value,
            result.elapsed_seconds,
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main()