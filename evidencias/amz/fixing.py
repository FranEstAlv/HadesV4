from __future__ import annotations

import html
import imaplib
import logging
import os
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.message import Message
from email.parser import BytesParser
from enum import Enum
from typing import Final, Iterable

from appium import webdriver
from appium.options.android import UiAutomator2Options
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


LOGGER = logging.getLogger("amazon_registration")

DEFAULT_APP_PACKAGE: Final = "com.amazon.mShop.android.shopping"
DEFAULT_APP_ACTIVITY: Final = "com.amazon.mShop.navigation.MainActivity"

OTP_PATTERN: Final = re.compile(
    r"(?:code|código|verification|verificación|otp)"
    r"[^0-9]{0,40}(\d{6})",
    re.IGNORECASE,
)

GENERIC_SIX_DIGIT_PATTERN: Final = re.compile(r"\b(\d{6})\b")

REGISTRATION_MARKERS: Final = (
    "input[name='customerName']",
    "input[name='email']",
    "#createAccountSubmit",
    "a[href*='/ap/register']",
)

CAPTCHA_SELECTORS: Final = (
    "img[src*='captcha' i]",
    "#captchacharacters",
    "input[name='cvf_captcha_input']",
    "input[name='captcha']",
    ".cvf-widget-captcha",
)

SUCCESS_SELECTORS: Final = (
    "#ap_email_confirmed_notification",
    "[data-registration-complete='true']",
)

AUTHENTICATED_SELECTORS: Final = (
    "#nav-link-accountList",
    "[data-csa-c-slot-id='nav-link-accountList']",
)


class ConfigurationError(ValueError):
    """La configuración requerida es inválida o está incompleta."""


class RegistrationError(RuntimeError):
    """El flujo de registro no pudo completarse."""


class ChallengeDetected(RegistrationError):
    """Se encontró un desafío que requiere intervención autorizada."""


class RegistrationStatus(str, Enum):
    SUCCESS = "success"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True, slots=True)
class Settings:
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
    imap_sender: str | None

    ui_timeout: float
    otp_timeout: float
    otp_poll_interval: float

    @classmethod
    def from_environment(cls) -> "Settings":
        settings = cls(
            appium_url=os.getenv(
                "APPIUM_URL",
                "http://127.0.0.1:4723",
            ).rstrip("/"),
            device_name=os.getenv("DEVICE_NAME", "theoretical-android"),
            app_package=os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE),
            app_activity=os.getenv("APP_ACTIVITY", DEFAULT_APP_ACTIVITY),
            name=_required_environment("REGISTRATION_NAME"),
            email_address=_required_environment("REGISTRATION_EMAIL"),
            password=_required_environment("REGISTRATION_PASSWORD"),
            imap_host=_required_environment("IMAP_HOST"),
            imap_port=_environment_int("IMAP_PORT", 993),
            imap_user=_required_environment("IMAP_USER"),
            imap_password=_required_environment("IMAP_PASSWORD"),
            imap_subject=os.getenv("IMAP_SUBJECT", "Amazon"),
            imap_sender=os.getenv("IMAP_SENDER") or None,
            ui_timeout=_environment_float("UI_TIMEOUT", 30.0),
            otp_timeout=_environment_float("OTP_TIMEOUT", 120.0),
            otp_poll_interval=_environment_float(
                "OTP_POLL_INTERVAL",
                5.0,
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.appium_url.startswith(("http://", "https://")):
            raise ConfigurationError(
                "APPIUM_URL debe comenzar con http:// o https://"
            )

        if "@" not in self.email_address:
            raise ConfigurationError(
                "REGISTRATION_EMAIL no parece un correo válido"
            )

        if len(self.password) < 8:
            raise ConfigurationError(
                "REGISTRATION_PASSWORD debe tener al menos 8 caracteres"
            )

        if not 1 <= self.imap_port <= 65535:
            raise ConfigurationError("IMAP_PORT está fuera de rango")

        if self.otp_timeout <= 0 or self.otp_poll_interval <= 0:
            raise ConfigurationError(
                "Los timeouts de OTP deben ser positivos"
            )


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    status: RegistrationStatus
    final_url: str
    elapsed_seconds: float


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"La variable de entorno {name} es obligatoria"
        )
    return value


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return default if raw is None else int(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} debe contener un número entero"
        ) from exc


