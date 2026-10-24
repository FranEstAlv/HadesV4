#!/usr/bin/env python3

import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


# ============================================================
# PATHS / CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parent
QUERIES_DIR = ROOT / "queries"
OUTPUT_ROOT = ROOT / "output"

load_dotenv(ROOT / ".env")

BROWSERLESS_TOKEN = os.getenv("BROWSERLESS_TOKEN")

BROWSERLESS_URL = os.getenv(
    "BROWSERLESS_URL",
    "https://production-sfo.browserless.io",
)

PROXY_COUNTRY = os.getenv(
    "BROWSERLESS_PROXY_COUNTRY",
    "mx",
)

AMAZON_LOGIN = os.getenv("AMAZON_LOGIN")
AMAZON_PASSWORD = os.getenv("AMAZON_PASSWORD")

# NOTA: BROWSERLESS_TOKEN puede contener múltiples claves separadas por coma.
# Si se alcanza el límite de concurrencia (HTTP 429), el script intentará
# usar la siguiente clave disponible en la lista.


# URL que ya comprobamos que redirige correctamente al login
DEFAULT_AMAZON_LOGIN_ENTRY = (
    "https://www.amazon.com.mx/"
    "gp/prime/pipeline/membersignup?campaignId=SlashPrime"
)


# ============================================================
# VALIDATION
# ============================================================

def validate_config(query_file: Path, url: str):
    if not BROWSERLESS_TOKEN:
        raise RuntimeError(
            "Falta BROWSERLESS_TOKEN en .env"
        )

    if not AMAZON_LOGIN:
        raise RuntimeError(
            "Falta AMAZON_LOGIN en .env"
        )

    if not AMAZON_PASSWORD:
        raise RuntimeError(
            "Falta AMAZON_PASSWORD en .env"
        )

    if not query_file.exists():
        raise RuntimeError(
            f"No existe el query: {query_file}"
        )

    if not url:
        raise RuntimeError(
            "Falta URL"
        )


# ============================================================
# FILE HELPERS
# ============================================================