def _environment_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return default if raw is None else float(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} debe contener un número"
        ) from exc


def create_driver(settings: Settings) -> WebDriver:
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.device_name = settings.device_name
    options.app_package = settings.app_package
    options.app_activity = settings.app_activity
    options.no_reset = False
    options.full_reset = False
    options.auto_grant_permissions = True
    options.new_command_timeout = 120

    LOGGER.info(
        "Creando sesión Appium en %s para %s",
        settings.appium_url,
        settings.app_package,
    )

    return webdriver.Remote(
        command_executor=settings.appium_url,
        options=options,
    )


def wait_for_registration_webview(
    driver: WebDriver,
    timeout: float,
) -> str:
    deadline = time.monotonic() + timeout
    observed_contexts: set[str] = set()

    while time.monotonic() < deadline:
        try:
            contexts = list(driver.contexts)
        except WebDriverException as exc:
            LOGGER.warning("No se pudieron consultar contextos: %s", exc)
            time.sleep(1)
            continue

        for context in contexts:
            if "WEBVIEW" not in context.upper():
                continue

            observed_contexts.add(context)

            try:
                driver.switch_to.context(context)

                if any(
                    driver.find_elements(By.CSS_SELECTOR, selector)
                    for selector in REGISTRATION_MARKERS
                ):
                    LOGGER.info(
                        "WebView de registro seleccionado: %s",
                        context,
                    )
                    return context
            except (
                NoSuchElementException,
                StaleElementReferenceException,
                WebDriverException,
            ):
                continue
            finally:
                # Si no se encontró el formulario, volvemos al contexto
                # nativo antes de examinar el siguiente candidato.
                try:
                    if driver.current_context == context:
                        if not any(
                            driver.find_elements(By.CSS_SELECTOR, selector)
                            for selector in REGISTRATION_MARKERS
                        ):
                            driver.switch_to.context("NATIVE_APP")
                except WebDriverException:
                    pass

        time.sleep(1)

    contexts_text = ", ".join(sorted(observed_contexts)) or "ninguno"
    raise RegistrationError(
        "No se encontró un WebView de registro. "
        f"Contextos observados: {contexts_text}"
    )


def find_clickable(
    driver: WebDriver,
    selector: str,
    timeout: float,
) -> WebElement:
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
    )


def find_visible(
    driver: WebDriver,
    selector: str,
    timeout: float,
) -> WebElement:
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, selector))
    )


def replace_text(element: WebElement, value: str) -> None:
    element.click()
    element.clear()
    element.send_keys(value)


def click(driver: WebDriver, element: WebElement) -> None:
    driver.execute_script(
        """
        arguments[0].scrollIntoView({
            behavior: "auto",
            block: "center",
            inline: "nearest"
        });
        """,
        element,
    )
    WebDriverWait(driver, 10).until(
        lambda _: element.is_displayed() and element.is_enabled()
    )
    element.click()


def challenge_is_visible(driver: WebDriver) -> bool:
    for selector in CAPTCHA_SELECTORS:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed():
                    return True
            except StaleElementReferenceException:
                continue
    return False


def require_no_challenge(driver: WebDriver) -> None:
    if challenge_is_visible(driver):
        raise ChallengeDetected(
            "Se detectó un CAPTCHA o desafío. "
            "La ejecución se detuvo para revisión manual."
        )


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""

    charset = part.get_content_charset() or "utf-8"

    try:
        return payload.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _html_to_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(without_tags)