def load_query(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def create_output_dir(step_name: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    safe_step = "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in step_name
    )

    output_dir = (
        OUTPUT_ROOT /
        f"step_{safe_step}_{timestamp}"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    return output_dir


def write_json(path: Path, data):
    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ============================================================
# ATTRIBUTE NORMALIZATION
# ============================================================

def flatten_attribute(value):
    """
    BrowserQL puede devolver atributos como:

        {
            "name": "type",
            "value": "password"
        }

    Para elements.json sólo nos interesa "value".
    """

    if isinstance(value, dict):
        if "value" in value:
            return value["value"]

    return value


def clean_element(element):
    if not isinstance(element, dict):
        return element

    cleaned = {}

    for key, value in element.items():
        cleaned[key] = flatten_attribute(value)

    return cleaned


def build_element_inventory(data):
    inventory = {}

    collections = (
        "headings",
        "forms",
        "inputs",
        "buttons",
        "selects",
        "links",
        "images",
        "dataAsin",
    )

    for key in collections:
        items = data.get(key)

        if isinstance(items, list):
            inventory[key] = [
                clean_element(item)
                for item in items
            ]
        else:
            inventory[key] = []

    # Nombre más cómodo para JSON
    inventory["data_asin"] = inventory.pop(
        "dataAsin",
        [],
    )

    return inventory


# ============================================================
# SCREENSHOT / HTML
# ============================================================

def save_screenshot(data, output_dir: Path):
    screenshot = data.get("screenshot")

    if not isinstance(screenshot, dict):
        return False

    encoded = screenshot.get("base64")

    if not encoded:
        return False

    try:
        image_bytes = base64.b64decode(encoded)

        path = output_dir / "screenshot.png"
        path.write_bytes(image_bytes)

        return True

    except Exception as exc:
        print(
            f"Warning: no pude guardar screenshot: {exc}",
            file=sys.stderr,
        )

        return False


def save_html(data, output_dir: Path):
    page_html = data.get("pageHTML")

    if not isinstance(page_html, dict):
        return False

    html = page_html.get("html")

    if not html:
        return False

    path = output_dir / "page.html"

    path.write_text(
        html,
        encoding="utf-8",
    )

    return True


# ============================================================
# SENSITIVE DATA REDACTION
# ============================================================

def redact_string(value: str) -> str:
    """
    Evita que AMAZON_LOGIN termine accidentalmente
    dentro de los JSON que guardamos.
    """

    if not isinstance(value, str):
        return value

    if AMAZON_LOGIN:
        value = value.replace(
            AMAZON_LOGIN,
            "[REDACTED_AMAZON_LOGIN]",
        )

    return value


def redact_recursive(value):
    if isinstance(value, str):
        return redact_string(value)

    if isinstance(value, list):
        return [
            redact_recursive(item)
            for item in value
        ]

    if isinstance(value, dict):
        return {
            key: redact_recursive(item)
            for key, item in value.items()
        }

    return value


# ============================================================
# METADATA
# ============================================================

def extract_title(data):
    title = data.get("title")

    if isinstance(title, dict):
        return title.get("text")

    return None


def extract_navigation(data):
    """
    login_identifier.graphql usa actualmente
    el alias 'open' para goto().
    """

    navigation = data.get("open")

    if not isinstance(navigation, dict):
        return {}

    return navigation


def build_metadata(
    data,
    errors,
    requested_url,
    step_name,
):
    navigation = extract_navigation(data)

    metadata = {
        "step": step_name,
        "requested_url": requested_url,
        "initial_navigation_status": navigation.get(
            "status"
        ),
        "initial_navigation_url": navigation.get(
            "url"
        ),
        "initial_navigation_time": navigation.get(
            "time"
        ),
        "title": extract_title(data),
        "proxy_country": PROXY_COUNTRY,
        "stealth": True,
        "humanlike": True,
        "bql_errors": errors or [],
    }

    return redact_recursive(metadata)


# ============================================================
# BROWSERLESS
# ============================================================

def run_browserql(query: str, variables: dict):
    endpoint = (
        BROWSERLESS_URL.rstrip("/")
        + "/stealth/bql"
    )

    tokens = [
        t.strip()
        for t in BROWSERLESS_TOKEN.split(",")
        if t.strip()
    ]

    last_error = None

    for token in tokens:
    params = {
        # Browserless native residential proxy
        "proxy": "residential",

        # Mexico
        "proxyCountry": PROXY_COUNTRY,

        # Maintain proxy identity during this session
        "proxySticky": "true",

        # Match locale/timezone/etc. to proxy
        "proxyLocaleMatch": "true",

        # Browserless human-like interaction
        "humanlike": "true",

        # Reduce advertising/tracker noise
        "blockAds": "true",

        # Remove common cookie/consent overlays
        "blockConsentModals": "true",

        # Session limit
        "timeout": "120000",
    }

        payload = {
            "query": query,
            "variables": variables,
        }

        response = requests.post(
            endpoint,
            params=params,
            json=payload,
            timeout=135,
        )

        if response.status_code == 429:
            last_error = response
            continue

        response.raise_for_status()

        try:
            return response.json()

        except ValueError as exc:
            raise RuntimeError(
                "Browserless devolvió una respuesta "
                "que no es JSON."
            ) from exc

    if last_error is not None:
        print(
            "WARNING: Browserless concurrency limit reached (429). "
            "Se han agotado los tokens disponibles. "
            "Espera unos minutos o actualiza BROWSERLESS_TOKEN en .env "
            "con claves adicionales.",
            file=sys.stderr,
        )
        last_error.raise_for_status()

    raise RuntimeError(
        "No se pudo completar la solicitud a Browserless. "
        "Verifica tu BROWSERLESS_TOKEN en .env"
    )


# ============================================================
# OUTPUT SUMMARY
# ============================================================

def print_summary(
    output_dir,
    data,
    errors,
    inventory,
):
    print("=" * 65)
    print(" STEP COMPLETED")
    print("=" * 65)

    headings = inventory.get(
        "headings",
        [],
    )

    if headings:
        print("Headings:")

        for heading in headings[:5]:
            text = heading.get("innerText")

            if text:
                print(f"  - {text}")

    print("Elements:")

    for key in (
        "forms",
        "inputs",
        "buttons",
        "selects",
        "links",
        "images",
        "data_asin",
    ):
        print(
            f"  {key:<12} "
            f"{len(inventory.get(key, []))}"
        )

    if errors:
        print(
            f"BQL errors/warnings: {len(errors)}"
        )

        for error in errors[:10]:
            if isinstance(error, dict):
                path = error.get("path")
                message = error.get(
                    "message",
                    "Unknown BQL error",
                )

                print(
                    f"  {path}: {message}"
                )
            else:
                print(f"  {error}")

    else:
        print("BQL errors/warnings: 0")

    print(f"Output: {output_dir}")


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta un paso BrowserQL y captura "
            "el estado resultante."
        )
    )

    parser.add_argument(
        "query",
        nargs="?",
        default="login_identifier.graphql",
        help=(
            "Query dentro de queries/ "
            "(default: login_identifier.graphql)"
        ),
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_AMAZON_LOGIN_ENTRY,
        help="URL inicial del flujo.",
    )

    parser.add_argument(
        "--name",
        default="login_identifier",
        help="Nombre del step para output/.",
    )

    args = parser.parse_args()

    query_file = QUERIES_DIR / args.query

    try:
        validate_config(
            query_file,
            args.url,
        )

        query = load_query(query_file)

        output_dir = create_output_dir(
            args.name
        )

        print("=" * 65)
        print(" BROWSERLESS STEP RUNNER")
        print("=" * 65)
        print(f"Step:     {args.name}")
        print(f"Query:    {query_file.name}")
        print(f"URL:      {args.url}")
        print(
            f"Proxy:    residential/"
            f"{PROXY_COUNTRY}"
        )
        print("Stealth:  enabled")
        print("Human:    enabled")
        print(
            "Login:    loaded from .env "
            "(not displayed)"
        )
        print("Executing...")

        # IMPORTANTE:
        # Las credenciales sólo existen en memoria.
        # No escribimos este diccionario a disco.
        variables = {
            "url": args.url,
            "login": AMAZON_LOGIN,
            "password": AMAZON_PASSWORD,
        }

        result = run_browserql(
            query,
            variables,
        )

        data = result.get("data") or {}
        errors = result.get("errors") or []

        reconnect_data = data.get("reconnect") or {}

        browserql_endpoint = reconnect_data.get(
            "browserQLEndpoint"
        )

        if browserql_endpoint:
            session_file = ROOT / ".browserless_session"

            session_file.write_text(
                browserql_endpoint,
                encoding="utf-8",
            )

            try:
                session_file.chmod(0o600)
            except OSError:
                pass

            print(
                "Session:  reconnect endpoint saved "
                "to .browserless_session"
            )

        # Redactamos antes de escribir cualquier
        # respuesta completa de Browserless.
        safe_result = redact_recursive(result)

        # No guardar el endpoint de reconexión
        # dentro de result.json.
        if isinstance(safe_result.get("data"), dict):
            safe_result["data"].pop("reconnect", None)

        inventory = build_element_inventory(
            data
        )

        inventory = redact_recursive(
            inventory
        )

        metadata = build_metadata(
            data=data,
            errors=errors,
            requested_url=args.url,
            step_name=args.name,
        )

        write_json(
            output_dir / "result.json",
            safe_result,
        )

        write_json(
            output_dir / "elements.json",
            inventory,
        )

        write_json(
            output_dir / "metadata.json",
            metadata,
        )

        # HTML también puede contener el login
        # después de introducirlo, así que primero
        # lo redactamos.
        page_html = data.get("pageHTML")

        if (
            isinstance(page_html, dict)
            and page_html.get("html")
        ):
            safe_html = redact_string(
                page_html["html"]
            )

            (
                output_dir / "page.html"
            ).write_text(
                safe_html,
                encoding="utf-8",
            )

        save_screenshot(
            data,
            output_dir,
        )

        print_summary(
            output_dir,
            data,
            errors,
            inventory,
        )

        # GraphQL puede devolver HTTP 200 pero
        # contener errores internos.
        if errors:
            sys.exit(2)

    except requests.exceptions.HTTPError as exc:
        print(
            f"HTTP error: {exc}",
            file=sys.stderr,
        )

        if exc.response is not None:
            # Limitamos el cuerpo para no inundar
            # la terminal.
            body = exc.response.text[:2000]

            print(
                body,
                file=sys.stderr,
            )

        sys.exit(1)

    except requests.exceptions.Timeout:
        print(
            "Timeout comunicando con Browserless.",
            file=sys.stderr,
        )
        sys.exit(1)

    except KeyboardInterrupt:
        print(
            "\nInterrumpido.",
            file=sys.stderr,
        )
        sys.exit(130)

    except Exception as exc:
        print(
            f"Error: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