def message_text(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    parts: Iterable[Message]
    parts = message.walk() if message.is_multipart() else (message,)

    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue

        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain_parts.append(_decode_part(part))
        elif content_type == "text/html":
            html_parts.append(_html_to_text(_decode_part(part)))

    selected = plain_parts if plain_parts else html_parts
    return "\n".join(selected)


def extract_otp(text: str) -> str | None:
    contextual_match = OTP_PATTERN.search(text)
    if contextual_match:
        return contextual_match.group(1)

    matches = GENERIC_SIX_DIGIT_PATTERN.findall(text)
    unique_matches = list(dict.fromkeys(matches))

    # Evitamos elegir arbitrariamente si el correo contiene varios
    # números de seis dígitos.
    if len(unique_matches) == 1:
        return unique_matches[0]

    return None


class ImapOtpProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def wait_for_code(self, not_before: datetime) -> str:
        deadline = time.monotonic() + self._settings.otp_timeout
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                code = self._poll_once(not_before)
                if code:
                    return code
            except imaplib.IMAP4.error as exc:
                # Los errores IMAP de autenticación o protocolo no se
                # deberían ocultar y reintentar durante dos minutos.
                raise RegistrationError(
                    f"Error de autenticación o protocolo IMAP: {exc}"
                ) from exc
            except (OSError, ssl.SSLError) as exc:
                last_error = exc
                LOGGER.warning(
                    "Error temporal consultando IMAP: %s",
                    type(exc).__name__,
                )

            time.sleep(self._settings.otp_poll_interval)

        detail = (
            f" Último error: {type(last_error).__name__}."
            if last_error
            else ""
        )
        raise RegistrationError(
            "No se recibió un OTP válido dentro del tiempo configurado."
            + detail
        )

    def _poll_once(self, not_before: datetime) -> str | None:
        settings = self._settings

        with imaplib.IMAP4_SSL(
            host=settings.imap_host,
            port=settings.imap_port,
            timeout=15,
        ) as mailbox:
            mailbox.login(settings.imap_user, settings.imap_password)

            status, _ = mailbox.select("INBOX", readonly=True)
            if status != "OK":
                raise RegistrationError(
                    "No se pudo seleccionar el buzón INBOX"
                )

            criteria: list[str] = ["UNSEEN"]

            if settings.imap_subject:
                criteria.extend(
                    ["SUBJECT", f'"{settings.imap_subject}"']
                )

            if settings.imap_sender:
                criteria.extend(
                    ["FROM", f'"{settings.imap_sender}"']
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

                message = BytesParser(policy=policy.default).parsebytes(
                    raw_message
                )

                if not self._matches_message(message, not_before):
                    continue

                code = extract_otp(message_text(message))
                if code:
                    return code

        return None

    def _matches_message(
        self,
        message: Message,
        not_before: datetime,
    ) -> bool:
        subject = _decode_header(message.get("Subject"))
        sender = _decode_header(message.get("From"))

        if (
            self._settings.imap_subject
            and self._settings.imap_subject.casefold()
            not in subject.casefold()
        ):
            return False

        if (
            self._settings.imap_sender
            and self._settings.imap_sender.casefold()
            not in sender.casefold()
        ):
            return False

        parsed_date = message.get("Date")
        if parsed_date:
            try:
                from email.utils import parsedate_to_datetime

                message_date = parsedate_to_datetime(parsed_date)
                if message_date.tzinfo is None:
                    message_date = message_date.replace(
                        tzinfo=timezone.utc
                    )

                # Se admite una pequeña diferencia entre relojes.
                if message_date.timestamp() < not_before.timestamp() - 30:
                    return False
            except (TypeError, ValueError, OverflowError):
                LOGGER.warning(
                    "El correo OTP contiene una fecha no interpretable"
                )

        return True


class RegistrationWorkflow:
    def __init__(
        self,
        driver: WebDriver,
        settings: Settings,
        otp_provider: ImapOtpProvider,
    ) -> None:
        self.driver = driver
        self.settings = settings
        self.otp_provider = otp_provider
        self.wait = WebDriverWait(driver, settings.ui_timeout)

    def run(self) -> RegistrationResult:
        started = time.monotonic()
        otp_not_before = datetime.now(timezone.utc)

        self._open_registration()
        self._fill_registration_form()
        self._submit_registration_form()
        require_no_challenge(self.driver)

        self._verify_email(otp_not_before)

        status = (
            RegistrationStatus.SUCCESS
            if self._registration_was_confirmed()
            else RegistrationStatus.UNCONFIRMED
        )

        return RegistrationResult(
            status=status,
            final_url=self._safe_current_url(),
            elapsed_seconds=time.monotonic() - started,
        )

    def _open_registration(self) -> None:
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
            self.settings.ui_timeout,
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
        click(self.driver, create_account)

        find_visible(
            self.driver,
            "input[name='customerName']",
            self.settings.ui_timeout,
        )

    def _fill_registration_form(self) -> None:
        replace_text(
            find_visible(
                self.driver,
                "input[name='customerName']",
                self.settings.ui_timeout,
            ),
            self.settings.name,
        )

        replace_text(
            find_visible(
                self.driver,
                "input[name='email']",
                self.settings.ui_timeout,
            ),
            self.settings.email_address,
        )

        replace_text(
            find_visible(
                self.driver,
                "input[name='password']",
                self.settings.ui_timeout,
            ),
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

    def _submit_registration_form(self) -> None:
        form = find_visible(
            self.driver,
            "form",
            self.settings.ui_timeout,
        )

        submit = form.find_element(
            By.CSS_SELECTOR,
            (
                "input#continue, "
                "input[type='submit'], "
                "button[type='submit']"
            ),
        )
        click(self.driver, submit)

        WebDriverWait(
            self.driver,
            self.settings.ui_timeout,
        ).until(
            lambda driver: (
                challenge_is_visible(driver)
                or bool(
                    driver.find_elements(
                        By.CSS_SELECTOR,
                        (
                            "input[name='code'], "
                            "#cvf-input-code, "
                            "input[autocomplete='one-time-code']"
                        ),
                    )
                )
            )
        )

    def _verify_email(self, not_before: datetime) -> None:
        otp_field = find_visible(
            self.driver,
            (
                "input[name='code'], "
                "#cvf-input-code, "
                "input[autocomplete='one-time-code']"
            ),
            self.settings.ui_timeout,
        )

        otp = self.otp_provider.wait_for_code(not_before)
        replace_text(otp_field, otp)

        otp_form = otp_field.find_element(By.XPATH, "./ancestor::form[1]")
        submit = otp_form.find_element(
            By.CSS_SELECTOR,
            (
                "input#continue, "
                "input[type='submit'], "
                "button[type='submit']"
            ),
        )
        click(self.driver, submit)

        WebDriverWait(
            self.driver,
            self.settings.ui_timeout,
        ).until(
            lambda driver: (
                challenge_is_visible(driver)
                or self._registration_was_confirmed()
                or "error" in self._safe_current_url().casefold()
            )
        )

        require_no_challenge(self.driver)

    def _registration_was_confirmed(self) -> bool:
        for selector in SUCCESS_SELECTORS:
            if _has_visible_element(self.driver, selector):
                return True

        # Estos marcadores solo se aceptan si el formulario de OTP ya
        # desapareció; encontrar únicamente el menú no prueba por sí
        # mismo que la cuenta se haya creado.
        otp_is_present = bool(
            self.driver.find_elements(
                By.CSS_SELECTOR,
                (
                    "input[name='code'], "
                    "#cvf-input-code, "
                    "input[autocomplete='one-time-code']"
                ),
            )
        )

        if otp_is_present:
            return False

        return any(
            _has_visible_element(self.driver, selector)
            for selector in AUTHENTICATED_SELECTORS
        )

    def _safe_current_url(self) -> str:
        try:
            return self.driver.current_url
        except WebDriverException:
            return ""


def _has_visible_element(driver: WebDriver, selector: str) -> bool:
    for element in driver.find_elements(By.CSS_SELECTOR, selector):
        try:
            if element.is_displayed():
                return True
        except StaleElementReferenceException:
            continue
    return False


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s: %(message)s"
        ),
    )


def main() -> int:
    configure_logging()

    try:
        settings = Settings.from_environment()
    except ConfigurationError as exc:
        LOGGER.error("Configuración inválida: %s", exc)
        return 2

    driver: WebDriver | None = None

    try:
        driver = create_driver(settings)
        workflow = RegistrationWorkflow(
            driver=driver,
            settings=settings,
            otp_provider=ImapOtpProvider(settings),
        )
        result = workflow.run()
    except ChallengeDetected as exc:
        LOGGER.warning("%s", exc)
        return 3
    except TimeoutException as exc:
        LOGGER.error("Timeout esperando la interfaz: %s", exc)
        return 4
    except RegistrationError as exc:
        LOGGER.error("Registro no completado: %s", exc)
        return 5
    except WebDriverException as exc:
        LOGGER.error(
            "Error de Appium/WebDriver: %s",
            type(exc).__name__,
        )
        return 6
    finally:
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException as exc:
                LOGGER.warning(
                    "No se pudo cerrar limpiamente Appium: %s",
                    type(exc).__name__,
                )

    LOGGER.info(
        "Flujo terminado: status=%s elapsed=%.2fs",
        result.status.value,
        result.elapsed_seconds,
    )

    return (
        0
        if result.status is RegistrationStatus.SUCCESS
        else 7
    )


if __name__ == "__main__":
    raise SystemExit(main())